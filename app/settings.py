"""
Zarządzanie ustawieniami aplikacji.
Dane w /data/settings.json z atomic write i asyncio.Lock.
"""
import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SETTINGS_PATH = "/data/settings.json"
_lock = asyncio.Lock()

DEFAULT_SETTINGS: dict = {
    "ai_engine": "gemini",
    "gemini_model": "gemini-2.5-flash",
    "gemini_api_key": "",          # NEW: overrides GEMINI_API_KEY env var
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:14b",
    "system_prompt": "",
    "grocy_url": "",               # NEW: overrides GROCY_BASE_URL env var
    "grocy_api_key": "",           # NEW: overrides GROCY_API_KEY env var
    "language": "en",              # NEW: 'en' | 'pl'
    "updated_at": "2026-03-16T00:00:00",
}


def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)


def _load_sync() -> dict:
    _ensure_data_dir()
    if not os.path.exists(SETTINGS_PATH):
        data = dict(DEFAULT_SETTINGS)
        _save_sync(data)
        return data
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for key, default in DEFAULT_SETTINGS.items():
            if key not in data:
                data[key] = default
        return data
    except (json.JSONDecodeError, OSError):
        try:
            os.rename(SETTINGS_PATH, SETTINGS_PATH + ".bak")
        except OSError:
            pass
        data = dict(DEFAULT_SETTINGS)
        _save_sync(data)
        return data


def _save_sync(data: dict) -> None:
    _ensure_data_dir()
    dir_path = os.path.dirname(SETTINGS_PATH)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=dir_path,
            delete=False,
            suffix=".tmp",
            encoding="utf-8",
        ) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path = f.name
        os.replace(tmp_path, SETTINGS_PATH)
    except OSError as e:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise RuntimeError(f"Błąd zapisu ustawień: {e}") from e


async def get_settings() -> dict:
    """Zwraca aktualne ustawienia."""
    async with _lock:
        return await asyncio.get_event_loop().run_in_executor(None, _load_sync)


async def update_settings(data: dict) -> dict:
    """Aktualizuje ustawienia (atomic write). Zwraca zaktualizowane ustawienia."""
    async with _lock:
        current = await asyncio.get_event_loop().run_in_executor(None, _load_sync)
        updated = {**current, **data, "updated_at": datetime.now().isoformat()}
        await asyncio.get_event_loop().run_in_executor(None, _save_sync, updated)
        return updated


def init_settings() -> None:
    """Inicjalizuje plik ustawień przy starcie jeśli nie istnieje."""
    if not os.path.exists(SETTINGS_PATH):
        _ensure_data_dir()
        _save_sync(dict(DEFAULT_SETTINGS))
