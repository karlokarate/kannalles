# -*- coding: utf-8 -*-
"""
Lesender DB-Layer für Filme/Serien
"""

from __future__ import annotations
import os, sqlite3
from pathlib import Path
from typing import Any, Dict, List

import xbmcaddon, xbmcvfs, xbmc

# --------------------------------------------------------------------------- #
ADDON        = xbmcaddon.Addon()
PROFILE_DIR  = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
DATA_DIR     = os.path.join(PROFILE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_MOVIES  = Path(os.path.join(DATA_DIR, "meta_movies.sqlite"))
DB_SERIES  = Path(os.path.join(DATA_DIR, "meta_series.sqlite"))

# --------------------------------------------------------------------------- #
def dict_factory(cur: sqlite3.Cursor, row: tuple[Any, ...]) -> Dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cur.description)}

def _db(type_: str) -> Path:
    return DB_MOVIES if type_ == "movie" else DB_SERIES

# --------------------------------------------------------------------------- #
def get_chats_by_type(type_: str) -> List[Dict[str, Any]]:
    with sqlite3.connect(_db(type_)) as conn:
        conn.row_factory = dict_factory
        cur = conn.execute(
            "SELECT DISTINCT chat_id, chat_title AS name FROM filme WHERE type=? ORDER BY name COLLATE NOCASE",
            (type_,),
        )
        return cur.fetchall()

# ---------------- Latest ----------------
def get_latest_movies(limit: int = 20, offset: int = 0):
    with sqlite3.connect(DB_MOVIES) as conn:
        conn.row_factory = dict_factory
        cur = conn.execute(
            "SELECT * FROM filme WHERE type='movie' ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return cur.fetchall()

def get_latest_movies_by_chat(chat_id: int, limit: int = 20, offset: int = 0):
    with sqlite3.connect(DB_MOVIES) as conn:
        conn.row_factory = dict_factory
        cur = conn.execute(
            "SELECT * FROM filme WHERE type='movie' AND chat_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (chat_id, limit, offset),
        )
        return cur.fetchall()

def get_latest_series(limit: int = 20, offset: int = 0):
    with sqlite3.connect(DB_SERIES) as conn:
        conn.row_factory = dict_factory
        cur = conn.execute(
            """
            SELECT * FROM filme
            WHERE type='series'
            GROUP BY series_id
            ORDER BY MAX(id) DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return cur.fetchall()

def get_series_for_chat(chat_id: int):
    with sqlite3.connect(DB_SERIES) as conn:
        conn.row_factory = dict_factory
        cur = conn.execute(
            "SELECT * FROM filme WHERE type='series' AND series_id=? ORDER BY id DESC LIMIT 1",
            (chat_id,),
        )
        return cur.fetchall()

def get_series_in_chat(chat_id: int, limit: int = 20, offset: int = 0):
    with sqlite3.connect(DB_SERIES) as conn:
        conn.row_factory = dict_factory
        cur = conn.execute(
            "SELECT * FROM filme WHERE type='series' AND series_id=? GROUP BY series_id ORDER BY MAX(id) DESC LIMIT ? OFFSET ?",
            (chat_id, limit, offset),
        )
        return cur.fetchall()

# ---------------- Suche ----------------
def search_movies(term: str, limit: int = 40, offset: int = 0):
    like = f"%{term}%"
    with sqlite3.connect(DB_MOVIES) as conn:
        conn.row_factory = dict_factory
        cur = conn.execute(
            """
            SELECT * FROM filme
            WHERE type='movie' AND (title LIKE ? OR plot LIKE ?)
            ORDER BY jahr DESC
            LIMIT ? OFFSET ?
            """,
            (like, like, limit, offset),
        )
        return cur.fetchall()

def search_series(term: str, limit: int = 40, offset: int = 0):
    like = f"%{term}%"
    with sqlite3.connect(DB_SERIES) as conn:
        conn.row_factory = dict_factory
        cur = conn.execute(
            """
            SELECT * FROM filme
            WHERE type='series' AND (title LIKE ? OR episodentitel LIKE ? OR plot LIKE ?)
            ORDER BY jahr DESC
            LIMIT ? OFFSET ?
            """,
            (like, like, like, limit, offset),
        )
        return cur.fetchall()

# ---------------- Serien-Details ----------------
def get_seasons(series_id: str):
    with sqlite3.connect(DB_SERIES) as conn:
        conn.row_factory = dict_factory
        return conn.execute(
            "SELECT DISTINCT season AS number FROM filme WHERE series_id=? ORDER BY number",
            (series_id,),
        ).fetchall()

def get_episodes(series_id: str, season: int):
    with sqlite3.connect(DB_SERIES) as conn:
        conn.row_factory = dict_factory
        return conn.execute(
            "SELECT * FROM filme WHERE series_id=? AND season=? ORDER BY episode",
            (series_id, season),
        ).fetchall()
