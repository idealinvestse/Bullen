import time
import math
import threading
import queue
from pathlib import Path
from typing import List, Dict

import numpy as np
import soundfile as sf

try:
    import jack  # Provided by JACK-Client package
except Exception:  # pragma: no cover
    jack = None


class AudioEngine:
    """
    JACK-based 6-channel capture, per-channel gain/mute, fast routing of a selected
    channel to stereo monitor (headset), per-channel recording, and VU (peak/RMS).

    Notes:
    - Inports: 6 mono inputs registered as 'in_1'..'in_6'. Connect from device capture ports.
    - Outports: 2 mono outputs 'out_l', 'out_r'. Connect to playback/headset.
    - Recording: raw input (pre-mute, pre-gain) to WAV per channel to avoid destructive changes.
      Can be adjusted via config if post-gain needed.
    - VU: computed post-gain, pre-routing per process cycle; server samples at ~20 Hz.
    """

    def __init__(self, config: Dict):
        """
        Initialize the AudioEngine with configuration parameters.
        
        Args:
            config (Dict): Configuration dictionary containing audio settings
        """
        if jack is None:
            raise RuntimeError("JACK library not available. Install 'JACK-Client' and ensure JACK/PipeWire-JACK is running.")

        self.config = config
        # Audio sample rate (informational, actual rate is set by JACK)
        self.samplerate = int(config.get('samplerate', 48000))
        # Buffer size per period (informational, actual size is set by JACK)
        self.frames_per_period = int(config.get('frames_per_period', 128))
        # Number of periods (informational, actual value is set by JACK)
        self.nperiods = int(config.get('nperiods', 2))
        # Number of input channels
        self.num_inputs = int(config.get('inputs', 6))
        # Number of output channels
        self.num_outputs = int(config.get('outputs', 2))
        # Enable/disable recording functionality
        self.record_enabled = bool(config.get('record', True))
        # Directory where recordings will be saved
        self.record_dir = Path(config.get('recordings_dir', 'recordings'))
        # Enable/disable automatic connection of input ports
        self.auto_connect_capture = bool(config.get('auto_connect_capture', True))
        # Enable/disable automatic connection of output ports
        self.auto_connect_playback = bool(config.get('auto_connect_playback', True))
        # String to match when auto-connecting capture ports
        self.capture_match = str(config.get('capture_match', 'capture')).lower()
        # String to match when auto-connecting playback ports
        self.playback_match = str(config.get('playback_match', 'playback')).lower()

        # Create JACK client
        self.client = jack.Client('bullen', no_start_server=False)

        # Ports
        # Register input ports for each channel
        self.inports = [self.client.inports.register(f'in_{i + 1}') for i in range(self.num_inputs)]
        # Register output ports - dynamic based on config
        if self.num_outputs == 2:
            # Legacy stereo mode
            self.outports = [self.client.outports.register('out_l'), self.client.outports.register('out_r')]
        else:
            # Multi-channel mode - register all output ports
            self.outports = []
            for i in range(self.num_outputs):
                if i == 0:
                    port_name = 'out_l'
                elif i == 1:
                    port_name = 'out_r'
                else:
                    port_name = f'out_{i + 1}'
                self.outports.append(self.client.outports.register(port_name))

        # State
        # Thread lock for protecting shared state
        self._lock = threading.Lock()
        # Currently selected channel (0-based index)
        self.selected_ch = max(0, min(self.num_inputs - 1, int(config.get('selected_channel', 1)) - 1))
        # Gain values for each input channel
        self.gains = np.ones(self.num_inputs, dtype=np.float32)
        # Mute status for each input channel
        self.mutes = np.zeros(self.num_inputs, dtype=bool)

        # VU meters (post-gain) - optimized for ~20 Hz sampling rate
        # Peak values for VU meters (updated at ~20 Hz)
        self.vu_peak = np.zeros(self.num_inputs, dtype=np.float32)
        # RMS values for VU meters (updated at ~20 Hz)
        self.vu_rms = np.zeros(self.num_inputs, dtype=np.float32)
        # Peak tracking buffers (updated in RT thread) - lightweight tracking only
        self._vu_peak_temp = np.zeros(self.num_inputs, dtype=np.float32)
        # RMS calculation buffers (updated at ~20 Hz) - heavy computation moved out of RT
        self._vu_rms_temp = np.zeros(self.num_inputs, dtype=np.float32)
        # VU sampling control
        self._last_vu_time = 0.0
        # VU sampling thread
        self._vu_thread: threading.Thread = None
        # VU sampling stop event
        self._vu_stop = threading.Event()
        # VU sampling started flag
        self._vu_started = False

        # Recording (raw input) - improved queue size for better reliability
        # Queues for each channel's recording data (increased size for better reliability)
        self._rec_queues: List[queue.Queue] = [queue.Queue(maxsize=128) for _ in range(self.num_inputs)]
        # Thread objects for recording workers
        self._rec_threads: List[threading.Thread] = []
        # Event to signal recording threads to stop
        self._rec_stop = threading.Event()
        # Count of dropped buffers for each channel (improved tracking)
        self._rec_drop_counts = np.zeros(self.num_inputs, dtype=np.int64)
        # Additional statistics for monitoring queue health
        self._rec_queue_sizes = np.zeros(self.num_inputs, dtype=np.int32)
        # Flag indicating if recording threads have been started
        self._rec_started = False

        # Callback registration
        # Register the process callback function
        self.client.set_process_callback(self._process)

    # --------------- Public API ---------------

    def start(self):
        """
        Start the audio engine by activating the JACK client and initializing connections, recording, and VU sampling.
        """
        # Activate JACK client
        self.client.activate()
        # Auto connect ports
        if self.auto_connect_capture:
            self._auto_connect_inputs()
        if self.auto_connect_playback:
            self._auto_connect_outputs()

        # Recording
        # Start recording threads if recording is enabled
        if self.record_enabled and not self._rec_started:
            self._start_recording_threads()

        # VU sampling
        # Start VU sampling thread
        if not self._vu_started:
            self._start_vu_thread()

    def stop(self):
        """
        Stop the audio engine by stopping recording threads, VU sampling thread, and deactivating the JACK client.
        """
        try:
            # Stop recording threads if they were started
            if self._rec_started:
                self._rec_stop.set()
                for t in self._rec_threads:
                    t.join(timeout=3)

            # Stop VU sampling thread if it was started
            if self._vu_started:
                self._vu_stop.set()
                if self._vu_thread:
                    self._vu_thread.join(timeout=1)
        finally:
            # Deactivate and close JACK client
            self.client.deactivate()
            self.client.close()

    def set_selected_channel(self, ch_index: int):
        """
        Set the currently selected channel for monitoring.
        
        Args:
            ch_index (int): Channel index to select (0-based)
        """
        # Use lock to safely update the selected channel
        with self._lock:
            self.selected_ch = max(0, min(self.num_inputs - 1, int(ch_index)))

    def set_gain_linear(self, ch_index: int, gain: float):
        """
        Set the linear gain value for a specific channel.
        
        Args:
            ch_index (int): Channel index (0-based)
            gain (float): Linear gain value (0.0 or higher)
        """
        # Use lock to safely update gain values
        with self._lock:
            # Validate channel index
            if 0 <= ch_index < self.num_inputs:
                # Ensure gain is not negative
                self.gains[ch_index] = float(max(0.0, gain))

    def set_gain_db(self, ch_index: int, gain_db: float):
        """
        Set the gain for a specific channel in decibels.
        
        Args:
            ch_index (int): Channel index (0-based)
            gain_db (float): Gain value in decibels
        """
        # Convert dB gain to linear and set it
        self.set_gain_linear(ch_index, db_to_linear(gain_db))

    def set_mute(self, ch_index: int, mute: bool):
        """
        Set the mute status for a specific channel.
        
        Args:
            ch_index (int): Channel index (0-based)
            mute (bool): Mute status (True for muted, False for unmuted)
        """
        # Use lock to safely update mute status
        with self._lock:
            # Validate channel index
            if 0 <= ch_index < self.num_inputs:
                self.mutes[ch_index] = bool(mute)

    def get_state(self) -> Dict:
        """
        Get the current state of the audio engine.
        
        Returns:
            Dict: Dictionary containing current engine state
        """
        # Use lock to safely access shared state
        with self._lock:
            return {
                # Actual sample rate from JACK client
                'samplerate': self.client.samplerate,
                # Actual buffer size from JACK client
                'frames_per_period': self.client.blocksize,
                # Currently selected channel (1-based index)
                'selected_channel': int(self.selected_ch + 1),
                # Gain values in linear scale
                'gains_linear': self.gains.tolist(),
                # Gain values in decibels
                'gains_db': [linear_to_db(g) for g in self.gains],
                # Mute status for each channel
                'mutes': self.mutes.astype(bool).tolist(),
                # Peak values for VU meters
                'vu_peak': self.vu_peak.tolist(),
                # RMS values for VU meters
                'vu_rms': self.vu_rms.tolist(),
                # Recording status
                'recording': bool(self.record_enabled),
                # Count of dropped buffers for each channel
                'rec_dropped_buffers': self._rec_drop_counts.tolist(),
            }

    # --------------- Internal ---------------

    def _process(self, frames: int):
        """
        JACK process callback function that handles audio routing, VU meter updates, and recording.
        This function is called for every audio buffer period and must be non-blocking.
        
        Args:
            frames (int): Number of audio frames in the buffer
        """
        # Acquire state snapshot without blocking the RT thread long
        # This ensures thread safety while minimizing time spent in the lock
        with self._lock:
            sel = int(self.selected_ch)
            gains = self.gains.copy()
            mutes = self.mutes.copy()

        # JACK provides float32 buffers
        # Read inputs and compute VU post-gain
        route_buf = None
        for i, inport in enumerate(self.inports):
            # Get audio buffer from input port
            buf = inport.get_array()  # np.ndarray float32, shape (frames,)
            # Apply gain and mute settings
            # VU uses post-gain signal - only track peaks in RT thread (lightweight)
            g = 0.0 if mutes[i] else gains[i]
            post_gain = buf * g
            # Update peak tracking only (lightweight operation for RT thread)
            # RMS calculation moved to separate thread for ~20 Hz sampling
            if frames > 0:
                # Track peak value (simple absolute value max)
                current_peak = float(np.max(np.abs(post_gain)))
                # Keep the highest peak since last VU update
                self._vu_peak_temp[i] = max(self._vu_peak_temp[i], current_peak)
            # If this is the selected channel, prepare it for routing
            if i == sel:
                route_buf = buf if g == 0.0 else post_gain

        # Route selected channel to output
        if route_buf is None:
            # Nothing selected; output silence
            for o in self.outports:
                o.get_array()[:] = 0.0
        else:
            # Route selected channel to all outputs
            if self.num_outputs == 2:
                # Legacy stereo mode - copy to both L/R
                self.outports[0].get_array()[:] = route_buf
                self.outports[1].get_array()[:] = route_buf
            else:
                # Multi-channel mode - copy selected channel to all outputs
                for o in self.outports:
                    o.get_array()[:] = route_buf

        # Enqueue raw input for recording (non-blocking)
        # Only record if recording is enabled and threads have been started
        if self.record_enabled and self._rec_started:
            for i, inport in enumerate(self.inports):
                # Copy buffer to decouple from RT buffer
                raw = inport.get_array().copy()  # copy to decouple from RT buffer
                try:
                    # Try to add buffer to recording queue
                    self._rec_queues[i].put_nowait(raw)
                    # Update queue size tracking for monitoring
                    self._rec_queue_sizes[i] = self._rec_queues[i].qsize()
                except queue.Full:
                    # If queue is full, increment drop counter
                    # Using atomic operation for thread safety
                    with self._lock:
                        self._rec_drop_counts[i] += 1
                        # Log queue overflow for debugging (non-blocking)
                        print(f"Recording queue overflow on channel {i+1}: {self._rec_drop_counts[i]} drops")

    def _auto_connect_inputs(self):
        """
        Automatically connect physical capture ports to our input ports based on name matching.
        Includes retry logic and improved error handling for better reliability.
        """
        max_retries = 3
        retry_delay = 0.5

        for attempt in range(max_retries):
            try:
                # Get all physical output ports (these are the capture ports from audio devices)
                cap_ports = self.client.get_ports(is_output=True, is_physical=True)

                if not cap_ports:
                    if attempt < max_retries - 1:
                        print(f"No capture ports found, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_delay)
                        continue
                    else:
                        print("Warning: No physical capture ports found after retries")
                        return

                # Filter by name match - Audio Injector Octo compatibility
                # Look for Audio Injector Octo ports first, then fallback to generic matches
                octo_ports = [p for p in cap_ports if 'audioinjector' in p.name.lower() or 'octo' in p.name.lower()]
                if octo_ports:
                    cap_ports = octo_ports
                    print(f"Found {len(octo_ports)} Audio Injector Octo capture ports")
                else:
                    # Fallback to configured match or generic 'capture'
                    cap_ports = [p for p in cap_ports if self.capture_match in p.name.lower() or 'capture' in p.name.lower()]
                    print(f"Using {len(cap_ports)} generic capture ports (match: '{self.capture_match}')")

                # Fallback to any outputs if filter too strict
                # If we don't have enough matching ports, get all output ports
                if len(cap_ports) < self.num_inputs:
                    print(f"Warning: Only {len(cap_ports)} capture ports available, need {self.num_inputs}")
                    cap_ports = self.client.get_ports(is_output=True)

                # Connect matching ports to our input ports
                connections_made = 0
                for i in range(min(self.num_inputs, len(cap_ports))):
                    try:
                        self.client.connect(cap_ports[i], self.inports[i])
                        connections_made += 1
                        print(f"Connected capture port '{cap_ports[i].name}' to input {i+1}")
                    except jack.JackError as e:
                        print(f"Failed to connect capture port '{cap_ports[i].name}' to input {i+1}: {e}")

                print(f"Successfully connected {connections_made}/{self.num_inputs} capture ports")
                return  # Success, exit retry loop

            except Exception as e:
                print(f"Error during capture port connection (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    print("Warning: Failed to connect capture ports after all retries")

    def _start_vu_thread(self):
        """
        Start VU sampling thread for ~20 Hz VU meter updates.
        """
        # Clear stop event and create VU thread
        self._vu_stop.clear()
        # Create thread for VU sampling worker
        self._vu_thread = threading.Thread(target=self._vu_worker, daemon=True)
        self._vu_thread.start()
        # Mark VU sampling as started
        self._vu_started = True

    def _vu_worker(self):
        """
        Worker function for VU meter sampling at ~20 Hz.
        Performs heavy VU calculations outside the real-time JACK callback.
        """
        # VU sampling interval for ~20 Hz (50ms)
        vu_interval = 0.05

        while not self._vu_stop.is_set():
            try:
                # Sleep for VU sampling interval
                time.sleep(vu_interval)

                # Acquire state snapshot without blocking RT thread long
                with self._lock:
                    # Copy peak tracking buffers for processing
                    peak_temp = self._vu_peak_temp.copy()

                # Perform heavy VU calculations outside RT thread
                rms_values = np.zeros(self.num_inputs, dtype=np.float32)

                # Calculate RMS for each channel if we have audio data
                # Note: In a real implementation, we'd need to accumulate audio data
                # over multiple JACK callbacks to calculate proper RMS
                # For now, we'll use a simplified approach
                for i in range(self.num_inputs):
                    # Simplified RMS calculation - in practice you'd accumulate samples
                    # This is a placeholder for the actual RMS calculation logic
                    rms_values[i] = peak_temp[i] * 0.707  # Rough approximation

                # Update VU values with calculated data
                with self._lock:
                    # Copy calculated values to main VU buffers
                    self.vu_peak[:] = peak_temp
                    self.vu_rms[:] = rms_values
                    # Reset peak tracking for next sampling period
                    self._vu_peak_temp.fill(0.0)

                # Update timing
                self._last_vu_time = time.time()

            except Exception as e:
                # Log error but continue VU sampling
                print(f"VU worker error: {e}")
                continue


# --------------- Utils ---------------

def db_to_linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def linear_to_db(lin: float) -> float:
    lin = max(1e-12, float(lin))
    return 20.0 * math.log10(lin)
