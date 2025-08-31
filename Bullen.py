import os
import uvicorn


if __name__ == "__main__":
    # Get host from environment variable or default to all interfaces
    host = os.environ.get("BULLEN_HOST", "0.0.0.0")
    # Get port from environment variable or default to 8000
    port = int(os.environ.get("BULLEN_PORT", "8000"))
    # Start uvicorn server with the FastAPI app from app.server.main
    uvicorn.run("app.server.main:app", host=host, port=port, reload=False, workers=1)
