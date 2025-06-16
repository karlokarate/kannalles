# Telegram Kodi Bridge

This folder contains a small FastAPI bridge (`bridge.py`) and Kodi addon scripts for streaming videos from Telegram chats.

## Components
- **bridge.py** – exposes HTTP endpoints using Telethon to communicate with Telegram.
- **default.py** – Kodi front‑end script that lists movies/series and calls the bridge.
- **telegram_fetcher.py** – handles syncing chats and storing metadata in SQLite.
- **db_async_split.py** – lightweight read helpers for the SQLite databases.
- **settings.xml** – Kodi settings including API credentials and folder IDs.

## Logging
The bridge writes verbose debug output to `debug.log` in the same directory. Kodi's log records each request made by `telegram_fetcher`.

