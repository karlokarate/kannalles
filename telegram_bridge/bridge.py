# -*- coding: utf-8 -*-
"""
FastAPI-Bridge zwischen Kodi-Addon und Telegram (Telethon)
"""

from __future__ import annotations
import asyncio, glob, io, json, os, time, logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Response, Request
from fastapi.responses import StreamingResponse
from telethon import TelegramClient, functions
from telethon.errors import FolderIdInvalidError
from telethon.tl.types import MessageMediaDocument

SESSION   = os.getenv("TELEGRAM_SESSION", "kodi")
TEMP_DIR  = Path(os.getenv("KODI_TEMP_DIR", Path.home() / ".kodi" / "temp"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = Path(__file__).with_name("debug.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bridge")

tg_client: Optional[TelegramClient] = None
tg_connected = False
client_lock  = asyncio.Lock()

app = FastAPI()

@app.middleware("http")
async def log_http(request: Request, call_next):
    body = await request.body()
    logger.debug(f"--> {request.method} {request.url.path}?{request.url.query} {body.decode(errors='ignore')}")
    response = await call_next(request)
    try:
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk
        response = Response(content=resp_body, status_code=response.status_code,
                            headers=dict(response.headers), media_type=response.media_type)
        logger.debug(f"<-- {response.status_code} {resp_body[:500].decode(errors='ignore')}")
    except Exception as e:
        logger.debug(f"Logging response failed: {e}")
    return response

# --------------------------------------------------------------------------- #
#  Hilfen
# --------------------------------------------------------------------------- #
def require_client():
    if not tg_client or not tg_connected:
        raise HTTPException(503, "Bridge nicht initialisiert – /set_api_creds")

async def ensure_client():
    if tg_client and tg_connected:
        return
    raise HTTPException(503, "Noch nicht verbunden → /set_api_creds")

# --------------------------------------------------------------------------- #
#  Login / API-Creds
# --------------------------------------------------------------------------- #
@app.post("/set_api_creds")
async def set_api_creds(data: Dict[str, str] = Body(...)):
    global tg_client, tg_connected
    api_id, api_hash = int(data["api_id"]), data["api_hash"]
    phone = data.get("phone") or None
    if tg_client:
        try: await tg_client.disconnect()
        except Exception: pass
    tg_client = TelegramClient(SESSION, api_id, api_hash)
    await tg_client.start(phone=phone)
    tg_connected = True
    return {"status":"connected"}

@app.get("/is_ready")
def is_ready(): return {"connected": tg_connected}

# --------------------------------------------------------------------------- #
#  Chat-Liste
# --------------------------------------------------------------------------- #
async def _collect_chats(folder_ids: Optional[str]):
    """Liefert Chats, optional gefiltert nach einer oder mehreren Folder-IDs."""
    require_client()
    chats: List[Dict[str, str]] = []
    async with client_lock:
        try:
            if folder_ids:
                ids = [int(f) for f in str(folder_ids).split(",") if f]
                for fid in ids:
                    try:
                        async for d in tg_client.iter_dialogs(folder=fid, limit=None):
                            t = "channel" if d.is_channel else "group" if d.is_group else "user"
                            chats.append({"chat_id": d.id, "title": d.name, "type": t})
                    except FolderIdInvalidError:
                        continue
            else:
                async for d in tg_client.iter_dialogs(limit=None):
                    t = "channel" if d.is_channel else "group" if d.is_group else "user"
                    chats.append({"chat_id": d.id, "title": d.name, "type": t})
        except FolderIdInvalidError:
            pass
    return chats

@app.post("/sync")
async def sync_post(folder: Optional[str] = Query(None)):
    return {"chats": await _collect_chats(folder)}

@app.get("/sync")
async def sync_get(folder: Optional[str] = Query(None)):
    return {"chats": await _collect_chats(folder)}

# --------------------------------------------------------------------------- #
#  Folders (Dialog Filters)
# --------------------------------------------------------------------------- #
@app.get("/folders")
async def get_folders():
    """Liefert vorhandene Telegram-Ordner (Dialog Filters)."""
    require_client()
    async with client_lock:
        result = await tg_client(functions.messages.GetDialogFiltersRequest())
        dialog_filters = []
        if result and hasattr(result, "filters"):
            try:
                dialog_filters = list(result.filters or [])
            except TypeError:
                dialog_filters = []
    return {
        "folders": [
            {
                "id": f.id,
                "title": str(getattr(f.title, "text", str(f.title))).replace("\n", " ").strip(),
            }
            for f in dialog_filters
        ]
    }

# --------------------------------------------------------------------------- #
#  Raw-History & Suche
# --------------------------------------------------------------------------- #
def _msg_to_dict(m):
    logger.debug("[TG MSG] %s", m.stringify())
    if m.photo:
        tp,sz,mt,dur,cap,txt="photo",None,"",None,"",""
    elif isinstance(m.media, MessageMediaDocument):
        tp="video"; sz=m.media.document.size; mt=m.media.document.mime_type
        dur=next((a.seconds for a in m.media.document.attributes if hasattr(a,"seconds")),None)
        cap,txt=m.message,""
    else:
        tp,sz,mt,dur,cap,txt="text",None,"",None,"",m.text or ""
    return {"id":m.id,"date":str(m.date),"type":tp,"file_size":sz,"mime_type":mt,
            "duration":dur,"caption":cap,"text":txt}

@app.post("/history_raw")
async def history_raw(payload: Dict[str,int]=Body(...)):
    require_client(); cid=payload["chat_id"]; limit=int(payload.get("limit",500))
    async with client_lock:
        msgs=await tg_client.get_messages(cid, limit=limit)
        for m in msgs:
            logger.debug("[HISTORY_RAW] %s", m.stringify())
    return [_msg_to_dict(m) for m in msgs]

@app.post("/search")
async def search(payload: Dict=Body(...)):
    """Volltextsuche in einem Chat."""
    require_client()
    cid=payload["chat_id"]; query=payload["query"]; limit=int(payload.get("limit",50))
    async with client_lock:
        msgs=await tg_client.get_messages(cid, limit=limit, search=query)
        for m in msgs:
            logger.debug("[SEARCH] %s", m.stringify())
    return [_msg_to_dict(m) for m in msgs]

# --------------------------------------------------------------------------- #
#  Logo & Poster
# --------------------------------------------------------------------------- #
@app.get("/logo/{chat_id}")
async def logo(chat_id:int):
    require_client()
    async with client_lock:
        chat=await tg_client.get_entity(chat_id)
        if not chat.photo: raise HTTPException(404)
        data=await tg_client.download_profile_photo(chat, file=bytes)
    return StreamingResponse(io.BytesIO(data), media_type="image/jpeg")

@app.get("/poster/{chat_id}/{msg_id}")
async def poster(chat_id:int, msg_id:int):
    """Poster oder Videothumb zu einer Nachricht"""
    require_client()
    async with client_lock:
        msg = await tg_client.get_messages(chat_id, ids=msg_id)
        logger.debug("[POSTER] %s", msg.stringify())
        data = None
        if msg.photo:
            data = await tg_client.download_media(msg.photo, file=bytes)
        elif isinstance(msg.media, MessageMediaDocument) and msg.media.document.thumbs:
            data = await tg_client.download_media(msg.media.document, file=bytes, thumb=-1)
        if not data:
            raise HTTPException(404)
    return StreamingResponse(io.BytesIO(data), media_type="image/jpeg")

# --------------------------------------------------------------------------- #
#  Stream-Download
# --------------------------------------------------------------------------- #
ACTIVE,CANCEL={},{}
async def _tg_to_file(chat_id:int,msg_id:int,fp:Path):
    require_client(); key=(chat_id,msg_id); CANCEL[key]=asyncio.Event()
    async with client_lock:
        msg=await tg_client.get_messages(chat_id, ids=msg_id)
        logger.debug("[STREAM] %s", msg.stringify())
    def prog(cur,total):
        if CANCEL[key].is_set(): raise asyncio.CancelledError()
    await tg_client.download_media(msg.media, file=str(fp),
                                   progress_callback=prog, seek=fp.stat().st_size if fp.exists() else 0)
    CANCEL.pop(key,None)

def _next_file()->Path:
    nums=[int(Path(f).stem) for f in glob.glob(str(TEMP_DIR/"*.mp4")) if Path(f).stem.isdigit()]
    return TEMP_DIR/f"{max(nums+[-1])+1:03d}.mp4"

async def _wait_header(fp:Path,t=60):
    for _ in range(t):
        if fp.exists() and fp.stat().st_size>1_000_000: return True
        await asyncio.sleep(1)
    return False

@app.get("/get_strm/{chat_id}/{msg_id}")
async def get_strm(chat_id:int,msg_id:int):
    require_client()
    fp=_next_file(); key=(chat_id,msg_id)
    if key not in ACTIVE or ACTIVE[key].done():
        ACTIVE[key]=asyncio.create_task(_tg_to_file(chat_id,msg_id,fp))
    if not await _wait_header(fp):
        raise HTTPException(503,"Header noch nicht fertig")
    return Response(str(fp), media_type="text/plain")

# --------------------------------------------------------------------------- #
#  Temp-Dateien
# --------------------------------------------------------------------------- #
@app.get("/list_temp_files")
def list_temp(): return {"files":[{"file":f,"size":os.path.getsize(f)} for f in glob.glob(str(TEMP_DIR/"*.mp4"))]}

@app.post("/delete_temp_file")
def del_temp(payload:Dict[str,str]=Body(...)):
    fp=payload.get("file","")
    if os.path.exists(fp): os.remove(fp)
    return {"status":"deleted"}

@app.post("/delete_all_temp_files")
def del_all():
    for f in glob.glob(str(TEMP_DIR/"*.mp4")): os.remove(f)
    return {"status":"ok"}
