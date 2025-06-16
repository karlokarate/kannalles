# -*- coding: utf-8 -*-
"""
Telegram-Fetcher: Holt Filme & Serien aus Chats, extrahiert Metadaten,
schreibt sie samt Postern in die lokale SQLite-DB.
Neu (2025-06):  • global_search_and_store()  • auto_sync_latest()
"""

from __future__ import annotations
import json, os, random, re, sqlite3, threading, time, traceback, unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

import xbmc, xbmcaddon, xbmcvfs
try:
    import xbmcgui
except ImportError:
    xbmcgui = None  # type: ignore

# --------------------------------------------------------------------------- #
#  Settings & Dirs
# --------------------------------------------------------------------------- #
ADDON         = xbmcaddon.Addon()
PROFILE_DIR   = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
DATA_DIR      = os.path.join(PROFILE_DIR, "data"); os.makedirs(DATA_DIR, exist_ok=True)
POSTER_DIR    = os.path.join(DATA_DIR, "poster"); os.makedirs(POSTER_DIR, exist_ok=True)

DB_MOVIES = Path(os.path.join(DATA_DIR, "meta_movies.sqlite"))
DB_SERIES = Path(os.path.join(DATA_DIR, "meta_series.sqlite"))

def get_bridge_url() -> str:
    url = ADDON.getSetting("bridge_url").strip()
    if url:
        return url.rstrip("/")
    host = ADDON.getSetting("bridge_host") or "127.0.0.1"
    port = ADDON.getSetting("bridge_port") or "5000"
    return f"http://{host}:{port}".rstrip("/")

BRIDGE_URL    = get_bridge_url()

BATCH_SIZE    = 50
MAX_RETRIES   = 3
RETRY_SLEEP   = 2.0

# --------------------------------------------------------------------------- #
#  Bridge Connection Helpers
# --------------------------------------------------------------------------- #
def _bridge_ready() -> bool:
    resp = _json_request(f"{BRIDGE_URL}/is_ready")
    return bool(isinstance(resp, dict) and resp.get("connected"))

def bridge_connect() -> bool:
    api_id = ADDON.getSetting("api_id")
    api_hash = ADDON.getSetting("api_hash")
    phone = ADDON.getSetting("phone_number")
    if not api_id or not api_hash:
        return False
    data = {"api_id": api_id, "api_hash": api_hash}
    if phone:
        data["phone"] = phone
    resp = _json_request(f"{BRIDGE_URL}/set_api_creds", method="POST", data=data)
    return bool(isinstance(resp, dict) and resp.get("status") == "connected")

def ensure_bridge() -> bool:
    return _bridge_ready() or bridge_connect()

# --------------------------------------------------------------------------- #
#  DB-Helpers
# --------------------------------------------------------------------------- #
def _speed_pragmas(conn: sqlite3.Connection):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=100000")
    conn.execute("PRAGMA temp_store=MEMORY")

def _open_db(path: Path) -> sqlite3.Connection:
    xbmc.log(f"[DB] open {path}", xbmc.LOGDEBUG)
    conn = sqlite3.connect(path)
    _speed_pragmas(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS filme (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id         INTEGER,
            chat_title      TEXT,
            msg_id          INTEGER,
            title           TEXT,
            plot            TEXT,
            jahr            INTEGER,
            episodentitel   TEXT,
            poster_path     TEXT,
            logo_path       TEXT,
            file_unique_id  TEXT,
            file_size       INTEGER,
            duration        INTEGER,
            mime_type       TEXT,
            rating          TEXT,
            genres          TEXT,
            land            TEXT,
            fsk             TEXT,
            regie           TEXT,
            tmdb_rating     TEXT,
            collection      TEXT,
            status          TEXT DEFAULT 'pending',
            type            TEXT,
            series_id       INTEGER,
            season          INTEGER,
            episode         INTEGER
        )
        """
    )
    conn.commit()
    return conn

def _safe_execute(conn: sqlite3.Connection, q: str, p: Tuple[Any, ...] = ()):
    for _ in range(3):
        try:
            xbmc.log(f"[DB] exec {q} {p}", xbmc.LOGDEBUG)
            conn.execute(q, p); return
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                time.sleep(1)
            else:
                raise

def _db_path(sync_type: str) -> Path:
    return DB_MOVIES if sync_type == "movie" else DB_SERIES

# --------------------------------------------------------------------------- #
#  Bridge-Request
# --------------------------------------------------------------------------- #
import urllib.parse, urllib.request

def _json_request(url: str, *, method="GET", params=None, data=None, timeout=30):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    xbmc.log(f"[FETCHER] {method} {url} data={data}", xbmc.LOGDEBUG)
    req = urllib.request.Request(url, method=method)
    if method == "POST":
        req.add_header("Content-Type", "application/json")
        if data is not None:
            req.data = json.dumps(data).encode() if not isinstance(data, bytes) else data
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode()
            xbmc.log(f"[FETCHER] -> {txt[:200]}", xbmc.LOGDEBUG)
            return json.loads(txt)
    except Exception as e:
        xbmc.log(f"[BRIDGE ERROR] {url}: {e}", xbmc.LOGERROR)
        return {}

# --------------------------------------------------------------------------- #
#  Bridge-Wrapper-API
# --------------------------------------------------------------------------- #
def get_user_chats(folder_ids: List[str] | None = None) -> List[Dict[str, Any]]:
    """Liest die verfügbaren Chats, optional gefiltert nach Folder-IDs."""
    chats: List[Dict[str, Any]] = []
    ids = folder_ids or [None]
    for fid in ids:
        params = {"folder": fid} if fid else None
        resp = _json_request(f"{BRIDGE_URL}/sync", method="POST", params=params)
        chs = resp.get("chats", []) if isinstance(resp, dict) else []
        for c in chs:
            c.setdefault("id", c.get("chat_id"))
            if "title" in c and isinstance(c["title"], str):
                c["title"] = c["title"].replace("\n", " ").strip()
            chats.append(c)
    return chats

def get_folders() -> List[Dict[str, Any]]:
    resp = _json_request(f"{BRIDGE_URL}/folders")
    folders = resp.get("folders", []) if isinstance(resp, dict) else resp
    for f in folders:
        if "title" in f and isinstance(f["title"], str):
            f["title"] = f["title"].replace("\n", " ").strip()
    return folders

def download_logo(chat_id: int) -> str:
    url = f"{BRIDGE_URL}/logo/{chat_id}"
    fp  = os.path.join(POSTER_DIR, f"logo_{chat_id}.jpg")
    if os.path.exists(fp) and os.path.getsize(fp) > 10_000:
        return fp
    try:
        with urllib.request.urlopen(url, timeout=20) as r, open(fp, "wb") as out:
            out.write(r.read())
        return fp
    except Exception:
        return ""

def download_poster(chat_id: int, photo_id: int) -> str:
    url = f"{BRIDGE_URL}/poster/{chat_id}/{photo_id}"
    fp  = os.path.join(POSTER_DIR, f"poster_{chat_id}_{photo_id}.jpg")
    if os.path.exists(fp) and os.path.getsize(fp) > 10_000:
        return fp
    try:
        with urllib.request.urlopen(url, timeout=20) as r, open(fp, "wb") as out:
            out.write(r.read())
        return fp
    except Exception:
        return ""

# --------------------------------------------------------------------------- #
#  Parser-Regex
# --------------------------------------------------------------------------- #
FILM_REGEX = re.compile(r"^(?:[^\w]*Titel[:\s]+)?\s*(.*?)\s*(?:[-–]|:)\s*(19|20)\d{2}", re.I|re.S)
YEAR_REGEX = re.compile(r"(19|20)\d{2}")
RATING_REGEX  = re.compile(r"Rating[:\s]+([0-9]\.?[0-9]*)")
PLOT_REGEX    = re.compile(r"Plot:\s*(.*?)(?:Rating[:\s]+[0-9]\.?[0-9]*|#|$)", re.S)
GENRE_REGEX   = re.compile(r"#([A-Za-zÄÖÜäöüß]+)")
LENGTH_REGEX  = re.compile(r"Länge[:\s]+([0-9]+)\s*Minuten")
COUNTRY_REGEX = re.compile(r"Produktionsland[:\s]+([^\n]+)")
FSK_REGEX     = re.compile(r"FSK[:\s]+([0-9]+)")
REGIE_REGEX   = re.compile(r"Regie[:\s]+([^\n]+)")
TMDB_REGEX    = re.compile(r"TMDbRating[:\s]+([0-9]\.?[0-9]*)")
COLLECTION_REGEX = re.compile(r"Filmreihe[:\s]+([^\n]+)")

def _duration(d):
    if not d: return 0
    if isinstance(d, int): return d//60 if d>3000 else d
    if isinstance(d, str):
        if ":" in d:
            h,m = map(int,d.split(":",1)); return h*60+m
        if d.isdigit(): return int(d)
    return 0

def _clean_title(txt: str)->str:
    txt = unicodedata.normalize("NFKC", txt or "")
    txt = re.sub(r"\s*\(?((19|20)\d{2})\)?\s*$","",txt)
    txt = re.sub(r"\s*\([A-Z]{2,}\)$","",txt)
    return txt.strip(" -–:")

def extract_metadata(text: str)->Dict[str,str]:
    text = unicodedata.normalize("NFKC", text or "")
    meta={}
    m = FILM_REGEX.search(text)
    if m:
        meta["title"]=_clean_title(m.group(1)); meta["year"]=m.group(2)
    else:
        first=_clean_title(text.splitlines()[0]); meta["title"]=first
        y = YEAR_REGEX.search(first) or YEAR_REGEX.search(text)
        meta["year"]=y.group(0) if y else ""
    meta["plot"]=text.strip()
    meta["rating"]=(RATING_REGEX.search(text) or [None,""])[1]
    meta["genres"]=GENRE_REGEX.findall(text)
    meta["duration"]=(LENGTH_REGEX.search(text) or [None,""])[1]
    meta["country"]=(COUNTRY_REGEX.search(text) or [None,""])[1].strip()
    meta["fsk"]=(FSK_REGEX.search(text) or [None,""])[1]
    meta["director"]=(REGIE_REGEX.search(text) or [None,""])[1].strip()
    meta["tmdb_rating"]=(TMDB_REGEX.search(text) or [None,""])[1]
    meta["collection"]=(COLLECTION_REGEX.search(text) or [None,""])[1].strip()
    return meta

def parse_film_block(msgs: List[Dict[str,Any]], chat_id:int)->List[Dict[str,Any]]:
    """Erkennt Filmblöcke in einer Nachrichtenliste."""
    films: List[Dict[str, Any]] = []
    for i, v in enumerate(msgs):
        if v.get("type") != "video":
            continue
        # Metadaten aus Caption oder benachbarter Textnachricht
        meta_txt = v.get("caption") or ""
        if not meta_txt:
            if i+1 < len(msgs) and msgs[i+1].get("type") == "text":
                meta_txt = msgs[i+1].get("text", "")
            elif i > 0 and msgs[i-1].get("type") == "text":
                meta_txt = msgs[i-1].get("text", "")
        meta = extract_metadata(meta_txt)

        # Poster: bevorzugt benachbarte Photo-Nachricht, sonst Videothumb
        poster_id = None
        if i+1 < len(msgs) and msgs[i+1].get("type") == "photo":
            poster_id = msgs[i+1]["id"]
        elif i > 0 and msgs[i-1].get("type") == "photo":
            poster_id = msgs[i-1]["id"]
        else:
            poster_id = v["id"]

        films.append({
            "msg_id": v["id"],
            "poster_id": poster_id,
            "title": meta["title"],
            "jahr": meta["year"],
            "plot": meta["plot"],
            "rating": meta["rating"],
            "genres": meta["genres"],
            "duration": v.get("duration") or _duration(meta["duration"]),
            "land": meta["country"],
            "fsk": meta["fsk"],
            "regie": meta["director"],
            "tmdb_rating": meta["tmdb_rating"],
            "collection": meta["collection"],
            "file_size": v.get("file_size"),
            "mime_type": v.get("mime_type"),
        })
    return films

def extract_series(text:str):
    m=re.search(r"[sS](\d{1,2})\s*[eE](\d{1,2})\s*[-:\s]+(.+)",text or "")
    if m:return int(m.group(1)),int(m.group(2)),m.group(3).strip()
    m=re.search(r"[eE](\d{1,2})\s*[-:\s]+(.+)",text or "")
    if m:return None,int(m.group(1)),m.group(2).strip()
    return None,None,text.strip() if text else ""

def first_year(*txts):
    for t in txts:
        y=YEAR_REGEX.search(t or "")
        if y:return y.group(0)
    return ""

# --------------------------------------------------------------------------- #
#  GLOBAL SUCHEN
# --------------------------------------------------------------------------- #
def global_search_and_store(term: str, dialog=None, folder_ids: List[str] | None = None,
                            limit_per_chat: int = 50):
    """Suche in ausgewählten Chats/Folders und speichere Treffer in die DB."""
    if not ensure_bridge():
        return
    chs = get_user_chats(folder_ids)
    total = len(chs)
    term_lc = term.lower()
    for idx, ch in enumerate(chs):
        if dialog:
            dialog.update(int(idx / max(1, total) * 100), f"{idx+1}/{total} {ch['title']}")
        msgs = _json_request(f"{BRIDGE_URL}/history_raw", method="POST",
                             data={"chat_id": ch["id"], "limit": limit_per_chat})
        if not msgs:
            continue
        is_series = term_lc in (ch.get("title") or "").lower()
        if is_series:
            fetch_and_store(ch["id"], dialog=None, sync_type="series",
                             limit_override=None, raw_messages=msgs)
            continue
        films = parse_film_block(msgs, ch["id"])
        allowed = [f["msg_id"] for f in films if term_lc in (f["title"] + " " + f["plot"]).lower()]
        if allowed:
            fetch_and_store(ch["id"], dialog=None, sync_type="movie",
                             limit_override=None, raw_messages=msgs, allowed_msg_ids=allowed)

# --------------------------------------------------------------------------- #
#  AUTO-SYNC LATEST
# --------------------------------------------------------------------------- #
def auto_sync_latest(sync_type: str, per_chat: int = 20, folder_ids: List[str] | None = None):
    if not ensure_bridge():
        return
    for ch in get_user_chats(folder_ids):
        msgs=_json_request(f"{BRIDGE_URL}/history_raw", method="POST",
                           data={"chat_id":ch["id"], "limit":per_chat})
        if msgs:
            fetch_and_store(ch["id"], dialog=None, sync_type=sync_type,
                            limit_override=None, raw_messages=msgs)

def sync_latest_chat(chat_id: int, sync_type: str, limit: int = 20):
    """Sync die neuesten Nachrichten eines einzelnen Chats."""
    if not ensure_bridge():
        return
    msgs = _json_request(f"{BRIDGE_URL}/history_raw", method="POST",
                         data={"chat_id": chat_id, "limit": limit})
    if msgs:
        fetch_and_store(chat_id, dialog=None, sync_type=sync_type,
                        limit_override=None, raw_messages=msgs)

# --------------------------------------------------------------------------- #
#  Kernfunktion fetch_and_store
# --------------------------------------------------------------------------- #
def fetch_and_store(chat_id:int, dialog=None, sync_type:str="movie",
                    limit_override:int|None=None, raw_messages:List[Dict]|None=None,
                    allowed_msg_ids: List[int] | None = None):
    xbmc.log(f"[FETCHER] {sync_type} Sync Chat {chat_id}", xbmc.LOGINFO)
    dbp=_db_path(sync_type); conn=_open_db(dbp)
    try:
        # 0) Messages holen
        if raw_messages is None:
            limit = limit_override if limit_override is not None else 1000
            raw_messages=_json_request(f"{BRIDGE_URL}/history_raw", method="POST",
                                       data={"chat_id":chat_id,"limit":limit})
        if not raw_messages: return

        # 1) Chat-Metadaten
        chat_meta=next((c for c in get_user_chats() if c["id"]==chat_id), {})
        chat_title=chat_meta.get("title") or chat_meta.get("first_name")\
                    or chat_meta.get("username") or str(chat_id)
        logo=download_logo(chat_id)

        # 2) Einträge builden
        entries=[]
        if sync_type=="movie":
            films=parse_film_block(raw_messages, chat_id)
            if allowed_msg_ids:
                films=[f for f in films if f["msg_id"] in allowed_msg_ids]
            for f in films:
                poster=download_poster(chat_id,f["poster_id"]) if f["poster_id"] else ""
                entries.append({
                    "id":f["msg_id"], "chat_title":chat_title, "title":f["title"],
                    "plot":f["plot"], "jahr":f["jahr"], "episodentitel":"",
                    "poster_path":poster, "logo_path":logo,
                    "file_size":f["file_size"], "duration":f["duration"], "mime":f["mime_type"],
                    "rating":f["rating"], "genres":",".join(f["genres"]), "land":f["land"],
                    "fsk":f["fsk"], "regie":f["regie"], "tmdb_rating":f["tmdb_rating"],
                    "collection":f["collection"], "type":"movie",
                    "series_id":None, "season":None, "episode":None
                })
        else:  # series
            series_plot=next((m.get("text") for m in raw_messages if m.get("type")=="text" and m.get("text")), "")
            series_year=first_year(series_plot)
            for m in raw_messages:
                if m.get("type")!="video": continue
                season,episode,title=extract_series(m.get("caption") or "")
                if allowed_msg_ids and m["id"] not in allowed_msg_ids:
                    continue
                entries.append({
                    "id":m["id"], "chat_title":chat_title, "title":chat_title,
                    "plot":series_plot, "jahr":series_year, "episodentitel":title,
                    "poster_path":logo, "logo_path":logo,
                    "file_size":m.get("file_size"), "duration":m.get("duration"),
                    "mime":m.get("mime_type"), "rating":"", "genres":"", "land":"",
                    "fsk":"", "regie":"", "tmdb_rating":"", "collection":"",
                    "type":"series", "series_id":chat_id, "season":season, "episode":episode
                })

        # 3) Write
        for n,e in enumerate(entries):
            _safe_execute(conn, "INSERT OR IGNORE INTO filme (chat_id,msg_id) VALUES (?,?)",
                          (chat_id,e["id"]))
            _safe_execute(conn, """
            UPDATE filme SET
              chat_title=?, title=?, plot=?, jahr=?, episodentitel=?,
              poster_path=COALESCE(?,poster_path), logo_path=?,
              file_size=?, duration=?, mime_type=?, rating=?, genres=?,
              land=?, fsk=?, regie=?, tmdb_rating=?, collection=?,
              status='pending', type=?, series_id=?, season=?, episode=?
            WHERE chat_id=? AND msg_id=?
            """, (e["chat_title"],e["title"],e["plot"],e["jahr"],e["episodentitel"],
                  e["poster_path"],e["logo_path"],e["file_size"],e["duration"],e["mime"],
                  e["rating"],e["genres"],e["land"],e["fsk"],e["regie"],e["tmdb_rating"],
                  e["collection"],e["type"],e["series_id"],e["season"],e["episode"],
                  chat_id,e["id"]))
            if dialog and n%5==0:
                dialog.update(int((n+1)/len(entries)*100))
            if (n+1)%BATCH_SIZE==0: conn.commit()
        xbmc.log("[DB] commit", xbmc.LOGDEBUG)
        conn.commit()
    finally:
        xbmc.log("[DB] close", xbmc.LOGDEBUG)
        conn.close()
