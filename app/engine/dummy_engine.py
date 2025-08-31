import math
import threading
from typing import Dict

import numpy as np


class DummyEngine:
    """
    Reduced-functionality engine that does NOT use JACK.
    - Provides the same public API surface as the JACK-based `AudioEngine` so the
      server and UI can run without a JACK server present.
    - No real audio I/O or recording is performed. VU meters remain at 0.
    - Useful for development, UI testing, and deployments where JACK is unavailable.
    """

    def __init__(self, config: Dict):
        self.config = config
        self.samplerate = int(config.get("samplerate", 48000))
        self.frames_per_period = int(config.get("frames_per_period", 128))
        self.nperiods = int(config.get("nperiods", 2))
        self.num_inputs = int(config.get("inputs", 6))
        self.num_outputs = int(config.get("outputs", 2))

        # In dummy mode, disable recording regardless of config
        self.record_enabled = False

        self._lock = threading.Lock()
        self.selected_ch = max(0, min(self.num_inputs - 1, int(config.get("selected_channel", 1)) - 1))
        self.gains = np.ones(self.num_inputs, dtype=np.float32)
        self.mutes = np.zeros(self.num_inputs, dtype=bool)

        self.vu_peak = np.zeros(self.num_inputs, dtype=np.float32)
        self.vu_rms = np.zeros(self.num_inputs, dtype=np.float32)
        self._rec_drop_counts = np.zeros(self.num_inputs, dtype=np.int64)

    # --------------- Public API ---------------

    def start(self):
        """No-op. There is no backend to start in dummy mode."""
        return None

    def stop(self):
        """No-op. There is no backend to stop in dummy mode."""
        return None

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
                "samplerate": int(self.samplerate),
                "frames_per_period": int(self.frames_per_period),
                "selected_channel": int(self.selected_ch + 1),
                "gains_linear": self.gains.tolist(),
                "gains_db": [linear_to_db(g) for g in self.gains],
                "mutes": self.mutes.astype(bool).tolist(),
                "vu_peak": self.vu_peak.tolist(),
                "vu_rms": self.vu_rms.tolist(),
                "recording": False,
                "rec_dropped_buffers": self._rec_drop_counts.tolist(),
            }


# --------------- Utils ---------------

def db_to_linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def linear_to_db(lin: float) -> float:
    lin = max(1e-12, float(lin))
    return 20.0 * math.log10(lin)
