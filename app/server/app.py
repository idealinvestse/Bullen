import sys
import subprocess
from pathlib import Path
from typing import Dict, Any, Set, List

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
import asyncio
import json
import logging
import shutil
import tempfile
import soundfile as sf
import uuid
from contextlib import asynccontextmanager
from scripts.make_test_wavs import make_tone  # reuse tone writer


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
    app.state.feed_procs: Dict[int, Any] = {}

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

    # -------- Tools: WAV generation and WAV feed --------

    def _project_root() -> Path:
        return Path(__file__).resolve().parents[2]

    @app.post("/api/tools/generate_wavs")
    def api_generate_wavs(payload: Dict[str, Any] | None = None):
        """
        Generate mono test WAVs to 'test_wavs/'.
        Body: {seconds?: float, samplerate?: int, outdir?: str}
        """
        payload = payload or {}
        seconds = float(payload.get("seconds", 2.0))
        samplerate = int(payload.get("samplerate", 48000))
        outdir = payload.get("outdir", "test_wavs")
        root = _project_root()
        out = (root / outdir).resolve()

        # Distinct frequencies per channel (6 inputs default)
        freqs = [440.0, 554.37, 659.25, 880.0, 987.77, 1318.51]
        files: List[str] = []
        for idx, f in enumerate(freqs[: engine.num_inputs], start=1):
            name = f"ch{idx}_{int(round(f))}Hz.wav"
            path = out / name
            make_tone(path, samplerate, seconds, f)
            files.append(str(path.relative_to(root)))
        return {"ok": True, "files": files, "outdir": str(out.relative_to(root))}

    @app.get("/api/tools/wavs")
    def api_list_wavs():
        """List available generated test WAVs in 'test_wavs/' directory."""
        root = _project_root()
        base = root / "test_wavs"
        mapping: Dict[int, List[str]] = {}
        if base.exists():
            for idx in range(1, engine.num_inputs + 1):
                files = sorted([str(p.relative_to(root)) for p in base.glob(f"ch{idx}_*.wav")])
                if files:
                    mapping[idx] = files
        return {"files": mapping}

    @app.post("/api/tools/feed/start")
    def api_feed_start(payload: Dict[str, Any]):
        """
        Start feeding a WAV file into a given input via JACK using scripts/feed_wav_to_input.py.
        Body: {file: str, input: int, loop?: bool, gain_db?: float}
        """
        if "file" not in payload or "input" not in payload:
            raise HTTPException(status_code=400, detail="Expected 'file' and 'input'")
        input_ch = int(payload["input"])
        if not 1 <= input_ch <= engine.num_inputs:
            raise HTTPException(status_code=400, detail=f"Input out of range (1..{engine.num_inputs})")
        root = _project_root()
        wav_path = (root / payload["file"]).resolve()
        if not wav_path.exists():
            raise HTTPException(status_code=400, detail=f"File not found: {wav_path}")
        scripts_dir = root / "scripts"
        script_path = scripts_dir / "feed_wav_to_input.py"
        if not script_path.exists():
            raise HTTPException(status_code=500, detail="feed_wav_to_input.py not found")

        # Stop any existing feeder on this input
        old = app.state.feed_procs.get(input_ch)
        if old and getattr(old, "poll", lambda: None)() is None:
            try:
                old.terminate()
            except Exception:
                pass

        client_name = f"bullen_wav_feed_ui_{input_ch}"
        cmd = [
            sys.executable,
            str(script_path),
            "--file",
            str(wav_path),
            "--input",
            str(input_ch),
            "--client_name",
            client_name,
        ]
        if bool(payload.get("loop", True)):
            cmd.append("--loop")
        if "gain_db" in payload:
            cmd.extend(["--gain_db", str(float(payload["gain_db"]))])

        try:
            proc = subprocess.Popen(cmd, cwd=str(root))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to start feeder: {e}")
        app.state.feed_procs[input_ch] = proc
        return {"ok": True, "pid": getattr(proc, "pid", None), "input": input_ch}

    @app.post("/api/tools/feed/stop")
    def api_feed_stop(payload: Dict[str, Any]):
        """Stop feeder for given input. Body: {input: int}"""
        if "input" not in payload:
            raise HTTPException(status_code=400, detail="Expected 'input'")
        input_ch = int(payload["input"])
        proc = app.state.feed_procs.get(input_ch)
        if not proc:
            return {"ok": True, "stopped": False}
        try:
            proc.terminate()
        except Exception:
            pass
        app.state.feed_procs.pop(input_ch, None)
        return {"ok": True, "stopped": True}

    @app.get("/api/tools/feed/status")
    def api_feed_status():
        """Return running feeder processes keyed by input channel."""
        status = {}
        for ch, proc in list(app.state.feed_procs.items()):
            alive = getattr(proc, "poll", lambda: None)() is None
            status[ch] = {"pid": getattr(proc, "pid", None), "alive": alive}
            if not alive:
                app.state.feed_procs.pop(ch, None)
        return {"status": status}

    # -------- Audio File Upload --------

    @app.post("/api/upload/audio")
    async def upload_audio_file(file: UploadFile = File(...)):
        """
        Upload an audio file and convert it to mono WAV format.
        
        Args:
            file: The uploaded audio file
            
        Returns:
            JSON response with file information
        """
        # Check file extension
        allowed_extensions = {'.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac'}
        file_ext = Path(file.filename).suffix.lower()
        
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400, 
                detail=f"Unsupported file format. Allowed: {', '.join(allowed_extensions)}"
            )
        
        # Create uploads directory
        root = _project_root()
        uploads_dir = root / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        
        # Generate unique filename
        unique_id = str(uuid.uuid4())[:8]
        original_name = Path(file.filename).stem
        output_filename = f"{original_name}_{unique_id}.wav"
        output_path = uploads_dir / output_filename
        
        try:
            # Save uploaded file to temporary location
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_file:
                shutil.copyfileobj(file.file, temp_file)
                temp_path = temp_file.name
            
            # Read audio file and convert to mono WAV
            data, samplerate = sf.read(temp_path)
            
            # Convert to mono if stereo
            if len(data.shape) > 1:
                data = data.mean(axis=1)
            
            # Write as mono WAV
            sf.write(str(output_path), data, samplerate)
            
            # Clean up temp file
            Path(temp_path).unlink()
            
            # Get file info
            duration = len(data) / samplerate
            
            return {
                "filename": output_filename,
                "original_name": file.filename,
                "path": str(output_path.relative_to(root)),
                "duration": duration,
                "samplerate": samplerate,
                "channels": 1
            }
            
        except Exception as e:
            # Clean up on error
            if 'temp_path' in locals():
                Path(temp_path).unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Error processing audio file: {str(e)}")

    @app.get("/api/upload/list")
    def list_uploaded_files():
        """
        List all uploaded audio files with metadata.
        
        Returns:
            JSON response with list of uploaded files
        """
        root = _project_root()
        uploads_dir = root / "uploads"
        
        if not uploads_dir.exists():
            return {"files": []}
        
        files = []
        for wav_file in uploads_dir.glob("*.wav"):
            try:
                # Get audio file info
                data, samplerate = sf.read(str(wav_file))
                duration = len(data) / samplerate
                channels = 1 if len(data.shape) == 1 else data.shape[1]
                
                files.append({
                    "filename": wav_file.name,
                    "path": str(wav_file.relative_to(root)),
                    "duration": duration,
                    "samplerate": samplerate,
                    "channels": channels
                })
            except Exception:
                # Skip files that can't be read
                continue
        
        # Sort by filename
        files.sort(key=lambda x: x["filename"])
        return {"files": files}

    @app.delete("/api/upload/{filename}")
    def delete_uploaded_file(filename: str):
        """
        Delete an uploaded audio file.
        
        Args:
            filename: Name of the file to delete
            
        Returns:
            JSON response confirming deletion
        """
        root = _project_root()
        uploads_dir = root / "uploads"
        file_path = uploads_dir / filename
        
        # Security check - ensure file is in uploads directory
        try:
            file_path.resolve().relative_to(uploads_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid file path")
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        try:
            file_path.unlink()
            return {"ok": True, "deleted": filename}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error deleting file: {str(e)}")

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

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: start engine and VU publisher
        engine.start()
        app.state.vu_task = asyncio.create_task(_vu_publisher())
        try:
            yield
        finally:
            # Shutdown: cancel VU task and stop engine
            if app.state.vu_task:
                app.state.vu_task.cancel()
                with contextlib.suppress(Exception):
                    await app.state.vu_task
            # Stop any running feeder processes
            for _, proc in list(app.state.feed_procs.items()):
                with contextlib.suppress(Exception):
                    proc.terminate()
            app.state.feed_procs.clear()
            engine.stop()

    # Use lifespan context instead of deprecated on_event hooks
    app.router.lifespan_context = lifespan

    return app
