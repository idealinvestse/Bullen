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
    # Number of output channels
    "outputs": 2,
    # Enable/disable recording functionality
    "record": True,
    # Directory where recordings will be saved
    "recordings_dir": "recordings",
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
    # Return final configuration
    return cfg
