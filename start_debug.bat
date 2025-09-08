@echo off
echo ========================================
echo Bullen Audio Router - Debug Mode
echo ========================================
echo.

REM Set debug environment
set BULLEN_ALLOW_NON_PI=1
set PYTHONUNBUFFERED=1
set BULLEN_HOST=127.0.0.1
set BULLEN_PORT=8000

echo Starting with FakeEngine (no JACK required)...
echo.

REM Run with full error output
python -u Bullen.py

echo.
echo ========================================
echo Server stopped. Check error messages above.
echo ========================================
pause
