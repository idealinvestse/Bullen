from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any

import yaml

DEFAULT_CONFIG: Dict[str, Any] = {
    "samplerate": 48000,           # effective rate set by JACK/PipeWire
    "frames_per_period": 128,      # informative; tune JACK separately
    "nperiods": 2,                 # informative; tune JACK separately
    "inputs": 6,
    "outputs": 2,
    "record": True,
    "recordings_dir": "recordings",
    "auto_connect_capture": True,
    "auto_connect_playback": True,
    "capture_match": "capture",
    "playback_match": "playback",
    "selected_channel": 1,
}


def load_config(path: str | None = None) -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    path = path or os.environ.get("BULLEN_CONFIG")
    if path:
        p = Path(path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if not isinstance(data, dict):
                    raise ValueError("config.yaml must contain a YAML mapping at top level")
                cfg.update(data)
    else:
        # Try default project root config.yaml (../config.yaml) if present
        p = Path(__file__).resolve().parents[1] / "config.yaml"
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    cfg.update(data)
    return cfg
