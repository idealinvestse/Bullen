import os
import uvicorn

if __name__ == "__main__":
    # Enable FakeEngine for development on non-Pi systems
    if not os.environ.get("BULLEN_ALLOW_NON_PI"):
        print("🎵 Starting Bullen Audio Router for Raspberry Pi...")
        print("💡 For development on non-Pi systems, use: BULLEN_ALLOW_NON_PI=1 python Bullen.py")
    else:
        print("🧪 Starting Bullen Audio Router in development mode (FakeEngine)")
        print("📱 UI optimized for 9\" touchscreen available at http://localhost:8000")
    
    uvicorn.run(
        "app.server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
