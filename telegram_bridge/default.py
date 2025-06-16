# -*- coding: utf-8 -*-
"""
Kodi-Addon-Frontend
Reiter:  Suche · Filme · Serien
Auto-Sync der neuesten Einträge & globale Volltextsuche
"""

from __future__ import annotations
import json, os, sys, urllib.parse, urllib.request

import xbmc, xbmcaddon, xbmcgui, xbmcplugin, xbmcvfs

from resources.lib import db_async_split as db
from resources.lib import telegram_fetcher

# --------------------------------------------------------------------------- #
#  Addon-Konstanten & Helfer
# --------------------------------------------------------------------------- #
ADDON        = xbmcaddon.Addon()
BASE_URL     = sys.argv[0]
HANDLE       = int(sys.argv[1])
ARGS         = urllib.parse.parse_qs(sys.argv[2][1:])

USE_FOLDER_FILTER = ADDON.getSettingBool("use_folder_filter")

# --------------------------------------------------------------------------- #
def get_bridge_url() -> str:
    """Ermittelt die Bridge-URL unter Berücksichtigung Host/Port-Fallback."""
    url = ADDON.getSetting("bridge_url").strip()
    if url:
        return url.rstrip("/")
    host = ADDON.getSetting("bridge_host") or "127.0.0.1"
    port = ADDON.getSetting("bridge_port") or "5000"
    return f"http://{host}:{port}".rstrip("/")

BRIDGE_URL = get_bridge_url()

# --------------------------------------------------------------------------- #
def add_folder(label: str, url: str, *, art=None, info=None, is_folder=True):
    li = xbmcgui.ListItem(label)
    if art:
        li.setArt(art)
    if info:
        li.setInfo("video", info)
    xbmcplugin.addDirectoryItem(HANDLE, url, li, is_folder)

def _int_or_zero(val, factor=1):
    try:
        return int(val) * factor
    except Exception:
        return 0

# --------------------------------------------------------------------------- #
#  Root-Menü
# --------------------------------------------------------------------------- #
def root_menu():
    add_folder("Suche", f"{BASE_URL}?action=search_ui")
    add_folder("Filme", f"{BASE_URL}?action=choose_movie_source")
    add_folder("Serien", f"{BASE_URL}?action=choose_series_source")
    add_folder("Einstellungen", f"{BASE_URL}?action=settings_menu")
    xbmcplugin.endOfDirectory(HANDLE)

# --------------------------------------------------------------------------- #
#  Einstellungen & Konfiguration
# --------------------------------------------------------------------------- #
def settings_menu():
    dlg = xbmcgui.Dialog()
    opts = [
        "Telegram Login",
        "Filme-Ordner wählen",
        "Serien-Ordner wählen",
    ]
    choice = dlg.select("Einstellungen", opts)
    if choice == 0:
        telegram_login()
    elif choice == 1:
        select_folder("movies")
    elif choice == 2:
        select_folder("series")

def telegram_login():
    dlg = xbmcgui.Dialog()
    api_id = dlg.input("API ID", defaultt=ADDON.getSetting("api_id"), type=xbmcgui.INPUT_NUM)
    if not api_id:
        return
    api_hash = dlg.input("API Hash", defaultt=ADDON.getSetting("api_hash"), type=xbmcgui.INPUT_ALPHANUM)
    phone = dlg.input("Telefon", defaultt=ADDON.getSetting("phone_number"), type=xbmcgui.INPUT_ALPHANUM)
    ADDON.setSettingString("api_id", api_id)
    ADDON.setSettingString("api_hash", api_hash)
    ADDON.setSettingString("phone_number", phone)
    telegram_fetcher.bridge_connect()

def select_folder(which:str):
    folders = telegram_fetcher.get_folders()
    if not folders:
        xbmcgui.Dialog().ok("Fehler", "Keine Ordner gefunden")
        return
    labels = [f["title"].replace("\n", " ").strip() for f in folders]
    labels.insert(0, "Alle")
    idx = xbmcgui.Dialog().select("Ordner wählen", labels)
    if idx == -1:
        return
    if idx == 0:
        ADDON.setSettingString(f"folder_{which}_id", "")
        ADDON.setSettingString(f"folder_{which}", "")
    else:
        sel = folders[idx-1]
        ADDON.setSettingString(f"folder_{which}_id", str(sel["id"]))
        ADDON.setSettingString(f"folder_{which}", sel["title"])

def choose_folders(multi: bool = False):
    folders = telegram_fetcher.get_folders()
    if not folders:
        xbmcgui.Dialog().ok("Fehler", "Keine Ordner gefunden")
        return None
    labels = [f["title"].replace("\n", " ").strip() for f in folders]
    if multi:
        idxs = xbmcgui.Dialog().multiselect("Ordner wählen", labels)
        if idxs is None:
            return None
        return [str(folders[i]["id"]) for i in idxs]
    idx = xbmcgui.Dialog().select("Ordner wählen", labels)
    if idx == -1:
        return None
    return str(folders[idx]["id"])

def choose_chat(folder_ids=None):
    chats = telegram_fetcher.get_user_chats(folder_ids)
    if not chats:
        xbmcgui.Dialog().ok("Fehler", "Keine Chats gefunden")
        return None
    labels = [str(c.get("title") or c.get("id")).replace("\n", " ").strip() for c in chats]
    idx = xbmcgui.Dialog().select("Chat wählen", labels)
    if idx == -1:
        return None
    return str(chats[idx]["id"])

def choose_movie_source():
    opts = ["Datenbank", "Telegram"]
    choice = xbmcgui.Dialog().select("Filme", opts)
    if choice == 0:
        xbmc.executebuiltin(
            f"Container.Update({BASE_URL}?action=list_movies_db&offset=0)"
        )
    elif choice == 1:
        pick_movie_chat()

def choose_series_source():
    opts = ["Datenbank", "Telegram"]
    choice = xbmcgui.Dialog().select("Serien", opts)
    if choice == 0:
        xbmc.executebuiltin(
            f"Container.Update({BASE_URL}?action=list_series_db&offset=0)"
        )
    elif choice == 1:
        pick_series_chat()

def pick_movie_chat():
    fid = None
    if USE_FOLDER_FILTER:
        fid = ADDON.getSetting("folder_movies_id") or None
        if not fid:
            fid = choose_folders()
        if fid is None:
            return
    chat = choose_chat([fid] if fid else None)
    if chat:
        xbmc.executebuiltin(f"Container.Update({BASE_URL}?action=list_movies&chat_id={chat}&offset=0)")

def pick_series_chat():
    fid = None
    if USE_FOLDER_FILTER:
        fid = ADDON.getSetting("folder_series_id") or None
        if not fid:
            fid = choose_folders()
        if fid is None:
            return
    chat = choose_chat([fid] if fid else None)
    if chat:
        xbmc.executebuiltin(f"Container.Update({BASE_URL}?action=list_series&chat_id={chat}&offset=0)")

# --------------------------------------------------------------------------- #
#  Globale Suche
# --------------------------------------------------------------------------- #
def search_ui() -> None:
    folder_ids = None
    if USE_FOLDER_FILTER:
        stored = []
        for key in ("folder_movies_id", "folder_series_id"):
            val = ADDON.getSetting(key)
            if val:
                stored.append(val)
        if stored:
            folder_ids = stored
        else:
            folder_ids = choose_folders(multi=True)
            if folder_ids is None:
                return
    dlg = xbmcgui.Dialog()
    term = dlg.input("Suchbegriff eingeben …", type=xbmcgui.INPUT_ALPHANUM)
    if not term:
        return
    dlg_progress = xbmcgui.DialogProgress()
    dlg_progress.create("Suche", f"Suche nach '{term}' …")
    telegram_fetcher.global_search_and_store(term, dlg_progress, folder_ids)
    dlg_progress.close()
    # Ergebnisse anzeigen
    xbmc.executebuiltin(
        f"Container.Update({BASE_URL}?action=list_search_results&query={urllib.parse.quote(term)}&offset=0)"
    )

def list_search_results(query: str, offset: int):
    page_size = 40
    movies = db.search_movies(query, limit=page_size, offset=offset)
    series = db.search_series(query, limit=page_size, offset=offset)
    for item in movies + series:
        art = {
            "thumb": item.get("poster_path") or item.get("logo_path"),
            "icon": item.get("poster_path") or item.get("logo_path"),
            "fanart": item.get("poster_path") or item.get("logo_path"),
        }
        info = {
            "title": item.get("title") or item.get("episodentitel"),
            "plot": item.get("plot", ""),
            "year": _int_or_zero(item.get("jahr")),
        }
        url = f"{BASE_URL}?action=play&chat_id={item['chat_id']}&msg_id={item['msg_id']}"
        add_folder(info["title"], url, art=art, info=info, is_folder=False)
    # „Mehr laden…“
    if len(movies) + len(series) == page_size:
        next_offset = offset + page_size
        add_folder(
            "Mehr laden …",
            f"{BASE_URL}?action=list_search_results&query={urllib.parse.quote(query)}&offset={next_offset}",
        )
    xbmcplugin.endOfDirectory(HANDLE)

# --------------------------------------------------------------------------- #
#  Auto-Sync & Listing Filme
# --------------------------------------------------------------------------- #
def list_movies_db(offset: int):
    batch = 20
    telegram_fetcher.auto_sync_latest("movie", batch)
    movies = db.get_latest_movies(batch, offset)
    for mv in movies:
        art = {"thumb": mv.get("poster_path") or mv.get("logo_path")}
        info = {
            "title": mv.get("title"),
            "plot": mv.get("plot", ""),
            "year": _int_or_zero(mv.get("jahr")),
            "duration": _int_or_zero(mv.get("duration"), 60),
        }
        url = f"{BASE_URL}?action=play&chat_id={mv['chat_id']}&msg_id={mv['msg_id']}"
        add_folder(mv["title"], url, art=art, info=info, is_folder=False)
    if len(movies) == batch:
        add_folder(
            "Mehr laden …",
            f"{BASE_URL}?action=list_movies_db&offset={offset+batch}",
        )
    xbmcplugin.endOfDirectory(HANDLE)

def list_latest_movies(chat_id: str, offset: int):
    batch = 20
    telegram_fetcher.sync_latest_chat(int(chat_id), "movie", offset + batch)
    movies = db.get_latest_movies_by_chat(int(chat_id), batch, offset)
    for mv in movies:
        art = {"thumb": mv.get("poster_path") or mv.get("logo_path")}
        info = {
            "title": mv.get("title"),
            "plot": mv.get("plot", ""),
            "year": _int_or_zero(mv.get("jahr")),
            "duration": _int_or_zero(mv.get("duration"), 60),
        }
        url = f"{BASE_URL}?action=play&chat_id={mv['chat_id']}&msg_id={mv['msg_id']}"
        add_folder(mv["title"], url, art=art, info=info, is_folder=False)
    if len(movies) == batch:
        add_folder(
            "Mehr laden …",
            f"{BASE_URL}?action=list_movies&chat_id={chat_id}&offset={offset+batch}",
        )
    xbmcplugin.endOfDirectory(HANDLE)

# --------------------------------------------------------------------------- #
#  Auto-Sync & Listing Serien
# --------------------------------------------------------------------------- #
def list_series_db(offset: int):
    batch = 20
    telegram_fetcher.auto_sync_latest("series", batch)
    series = db.get_latest_series(batch, offset)
    for se in series:
        art = {"thumb": se.get("poster_path") or se.get("logo_path")}
        info = {
            "plot": se.get("plot", ""),
            "year": _int_or_zero(se.get("jahr")),
        }
        url = f"{BASE_URL}?action=series_detail&series_id={se['series_id']}&chat_id={se['chat_id']}"
        add_folder(se["title"], url, art=art, info=info)
    if len(series) == batch:
        add_folder(
            "Mehr laden …",
            f"{BASE_URL}?action=list_series_db&offset={offset+batch}",
        )
    xbmcplugin.endOfDirectory(HANDLE)

def list_latest_series(chat_id: str, offset: int):
    batch = 20
    telegram_fetcher.sync_latest_chat(int(chat_id), "series", offset + batch)
    series = db.get_series_for_chat(int(chat_id))
    for se in series:
        art = {"thumb": se.get("poster_path") or se.get("logo_path")}
        info = {
            "plot": se.get("plot", ""),
            "year": _int_or_zero(se.get("jahr")),
        }
        url = f"{BASE_URL}?action=series_detail&series_id={se['series_id']}&chat_id={se['chat_id']}"
        add_folder(se["title"], url, art=art, info=info)
    if len(series) == batch:
        add_folder(
            "Mehr laden …",
            f"{BASE_URL}?action=list_series&chat_id={chat_id}&offset={offset+batch}",
        )
    xbmcplugin.endOfDirectory(HANDLE)

# --------------------------------------------------------------------------- #
#  Serien-Untermenüs
# --------------------------------------------------------------------------- #
def list_seasons(series_id: str, chat_id: str):
    seasons = db.get_seasons(series_id)
    for s in seasons:
        label = f"Staffel {s['number']}"
        url   = f"{BASE_URL}?action=list_episodes&series_id={series_id}&season={s['number']}&chat_id={chat_id}"
        add_folder(label, url)
    xbmcplugin.endOfDirectory(HANDLE)

def list_episodes(series_id: str, season: int, chat_id: str):
    eps = db.get_episodes(series_id, season)
    for ep in eps:
        title = ep.get("episodentitel") or f"S{season:02d}E{ep.get('episode'):02d}"
        info  = {"title": title, "plot": ep.get("plot", "")}
        art   = {"thumb": ep.get("poster_path") or ep.get("logo_path")}
        url   = f"{BASE_URL}?action=play&chat_id={chat_id}&msg_id={ep['msg_id']}"
        add_folder(title, url, art=art, info=info, is_folder=False)
    xbmcplugin.endOfDirectory(HANDLE)

# --------------------------------------------------------------------------- #
#  Playback
# --------------------------------------------------------------------------- #
def play(chat_id: str, msg_id: str):
    strm_url = f"{BRIDGE_URL}/get_strm/{chat_id}/{msg_id}"
    try:
        with urllib.request.urlopen(strm_url) as r:
            fp = r.read().decode().strip()
        li = xbmcgui.ListItem(path=fp); li.setProperty("is_playable", "true")
        xbmcplugin.setResolvedUrl(HANDLE, True, li)
    except Exception as e:
        xbmcgui.Dialog().ok("Fehler", str(e))

# --------------------------------------------------------------------------- #
#  Router
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    action = ARGS.get("action", ["root"])[0]

    if action == "root":
        root_menu()

    elif action == "search_ui":
        search_ui()

    elif action == "list_search_results":
        list_search_results(
            ARGS["query"][0],
            int(ARGS.get("offset", ["0"])[0]),
        )

    elif action == "pick_movie_chat":
        pick_movie_chat()

    elif action == "pick_series_chat":
        pick_series_chat()

    elif action == "choose_movie_source":
        choose_movie_source()

    elif action == "choose_series_source":
        choose_series_source()

    elif action == "list_movies_db":
        list_movies_db(int(ARGS.get("offset", ["0"])[0]))

    elif action == "list_series_db":
        list_series_db(int(ARGS.get("offset", ["0"])[0]))

    elif action == "list_movies":
        list_latest_movies(ARGS["chat_id"][0], int(ARGS.get("offset", ["0"])[0]))

    elif action == "list_series":
        list_latest_series(ARGS["chat_id"][0], int(ARGS.get("offset", ["0"])[0]))

    elif action == "series_detail":
        list_seasons(ARGS["series_id"][0], ARGS["chat_id"][0])

    elif action == "list_episodes":
        list_episodes(
            ARGS["series_id"][0],
            int(ARGS["season"][0]),
            ARGS["chat_id"][0],
        )

    elif action == "play":
        play(ARGS["chat_id"][0], ARGS["msg_id"][0])

    elif action == "settings_menu":
        settings_menu()

    else:
        root_menu()
