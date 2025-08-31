import os
import uvicorn

from app.config import load_config
from app.server.app import create_app


# Build FastAPI app with engine so uvicorn can import as 'app.server.main:app'
# Load configuration and create audio engine based on selected backend
_config = load_config()
_backend = str(_config.get("backend", "jack")).lower()

if _backend == "jack":
    # Import JACK engine lazily to avoid requiring JACK when running dummy backend
    from app.engine.audio_engine import AudioEngine as _Engine
    engine = _Engine(_config)
else:
    # Dummy backend: no JACK dependency, UI-only
    from app.engine.dummy_engine import DummyEngine as _Engine
    engine = _Engine(_config)

# Create FastAPI application with audio engine
app = create_app(engine)

# Entry point for running the server directly
if __name__ == "__main__":
    # Get host from environment variable or default to all interfaces
    host = os.environ.get("BULLEN_HOST", "0.0.0.0")
    # Get port from environment variable or default to 8000
    port = int(os.environ.get("BULLEN_PORT", "8000"))
    # Start uvicorn server with the FastAPI app
    uvicorn.run("app.server.main:app", host=host, port=port, reload=False, workers=1)
