"""
Zarządzanie trwałą pamięcią między sesjami.
Plik /data/memory.json z atomic write i asyncio.Lock.
"""
import asyncio
import json
import os
import tempfile
from typing import Any

MEMORY_PATH = "/data/memory.json"

_lock = asyncio.Lock()

DEFAULT_MEMORY: dict = {
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


def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)


def _load_sync() -> dict:
    """Wczytuje pamięć synchronicznie (przy starcie serwera)."""
    _ensure_data_dir()
    if not os.path.exists(MEMORY_PATH):
        return dict(DEFAULT_MEMORY)
    try:
        with open(MEMORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # Uzupełnij brakujące klucze (np. przy aktualizacji struktury)
        for key, default in DEFAULT_MEMORY.items():
            if key not in data:
                data[key] = default
        return data
    except (json.JSONDecodeError, OSError):
        # Uszkodzony plik — zacznij od zera, ale zrób backup
        try:
            os.rename(MEMORY_PATH, MEMORY_PATH + ".bak")
        except OSError:
            pass
        return dict(DEFAULT_MEMORY)


def _save_sync(data: dict) -> None:
    """Atomowy zapis do pliku (rename trick — nie korumpuje przy crashu)."""
    _ensure_data_dir()
    dir_path = os.path.dirname(MEMORY_PATH)
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
        os.replace(tmp_path, MEMORY_PATH)
    except OSError as e:
        # Spróbuj wyczyścić plik tymczasowy
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise RuntimeError(f"Błąd zapisu pamięci: {e}") from e


async def load_memory() -> dict:
    """Wczytuje pamięć asynchronicznie (thread-safe)."""
    async with _lock:
        return await asyncio.get_event_loop().run_in_executor(None, _load_sync)


async def save_memory(data: dict) -> None:
    """Atomowy zapis asynchronicznie (thread-safe, race-condition safe)."""
    async with _lock:
        await asyncio.get_event_loop().run_in_executor(None, _save_sync, data)


async def get_memory() -> dict:
    """Zwraca całą zawartość memory.json."""
    return await load_memory()


async def update_memory(key: str, value: str) -> dict:
    """
    Zapisuje nowo poznaną informację o użytkowniczce.
    key: 'dislikes', 'intolerances', 'health_notes', 'likes', 'learned_facts', 'meal_history'
    value: tekst do dodania (lub JSON dla meal_history)
    """
    memory = await load_memory()

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
        # value powinno być JSON-em {"week": "YYYY-WXX", "meals": [...]}
        try:
            entry = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            entry = {"week": "unknown", "meals": [value]}

        history = memory.get("meal_history", [])
        history = [*history, entry]
        # Limit do MAX_MEAL_HISTORY_WEEKS tygodni
        if len(history) > MAX_MEAL_HISTORY_WEEKS:
            history = history[-MAX_MEAL_HISTORY_WEEKS:]
        memory = {**memory, "meal_history": history}

    else:
        # Nieznany klucz — zapisz jako learned_fact
        fact = f"{key}: {value}"
        facts = memory.get("learned_facts", [])
        if fact not in facts:
            facts = [*facts, fact]
        memory = {**memory, "learned_facts": facts}

    await save_memory(memory)
    return memory


def format_memory_for_prompt(memory: dict) -> str:
    """Formatuje pamięć jako tekst do system promptu."""
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

    return "## Pamięć o Justynie (z poprzednich rozmów)\n" + "\n".join(lines) + "\n\n"
