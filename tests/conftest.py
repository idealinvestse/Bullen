import threading
from typing import Any, Dict
import time
from pathlib import Path
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient

# Ensure local project root is on sys.path before importing 'app.*'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.server.app import create_app


class FakeNoiseSuppressor:
    """Fake noise suppressor for testing."""
    
    def __init__(self):
        self.aggressiveness = 0.7
        self.enable_cross_channel = True
        self.comfort_noise_level = 0.01
    
    def set_aggressiveness(self, level: float):
        self.aggressiveness = level


class FakeEngine:
    """Fake audio engine for testing without JACK."""
    
    def __init__(self, num_inputs: int = 6):
        self.num_inputs = num_inputs
        self.num_outputs = 2  # Default to 2 outputs for testing
        self.selected_ch = 0
        self.gains = np.ones(num_inputs)
        self.mutes = np.zeros(num_inputs, dtype=bool)
        self.vu_peak = np.random.rand(num_inputs) * 0.5
        self.vu_rms = np.random.rand(num_inputs) * 0.3
        self.config = {
            "inputs": num_inputs,
            "outputs": self.num_outputs,
            "recordings_dir": "recordings",
            "enable_advanced_features": False,
            "noise_suppression": {
                "enabled": True,
                "aggressiveness": 0.7
            }
        }
        self.advanced_processing_enabled = False
        self.advanced_processors = None
        
        # Mock noise suppressor
        self.noise_suppressor = FakeNoiseSuppressor() if self.config.get("noise_suppression", {}).get("enabled") else None

    # API used by app
    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def set_selected_channel(self, idx: int) -> None:
        with self._lock:
            self._selected = max(0, min(self.num_inputs - 1, int(idx)))

    def set_gain_linear(self, idx: int, gain: float) -> None:
        with self._lock:
            if 0 <= idx < self.num_inputs:
                self._gains_lin[idx] = float(max(0.0, gain))

    def set_gain_db(self, idx: int, gain_db: float) -> None:
        self.set_gain_linear(idx, self.db_to_linear(gain_db))

    def set_mute(self, idx: int, mute: bool) -> None:
        with self._lock:
            if 0 <= idx < self.num_inputs:
                self._mutes[idx] = bool(mute)

    # Helpers
    @staticmethod
    def db_to_linear(db: float) -> float:
        return float(10.0 ** (db / 20.0))

    @staticmethod
    def linear_to_db(lin: float) -> float:
        lin = max(1e-12, float(lin))
        return 20.0 * np.log10(lin)

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            self._tick += 1
            # Simulate simple VU: selected channel active, others low
            rms_base = 0.02
            peak_base = 0.05
            self._vu_rms[:] = rms_base
            self._vu_peak[:] = peak_base
            self._vu_rms[self._selected] = 0.1
            self._vu_peak[self._selected] = 0.2
            # Apply mutes by zeroing VU
            mut_idx = np.where(self._mutes)[0]
            if mut_idx.size:
                self._vu_rms[mut_idx] = 0.0
                self._vu_peak[mut_idx] = 0.0
            return {
                'samplerate': self._samplerate,
                'frames_per_period': self._blocksize,
                'selected_channel': int(self._selected + 1),
                'gains_linear': self._gains_lin.tolist(),
                'gains_db': [float(self.linear_to_db(g)) for g in self._gains_lin],
                'mutes': self._mutes.astype(bool).tolist(),
                'vu_peak': self._vu_peak.tolist(),
                'vu_rms': self._vu_rms.tolist(),
                'recording': False,
                'rec_dropped_buffers': [0] * self.num_inputs,
            }


@pytest.fixture(scope="session")
def app_instance():
    engine = FakeEngine()
    app = create_app(engine)
    return app


@pytest.fixture()
def client(app_instance):
    # TestClient manages startup/shutdown events for the app
    with TestClient(app_instance) as c:
        yield c
