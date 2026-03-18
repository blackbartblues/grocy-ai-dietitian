"""
Klient AI z obsługą Gemini i Ollama z function calling i SSE streaming.
Obsługuje wielokrotne wywołania narzędzi w jednej odpowiedzi.
"""
import asyncio
import json
import os
import time
from typing import AsyncGenerator, Any, Optional

import httpx
import google.genai as genai
from google.genai import types

import grocy
import sessions as sess
import settings as cfg_module
import users as usr_module

async def _get_gemini_client():
    """Returns Gemini client using API key from settings (preferred) or env var."""
    settings = await cfg_module.get_settings()
    api_key = settings.get("gemini_api_key") or os.getenv("GEMINI_API_KEY", "")
    return genai.Client(api_key=api_key)

# Timeout sesji: 2 godziny bezczynności
SESSION_TIMEOUT = 7200
_sessions: dict[str, dict] = {}

BASE_SYSTEM_PROMPT = """Jesteś wysoko wyspecjalizowanym Dietetykiem Klinicznym z wieloletnim doświadczeniem w pracy z pacjentami z zaburzeniami endokrynologicznymi. Twoim głównym celem jest projektowanie zbalansowanych strategii żywieniowych wspierających zdrowie tarczycy (niedoczynność, Hashimoto, nadczynność) oraz ogólną regulację gospodarki hormonalnej (insulinooporność, kortyzol, PCOS).

## Zasady działania
**Fundamenty naukowe**: Opierasz się na dowodach naukowych (EBM). Promujesz dietę przeciwzapalną, bogatą w selen, cynk, jod, żelazo i kwasy omega-3, zwracając uwagę na odpowiednią podaż białka i gęstość odżywczą.
**Indywidualizacja**: Zawsze bierzesz pod uwagę interakcje leków z żywnością oraz profil użytkownika.
**Transparentność**: Jeśli zauważysz błąd — przyznaj się i popraw.
**Ostrzeżenia**: Każda porada opatrzona przypomnieniem, że dieta wspiera leczenie, nie zastępuje lekarza.

## Pamięć i ciągłość rozmów
Na początku każdej sesji dostajesz załadowaną pamięć o użytkowniku z poprzednich rozmów. Używaj tej wiedzy bez pytania od nowa. Gdy odkryjesz coś nowego — zapisz przez update_memory().
Nigdy nie pytaj o rzeczy które już wiesz z pamięci.

## Integracja z Grocy
Masz dostęp do narzędzi zarządzania kuchnią. Gdy tworzysz jadłospis lub przepis:
1. Sprawdź get_recipes() czy podobny przepis już istnieje
2. Sprawdź get_stock() co jest w domu — zaproponuj przepisy które można zrobić teraz
3. Sprawdź historię posiłków z pamięci — unikaj powtórzeń z ostatnich 2 tygodni
4. Zaproponuj jadłospis uwzględniając dostępne składniki
5. Zapytaj użytkownika czy zapisać przepisy, dodać brakujące składniki do listy zakupów i zsynchronizować z planem posiłków Grocy
6. Wykonaj odpowiednie akcje (backend konsoliduje ilości automatycznie przed dodaniem do listy)
7. Zapisz listę dań tego tygodnia przez update_memory()

Gdy użytkownik pyta "co ugotować?" bez konkretnego dania:
- Najpierw sprawdź stock i zaproponuj przepisy z Grocy które można zrobić teraz
- Podaj ile składników brakuje do każdej opcji
- Zaproponuj opcję z minimum zakupów

## Jednostki miary (v6)
Gdy zapisujesz składniki przepisu, ZAWSZE podaj ilość i jednostkę dla każdego składnika.
Dostępne jednostki: g (gramy), ml (mililitry), lyzka (łyżka stołowa ~15ml), lyzeczka (łyżeczka ~5ml), szczypta, szt. (sztuki).
Przykłady: name="platki owsiane" amount=60 unit="g", name="mleko" amount=200 unit="ml", name="miod" amount=1 unit="lyzka", name="cynamon" amount=1 unit="szczypta", name="jaja" amount=3 unit="szt.".
Nigdy nie używaj ogólnego "szt." jeśli możesz użyć bardziej precyzyjnej jednostki wagowej lub objętościowej.

## Logika spiżarni (v6)
Sprawdzając czy użytkownik ma dany produkt w domu:
- Jeśli produkt jest w spiżarni (nawet bez podanej ilości) → ZAKŁADAJ że jest wystarczająco. NIE dodawaj do listy zakupów.
- Nie pytaj o ilość jeśli produkt jest na liście spiżarni.
- Tylko jeśli produkt w ogóle nie figuruje w spiżarni → sugeruj dodanie do listy zakupów.
Przykład: "miód" jest w spiżarni → nie dodawaj miodu do zakupów, nawet jeśli przepis mówi "2 łyżki miodu".

## Styl komunikacji
- Empatyczny, profesjonalny, konkretny
- Strukturyzowane odpowiedzi (listy, tabele w Markdown)
- Unikaj żargonu medycznego (lub wyjaśnij)
- Język: polski
"""

# Definicje narzędzi — format Gemini
GEMINI_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_recipes",
                description="Zwraca listę wszystkich przepisów zapisanych w Grocy.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
            types.FunctionDeclaration(
                name="get_recipe_details",
                description="Zwraca składniki konkretnego przepisu z Grocy.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "recipe_id": types.Schema(type=types.Type.INTEGER, description="ID przepisu")
                    },
                    required=["recipe_id"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_stock",
                description="Zwraca aktualny stan spiżarni (co mamy w domu).",
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
            types.FunctionDeclaration(
                name="get_products",
                description="Zwraca listę wszystkich produktów w Grocy.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
            types.FunctionDeclaration(
                name="save_recipe",
                description="WYWOŁAJ GDY użytkownik prosi o zapisanie przepisu. Zapisuje przepis z nazwą, opisem kroków, składnikami i wartościami odżywczymi (kalorie, białko, tłuszcz, węglowodany, błonnik). Zawsze podawaj wartości odżywcze gdy je znasz.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "name": types.Schema(type=types.Type.STRING, description="Nazwa przepisu"),
                        "description": types.Schema(type=types.Type.STRING, description="Kroki przygotowania — pełna instrukcja"),
                        "servings": types.Schema(type=types.Type.INTEGER, description="Liczba porcji"),
                        "ingredients": types.Schema(
                            type=types.Type.ARRAY,
                            description="Lista składników przepisu",
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                properties={
                                    "name": types.Schema(type=types.Type.STRING, description="Nazwa składnika"),
                                    "amount": types.Schema(type=types.Type.NUMBER, description="Ilość"),
                                    "unit": types.Schema(type=types.Type.STRING, description="Jednostka np. g, ml, szt"),
                                },
                                required=["name", "amount"],
                            ),
                        ),
                        "calories": types.Schema(type=types.Type.NUMBER, description="Kalorie na porcję (kcal)"),
                        "protein_g": types.Schema(type=types.Type.NUMBER, description="Białko na porcję (g)"),
                        "fat_g": types.Schema(type=types.Type.NUMBER, description="Tłuszcz na porcję (g)"),
                        "carbs_g": types.Schema(type=types.Type.NUMBER, description="Węglowodany na porcję (g)"),
                        "fiber_g": types.Schema(type=types.Type.NUMBER, description="Błonnik na porcję (g)"),
                    },
                    required=["name", "description", "servings"],
                ),
            ),
            types.FunctionDeclaration(
                name="add_ingredient_to_recipe",
                description="Dodaje składnik do przepisu. Automatycznie tworzy produkt jeśli nie istnieje.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "recipe_id": types.Schema(type=types.Type.INTEGER, description="ID przepisu"),
                        "product_name": types.Schema(type=types.Type.STRING, description="Nazwa składnika"),
                        "amount": types.Schema(type=types.Type.NUMBER, description="Ilość"),
                        "unit": types.Schema(type=types.Type.STRING, description="Jednostka (g, ml, szt)"),
                    },
                    required=["recipe_id", "product_name", "amount"],
                ),
            ),
            types.FunctionDeclaration(
                name="add_to_shopping_list",
                description="Dodaje produkt do listy zakupów w Grocy.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "product_name": types.Schema(type=types.Type.STRING, description="Nazwa produktu"),
                        "amount": types.Schema(type=types.Type.NUMBER, description="Ilość"),
                        "note": types.Schema(type=types.Type.STRING, description="Notatka opcjonalna"),
                    },
                    required=["product_name", "amount"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_shopping_list",
                description="Zwraca aktualną listę zakupów z Grocy.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
            types.FunctionDeclaration(
                name="get_meal_plan",
                description="Zwraca aktualny plan posiłków z Grocy.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
            types.FunctionDeclaration(
                name="save_meal_plan_entry",
                description="Zapisuje wpis do planu posiłków Grocy.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "recipe_id": types.Schema(type=types.Type.INTEGER, description="ID przepisu"),
                        "day": types.Schema(type=types.Type.STRING, description="Data YYYY-MM-DD"),
                        "meal_type": types.Schema(type=types.Type.STRING, description="breakfast/lunch/dinner/snack"),
                    },
                    required=["recipe_id", "day", "meal_type"],
                ),
            ),
            types.FunctionDeclaration(
                name="update_memory",
                description="Zapisuje nową informację o użytkowniku do trwałej pamięci.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "key": types.Schema(
                            type=types.Type.STRING,
                            description="Kategoria: 'likes', 'dislikes', 'intolerances', 'health_notes', 'learned_facts', 'meal_history'",
                        ),
                        "value": types.Schema(type=types.Type.STRING, description="Treść do zapamiętania"),
                    },
                    required=["key", "value"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_memory",
                description="Zwraca całą trwałą pamięć o użytkowniku.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
        ]
    )
]

# Format narzędzi dla Ollama (OpenAI-compatible)
OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_recipes",
            "description": "Zwraca listę wszystkich przepisów zapisanych w Grocy.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recipe_details",
            "description": "Zwraca składniki konkretnego przepisu z Grocy.",
            "parameters": {
                "type": "object",
                "properties": {"recipe_id": {"type": "integer", "description": "ID przepisu"}},
                "required": ["recipe_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock",
            "description": "Zwraca aktualny stan spiżarni (co mamy w domu).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_products",
            "description": "Zwraca listę wszystkich produktów w Grocy.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_recipe",
            "description": "WYWOŁAJ GDY użytkownik prosi o zapisanie przepisu. Zapisuje przepis z nazwą, opisem kroków, składnikami i wartościami odżywczymi (kalorie, białko, tłuszcz, węglowodany, błonnik). Zawsze podawaj wartości odżywcze gdy je znasz.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nazwa przepisu"},
                    "description": {"type": "string", "description": "Kroki przygotowania — pełna instrukcja"},
                    "servings": {"type": "integer", "description": "Liczba porcji"},
                    "ingredients": {
                        "type": "array",
                        "description": "Lista składników przepisu",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Nazwa składnika"},
                                "amount": {"type": "number", "description": "Ilość"},
                                "unit": {"type": "string", "description": "Jednostka np. g, ml, szt"},
                            },
                            "required": ["name", "amount"],
                        },
                    },
                    "calories": {"type": "number", "description": "Kalorie na porcję (kcal)"},
                    "protein_g": {"type": "number", "description": "Białko na porcję (g)"},
                    "fat_g": {"type": "number", "description": "Tłuszcz na porcję (g)"},
                    "carbs_g": {"type": "number", "description": "Węglowodany na porcję (g)"},
                    "fiber_g": {"type": "number", "description": "Błonnik na porcję (g)"},
                    "meal_type": {"type": "string", "description": "Typ posiłku: sniadanie, obiad, kolacja, przekaska", "enum": ["sniadanie", "obiad", "kolacja", "przekaska"]},
                },
                "required": ["name", "description", "servings"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_ingredient_to_recipe",
            "description": "Dodaje składnik do przepisu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe_id": {"type": "integer", "description": "ID przepisu"},
                    "product_name": {"type": "string", "description": "Nazwa składnika"},
                    "amount": {"type": "number", "description": "Ilość"},
                    "unit": {"type": "string", "description": "Jednostka (g, ml, szt)"},
                },
                "required": ["recipe_id", "product_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_shopping_list",
            "description": "Dodaje produkt do listy zakupów w Grocy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "Nazwa produktu"},
                    "amount": {"type": "number", "description": "Ilość"},
                    "note": {"type": "string", "description": "Notatka opcjonalna"},
                },
                "required": ["product_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_shopping_list",
            "description": "Zwraca aktualną listę zakupów z Grocy.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_meal_plan",
            "description": "Zwraca aktualny plan posiłków z Grocy.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_meal_plan_entry",
            "description": "Zapisuje wpis do planu posiłków Grocy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe_id": {"type": "integer", "description": "ID przepisu"},
                    "day": {"type": "string", "description": "Data YYYY-MM-DD"},
                    "meal_type": {"type": "string", "description": "breakfast/lunch/dinner/snack"},
                },
                "required": ["recipe_id", "day", "meal_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "Zapisuje nową informację o użytkowniku do trwałej pamięci.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Kategoria: likes, dislikes, intolerances, health_notes, learned_facts, meal_history"},
                    "value": {"type": "string", "description": "Treść do zapamiętania"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_memory",
            "description": "Zwraca całą trwałą pamięć o użytkowniku.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


async def _execute_tool(name: str, args: dict, user_id: Optional[str] = None) -> Any:
    """Wykonuje wywołanie narzędzia i zwraca wynik."""
    try:
        if name == "get_recipes":
            return await grocy.get_recipes()
        elif name == "get_recipe_details":
            return await grocy.get_recipe_details(args["recipe_id"])
        elif name == "get_stock":
            return await grocy.get_stock()
        elif name == "get_products":
            return await grocy.get_products()
        elif name == "save_recipe":
            return await grocy.save_recipe(
                args["name"], args["description"], args.get("servings", 2),
                args.get("ingredients", []),
                nutrition={
                    "calories": args.get("calories"),
                    "protein_g": args.get("protein_g"),
                    "fat_g": args.get("fat_g"),
                    "carbs_g": args.get("carbs_g"),
                    "fiber_g": args.get("fiber_g"),
                },
                meal_type=args.get("meal_type"),
                author_name=args.get("author_name"),
                author_avatar=args.get("author_avatar"),
            )
        elif name == "add_ingredient_to_recipe":
            return await grocy.add_ingredient_to_recipe(
                args["recipe_id"], args["product_name"], args["amount"], args.get("unit", "")
            )
        elif name == "add_to_shopping_list":
            return await grocy.add_to_shopping_list(
                args["product_name"], args["amount"], args.get("note", "")
            )
        elif name == "get_shopping_list":
            return await grocy.get_shopping_list()
        elif name == "get_meal_plan":
            return await grocy.get_meal_plan()
        elif name == "save_meal_plan_entry":
            return await grocy.save_meal_plan_entry(args["recipe_id"], args["day"], args["meal_type"])
        elif name == "update_memory":
            if user_id:
                return await usr_module.update_user_memory(user_id, args["key"], args["value"])
            else:
                import memory as mem
                return await mem.update_memory(args["key"], args["value"])
        elif name == "get_memory":
            if user_id:
                return await usr_module.get_user_memory(user_id)
            else:
                import memory as mem
                return await mem.get_memory()
        else:
            return {"error": f"Nieznane narzędzie: {name}"}
    except Exception as e:
        return {"error": f"Błąd wykonania {name}: {str(e)}"}


def _get_or_create_session(session_id: str, memory_context: str) -> dict:
    """Zwraca istniejącą lub tworzy nową sesję."""
    now = time.time()
    expired = [sid for sid, s in _sessions.items() if now - s["last_active"] > SESSION_TIMEOUT]
    for sid in expired:
        del _sessions[sid]

    if session_id not in _sessions:
        _sessions[session_id] = {
            "history": [],
            "last_active": now,
            "memory_context": memory_context,
            "session_recipes": [],
            "user_id": None,
        }
    else:
        _sessions[session_id]["last_active"] = now
        _sessions[session_id]["memory_context"] = memory_context

    return _sessions[session_id]


def get_session_recipes(session_id: str) -> list:
    """Zwraca przepisy wygenerowane w danej sesji."""
    return _sessions.get(session_id, {}).get("session_recipes", [])


def add_session_recipe(session_id: str, recipe: dict) -> None:
    """Dodaje przepis do listy sesji."""
    if session_id in _sessions:
        _sessions[session_id]["session_recipes"] = [
            *_sessions[session_id]["session_recipes"],
            recipe,
        ]


async def _build_system_prompt(user_id: Optional[str]) -> str:
    """Buduje system prompt: bazowy (z ustawień lub hardcoded) + profil użytkownika + pamięć."""
    cfg = await cfg_module.get_settings()
    custom_base = cfg.get("system_prompt", "").strip()
    prompt = custom_base if custom_base else BASE_SYSTEM_PROMPT

    if user_id:
        user = await usr_module.get_user(user_id)
        if user and user.get("system_prompt"):
            prompt = prompt + f"\n\n## Profil użytkownika ({user['name']})\n{user['system_prompt']}\n"
        try:
            memory_data = await usr_module.get_user_memory(user_id)
            memory_ctx = usr_module.format_user_memory_for_prompt(
                memory_data, user["name"] if user else ""
            )
            if memory_ctx:
                prompt = memory_ctx + prompt
        except Exception:
            pass
    else:
        try:
            import memory as mem
            memory_data = await mem.get_memory()
            memory_ctx = mem.format_memory_for_prompt(memory_data)
            if memory_ctx:
                prompt = memory_ctx + prompt
        except Exception:
            pass

    return prompt


async def _stream_gemini(
    session_id: str,
    user_message: str,
    user_id: Optional[str],
    cfg: dict,
) -> AsyncGenerator[str, None]:
    """Strumień odpowiedzi przez Gemini."""
    model_name = cfg.get("gemini_model", "gemini-1.5-flash")
    full_system_prompt = await _build_system_prompt(user_id)

    session = _get_or_create_session(session_id, full_system_prompt)
    session["user_id"] = user_id
    history = session["history"]

    client = await _get_gemini_client()

    contents = []
    for msg in history:
        contents.append(msg)
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

    # Obsługa modelu z myśleniem: np. gemini-2.5-pro:thinking
    thinking_mode = model_name.endswith(":thinking")
    if thinking_mode:
        model_name = model_name[:-len(":thinking")]
    thinking_budget = 8000 if thinking_mode else cfg.get("thinking_budget", 0)
    config = types.GenerateContentConfig(
        system_instruction=full_system_prompt,
        tools=GEMINI_TOOLS,
        temperature=1.0 if thinking_mode else 0.7,
        thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget, include_thoughts=False) if thinking_budget else None,
    )

    max_tool_iterations = 10
    iteration = 0

    while iteration < max_tool_iterations:
        iteration += 1

        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                ),
            )
        except Exception as e:
            import traceback
            error_msg = str(e)
            print(f"[GEMINI ERROR] {traceback.format_exc()}", flush=True)
            if "quota" in error_msg.lower() or "429" in error_msg:
                yield 'data: {"error": "Przekroczono limit API Gemini. Spróbuj za chwilę."}\n\n'
            else:
                yield f'data: {{"error": "Błąd Gemini: {error_msg[:300]}"}}\n\n'
            return

        candidate = response.candidates[0] if response.candidates else None
        if not candidate:
            yield "data: {\"error\": \"Brak odpowiedzi od modelu.\"}\n\n"
            return

        has_function_calls = False
        function_call_parts = []

        for part in candidate.content.parts:
            if hasattr(part, "function_call") and part.function_call:
                has_function_calls = True
                function_call_parts.append(part)

        if has_function_calls:
            contents.append(candidate.content)
            tasks = []
            for part in function_call_parts:
                fc = part.function_call
                args = dict(fc.args) if fc.args else {}
                tasks.append(_execute_tool(fc.name, args, user_id))
                yield f"data: {{\"tool_call\": \"{fc.name}\"}}\n\n"

            results = await asyncio.gather(*tasks, return_exceptions=True)

            function_response_parts = []
            for part, result in zip(function_call_parts, results):
                fc = part.function_call
                if isinstance(result, Exception):
                    result = {"error": str(result)}

                if fc.name == "save_recipe" and isinstance(result, dict) and "id" in result:
                    recipe_id = result.get("id") or result.get("created_object_id")
                    add_session_recipe(session_id, {
                        "id": recipe_id,
                        "name": dict(fc.args).get("name", "Przepis"),
                        "description": dict(fc.args).get("description", ""),
                        "meal_type": "",
                        "calories": None,
                        "ingredients": [],
                    })
                    yield f"data: {{\"recipe_saved\": {recipe_id}}}\n\n"

                function_response_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": json.dumps(result, ensure_ascii=False, default=str)},
                        )
                    )
                )

            contents.append(types.Content(role="user", parts=function_response_parts))
            continue

        else:
            final_text_parts = []
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    final_text_parts.append(part.text)

            final_text = "".join(final_text_parts)

            if not final_text:
                # Fix A: model responded only via function calls — save session with placeholder
                print(f"[GEMINI] final_text empty after tool calls for session {session_id}, saving with placeholder", flush=True)
                placeholder = "✓ Wykonano akcję"
                try:
                    _existing = await sess.get_session(session_id)
                    _title = user_message[:60] if not _existing else ""
                    await sess.create_or_update_session(session_id, _title, user_id=user_id)
                    await sess.append_message(session_id, "user", user_message)
                    await sess.append_message(session_id, "assistant", placeholder)
                    print(f"[GEMINI] Session {session_id} saved (placeholder)", flush=True)
                except Exception as save_err:
                    print(f"[GEMINI] ERROR saving session (placeholder): {save_err}", flush=True)
                yield "data: {\"done\": true}\n\n"
                return

            try:
                stream_response = client.models.generate_content_stream(
                    model=model_name,
                    contents=contents,
                    config=config,
                )

                streamed_text = []
                for chunk in stream_response:
                    if chunk.text:
                        streamed_text.append(chunk.text)
                        escaped = chunk.text.replace('"', '\\"').replace('\n', '\\n')
                        yield f'data: {{"chunk": "{escaped}"}}\n\n'

                final_text = "".join(streamed_text)

            except Exception:
                escaped = final_text.replace('"', '\\"').replace('\n', '\\n')
                yield f'data: {{"chunk": "{escaped}"}}\n\n'

            new_history = []
            for msg in contents:
                if isinstance(msg, types.Content):
                    text_parts = [p for p in msg.parts if hasattr(p, "text") and p.text and not hasattr(p, "function_call")]
                    if text_parts:
                        new_history.append(types.Content(role=msg.role, parts=text_parts))
            new_history.append(
                types.Content(role="model", parts=[types.Part(text=final_text)])
            )
            session["history"] = new_history[-20:]

            print(f"[GEMINI] Saving session {session_id} (final_text len={len(final_text)})", flush=True)
            try:
                _existing = await sess.get_session(session_id)
                _title = user_message[:60] if not _existing else ""
                await sess.create_or_update_session(session_id, _title, user_id=user_id)
                await sess.append_message(session_id, "user", user_message)
                await sess.append_message(session_id, "assistant", final_text)
                print(f"[GEMINI] Session {session_id} saved OK", flush=True)
            except Exception as save_err:
                print(f"[GEMINI] ERROR saving session: {save_err}", flush=True)

            yield "data: {\"done\": true}\n\n"
            return

    # Fix B: save session before returning tool iteration limit error
    print(f"[GEMINI] max_tool_iterations reached for session {session_id}, saving partial", flush=True)
    try:
        _existing = await sess.get_session(session_id)
        _title = user_message[:60] if not _existing else ""
        await sess.create_or_update_session(session_id, _title, user_id=user_id)
        await sess.append_message(session_id, "user", user_message)
        await sess.append_message(session_id, "assistant", "✓ Wykonano wiele akcji (przekroczono limit iteracji)")
        print(f"[GEMINI] Session {session_id} saved (limit reached)", flush=True)
    except Exception as save_err:
        print(f"[GEMINI] ERROR saving session at limit: {save_err}", flush=True)
    yield "data: {\"error\": \"Przekroczono limit wywołań narzędzi. Spróbuj ponownie.\"}\n\n"


async def _stream_ollama(
    session_id: str,
    user_message: str,
    user_id: Optional[str],
    cfg: dict,
) -> AsyncGenerator[str, None]:
    """Strumień odpowiedzi przez Ollama (OpenAI-compatible API)."""
    ollama_url = cfg.get("ollama_url", "http://localhost:11434")
    model_name = cfg.get("ollama_model", "qwen2.5:14b")
    full_system_prompt = await _build_system_prompt(user_id)

    session = _get_or_create_session(session_id, full_system_prompt)
    session["user_id"] = user_id

    # Buduj historię jako lista OpenAI messages
    messages = [{"role": "system", "content": full_system_prompt}]
    for msg in session.get("ollama_history", []):
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})

    max_tool_iterations = 10
    iteration = 0

    while iteration < max_tool_iterations:
        iteration += 1

        payload = {
            "model": model_name,
            "messages": messages,
            "tools": OLLAMA_TOOLS,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(f"{ollama_url}/v1/chat/completions", json=payload)
                r.raise_for_status()
                response_data = r.json()
        except httpx.TimeoutException:
            yield "data: {\"error\": \"Przekroczono czas oczekiwania na odpowiedź Ollama (120s). Spróbuj ponownie.\"}\n\n"
            return
        except httpx.ConnectError:
            yield "data: {\"error\": \"Nie można połączyć się z Ollama. Sprawdź czy serwer działa.\"}\n\n"
            return
        except Exception as e:
            yield f"data: {{\"error\": \"Błąd Ollama: {str(e)[:100]}\"}}\n\n"
            return

        choice = response_data.get("choices", [{}])[0]
        msg_resp = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")
        tool_calls = msg_resp.get("tool_calls", [])

        if tool_calls:
            # Dodaj odpowiedź modelu z tool_calls do historii
            messages.append(msg_resp)

            tasks = []
            call_info = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                fn_name = fn.get("name", "")
                try:
                    fn_args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}
                tasks.append(_execute_tool(fn_name, fn_args, user_id))
                call_info.append((tc.get("id", fn_name), fn_name, fn_args))
                yield f"data: {{\"tool_call\": \"{fn_name}\"}}\n\n"

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for (tc_id, fn_name, fn_args), result in zip(call_info, results):
                if isinstance(result, Exception):
                    result = {"error": str(result)}

                if fn_name == "save_recipe" and isinstance(result, dict) and "id" in result:
                    recipe_id = result.get("id") or result.get("created_object_id")
                    add_session_recipe(session_id, {
                        "id": recipe_id,
                        "name": fn_args.get("name", "Przepis"),
                        "description": fn_args.get("description", ""),
                        "meal_type": "",
                        "calories": None,
                        "ingredients": [],
                    })
                    yield f"data: {{\"recipe_saved\": {recipe_id}}}\n\n"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

            continue

        else:
            # Finalna odpowiedź tekstowa
            final_text = msg_resp.get("content", "")

            if not final_text:
                # Fix A (Ollama): model responded only via function calls — save with placeholder
                print(f"[OLLAMA] final_text empty for session {session_id}, saving with placeholder", flush=True)
                placeholder = "✓ Wykonano akcję"
                try:
                    _existing = await sess.get_session(session_id)
                    _title = user_message[:60] if not _existing else ""
                    await sess.create_or_update_session(session_id, _title, user_id=user_id)
                    await sess.append_message(session_id, "user", user_message)
                    await sess.append_message(session_id, "assistant", placeholder)
                    print(f"[OLLAMA] Session {session_id} saved (placeholder)", flush=True)
                except Exception as save_err:
                    print(f"[OLLAMA] ERROR saving session (placeholder): {save_err}", flush=True)
                yield "data: {\"done\": true}\n\n"
                return

            # Streamuj tekst w chunkach (podział na słowa dla płynności)
            words = final_text.split(" ")
            chunk_size = 5
            for i in range(0, len(words), chunk_size):
                chunk = " ".join(words[i:i + chunk_size])
                if i + chunk_size < len(words):
                    chunk += " "
                escaped = chunk.replace('"', '\\"').replace('\n', '\\n')
                yield f'data: {{"chunk": "{escaped}"}}\n\n'
                await asyncio.sleep(0)  # Pozwól event loop przetworzyć inne zadania

            # Aktualizuj historię Ollama (tylko user/assistant, bez tool)
            session["ollama_history"] = session.get("ollama_history", [])
            session["ollama_history"] = [
                *session["ollama_history"],
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": final_text},
            ]
            # Limit historii
            if len(session["ollama_history"]) > 20:
                session["ollama_history"] = session["ollama_history"][-20:]

            print(f"[OLLAMA] Saving session {session_id} (final_text len={len(final_text)})", flush=True)
            try:
                _existing = await sess.get_session(session_id)
                _title = user_message[:60] if not _existing else ""
                await sess.create_or_update_session(session_id, _title, user_id=user_id)
                await sess.append_message(session_id, "user", user_message)
                await sess.append_message(session_id, "assistant", final_text)
                print(f"[OLLAMA] Session {session_id} saved OK", flush=True)
            except Exception as save_err:
                print(f"[OLLAMA] ERROR saving session: {save_err}", flush=True)

            yield "data: {\"done\": true}\n\n"
            return

    # Fix B (Ollama): save session before returning tool iteration limit error
    print(f"[OLLAMA] max_tool_iterations reached for session {session_id}, saving partial", flush=True)
    try:
        _existing = await sess.get_session(session_id)
        _title = user_message[:60] if not _existing else ""
        await sess.create_or_update_session(session_id, _title, user_id=user_id)
        await sess.append_message(session_id, "user", user_message)
        await sess.append_message(session_id, "assistant", "✓ Wykonano wiele akcji (przekroczono limit iteracji)")
        print(f"[OLLAMA] Session {session_id} saved (limit reached)", flush=True)
    except Exception as save_err:
        print(f"[OLLAMA] ERROR saving session at limit: {save_err}", flush=True)
    yield "data: {\"error\": \"Przekroczono limit wywołań narzędzi. Spróbuj ponownie.\"}\n\n"


async def chat_stream(
    session_id: str,
    user_message: str,
    user_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Główna funkcja chatu ze streamingiem SSE.
    Wybiera silnik na podstawie ustawień.
    """
    if not user_message.strip():
        yield "data: {\"error\": \"Wiadomość nie może być pusta.\"}\n\n"
        return

    cfg = await cfg_module.get_settings()
    engine = cfg.get("ai_engine", "gemini")

    if engine == "ollama":
        async for chunk in _stream_ollama(session_id, user_message, user_id, cfg):
            yield chunk
    else:
        async for chunk in _stream_gemini(session_id, user_message, user_id, cfg):
            yield chunk
