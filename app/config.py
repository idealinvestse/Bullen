from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any

import yaml

# Default configuration values
DEFAULT_CONFIG: Dict[str, Any] = {
    # Audio sample rate (informational, actual rate is set by JACK/PipeWire)
    "samplerate": 48000,           # effective rate set by JACK/PipeWire
    # Buffer size per period (informational, actual size is set by JACK)
    "frames_per_period": 128,      # informative; tune JACK separately
    # Number of periods (informational, actual value is set by JACK)
    "nperiods": 2,                 # informative; tune JACK separately
    # Number of input channels
    "inputs": 6,
    # Number of output channels (Octo default)
    "outputs": 8,
    # Enable/disable recording functionality
    "record": True,
    # Directory where recordings will be saved
    "recordings_dir": "recordings",
    # Size of per-channel recording queues (frames buffers)
    "record_queue_size": 64,
    # Enable/disable automatic connection of input ports
    "auto_connect_capture": True,
    # Enable/disable automatic connection of output ports
    "auto_connect_playback": True,
    # String to match when auto-connecting capture ports
    "capture_match": "capture",
    # String to match when auto-connecting playback ports
    "playback_match": "playback",
    # Initially selected channel (1-based index)
    "selected_channel": 1,
    # Logging level (DEBUG, INFO, WARNING, ERROR)
    "log_level": "INFO",
}


def load_config(path: str | None = None) -> Dict[str, Any]:
    """
    Load configuration from file or environment variable.
    
    Args:
        path (str | None): Path to configuration file
        
    Returns:
        Dict[str, Any]: Configuration dictionary
    """
    # Start with default configuration
    cfg = dict(DEFAULT_CONFIG)
    # Get config path from parameter or environment variable
    path = path or os.environ.get("BULLEN_CONFIG")
    if path:
        # Load configuration from specified path
        p = Path(path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                # Load YAML data
                data = yaml.safe_load(f) or {}
                # Validate that data is a dictionary
                if not isinstance(data, dict):
                    raise ValueError("config.yaml must contain a YAML mapping at top level")
                # Update configuration with loaded data
                cfg.update(data)
    else:
        # Try default project root config.yaml (../config.yaml) if present
        p = Path(__file__).resolve().parents[1] / "config.yaml"
        if p.exists():
            # Load configuration from default path
            with p.open("r", encoding="utf-8") as f:
                # Load YAML data
                data = yaml.safe_load(f) or {}
                # Only update if data is a dictionary
                if isinstance(data, dict):
                    cfg.update(data)
    # Validate and normalize config
    return _validate_config(cfg)


def _validate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize configuration values.
    Clamps out-of-range values to safe ranges.
    """
    def _as_int(x: Any, default: int) -> int:
        try:
            return int(x)
        except Exception:
            return int(default)

    # inputs: at least 1
    cfg["inputs"] = max(1, _as_int(cfg.get("inputs", DEFAULT_CONFIG["inputs"]), DEFAULT_CONFIG["inputs"]))
    # outputs: at least 2, cap at 8 for Octo (but keep generic)
    cfg["outputs"] = min(8, max(2, _as_int(cfg.get("outputs", DEFAULT_CONFIG["outputs"]), DEFAULT_CONFIG["outputs"])) )
    # selected_channel: clamp to [1..inputs]
    sel = _as_int(cfg.get("selected_channel", DEFAULT_CONFIG["selected_channel"]), DEFAULT_CONFIG["selected_channel"])
    cfg["selected_channel"] = min(cfg["inputs"], max(1, sel))
    # record_queue_size: clamp [16..4096]
    rqs = _as_int(cfg.get("record_queue_size", DEFAULT_CONFIG["record_queue_size"]), DEFAULT_CONFIG["record_queue_size"])
    cfg["record_queue_size"] = min(4096, max(16, rqs))
    # booleans
    cfg["record"] = bool(cfg.get("record", DEFAULT_CONFIG["record"]))
    cfg["auto_connect_capture"] = bool(cfg.get("auto_connect_capture", DEFAULT_CONFIG["auto_connect_capture"]))
    cfg["auto_connect_playback"] = bool(cfg.get("auto_connect_playback", DEFAULT_CONFIG["auto_connect_playback"]))
    # strings
    cfg["recordings_dir"] = str(cfg.get("recordings_dir", DEFAULT_CONFIG["recordings_dir"]))
    cfg["capture_match"] = str(cfg.get("capture_match", DEFAULT_CONFIG["capture_match"]))
    cfg["playback_match"] = str(cfg.get("playback_match", DEFAULT_CONFIG["playback_match"]))
    # log level
    lvl = str(cfg.get("log_level", DEFAULT_CONFIG["log_level"]))
    cfg["log_level"] = lvl.upper()
    # informative ints
    cfg["samplerate"] = _as_int(cfg.get("samplerate", DEFAULT_CONFIG["samplerate"]), DEFAULT_CONFIG["samplerate"])
    cfg["frames_per_period"] = _as_int(cfg.get("frames_per_period", DEFAULT_CONFIG["frames_per_period"]), DEFAULT_CONFIG["frames_per_period"]) 
    cfg["nperiods"] = _as_int(cfg.get("nperiods", DEFAULT_CONFIG["nperiods"]), DEFAULT_CONFIG["nperiods"]) 
    return cfg
