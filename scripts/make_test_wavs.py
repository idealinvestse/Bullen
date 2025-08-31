#!/usr/bin/env python3
"""
Generate small mono WAV test tones for Bullen on Raspberry Pi.
Outputs to ./test_wavs/ as ch1_*.wav .. ch6_*.wav

- Sample rate: 48000 Hz
- Duration: 2.0 s (configurable via --seconds)
- Amplitude: -12 dBFS (~0.25 linear)
- Frequencies: CH1..CH6 use distinct musical tones

Usage:
  python3 scripts/make_test_wavs.py [--seconds 2.0] [--samplerate 48000]
"""
from __future__ import annotations
import argparse
from pathlib import Path
import math
import numpy as np
import soundfile as sf


def db_to_linear(db: float) -> float:
    return float(10 ** (db / 20.0))


def make_tone(path: Path, sr: int, seconds: float, freq: float, amp_db: float = -12.0) -> None:
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float32) / float(sr)
    amp = db_to_linear(amp_db)
    # Slight fade in/out to avoid clicks
    fade_len = max(1, int(0.01 * sr))
    window = np.ones(n, dtype=np.float32)
    window[:fade_len] = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
    window[-fade_len:] = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
    x = (amp * np.sin(2 * math.pi * freq * t) * window).astype(np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), x, sr, subtype='PCM_16', format='WAV')
    print(f"Wrote {path} @ {sr} Hz, {seconds:.2f} s, {freq:.1f} Hz, {amp_db:.1f} dBFS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=2.0)
    parser.add_argument('--samplerate', type=int, default=48000)
    parser.add_argument('--outdir', type=Path, default=Path('test_wavs'))
    args = parser.parse_args()

    sr = int(args.samplerate)
    sec = float(args.seconds)
    outdir: Path = args.outdir

    # Distinct frequencies per channel
    freqs = [440.0, 554.37, 659.25, 880.0, 987.77, 1318.51]

    for idx, f in enumerate(freqs, start=1):
        name = f"ch{idx}_{int(round(f))}Hz.wav"
        make_tone(outdir / name, sr, sec, f)

    print("Done. Files in:", outdir.resolve())


if __name__ == '__main__':
    main()
