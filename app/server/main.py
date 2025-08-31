import os
import uvicorn

from app.config import load_config
from app.engine.audio_engine import AudioEngine
from app.server.app import create_app


# Build FastAPI app with engine so uvicorn can import as 'app.server.main:app'
# Load configuration and create audio engine
engine = AudioEngine(load_config())
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
