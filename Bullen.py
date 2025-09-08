#!/usr/bin/env python3
"""
Bullen Audio Router - Main Entry Point
=====================================

Professional audio routing system for call centers and live audio monitoring.
Optimized for Raspberry Pi with Audio Injector Octo (6 in, 8 out).

For development on non-Pi systems, set environment variable:
BULLEN_ALLOW_NON_PI=1
"""

import os
import sys
import uvicorn

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

if __name__ == "__main__":
    # Enable FakeEngine for development on non-Pi systems
    if not os.environ.get("BULLEN_ALLOW_NON_PI"):
        print("[INFO] Starting Bullen Audio Router for Raspberry Pi...")
        print("[TIP] For development on non-Pi systems, use: BULLEN_ALLOW_NON_PI=1 python Bullen.py")
    else:
        print("[DEV] Starting Bullen Audio Router in development mode (FakeEngine)")
        print("[UI] Optimized for 9\" touchscreen available at http://localhost:8000")
    
    uvicorn.run(
        "app.server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
