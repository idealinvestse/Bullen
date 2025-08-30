import asyncio
from pathlib import Path
from typing import Dict, Any, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.engine.audio_engine import AudioEngine
import contextlib


def create_app(engine: AudioEngine) -> FastAPI:
    app = FastAPI(title="Bullen Audio Router")

    ui_dir = Path(__file__).resolve().parents[1] / "ui"
    app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    app.state.engine = engine
    app.state.clients: Set[WebSocket] = set()
    app.state.vu_task = None

    # -------- Helpers --------

    def _validate_channel(ch: int) -> int:
        if not 1 <= ch <= engine.num_inputs:
            raise HTTPException(status_code=400, detail=f"Channel out of range (1..{engine.num_inputs})")
        return ch - 1

    # Root redirect to UI
    @app.get("/")
    def root():
        return RedirectResponse(url="/ui/")

    # -------- API --------

    @app.get("/api/state")
    def get_state():
        return JSONResponse(engine.get_state())

    @app.post("/api/select/{ch}")
    def select_channel(ch: int):
        idx = _validate_channel(ch)
        engine.set_selected_channel(idx)
        return {"ok": True, "selected_channel": ch}

    @app.post("/api/gain/{ch}")
    async def set_gain(ch: int, payload: Dict[str, Any]):
        idx = _validate_channel(ch)
        if "gain_db" in payload:
            engine.set_gain_db(idx, float(payload["gain_db"]))
        elif "gain_linear" in payload:
            engine.set_gain_linear(idx, float(payload["gain_linear"]))
        else:
            raise HTTPException(status_code=400, detail="Expected 'gain_db' or 'gain_linear'")
        return {"ok": True}

    @app.post("/api/mute/{ch}")
    async def set_mute(ch: int, payload: Dict[str, Any]):
        idx = _validate_channel(ch)
        if "mute" not in payload:
            raise HTTPException(status_code=400, detail="Expected 'mute': true/false")
        engine.set_mute(idx, bool(payload["mute"]))
        return {"ok": True}

    @app.get("/api/config")
    def get_config():
        return JSONResponse(engine.config)

    # -------- WebSocket for VU --------

    @app.websocket("/ws/vu")
    async def vu_socket(ws: WebSocket):
        await ws.accept()
        app.state.clients.add(ws)
        try:
            # Keep open until client disconnects
            while True:
                try:
                    await ws.receive_text()
                except Exception:
                    await asyncio.sleep(1)
        except WebSocketDisconnect:
            pass
        finally:
            app.state.clients.discard(ws)

    async def _vu_publisher():
        try:
            while True:
                # 20 Hz updates
                await asyncio.sleep(0.05)
                state = engine.get_state()
                payload = {
                    "vu_peak": state["vu_peak"],
                    "vu_rms": state["vu_rms"],
                    "selected_channel": state["selected_channel"],
                    "mutes": state["mutes"],
                    "gains_db": state["gains_db"],
                }
                if not app.state.clients:
                    continue
                dead = []
                for ws in list(app.state.clients):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    app.state.clients.discard(ws)
        except asyncio.CancelledError:
            pass

    @app.on_event("startup")
    async def _startup():
        engine.start()
        app.state.vu_task = asyncio.create_task(_vu_publisher())

    @app.on_event("shutdown")
    async def _shutdown():
        if app.state.vu_task:
            app.state.vu_task.cancel()
            with contextlib.suppress(Exception):
                await app.state.vu_task
        engine.stop()

    return app
