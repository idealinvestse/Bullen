import os
import uvicorn

from app.config import load_config
from app.engine.audio_engine import AudioEngine
from app.server.app import create_app


# Build FastAPI app with engine so uvicorn can import as 'app.server.main:app'
engine = AudioEngine(load_config())
app = create_app(engine)

if __name__ == "__main__":
    host = os.environ.get("BULLEN_HOST", "0.0.0.0")
    port = int(os.environ.get("BULLEN_PORT", "8000"))
    uvicorn.run("app.server.main:app", host=host, port=port, reload=False, workers=1)
