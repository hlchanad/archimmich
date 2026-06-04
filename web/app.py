import os
import sys
import threading
import time
import queue
import json
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

# 1. DYNAMIC PYQT5 MOCKING (Before importing anything from src)
from unittest.mock import MagicMock
import types

class MockQApplication:
    @staticmethod
    def processEvents():
        pass
    @staticmethod
    def instance():
        return None

class MockQThread:
    def __init__(self, *args, **kwargs):
        pass
    def start(self):
        pass

class MockPyQtSignal:
    def __init__(self, *args):
        self._listeners = []
    def connect(self, slot):
        self._listeners.append(slot)
    def emit(self, *args):
        for listener in self._listeners:
            try:
                listener(*args)
            except Exception:
                pass

mock_qt = types.ModuleType('PyQt5')
mock_qt_widgets = types.ModuleType('PyQt5.QtWidgets')
mock_qt_core = types.ModuleType('PyQt5.QtCore')
mock_qt_gui = types.ModuleType('PyQt5.QtGui')

# Populate QtWidgets mock
mock_qt_widgets.QApplication = MockQApplication

# Populate QtCore mock
mock_qt_core.QThread = MockQThread
mock_qt_core.pyqtSignal = MockPyQtSignal
mock_qt_core.Qt = MagicMock()
mock_qt_core.QTimer = MagicMock()

# Populate QtGui mock
mock_qt_gui.QPixmap = MagicMock()
mock_qt_gui.QPainter = MagicMock()
mock_qt_gui.QBrush = MagicMock()
mock_qt_gui.QColor = MagicMock()

sys.modules['PyQt5'] = mock_qt
sys.modules['PyQt5.QtWidgets'] = mock_qt_widgets
sys.modules['PyQt5.QtCore'] = mock_qt_core
sys.modules['PyQt5.QtGui'] = mock_qt_gui

# Add parent directory to path to load src
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.append(project_root)

# Now import the untouched managers and helpers
from src.managers.login_manager import LoginManager
from src.managers.export_manager import ExportManager
from src.constants import CONFIG_FILE

# FastAPI imports
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="ArchImmich Web Backend")

# Enable CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom structures for Web logging & progress updating
class WebLogger:
    def __init__(self):
        self.logs = []
        self.listeners = []

    def append(self, message, level=None):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}"
        self.logs.append(formatted)
        # Keep logs list size reasonable (e.g., last 1000 lines)
        if len(self.logs) > 1000:
            self.logs.pop(0)
        # Dispatch to all active SSE queues
        for q in list(self.listeners):
            try:
                q.put_nowait(formatted)
            except Exception:
                # Remove listener if queue is full or closed
                self.listeners.remove(q)
        print(formatted, flush=True)

class WebProgressBar:
    def __init__(self, logger: WebLogger):
        self.logger = logger
        self.value = 0
        self.format_str = ""

    def setValue(self, val):
        self.value = val
        self.logger.append(f"Progress update: {self.format_str} - {val}%")

    def setFormat(self, fmt):
        self.format_str = fmt
        self.logger.append(fmt)

    def show(self):
        pass

# Global Application State
web_logger = WebLogger()
state = {
    "login_manager": None,
    "export_manager": None,
    "is_downloading": False,
    "stop_requested": False,
    "active_thread": None,
    "progress_bar": WebProgressBar(web_logger),
    "current_status": "Idle",
    "saved_config": {}
}

# Load settings from persistent file inside config directory
# Default to mapping /config and /downloads inside Docker
CONFIG_DIR = os.environ.get("ARCHIMMICH_CONFIG_DIR", "/config" if os.path.exists("/config") else project_root)
DOWNLOADS_DIR = os.environ.get("ARCHIMMICH_DOWNLOADS_DIR", "/downloads" if os.path.exists("/downloads") else os.path.join(project_root, "downloads"))

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Override helpers.get_app_directory so config/logs save in the Docker mapping
import src.utils.helpers
src.utils.helpers.get_app_directory = lambda: CONFIG_DIR

# Initial configuration load
def load_saved_config():
    config_file_path = os.path.join(CONFIG_DIR, CONFIG_FILE)
    if os.path.exists(config_file_path):
        try:
            with open(config_file_path, "r") as f:
                return json.load(f)
        except Exception as e:
            web_logger.append(f"Error loading config.json: {e}")
    return {}

state["saved_config"] = load_saved_config()

# Data models
class LoginRequest(BaseModel):
    server_url: str
    api_key: str
    remember_me: bool

class ExportRequest(BaseModel):
    bucket_ids: Optional[List[str]] = None
    album_ids: Optional[List[str]] = None
    max_archive_size_mb: int
    is_archived: bool = False
    with_partners: bool = False
    with_stacked: bool = False
    visibility: str = ""
    is_favorite: bool = False
    is_trashed: bool = False
    order: str = "desc"

@app.get("/api/config")
def get_config():
    config = load_saved_config()
    server_url = config.get("server_ip", "").replace("/api", "")
    if not server_url:
        server_url = "http://immich-server:2283"
    # Remove sensitive key if any, or pass for auto-fill helper
    return {
        "server_url": server_url,
        "api_key": config.get("api_key", ""),
        "downloads_dir": DOWNLOADS_DIR
    }

@app.post("/api/login")
def login(req: LoginRequest):
    try:
        lm = LoginManager(config=state["saved_config"])
        lm.set_logger(web_logger)
        lm.set_credentials(req.server_url, req.api_key, req.remember_me)
        
        user_info = lm.login()
        if user_info:
            state["login_manager"] = lm
            # Initialize export manager with standard callback
            state["export_manager"] = ExportManager(
                login_manager=lm,
                logger=web_logger,
                output_dir=DOWNLOADS_DIR,
                stop_flag_callback=lambda: state["stop_requested"]
            )
            state["saved_config"] = load_saved_config()
            return {"status": "success", "user": user_info}
        else:
            raise HTTPException(status_code=401, detail="Authentication failed")
    except Exception as e:
        web_logger.append(f"Login failed: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/logout")
def logout():
    state["login_manager"] = None
    state["export_manager"] = None
    state["is_downloading"] = False
    return {"status": "success"}

@app.get("/api/status")
def get_status():
    lm = state["login_manager"]
    user = lm.get_user() if (lm and lm.is_logged_in()) else None
    
    return {
        "logged_in": lm is not None and lm.is_logged_in(),
        "user": user,
        "is_downloading": state["is_downloading"],
        "status_text": state["current_status"],
        "progress_percent": state["progress_bar"].value,
        "progress_format": state["progress_bar"].format_str
    }

@app.get("/api/buckets")
def get_buckets(
    is_archived: bool = False,
    with_partners: bool = False,
    with_stacked: bool = False,
    visibility: str = "",
    is_favorite: bool = False,
    is_trashed: bool = False,
    order: str = "desc"
):
    em = state["export_manager"]
    if not em:
        raise HTTPException(status_code=400, detail="Not logged in")
    try:
        buckets = em.get_timeline_buckets(
            is_archived=is_archived,
            with_partners=with_partners,
            with_stacked=with_stacked,
            visibility=visibility,
            is_favorite=is_favorite,
            is_trashed=is_trashed,
            order=order
        )
        return {"buckets": buckets}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/albums")
def get_albums():
    em = state["export_manager"]
    if not em:
        raise HTTPException(status_code=400, detail="Not logged in")
    try:
        albums = em.get_albums()
        return {"albums": albums}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run_download_in_background(req: ExportRequest):
    state["is_downloading"] = True
    state["stop_requested"] = False
    em = state["export_manager"]
    
    try:
        archive_size_bytes = req.max_archive_size_mb * 1024 * 1024 if req.max_archive_size_mb > 0 else None
        
        if req.album_ids:
            web_logger.append(f"Starting export for {len(req.album_ids)} album(s)")
            
            # Fetch albums to get their names
            try:
                albums = em.get_albums()
                album_map = {a["id"]: a["albumName"] for a in albums}
            except Exception as e:
                web_logger.append(f"Failed to fetch albums map: {e}")
                album_map = {}
                
            for i, album_id in enumerate(req.album_ids):
                if state["stop_requested"]:
                    web_logger.append("Export cancelled by user request.")
                    break
                    
                album_name = album_map.get(album_id, f"Album_{album_id}")
                import re
                safe_album_name = re.sub(r'[\\/*?:"<>|]', "_", album_name)
                
                state["current_status"] = f"Processing album {i+1} of {len(req.album_ids)}"
                web_logger.append(f"Preparing archive for album: {album_name} ({album_id})")
                
                # Prepare archive metadata
                info = em.prepare_archive(album_id=album_id, archive_size_bytes=archive_size_bytes)
                total_size = info.get("totalSize", 0)
                
                # Call download logic
                result = em.download_archive(
                    album_id=album_id,
                    bucket_name=safe_album_name,
                    total_size=total_size,
                    current_download_progress_bar=state["progress_bar"]
                )
                
                web_logger.append(f"Album {album_name} completed with result: {result}")
        else:
            web_logger.append(f"Starting export for {len(req.bucket_ids)} bucket(s)")
            
            for i, bucket_time in enumerate(req.bucket_ids):
                if state["stop_requested"]:
                    web_logger.append("Export cancelled by user request.")
                    break
                    
                state["current_status"] = f"Processing bucket {i+1} of {len(req.bucket_ids)}"
                web_logger.append(f"Fetching assets for bucket: {bucket_time}")
                
                assets = em.get_timeline_bucket_assets(
                    time_bucket=bucket_time,
                    is_archived=req.is_archived,
                    with_partners=req.with_partners,
                    with_stacked=req.with_stacked,
                    visibility=req.visibility,
                    is_favorite=req.is_favorite,
                    is_trashed=req.is_trashed,
                    order=req.order
                )
                
                if not assets:
                    web_logger.append(f"No assets found in bucket {bucket_time}. Skipping.")
                    continue
                    
                asset_ids = [a["id"] for a in assets]
                
                # Prepare archive metadata
                info = em.prepare_archive(asset_ids=asset_ids, archive_size_bytes=archive_size_bytes)
                total_size = info.get("totalSize", 0)
                
                formatted_bucket_name = em.format_time_bucket(bucket_time)
                
                # Call untouched download logic
                result = em.download_archive(
                    asset_ids=asset_ids,
                    bucket_name=formatted_bucket_name,
                    total_size=total_size,
                    current_download_progress_bar=state["progress_bar"]
                )
                
                web_logger.append(f"Bucket {formatted_bucket_name} completed with result: {result}")
                
        state["current_status"] = "Completed" if not state["stop_requested"] else "Cancelled"
    except Exception as e:
        web_logger.append(f"Error during export background task: {str(e)}")
        state["current_status"] = "Error"
    finally:
        state["is_downloading"] = False
        state["active_thread"] = None

@app.post("/api/export/start")
def start_export(req: ExportRequest, background_tasks: BackgroundTasks):
    if not state["export_manager"]:
        raise HTTPException(status_code=400, detail="Not logged in")
    if state["is_downloading"]:
        raise HTTPException(status_code=400, detail="Download already running")
        
    state["stop_requested"] = False
    state["current_status"] = "Starting..."
    state["progress_bar"].value = 0
    state["progress_bar"].format_str = ""
    
    t = threading.Thread(target=run_download_in_background, args=(req,), daemon=True)
    state["active_thread"] = t
    t.start()
    
    return {"status": "started"}

@app.post("/api/export/stop")
def stop_export():
    if not state["is_downloading"]:
        return {"status": "not_running"}
    state["stop_requested"] = True
    state["current_status"] = "Stopping..."
    return {"status": "stopping"}

@app.get("/api/logs/stream")
def stream_logs(request: Request):
    async def event_generator():
        q = queue.Queue()
        web_logger.listeners.append(q)
        
        # Send historical logs first
        for history in web_logger.logs:
            yield f"data: {history}\n\n"
            
        try:
            while True:
                # Check client connection
                if await request.is_disconnected():
                    break
                try:
                    msg = q.get(timeout=1.0)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    # Keepalive ping
                    yield "data: :ping\n\n"
        except Exception:
            pass
        finally:
            if q in web_logger.listeners:
                web_logger.listeners.remove(q)
                
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/downloads")
def list_downloads():
    if not os.path.exists(DOWNLOADS_DIR):
        return {"files": []}
    files = []
    for f in os.listdir(DOWNLOADS_DIR):
        if f.endswith(".zip"):
            full_path = os.path.join(DOWNLOADS_DIR, f)
            size = os.path.getsize(full_path)
            # Format size nicely
            if size >= 1024 ** 3:
                size_str = f"{size / (1024 ** 3):.2f} GB"
            else:
                size_str = f"{size / (1024 ** 2):.2f} MB"
            files.append({
                "name": f,
                "size_formatted": size_str,
                "size_bytes": size,
                "created_at": os.path.getmtime(full_path)
            })
    # Sort newest first
    files.sort(key=lambda x: x["created_at"], reverse=True)
    return {"files": files}

from fastapi.responses import FileResponse
@app.get("/api/downloads/{filename}")
def download_file(filename: str):
    # Security: check to prevent directory traversal
    filename = os.path.basename(filename)
    file_path = os.path.join(DOWNLOADS_DIR, filename)
    if not filename.endswith(".zip") or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=file_path, filename=filename, media_type="application/zip")

# Mount Static Files UI
static_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_path):
    app.mount("/", StaticFiles(directory=static_path, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # Default to running on port 8000
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
