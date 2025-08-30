import os
import uvicorn

if __name__ == "__main__":
    host = os.environ.get("BULLEN_HOST", "0.0.0.0")
    port = int(os.environ.get("BULLEN_PORT", "8000"))
    uvicorn.run("app.server.main:app", host=host, port=port, reload=False, workers=1)
