#!/usr/bin/env python3
"""
Feed a mono WAV file into a selected Bullen engine input via JACK.

- Auto-connects to JACK port 'bullen:in_<N>' (N = --input)
- Resamples to JACK samplerate if needed (simple linear)
- Non-RT: audio buffer is preloaded to memory (intended for short test tones)

Usage:
  python3 scripts/feed_wav_to_input.py --file test_wavs/ch1_440Hz.wav --input 1 [--loop] [--gain_db -6]
Stop with Ctrl+C.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import time
from typing import Optional

import numpy as np
import soundfile as sf

jack = None  # type: ignore
try:
    import jack  # type: ignore
except Exception:
    # Allow importing this module without JACK present (for tests on non-Pi).
    jack = None  # type: ignore


def db_to_linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def simple_resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x.astype(np.float32, copy=False)
    # Linear interpolation
    dur = x.shape[0] / float(sr_in)
    n_out = int(round(dur * sr_out))
    if n_out <= 1:
        return np.zeros((1,), dtype=np.float32)
    t_in = np.linspace(0.0, dur, num=x.shape[0], endpoint=False, dtype=np.float64)
    t_out = np.linspace(0.0, dur, num=n_out, endpoint=False, dtype=np.float64)
    y = np.interp(t_out, t_in, x.astype(np.float64))
    return y.astype(np.float32)


def find_bullen_input_port(client: jack.Client, input_index: int) -> Optional[str]:
    # Try exact name first
    exact = f"bullen:in_{input_index}"
    ports = client.get_ports(is_input=True)
    names = [p.name for p in ports]
    if exact in names:
        return exact
    # Fallback: case-insensitive contains
    target_lc = f":in_{input_index}".lower()
    for p in ports:
        if "bullen" in p.name.lower() and target_lc in p.name.lower():
            return p.name
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True, type=Path, help='Path to mono WAV file')
    ap.add_argument('--input', required=True, type=int, help='Engine input channel (1-based)')
    ap.add_argument('--loop', action='store_true', help='Loop playback')
    ap.add_argument('--gain_db', type=float, default=0.0, help='Apply gain (dB) to file')
    ap.add_argument('--client_name', type=str, default='bullen_wav_feed', help='JACK client name')
    args = ap.parse_args()

    wav_path: Path = args.file
    if not wav_path.exists():
        print(f"File not found: {wav_path}")
        sys.exit(1)

    if jack is None:
        print("JACK library not available. Install 'JACK-Client' and run on Raspberry Pi with PipeWire-JACK.")
        sys.exit(1)

    data, sr = sf.read(str(wav_path), dtype='float32', always_2d=False)
    if data.ndim > 1:
        # Mixdown to mono
        data = data.mean(axis=1).astype(np.float32)
    data = data.astype(np.float32, copy=False)

    with jack.Client(args.client_name, no_start_server=False) as client:
        jack_sr = client.samplerate
        x = simple_resample(data, sr, jack_sr)
        if args.gain_db != 0.0:
            x = (x * db_to_linear(args.gain_db)).astype(np.float32)
        # Clip to [-1, 1]
        x = np.clip(x, -1.0, 1.0)

        outport = client.outports.register('wav_out')
        pos = {'i': 0}

        def process(frames: int):
            buf = outport.get_array()
            n = x.shape[0]
            i = pos['i']
            end = i + frames
            if i >= n:
                if args.loop:
                    i = 0
                    end = frames
                else:
                    buf[:] = 0.0
                    return
            if end <= n:
                buf[:] = x[i:end]
                pos['i'] = end
            else:
                # Tail then wrap/zero
                tail = n - i
                if tail > 0:
                    buf[:tail] = x[i:n]
                if args.loop:
                    wrap = frames - tail
                    wrap_n = min(wrap, n)
                    buf[tail:tail+wrap_n] = x[:wrap_n]
                    if wrap_n < wrap:
                        buf[tail+wrap_n:] = 0.0
                        pos['i'] = wrap_n
                    else:
                        pos['i'] = wrap_n
                else:
                    buf[tail:] = 0.0
                    pos['i'] = n

        client.set_process_callback(process)
        client.activate()

        # Attempt auto-connect to bullen input port
        target = find_bullen_input_port(client, args.input)
        if target is None:
            print(f"Could not find target port for input {args.input}. Use qpwgraph/jack_connect manually.")
        else:
            try:
                client.connect(outport, target)
                print(f"Connected: {outport.name} -> {target}")
            except jack.JackError:
                print(f"Failed to auto-connect to {target}. Connect manually.")

        print(f"Streaming {wav_path.name} @ {jack_sr} Hz into bullen:in_{args.input}. Ctrl+C to stop.")
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass

        client.deactivate()


if __name__ == '__main__':
    main()
