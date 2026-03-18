"""
Zarządzanie historią sesji czatu.
Każda sesja zapisywana jako /data/sessions/{session_id}.json
"""
import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

SESSIONS_DIR = Path("/data/sessions")
_lock = asyncio.Lock()


def _ensure_dir():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _session_path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def _atomic_write(path: Path, data: dict):
    _ensure_dir()
    with tempfile.NamedTemporaryFile(
        "w", dir=str(SESSIONS_DIR), delete=False, suffix=".tmp", encoding="utf-8"
    ) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, str(path))


async def create_or_update_session(session_id: str, title: str = "", user_id: str = None):
    """Tworzy nową sesję lub aktualizuje timestamp istniejącej."""
    async with _lock:
        path = _session_path(session_id)
        now = datetime.now().isoformat()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            data["updated_at"] = now
            if title and not data.get("title"):
                data["title"] = title[:60]
            if user_id is not None:
                data["user_id"] = user_id
        else:
            data = {
                "id": session_id,
                "title": title[:60] if title else "New conversation",
                "created_at": now,
                "updated_at": now,
                "messages": [],
                "user_id": user_id,
            }
        _atomic_write(path, data)


async def append_message(session_id: str, role: str, content: str):
    """Dodaje wiadomość do historii sesji."""
    async with _lock:
        path = _session_path(session_id)
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        data["messages"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        data["updated_at"] = datetime.now().isoformat()
        _atomic_write(path, data)


async def get_session(session_id: str) -> dict | None:
    """Zwraca dane sesji lub None jeśli nie istnieje."""
    path = _session_path(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


async def list_sessions(user_id: str = None) -> list:
    """Zwraca listę sesji posortowaną od najnowszej, opcjonalnie filtrowaną po user_id."""
    _ensure_dir()
    sessions = []
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Filtruj po user_id jeśli podano
            if user_id is not None:
                session_user = data.get("user_id")
                if session_user != user_id:
                    continue
            sessions.append({
                "id": data["id"],
                "title": data.get("title", "Conversation"),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "message_count": len(data.get("messages", [])),
                "user_id": data.get("user_id", ""),
            })
        except Exception:
            continue
    sessions.sort(key=lambda x: x["updated_at"], reverse=True)
    return sessions


async def delete_session(session_id: str) -> bool:
    """Usuwa sesję. Zwraca True jeśli usunięto."""
    async with _lock:
        path = _session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False
