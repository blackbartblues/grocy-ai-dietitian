"""
Zarządzanie użytkownikami aplikacji Dietetyk AI.
Dane w /data/users.json. Pamięć per użytkownik: /data/users/{user_id}/memory.json
"""
import asyncio
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

USERS_PATH = "/data/users.json"
USERS_DIR = Path("/data/users")
_lock = asyncio.Lock()

JUSTYNA_PROFILE = """Kobieta, 32 lata, 70 kg, wzrost 164 cm, cel: redukcja masy ciała.
Trenuje 4 razy w tygodniu (wieczorami). Wstaje o 5:55, pracuje w biurze 7:00-15:00 (lub 7:30-15:30).
Śniadanie o 8:00, obiad o 12:30. Faza lutealna: duże zmęczenie i zmiany nastroju przed miesiączką.
LUBI: sery twarde (parmezan, grana padano), maliny, truskawki, kasza gryczana prażona (obiady),
kasza gryczana biała (śniadania), dobrze przyprawione jedzenie (zioła, ale nie ostre), pieczywo Chaber graham.
NIE LUBI: orzechy włoskie, surowe warzywa (preferuje gotowane/pieczone), szparagi, fasolka szparagowa,
owoce morza, grzyby, dynia, cukinia.
REFLUKS: unika pomidorów, papryki, kwaśnych sosów. Śniadania na ciepło. Gotuje raz na dwa dni (meal prep).
Suplementacja: pestki dyni codziennie rano (25g). Priorytet mikroskładników: tyrozyna, magnez,
witamina B6, żelazo, cynk, omega-3."""

DEFAULT_USERS: dict = {
    "users": [
        {
            "id": "justyna",
            "name": "Justyna",
            "avatar": "👩",
            "system_prompt": JUSTYNA_PROFILE,
            "created_at": "2026-03-16T00:00:00",
        }
    ]
}


def _ensure_dirs() -> None:
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    USERS_DIR.mkdir(parents=True, exist_ok=True)


def _load_sync() -> dict:
    _ensure_dirs()
    if not os.path.exists(USERS_PATH):
        _save_sync(DEFAULT_USERS)
        return dict(DEFAULT_USERS)
    try:
        with open(USERS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        try:
            os.rename(USERS_PATH, USERS_PATH + ".bak")
        except OSError:
            pass
        _save_sync(DEFAULT_USERS)
        return dict(DEFAULT_USERS)


def _save_sync(data: dict) -> None:
    _ensure_dirs()
    dir_path = os.path.dirname(USERS_PATH)
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
        os.replace(tmp_path, USERS_PATH)
    except OSError as e:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise RuntimeError(f"Users save error: {e}") from e


def _slugify(name: str) -> str:
    """Konwertuje imię na bezpieczny identyfikator (slug)."""
    slug = name.lower().strip()
    slug = re.sub(r"[ąàáâãäå]", "a", slug)
    slug = re.sub(r"[ćçč]", "c", slug)
    slug = re.sub(r"[ęèéêë]", "e", slug)
    slug = re.sub(r"[łl]", "l", slug)
    slug = re.sub(r"[ńñ]", "n", slug)
    slug = re.sub(r"[óòôõö]", "o", slug)
    slug = re.sub(r"[śšş]", "s", slug)
    slug = re.sub(r"[źżž]", "z", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "user"


def _user_memory_path(user_id: str) -> Path:
    user_dir = USERS_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "memory.json"


async def get_users() -> list:
    """Zwraca listę wszystkich użytkowników."""
    async with _lock:
        data = await asyncio.get_event_loop().run_in_executor(None, _load_sync)
        return data.get("users", [])


async def get_user(user_id: str) -> dict | None:
    """Zwraca konkretnego użytkownika lub None."""
    users = await get_users()
    return next((u for u in users if u["id"] == user_id), None)


async def create_user(name: str, avatar: str, system_prompt: str) -> dict:
    """Tworzy nowego użytkownika."""
    async with _lock:
        data = await asyncio.get_event_loop().run_in_executor(None, _load_sync)
        users = data.get("users", [])

        base_slug = _slugify(name)
        slug = base_slug
        existing_ids = {u["id"] for u in users}
        counter = 1
        while slug in existing_ids:
            slug = f"{base_slug}-{counter}"
            counter += 1

        new_user = {
            "id": slug,
            "name": name.strip(),
            "avatar": avatar or "👤",
            "system_prompt": system_prompt.strip(),
            "created_at": datetime.now().isoformat(),
        }
        updated_data = {**data, "users": [*users, new_user]}
        await asyncio.get_event_loop().run_in_executor(None, _save_sync, updated_data)
        return new_user


async def update_user(user_id: str, upd: dict) -> dict:
    """Aktualizuje dane użytkownika. Zwraca zaktualizowanego użytkownika."""
    async with _lock:
        data = await asyncio.get_event_loop().run_in_executor(None, _load_sync)
        users = data.get("users", [])
        idx = next((i for i, u in enumerate(users) if u["id"] == user_id), None)
        if idx is None:
            raise ValueError(f"User '{user_id}' does not exist.")
        allowed = {"name", "avatar", "system_prompt"}
        patch = {k: v for k, v in upd.items() if k in allowed}
        updated_user = {**users[idx], **patch}
        new_users = [*users[:idx], updated_user, *users[idx + 1:]]
        updated_data = {**data, "users": new_users}
        await asyncio.get_event_loop().run_in_executor(None, _save_sync, updated_data)
        return updated_user


async def delete_user(user_id: str) -> bool:
    """Usuwa użytkownika. Zwraca False jeśli nie istnieje lub jest ostatnim."""
    async with _lock:
        data = await asyncio.get_event_loop().run_in_executor(None, _load_sync)
        users = data.get("users", [])
        if len(users) <= 1:
            raise ValueError("Cannot delete the last user.")
        new_users = [u for u in users if u["id"] != user_id]
        if len(new_users) == len(users):
            return False
        updated_data = {**data, "users": new_users}
        await asyncio.get_event_loop().run_in_executor(None, _save_sync, updated_data)
        return True


def init_users() -> None:
    """Inicjalizuje plik użytkowników przy starcie jeśli nie istnieje."""
    if not os.path.exists(USERS_PATH):
        _ensure_dirs()
        _save_sync(DEFAULT_USERS)


# Memory per użytkownik — te same operacje co memory.py ale per user_id

DEFAULT_USER_MEMORY: dict = {
    "preferences": {
        "likes": [],
        "dislikes": [],
        "intolerances": [],
        "health_notes": [],
    },
    "meal_history": [],
    "learned_facts": [],
}

MAX_MEAL_HISTORY_WEEKS = 4
_memory_lock = asyncio.Lock()


def _load_user_memory_sync(user_id: str) -> dict:
    path = _user_memory_path(user_id)
    if not path.exists():
        return dict(DEFAULT_USER_MEMORY)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for key, default in DEFAULT_USER_MEMORY.items():
            if key not in data:
                data[key] = default
        return data
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_USER_MEMORY)


def _save_user_memory_sync(user_id: str, data: dict) -> None:
    path = _user_memory_path(user_id)
    parent = path.parent
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=str(parent),
            delete=False,
            suffix=".tmp",
            encoding="utf-8",
        ) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path = f.name
        os.replace(tmp_path, str(path))
    except OSError as e:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise RuntimeError(f"User memory save error {user_id}: {e}") from e


async def get_user_memory(user_id: str) -> dict:
    """Zwraca pamięć konkretnego użytkownika."""
    async with _memory_lock:
        return await asyncio.get_event_loop().run_in_executor(
            None, _load_user_memory_sync, user_id
        )


async def update_user_memory(user_id: str, key: str, value: str) -> dict:
    """Zapisuje nową informację do pamięci użytkownika."""
    async with _memory_lock:
        memory = await asyncio.get_event_loop().run_in_executor(
            None, _load_user_memory_sync, user_id
        )

        if key in ("likes", "dislikes", "intolerances", "health_notes"):
            lst = memory["preferences"].get(key, [])
            if value not in lst:
                lst = [*lst, value]
            memory = {**memory, "preferences": {**memory["preferences"], key: lst}}

        elif key == "learned_facts":
            facts = memory.get("learned_facts", [])
            if value not in facts:
                facts = [*facts, value]
            memory = {**memory, "learned_facts": facts}

        elif key == "meal_history":
            try:
                entry = json.loads(value) if isinstance(value, str) else value
            except json.JSONDecodeError:
                entry = {"week": "unknown", "meals": [value]}
            history = memory.get("meal_history", [])
            history = [*history, entry]
            if len(history) > MAX_MEAL_HISTORY_WEEKS:
                history = history[-MAX_MEAL_HISTORY_WEEKS:]
            memory = {**memory, "meal_history": history}

        else:
            fact = f"{key}: {value}"
            facts = memory.get("learned_facts", [])
            if fact not in facts:
                facts = [*facts, fact]
            memory = {**memory, "learned_facts": facts}

        await asyncio.get_event_loop().run_in_executor(
            None, _save_user_memory_sync, user_id, memory
        )
        return memory


def format_user_memory_for_prompt(memory: dict, user_name: str = "") -> str:
    """Formatuje pamięć użytkownika jako tekst do system promptu."""
    lines = []
    prefs = memory.get("preferences", {})

    if prefs.get("likes"):
        lines.append(f"LUBI: {', '.join(prefs['likes'])}")
    if prefs.get("dislikes"):
        lines.append(f"NIE LUBI: {', '.join(prefs['dislikes'])}")
    if prefs.get("intolerances"):
        lines.append(f"NIETOLERANCJE/REFLUKS: {', '.join(prefs['intolerances'])}")
    if prefs.get("health_notes"):
        lines.append(f"NOTATKI ZDROWOTNE: {', '.join(prefs['health_notes'])}")
    if memory.get("learned_facts"):
        lines.append(f"DODATKOWE FAKTY: {'; '.join(memory['learned_facts'])}")
    if memory.get("meal_history"):
        recent = memory["meal_history"][-2:]
        history_str = "; ".join(
            f"{h.get('week', '?')}: {', '.join(h.get('meals', []))}"
            for h in recent
        )
        lines.append(f"OSTATNIE JADŁOSPISY: {history_str}")

    if not lines:
        return ""

    label = f"o użytkowniku {user_name}" if user_name else "o użytkowniku"
    return f"## Pamięć {label} (z poprzednich rozmów)\n" + "\n".join(lines) + "\n\n"
