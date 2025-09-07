import os
from pathlib import Path
import uvicorn
import logging

from app.config import load_config
from app.server.app import create_app
from app.engine.audio_engine import AudioEngine as _Engine


def _ensure_raspberry_pi() -> None:
    """Raise if not running on a Raspberry Pi (unless explicitly overridden)."""
    # Allow override for development if explicitly requested
    if os.environ.get("BULLEN_ALLOW_NON_PI") == "1":
        return
    model = ""
    try:
        model = Path("/proc/device-tree/model").read_text(errors="ignore").lower()
    except Exception:
        model = ""
    if "raspberry pi" not in model:
        raise RuntimeError(
            "Bullen is configured to run only on Raspberry Pi. "
            "Set BULLEN_ALLOW_NON_PI=1 to override (for development only)."
        )


# Build FastAPI app with JACK engine so uvicorn can import as 'app.server.main:app'
_config = load_config()

# Configure logging early (before creating engine/app)
_log_level = os.environ.get("BULLEN_LOG_LEVEL", str(_config.get("log_level", "INFO"))).upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
_ensure_raspberry_pi()
engine = _Engine(_config)
app = create_app(engine)


# Entry point for running the server directly
if __name__ == "__main__":
    # Get host from environment variable or default to all interfaces
    host = os.environ.get("BULLEN_HOST", "0.0.0.0")
    # Get port from environment variable or default to 8000
    port = int(os.environ.get("BULLEN_PORT", "8000"))
    # Start uvicorn server with the FastAPI app
    uvicorn.run("app.server.main:app", host=host, port=port, reload=False, workers=1)
