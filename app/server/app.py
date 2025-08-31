import asyncio
from pathlib import Path
from typing import Dict, Any, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import contextlib


def create_app(engine: Any) -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Args:
        engine (Any): The audio engine instance to use
        
    Returns:
        FastAPI: Configured FastAPI application
    """
    # Create FastAPI app with title
    app = FastAPI(title="Bullen Audio Router")

    # Mount static files directory for UI
    ui_dir = Path(__file__).resolve().parents[1] / "ui"
    app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    # Store engine and initialize app state
    app.state.engine = engine
    app.state.clients: Set[WebSocket] = set()
    app.state.vu_task = None

    # -------- Helpers --------

    def _validate_channel(ch: int) -> int:
        """
        Validate that a channel number is within the allowed range.
        
        Args:
            ch (int): Channel number (1-based)
            
        Returns:
            int: Channel index (0-based)
            
        Raises:
            HTTPException: If channel is out of range
        """
        # Check if channel number is valid
        if not 1 <= ch <= engine.num_inputs:
            raise HTTPException(status_code=400, detail=f"Channel out of range (1..{engine.num_inputs})")
        # Return 0-based index
        return ch - 1

    # Root redirect to UI
    @app.get("/")
    def root():
        """
        Redirect root path to UI.
        
        Returns:
            RedirectResponse: Redirect to /ui/
        """
        # Redirect to UI page
        return RedirectResponse(url="/ui/")

    # -------- API --------

    @app.get("/api/state")
    def get_state():
        """
        Get the current state of the audio engine.
        
        Returns:
            JSONResponse: Current engine state as JSON
        """
        # Return engine state as JSON
        return JSONResponse(engine.get_state())

    @app.post("/api/select/{ch}")
    def select_channel(ch: int):
        """
        Select a channel for monitoring.
        
        Args:
            ch (int): Channel number to select (1-based)
            
        Returns:
            Dict: Success response with selected channel
        """
        # Validate and convert channel number to index
        idx = _validate_channel(ch)
        # Set selected channel in engine
        engine.set_selected_channel(idx)
        # Return success response
        return {"ok": True, "selected_channel": ch}

    @app.post("/api/gain/{ch}")
    async def set_gain(ch: int, payload: Dict[str, Any]):
        """
        Set gain for a specific channel.
        
        Args:
            ch (int): Channel number (1-based)
            payload (Dict): Request payload containing gain values
            
        Returns:
            Dict: Success response
        """
        # Validate and convert channel number to index
        idx = _validate_channel(ch)
        # Set gain based on provided value type
        if "gain_db" in payload:
            # Set gain in decibels
            engine.set_gain_db(idx, float(payload["gain_db"]))
        elif "gain_linear" in payload:
            # Set gain in linear scale
            engine.set_gain_linear(idx, float(payload["gain_linear"]))
        else:
            # Raise error if no valid gain value provided
            raise HTTPException(status_code=400, detail="Expected 'gain_db' or 'gain_linear'")
        # Return success response
        return {"ok": True}

    @app.post("/api/mute/{ch}")
    async def set_mute(ch: int, payload: Dict[str, Any]):
        """
        Set mute status for a specific channel.
        
        Args:
            ch (int): Channel number (1-based)
            payload (Dict): Request payload containing mute status
            
        Returns:
            Dict: Success response
        """
        # Validate and convert channel number to index
        idx = _validate_channel(ch)
        # Check if mute value is provided
        if "mute" not in payload:
            raise HTTPException(status_code=400, detail="Expected 'mute': true/false")
        # Set mute status in engine
        engine.set_mute(idx, bool(payload["mute"]))
        # Return success response
        return {"ok": True}

    @app.get("/api/config")
    def get_config():
        """
        Get the current configuration.
        
        Returns:
            JSONResponse: Current configuration as JSON
        """
        # Return engine configuration as JSON
        return JSONResponse(engine.config)

    # -------- WebSocket for VU --------

    @app.websocket("/ws/vu")
    async def vu_socket(ws: WebSocket):
        """
        WebSocket endpoint for VU meter updates.
        
        Args:
            ws (WebSocket): WebSocket connection
        """
        # Accept WebSocket connection
        await ws.accept()
        # Add connection to clients set
        app.state.clients.add(ws)
        try:
            # Keep open until client disconnects
            while True:
                try:
                    # Wait for messages (to keep connection alive)
                    await ws.receive_text()
                except Exception:
                    # Sleep to prevent busy loop
                    await asyncio.sleep(1)
        except WebSocketDisconnect:
            # Handle client disconnection
            pass
        finally:
            # Remove connection from clients set
            app.state.clients.discard(ws)

    async def _vu_publisher():
        """
        Publish VU meter updates to all connected WebSocket clients.
        """
        try:
            while True:
                # 20 Hz updates (50ms interval)
                await asyncio.sleep(0.05)
                # Get current engine state
                state = engine.get_state()
                # Prepare payload for VU meter updates
                payload = {
                    "vu_peak": state["vu_peak"],
                    "vu_rms": state["vu_rms"],
                    "selected_channel": state["selected_channel"],
                    "mutes": state["mutes"],
                    "gains_db": state["gains_db"],
                }
                # Skip if no clients connected
                if not app.state.clients:
                    continue
                # Track dead connections
                dead = []
                # Send updates to all clients
                for ws in list(app.state.clients):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        # Mark connection as dead if sending fails
                        dead.append(ws)
                # Remove dead connections
                for ws in dead:
                    app.state.clients.discard(ws)
        except asyncio.CancelledError:
            # Handle task cancellation
            pass

    @app.on_event("startup")
    async def _startup():
        """
        Startup event handler to start the audio engine and VU publisher task.
        """
        # Start audio engine
        engine.start()
        # Create and start VU publisher task
        app.state.vu_task = asyncio.create_task(_vu_publisher())

    @app.on_event("shutdown")
    async def _shutdown():
        """
        Shutdown event handler to stop the VU publisher task and audio engine.
        """
        # Cancel VU publisher task if it exists
        if app.state.vu_task:
            app.state.vu_task.cancel()
            # Suppress any exceptions during task cancellation
            with contextlib.suppress(Exception):
                await app.state.vu_task
        # Stop audio engine
        engine.stop()

    return app
