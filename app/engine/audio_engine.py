import time
import math
import threading
import queue
from pathlib import Path
from typing import List, Dict

import numpy as np
import soundfile as sf
import logging

try:
    import jack  # Provided by JACK-Client package
except Exception:  # pragma: no cover
    jack = None

logger = logging.getLogger(__name__)

class AudioEngine:
    """
    JACK-based 6-channel capture, per-channel gain/mute, fast routing of a selected
    channel to stereo monitor (headset), per-channel recording, and VU (peak/RMS).

    Notes:
    - Inports: 6 mono inputs registered as 'in_1'..'in_6'. Connect from device capture ports.
    - Outports: dynamic (2 or N from config). Ports are named 'out_l', 'out_r', then 'out_3'..'out_N'.
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
        # Size of recording queues (configurable for memory/performance balance)
        self.record_queue_size = max(16, int(config.get('record_queue_size', 64)))
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
        # Accumulators for RMS calculation outside RT thread
        self._vu_sumsq_temp = np.zeros(self.num_inputs, dtype=np.float64)
        self._vu_count_temp = np.zeros(self.num_inputs, dtype=np.int64)
        self._vu_stats_lock = threading.Lock()
        # Lock for publishing VU arrays independently of main state lock
        self._vu_pub_lock = threading.Lock()
        # VU sampling control
        self._last_vu_time = 0.0
        # VU smoothing factors (exponential smoothing for stable display)
        self._vu_peak_smooth = np.zeros(self.num_inputs, dtype=np.float32)
        self._vu_rms_smooth = np.zeros(self.num_inputs, dtype=np.float32)
        self._vu_smooth_alpha = 0.3  # Smoothing factor (0.0 = no smoothing, 1.0 = instant)
        # VU sampling thread
        self._vu_thread: threading.Thread | None = None
        # VU sampling stop event
        self._vu_stop = threading.Event()
        # VU sampling started flag
        self._vu_started = False

        # Recording (raw input) - improved queue size for better reliability
        # Queues for each channel's recording data (configurable size for memory/performance balance)
        self._rec_queues: List[queue.Queue] = [queue.Queue(maxsize=self.record_queue_size) for _ in range(self.num_inputs)]
        # Thread objects for recording workers
        self._rec_threads: List[threading.Thread] = []
        # Event to signal recording threads to stop
        self._rec_stop = threading.Event()
        # Count of dropped buffers for each channel (improved tracking)
        self._rec_drop_counts = np.zeros(self.num_inputs, dtype=np.int64)
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
                self._rec_started = False

            # Stop VU sampling thread if it was started
            if self._vu_started:
                self._vu_stop.set()
                if self._vu_thread:
                    self._vu_thread.join(timeout=1)
                self._vu_started = False
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
        # Read main state under main lock
        with self._lock:
            sr = self.client.samplerate
            bs = self.client.blocksize
            sel_ch = int(self.selected_ch + 1)
            gains_linear = self.gains.tolist()
            gains_db = [linear_to_db(g) for g in self.gains]
            mutes = self.mutes.astype(bool).tolist()
            recording = bool(self.record_enabled)
            drops = self._rec_drop_counts.tolist()

        # Read VU arrays under dedicated VU lock (no main lock held)
        with self._vu_pub_lock:
            vu_peak = self.vu_peak.tolist()
            vu_rms = self.vu_rms.tolist()

        return {
            'samplerate': sr,
            'frames_per_period': bs,
            'selected_channel': sel_ch,
            'gains_linear': gains_linear,
            'gains_db': gains_db,
            'mutes': mutes,
            'vu_peak': vu_peak,
            'vu_rms': vu_rms,
            'recording': recording,
            'rec_dropped_buffers': drops,
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
            # RMS accumulation moved to separate thread for ~20 Hz sampling
            if frames > 0:
                # Track peak value (simple absolute value max)
                current_peak = float(np.max(np.abs(post_gain)))
                # Keep the highest peak since last VU update
                self._vu_peak_temp[i] = max(self._vu_peak_temp[i], current_peak)
                # Accumulate sum of squares and sample count using try-lock to avoid blocking RT
                if self._vu_stats_lock.acquire(False):
                    try:
                        self._vu_sumsq_temp[i] += float(np.dot(post_gain, post_gain))
                        self._vu_count_temp[i] += frames
                    finally:
                        self._vu_stats_lock.release()
            # If this is the selected channel, prepare it for routing (respect mute -> silence)
            if i == sel:
                route_buf = post_gain

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
                except queue.Full:
                    # If queue is full, increment drop counter (no logging in RT thread)
                    with self._lock:
                        self._rec_drop_counts[i] += 1

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
                        logger.warning("No capture ports found, retrying in %ss... (attempt %s/%s)", retry_delay, attempt + 1, max_retries)
                        time.sleep(retry_delay)
                        continue
                    else:
                        logger.warning("No physical capture ports found after retries")
                        return

                # Filter by name match - Audio Injector Octo compatibility
                # Look for Audio Injector Octo ports first, then fallback to generic matches
                octo_ports = [p for p in cap_ports if 'audioinjector' in p.name.lower() or 'octo' in p.name.lower()]
                if octo_ports:
                    cap_ports = octo_ports
                    logger.info("Found %s Audio Injector Octo capture ports", len(octo_ports))
                else:
                    # Fallback to configured match or generic 'capture'
                    cap_ports = [p for p in cap_ports if self.capture_match in p.name.lower() or 'capture' in p.name.lower()]
                    logger.info("Using %s generic capture ports (match: '%s')", len(cap_ports), self.capture_match)

                # Fallback to any outputs if filter too strict
                # If we don't have enough matching ports, get all output ports
                if len(cap_ports) < self.num_inputs:
                    logger.warning("Only %s capture ports available, need %s", len(cap_ports), self.num_inputs)
                    cap_ports = self.client.get_ports(is_output=True)

                # Connect matching ports to our input ports
                connections_made = 0
                for i in range(min(self.num_inputs, len(cap_ports))):
                    try:
                        self.client.connect(cap_ports[i], self.inports[i])
                        connections_made += 1
                        logger.info("Connected capture port '%s' to input %s", cap_ports[i].name, i + 1)
                    except jack.JackError as e:
                        logger.error("Failed to connect capture port '%s' to input %s: %s", cap_ports[i].name, i + 1, e)

                logger.info("Successfully connected %s/%s capture ports", connections_made, self.num_inputs)
                return  # Success, exit retry loop

            except Exception as e:
                logger.error("Error during capture port connection (attempt %s/%s): %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    logger.warning("Failed to connect capture ports after all retries")

    def _auto_connect_outputs(self):
        """
        Automatically connect our output ports to physical playback ports based on name matching.
        """
        # Get all physical input ports (these are the playback ports to audio devices)
        pb_ports = self.client.get_ports(is_input=True, is_physical=True)
        # Filter by name match - Audio Injector Octo compatibility
        # Look for Audio Injector Octo ports first, then fallback to generic matches
        octo_ports = [p for p in pb_ports if 'audioinjector' in p.name.lower() or 'octo' in p.name.lower()]
        if octo_ports:
            pb_ports = octo_ports
        else:
            # Fallback to configured match or generic 'playback'
            pb_ports = [p for p in pb_ports if self.playback_match in p.name.lower() or 'playback' in p.name.lower()]
        
        # Fallback to any inputs if filter too strict
        if len(pb_ports) < self.num_outputs:
            pb_ports = self.client.get_ports(is_input=True)
        
        # Connect our outputs to available playback ports
        num_connections = min(self.num_outputs, len(pb_ports))
        for i in range(num_connections):
            try:
                self.client.connect(self.outports[i], pb_ports[i])
            except jack.JackError:
                # Ignore connection errors
                pass

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

                # Copy peak tracking buffers for processing (no main-state lock to avoid contention)
                peak_temp = self._vu_peak_temp.copy()

                # Snapshot and reset accumulators for RMS outside RT thread
                with self._vu_stats_lock:
                    sumsq = self._vu_sumsq_temp.copy()
                    counts = self._vu_count_temp.copy()
                    self._vu_sumsq_temp.fill(0.0)
                    self._vu_count_temp.fill(0)

                # Compute RMS using accumulated sums
                rms_values = np.zeros(self.num_inputs, dtype=np.float32)
                nz = counts > 0
                if np.any(nz):
                    rms_values[nz] = np.sqrt((sumsq[nz] / counts[nz]).astype(np.float32))

                # Update VU values with calculated data
                # Apply exponential smoothing and publish VU values (no main-state lock)
                self._vu_peak_smooth = self._vu_smooth_alpha * peak_temp + (1 - self._vu_smooth_alpha) * self._vu_peak_smooth
                self._vu_rms_smooth = self._vu_smooth_alpha * rms_values + (1 - self._vu_smooth_alpha) * self._vu_rms_smooth
                # Publish smoothed values under VU publication lock
                with self._vu_pub_lock:
                    self.vu_peak[:] = self._vu_peak_smooth
                    self.vu_rms[:] = self._vu_rms_smooth
                # Reset peak tracking for next sampling period
                self._vu_peak_temp.fill(0.0)

                # Update timing
                self._last_vu_time = time.time()

            except Exception as e:
                # Log error but continue VU sampling
                logger.exception("VU worker error: %s", e)
                continue


    def _start_recording_threads(self):
        """
        Start recording threads for each input channel.
        """
        # Create directory per run
        ts = time.strftime('%Y%m%d_%H%M%S')
        session_dir = self.record_dir / ts
        session_dir.mkdir(parents=True, exist_ok=True)

        # Clear stop event and reset threads list
        self._rec_stop.clear()
        self._rec_threads = []

        # Start a recording thread for each input channel
        for i in range(self.num_inputs):
            ch_path = session_dir / f'channel_{i + 1}.wav'
            # Not using daemon=True to ensure proper buffer flushing on shutdown
            t = threading.Thread(target=self._rec_worker, args=(i, ch_path), daemon=False)
            self._rec_threads.append(t)
            t.start()

        # Mark recording as started
        self._rec_started = True

    def _rec_worker(self, ch_index: int, path: Path):
        """
        Worker function for recording audio from a specific channel.
        """
        with sf.SoundFile(str(path), mode='w', samplerate=self.client.samplerate, channels=1, subtype='PCM_24', format='WAV') as f:
            while not self._rec_stop.is_set():
                try:
                    block = self._rec_queues[ch_index].get(timeout=0.2)
                except queue.Empty:
                    continue
                # Ensure shape (n, 1) for proper WAV file format
                if block.ndim == 1:
                    f.write(block.reshape(-1, 1))
                else:
                    f.write(block)

# --------------- Utils ---------------

def db_to_linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def linear_to_db(lin: float) -> float:
    lin = max(1e-12, float(lin))
    return 20.0 * math.log10(lin)
