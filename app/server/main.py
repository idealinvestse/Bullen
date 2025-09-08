import os
import uvicorn
import logging

from app.config import load_config
from app.server.app import create_app


def _ensure_raspberry_pi():
    """Ensure we're running on a Raspberry Pi (unless override set)."""
    if os.environ.get("BULLEN_ALLOW_NON_PI"):
        return
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read().strip()
        if "Raspberry Pi" not in model:
            raise RuntimeError(f"Not a Raspberry Pi: {model}")
    except FileNotFoundError:
        raise RuntimeError("Not a Raspberry Pi: /proc/device-tree/model not found")


def _create_engine(config):
    """Create appropriate engine based on environment."""
    if os.environ.get("BULLEN_ALLOW_NON_PI"):
        # Use FakeEngine for non-Pi testing
        from tests.conftest import FakeEngine
        return FakeEngine(config.get("inputs", 6))
    else:
        # Use real AudioEngine on Pi
        from app.engine.audio_engine import AudioEngine
        return AudioEngine(config)


# Load config once at module level
_config = load_config()

# Configure logging early (before creating engine/app)
_log_level = os.environ.get("BULLEN_LOG_LEVEL", str(_config.get("log_level", "INFO"))).upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
_ensure_raspberry_pi()
engine = _create_engine(_config)
app = create_app(engine)


# Entry point for running the server directly
if __name__ == "__main__":
    # Get host from environment variable or default to all interfaces
    host = os.environ.get("BULLEN_HOST", "0.0.0.0")
    # Get port from environment variable or default to 8000
    port = int(os.environ.get("BULLEN_PORT", "8000"))
    # Start uvicorn server with the FastAPI app
    uvicorn.run("app.server.main:app", host=host, port=port, reload=False, workers=1)
