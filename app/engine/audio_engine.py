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
        if jack is None:
            raise RuntimeError("JACK library not available. Install 'JACK-Client' and ensure JACK/PipeWire-JACK is running.")

        self.config = config
        self.samplerate = int(config.get('samplerate', 48000))
        self.frames_per_period = int(config.get('frames_per_period', 128))
        self.nperiods = int(config.get('nperiods', 2))
        self.num_inputs = int(config.get('inputs', 6))
        self.num_outputs = int(config.get('outputs', 2))
        self.record_enabled = bool(config.get('record', True))
        self.record_dir = Path(config.get('recordings_dir', 'recordings'))
        self.auto_connect_capture = bool(config.get('auto_connect_capture', True))
        self.auto_connect_playback = bool(config.get('auto_connect_playback', True))
        self.capture_match = str(config.get('capture_match', 'capture')).lower()
        self.playback_match = str(config.get('playback_match', 'playback')).lower()

        self.client = jack.Client('bullen', no_start_server=False)

        # Ports
        self.inports = [self.client.inports.register(f'in_{i + 1}') for i in range(self.num_inputs)]
        self.outports = [self.client.outports.register('out_l'), self.client.outports.register('out_r')]

        # State
        self._lock = threading.Lock()
        self.selected_ch = max(0, min(self.num_inputs - 1, int(config.get('selected_channel', 1)) - 1))
        self.gains = np.ones(self.num_inputs, dtype=np.float32)
        self.mutes = np.zeros(self.num_inputs, dtype=bool)

        # VU meters (post-gain)
        self.vu_peak = np.zeros(self.num_inputs, dtype=np.float32)
        self.vu_rms = np.zeros(self.num_inputs, dtype=np.float32)
        self._last_vu_time = 0.0

        # Recording (raw input)
        self._rec_queues: List[queue.Queue] = [queue.Queue(maxsize=16) for _ in range(self.num_inputs)]
        self._rec_threads: List[threading.Thread] = []
        self._rec_stop = threading.Event()
        self._rec_drop_counts = np.zeros(self.num_inputs, dtype=np.int64)
        self._rec_started = False

        # Callback registration
        self.client.set_process_callback(self._process)

    # --------------- Public API ---------------

    def start(self):
        self.client.activate()
        # Auto connect ports
        if self.auto_connect_capture:
            self._auto_connect_inputs()
        if self.auto_connect_playback:
            self._auto_connect_outputs()

        # Recording
        if self.record_enabled and not self._rec_started:
            self._start_recording_threads()

    def stop(self):
        try:
            if self._rec_started:
                self._rec_stop.set()
                for t in self._rec_threads:
                    t.join(timeout=3)
        finally:
            self.client.deactivate()
            self.client.close()

    def set_selected_channel(self, ch_index: int):
        with self._lock:
            self.selected_ch = max(0, min(self.num_inputs - 1, int(ch_index)))

    def set_gain_linear(self, ch_index: int, gain: float):
        with self._lock:
            if 0 <= ch_index < self.num_inputs:
                self.gains[ch_index] = float(max(0.0, gain))

    def set_gain_db(self, ch_index: int, gain_db: float):
        self.set_gain_linear(ch_index, db_to_linear(gain_db))

    def set_mute(self, ch_index: int, mute: bool):
        with self._lock:
            if 0 <= ch_index < self.num_inputs:
                self.mutes[ch_index] = bool(mute)

    def get_state(self) -> Dict:
        with self._lock:
            return {
                'samplerate': self.client.samplerate,
                'frames_per_period': self.client.blocksize,
                'selected_channel': int(self.selected_ch + 1),
                'gains_linear': self.gains.tolist(),
                'gains_db': [linear_to_db(g) for g in self.gains],
                'mutes': self.mutes.astype(bool).tolist(),
                'vu_peak': self.vu_peak.tolist(),
                'vu_rms': self.vu_rms.tolist(),
                'recording': bool(self.record_enabled),
                'rec_dropped_buffers': self._rec_drop_counts.tolist(),
            }

    # --------------- Internal ---------------

    def _process(self, frames: int):
        # Acquire state snapshot without blocking the RT thread long
        with self._lock:
            sel = int(self.selected_ch)
            gains = self.gains.copy()
            mutes = self.mutes.copy()

        # JACK provides float32 buffers
        # Read inputs and compute VU post-gain
        route_buf = None
        for i, inport in enumerate(self.inports):
            buf = inport.get_array()  # np.ndarray float32, shape (frames,)
            # VU uses post-gain signal
            g = 0.0 if mutes[i] else gains[i]
            post_gain = buf * g
            # Update VU instantly; UI side may smooth
            # Avoid heavy ops if frames is 0
            if frames > 0:
                self.vu_peak[i] = float(np.max(np.abs(post_gain)))
                # RMS
                self.vu_rms[i] = float(np.sqrt(np.mean(post_gain * post_gain)))
            if i == sel:
                route_buf = buf if g == 0.0 else post_gain

        if route_buf is None:
            # Nothing selected; output silence
            for o in self.outports:
                o.get_array()[:] = 0.0
        else:
            # Fast monitor to both L/R
            self.outports[0].get_array()[:] = route_buf
            self.outports[1].get_array()[:] = route_buf

        # Enqueue raw input for recording (non-blocking)
        if self.record_enabled and self._rec_started:
            for i, inport in enumerate(self.inports):
                raw = inport.get_array().copy()  # copy to decouple from RT buffer
                try:
                    self._rec_queues[i].put_nowait(raw)
                except queue.Full:
                    self._rec_drop_counts[i] += 1

        self._last_vu_time = time.time()

    def _auto_connect_inputs(self):
        # Connect physical capture ports to our inports
        cap_ports = self.client.get_ports(is_output=True, is_physical=True)
        # Filter by name match
        cap_ports = [p for p in cap_ports if self.capture_match in p.name.lower() or 'capture' in p.name.lower()]
        # Fallback to any outputs if filter too strict
        if len(cap_ports) < self.num_inputs:
            cap_ports = self.client.get_ports(is_output=True)
        for i in range(min(self.num_inputs, len(cap_ports))):
            try:
                self.client.connect(cap_ports[i], self.inports[i])
            except jack.JackError:
                pass

    def _auto_connect_outputs(self):
        pb_ports = self.client.get_ports(is_input=True, is_physical=True)
        pb_ports = [p for p in pb_ports if self.playback_match in p.name.lower() or 'playback' in p.name.lower()]
        if len(pb_ports) < 2:
            pb_ports = self.client.get_ports(is_input=True)
        # Connect our L/R to first two playback ports
        if len(pb_ports) >= 2:
            try:
                self.client.connect(self.outports[0], pb_ports[0])
                self.client.connect(self.outports[1], pb_ports[1])
            except jack.JackError:
                pass

    def _start_recording_threads(self):
        # Create directory per run
        ts = time.strftime('%Y%m%d_%H%M%S')
        session_dir = self.record_dir / ts
        session_dir.mkdir(parents=True, exist_ok=True)

        self._rec_stop.clear()
        self._rec_threads = []
        for i in range(self.num_inputs):
            ch_path = session_dir / f'channel_{i + 1}.wav'
            t = threading.Thread(target=self._rec_worker, args=(i, ch_path), daemon=True)
            self._rec_threads.append(t)
            t.start()
        self._rec_started = True

    def _rec_worker(self, ch_index: int, path: Path):
        # Stream-write WAV with libsndfile
        with sf.SoundFile(str(path), mode='w', samplerate=self.client.samplerate, channels=1, subtype='PCM_24', format='WAV') as f:
            while not self._rec_stop.is_set():
                try:
                    block = self._rec_queues[ch_index].get(timeout=0.2)
                except queue.Empty:
                    continue
                # Ensure shape (n, 1)
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
