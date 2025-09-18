"""
Matrix Audio Engine - Enhanced audio engine with full matrix routing support
Allows any input to be routed to any output with individual gain control
"""
import time
import math
import threading
import queue
from pathlib import Path
from typing import List, Dict, Tuple
import numpy as np
import soundfile as sf

try:
    import jack
except Exception:
    jack = None


class MatrixAudioEngine:
    """
    Enhanced JACK-based audio engine with full matrix routing capabilities.
    Supports routing any input to any output with individual gain control.
    """
    
    def __init__(self, config: Dict):
        """Initialize the Matrix Audio Engine with configuration parameters."""
        if jack is None:
            raise RuntimeError("JACK library not available. Install 'JACK-Client' and ensure JACK is running.")
        
        self.config = config
        self.samplerate = int(config.get('samplerate', 48000))
        self.frames_per_period = int(config.get('frames_per_period', 128))
        self.nperiods = int(config.get('nperiods', 2))
        self.num_inputs = int(config.get('inputs', 6))
        self.num_outputs = int(config.get('outputs', 8))
        self.record_enabled = bool(config.get('record', True))
        self.record_dir = Path(config.get('recordings_dir', 'recordings'))
        self.auto_connect_capture = bool(config.get('auto_connect_capture', True))
        self.auto_connect_playback = bool(config.get('auto_connect_playback', True))
        self.capture_match = str(config.get('capture_match', 'capture')).lower()
        self.playback_match = str(config.get('playback_match', 'playback')).lower()
        
        # Create JACK client
        self.client = jack.Client('bullen_matrix', no_start_server=False)
        
        # Register ports
        self.inports = [self.client.inports.register(f'in_{i + 1}') for i in range(self.num_inputs)]
        self.outports = []
        for i in range(self.num_outputs):
            if i == 0:
                port_name = 'out_l'
            elif i == 1:
                port_name = 'out_r'
            else:
                port_name = f'out_{i + 1}'
            self.outports.append(self.client.outports.register(port_name))
        
        # Matrix routing state
        self._lock = threading.Lock()
        # Dictionary mapping (input_idx, output_idx) -> gain (0.0 to 2.0)
        self.routing_matrix: Dict[Tuple[int, int], float] = {}
        # Input channel settings
        self.input_gains = np.ones(self.num_inputs, dtype=np.float32)
        self.input_mutes = np.zeros(self.num_inputs, dtype=bool)
        # Output channel settings
        self.output_gains = np.ones(self.num_outputs, dtype=np.float32)
        self.output_mutes = np.zeros(self.num_outputs, dtype=bool)
        
        # VU meters
        self.vu_peak_in = np.zeros(self.num_inputs, dtype=np.float32)
        self.vu_rms_in = np.zeros(self.num_inputs, dtype=np.float32)
        self.vu_peak_out = np.zeros(self.num_outputs, dtype=np.float32)
        self.vu_rms_out = np.zeros(self.num_outputs, dtype=np.float32)
        self._vu_peak_in_temp = np.zeros(self.num_inputs, dtype=np.float32)
        self._vu_rms_in_temp = np.zeros(self.num_inputs, dtype=np.float32)
        self._vu_peak_out_temp = np.zeros(self.num_outputs, dtype=np.float32)
        self._vu_rms_out_temp = np.zeros(self.num_outputs, dtype=np.float32)
        self._last_vu_time = 0.0
        
        # Recording setup (same as original)
        self._rec_queues: List[queue.Queue] = [queue.Queue(maxsize=16) for _ in range(self.num_inputs)]
        self._rec_threads: List[threading.Thread] = []
        self._rec_stop = threading.Event()
        self._rec_drop_counts = np.zeros(self.num_inputs, dtype=np.int64)
        self._rec_started = False
        
        # Processed input buffers for routing
        self._input_buffers = [np.zeros(self.frames_per_period, dtype=np.float32) for _ in range(self.num_inputs)]
        
        # Register process callback
        self.client.set_process_callback(self._process)
    
    # --------------- Public API ---------------
    
    def start(self):
        """Start the audio engine."""
        self.client.activate()
        if self.auto_connect_capture:
            self._auto_connect_inputs()
        if self.auto_connect_playback:
            self._auto_connect_outputs()
        if self.record_enabled and not self._rec_started:
            self._start_recording_threads()
    
    def stop(self):
        """Stop the audio engine."""
        try:
            if self._rec_started:
                self._rec_stop.set()
                for t in self._rec_threads:
                    t.join(timeout=3)
        finally:
            self.client.deactivate()
            self.client.close()
    
    def set_route(self, input_idx: int, output_idx: int, gain: float = 1.0):
        """
        Set or update a routing connection.
        
        Args:
            input_idx: Input channel index (0-based)
            output_idx: Output channel index (0-based)
            gain: Routing gain (0.0 = off, 1.0 = unity)
        """
        with self._lock:
            if 0 <= input_idx < self.num_inputs and 0 <= output_idx < self.num_outputs:
                if gain > 0.001:  # Threshold for "active" routing
                    self.routing_matrix[(input_idx, output_idx)] = float(gain)
                else:
                    # Remove routing if gain is effectively zero
                    self.routing_matrix.pop((input_idx, output_idx), None)
    
    def clear_route(self, input_idx: int, output_idx: int):
        """Remove a routing connection."""
        with self._lock:
            self.routing_matrix.pop((input_idx, output_idx), None)
    
    def clear_all_routes(self):
        """Clear all routing connections."""
        with self._lock:
            self.routing_matrix.clear()
    
    def get_routes(self) -> Dict[Tuple[int, int], float]:
        """Get current routing matrix."""
        with self._lock:
            return self.routing_matrix.copy()
    
    def set_input_gain(self, channel: int, gain_linear: float):
        """Set input channel gain (linear)."""
        with self._lock:
            if 0 <= channel < self.num_inputs:
                self.input_gains[channel] = float(max(0.0, gain_linear))
    
    def set_input_gain_db(self, channel: int, gain_db: float):
        """Set input channel gain (dB)."""
        self.set_input_gain(channel, db_to_linear(gain_db))
    
    def set_input_mute(self, channel: int, mute: bool):
        """Set input channel mute."""
        with self._lock:
            if 0 <= channel < self.num_inputs:
                self.input_mutes[channel] = bool(mute)
    
    def set_output_gain(self, channel: int, gain_linear: float):
        """Set output channel gain (linear)."""
        with self._lock:
            if 0 <= channel < self.num_outputs:
                self.output_gains[channel] = float(max(0.0, gain_linear))
    
    def set_output_mute(self, channel: int, mute: bool):
        """Set output channel mute."""
        with self._lock:
            if 0 <= channel < self.num_outputs:
                self.output_mutes[channel] = bool(mute)
    
    def get_state(self) -> Dict:
        """Get current engine state including routing matrix."""
        with self._lock:
            # Convert routing matrix to list format for JSON
            routes = [
                {"input": i, "output": o, "gain": g}
                for (i, o), g in self.routing_matrix.items()
            ]
            
            return {
                'samplerate': self.client.samplerate,
                'frames_per_period': self.client.blocksize,
                'routing': routes,
                'input_gains_linear': self.input_gains.tolist(),
                'input_gains_db': [linear_to_db(g) for g in self.input_gains],
                'input_mutes': self.input_mutes.astype(bool).tolist(),
                'output_gains_linear': self.output_gains.tolist(),
                'output_mutes': self.output_mutes.astype(bool).tolist(),
                'vu_peak_in': self.vu_peak_in.tolist(),
                'vu_rms_in': self.vu_rms_in.tolist(),
                'vu_peak_out': self.vu_peak_out.tolist(),
                'vu_rms_out': self.vu_rms_out.tolist(),
                'recording': bool(self.record_enabled),
                'rec_dropped_buffers': self._rec_drop_counts.tolist(),
            }
    
    def load_routing_preset(self, preset: Dict):
        """Load a routing preset."""
        with self._lock:
            self.routing_matrix.clear()
            if 'routes' in preset:
                for route in preset['routes']:
                    input_idx = route.get('input')
                    output_idx = route.get('output')
                    gain = route.get('gain', 1.0)
                    if input_idx is not None and output_idx is not None:
                        self.routing_matrix[(input_idx, output_idx)] = gain
    
    # --------------- Internal ---------------
    
    def _process(self, frames: int):
        """JACK process callback with matrix routing."""
        # Get state snapshot
        with self._lock:
            routing = self.routing_matrix.copy()
            in_gains = self.input_gains.copy()
            in_mutes = self.input_mutes.copy()
            out_gains = self.output_gains.copy()
            out_mutes = self.output_mutes.copy()
        
        # Process inputs
        for i, inport in enumerate(self.inports):
            buf = inport.get_array()
            
            # Apply input gain and mute
            g = 0.0 if in_mutes[i] else in_gains[i]
            self._input_buffers[i] = buf * g
            
            # Update input VU meters
            if frames > 0:
                self._vu_peak_in_temp[i] = float(np.max(np.abs(self._input_buffers[i])))
                self._vu_rms_in_temp[i] = float(np.sqrt(np.mean(self._input_buffers[i] ** 2)))
        
        # Clear output buffers
        output_buffers = [np.zeros(frames, dtype=np.float32) for _ in range(self.num_outputs)]
        
        # Apply routing matrix
        for (input_idx, output_idx), route_gain in routing.items():
            if input_idx < self.num_inputs and output_idx < self.num_outputs:
                # Add routed signal to output buffer
                output_buffers[output_idx] += self._input_buffers[input_idx] * route_gain
        
        # Write to outputs and calculate output VU
        for o, outport in enumerate(self.outports):
            # Apply output gain and mute
            g = 0.0 if out_mutes[o] else out_gains[o]
            final_output = output_buffers[o] * g
            
            # Soft clipping to prevent harsh distortion
            final_output = np.tanh(final_output)
            
            # Write to JACK port
            outport.get_array()[:] = final_output
            
            # Update output VU meters
            if frames > 0:
                self._vu_peak_out_temp[o] = float(np.max(np.abs(final_output)))
                self._vu_rms_out_temp[o] = float(np.sqrt(np.mean(final_output ** 2)))
        
        # Recording (same as original)
        if self.record_enabled and self._rec_started:
            for i, inport in enumerate(self.inports):
                raw = inport.get_array().copy()
                try:
                    self._rec_queues[i].put_nowait(raw)
                except queue.Full:
                    with self._lock:
                        self._rec_drop_counts[i] += 1
        
        # Update VU meters
        with self._lock:
            self.vu_peak_in[:] = self._vu_peak_in_temp
            self.vu_rms_in[:] = self._vu_rms_in_temp
            self.vu_peak_out[:] = self._vu_peak_out_temp
            self.vu_rms_out[:] = self._vu_rms_out_temp
        
        self._last_vu_time = time.time()
    
    def _auto_connect_inputs(self):
        """Auto-connect input ports."""
        cap_ports = self.client.get_ports(is_output=True, is_physical=True)
        octo_ports = [p for p in cap_ports if 'audioinjector' in p.name.lower() or 'octo' in p.name.lower()]
        if octo_ports:
            cap_ports = octo_ports
        else:
            cap_ports = [p for p in cap_ports if self.capture_match in p.name.lower() or 'capture' in p.name.lower()]
        
        if len(cap_ports) < self.num_inputs:
            cap_ports = self.client.get_ports(is_output=True)
        
        for i in range(min(self.num_inputs, len(cap_ports))):
            try:
                self.client.connect(cap_ports[i], self.inports[i])
            except jack.JackError:
                pass
    
    def _auto_connect_outputs(self):
        """Auto-connect output ports."""
        pb_ports = self.client.get_ports(is_input=True, is_physical=True)
        octo_ports = [p for p in pb_ports if 'audioinjector' in p.name.lower() or 'octo' in p.name.lower()]
        if octo_ports:
            pb_ports = octo_ports
        else:
            pb_ports = [p for p in pb_ports if self.playback_match in p.name.lower() or 'playback' in p.name.lower()]
        
        if len(pb_ports) < self.num_outputs:
            pb_ports = self.client.get_ports(is_input=True)
        
        num_connections = min(self.num_outputs, len(pb_ports))
        for i in range(num_connections):
            try:
                self.client.connect(self.outports[i], pb_ports[i])
            except jack.JackError:
                pass
    
    def _start_recording_threads(self):
        """Start recording threads."""
        ts = time.strftime('%Y%m%d_%H%M%S')
        session_dir = self.record_dir / ts
        session_dir.mkdir(parents=True, exist_ok=True)
        
        self._rec_stop.clear()
        self._rec_threads = []
        for i in range(self.num_inputs):
            ch_path = session_dir / f'channel_{i + 1}.wav'
            t = threading.Thread(target=self._rec_worker, args=(i, ch_path), daemon=False)
            self._rec_threads.append(t)
            t.start()
        self._rec_started = True
    
    def _rec_worker(self, ch_index: int, path: Path):
        """Recording worker thread."""
        with sf.SoundFile(str(path), mode='w', samplerate=self.client.samplerate, 
                          channels=1, subtype='PCM_24', format='WAV') as f:
            while not self._rec_stop.is_set():
                try:
                    block = self._rec_queues[ch_index].get(timeout=0.2)
                except queue.Empty:
                    continue
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
