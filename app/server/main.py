import os
import uvicorn
import logging

from app.config import load_config
from app.server.app import create_app

# Configure logging early
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Suppress D-Bus warnings that don't affect functionality
logging.getLogger('dbus').setLevel(logging.ERROR)
logging.getLogger('dbus.proxies').setLevel(logging.ERROR)

# Set environment variable to suppress D-Bus warnings
os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/dev/null')


def _ensure_raspberry_pi():
    """Ensure we're running on a Raspberry Pi (unless override set)."""
    if os.environ.get("BULLEN_ALLOW_NON_PI"):
        logging.info("Running in development mode (FakeEngine)")
        return False
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read().strip()
        if "Raspberry Pi" not in model:
            raise RuntimeError(f"Not a Raspberry Pi: {model}")
        logging.info(f"Running on: {model}")
        return True
    except FileNotFoundError:
        if os.name == 'nt':  # Windows
            logging.warning("Windows detected - use BULLEN_ALLOW_NON_PI=1 for development")
        raise RuntimeError("Not a Raspberry Pi: /proc/device-tree/model not found")


def _create_engine(config):
    """Create appropriate engine based on environment."""
    is_pi = _ensure_raspberry_pi()
    
    if not is_pi:
        # Use FakeEngine for non-Pi testing
        from tests.conftest import FakeEngine
        logging.info("Using FakeEngine for development")
        return FakeEngine(config.get("inputs", 6))
    else:
        # Use real AudioEngine on Pi
        from app.engine.audio_engine import AudioEngine
        logging.info("Using AudioEngine with JACK")
        return AudioEngine(config)


# Lazy initialization - only create engine and app when needed
_engine = None
_app = None


def get_app():
    """Get or create the FastAPI app instance."""
    global _app, _engine
    if _app is None:
        config = load_config()
        _engine = _create_engine(config)
        _app = create_app(_engine)
    return _app


# Create app for uvicorn
app = get_app()


# Entry point for running the server directly
if __name__ == "__main__":
    # Get host from environment variable or default to all interfaces
    host = os.environ.get("BULLEN_HOST", "0.0.0.0")
    # Get port from environment variable or default to 8000
    port = int(os.environ.get("BULLEN_PORT", "8000"))
    # Start uvicorn server with the FastAPI app
    uvicorn.run("app.server.main:app", host=host, port=port, reload=False, workers=1)
