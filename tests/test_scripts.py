import numpy as np
import soundfile as sf

from scripts.make_test_wavs import db_to_linear, make_tone
from scripts.feed_wav_to_input import simple_resample, db_to_linear as db2, find_bullen_input_port


def test_db_helpers_same_formula():
    # both modules use same definition
    assert abs(db_to_linear(-6.0) - db2(-6.0)) < 1e-12


def test_make_tone_writes_file(tmp_path):
    p = tmp_path / "tone.wav"
    sr = 22050
    make_tone(p, sr=sr, seconds=0.1, freq=440.0, amp_db=-12.0)
    assert p.exists()
    data, file_sr = sf.read(str(p), dtype='float32')
    assert file_sr == sr
    assert data.ndim == 1
    assert len(data) == int(0.1 * sr)
    # amplitude around -12 dBFS (~0.25)
    assert float(np.max(np.abs(data))) <= 0.4


def test_simple_resample_identity():
    x = np.linspace(-1, 1, 100, dtype=np.float32)
    y = simple_resample(x, sr_in=48000, sr_out=48000)
    assert y.dtype == np.float32
    assert np.allclose(x, y)


def test_simple_resample_up_down():
    x = np.sin(2 * np.pi * 440.0 * np.arange(100, dtype=np.float32) / 1000.0).astype(np.float32)
    y = simple_resample(x, 1000, 2000)
    z = simple_resample(y, 2000, 1000)
    assert len(y) == 200
    assert len(z) == 100
    # Back-and-forth should be roughly similar
    assert np.allclose(x, z, atol=1e-1)


def test_find_bullen_input_port_prefers_exact():
    class P:
        def __init__(self, name):
            self.name = name

    class C:
        def __init__(self, names):
            self._ports = [P(n) for n in names]
        def get_ports(self, is_input=True):
            return self._ports

    c = C(["system:capture_1", "bullen:in_3", "bullen:in_1"]) 
    assert find_bullen_input_port(c, 1) == "bullen:in_1"
    assert find_bullen_input_port(c, 3) == "bullen:in_3"

    c2 = C(["System:Capture_1", "BULLEN:IN_6"]) 
    # case-insensitive contains fallback
    assert find_bullen_input_port(c2, 6).lower().endswith(":in_6")
