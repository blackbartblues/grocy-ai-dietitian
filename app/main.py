"""
FastAPI aplikacja Dietetyk AI v2.
Endpointy: /, /chat (SSE), /health, /api/recipes-panel, /api/recipes/{id} (DELETE), /export/meal-plan,
           /api/users, /api/settings, /api/ollama/models
"""
import asyncio
import json
import os
import signal
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import grocy
import sessions as sess
import gemini as gem
import memory as mem
import settings as cfg_module
import users as usr_module

app = FastAPI(title="Dietetyk AI", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

MAX_MESSAGE_LENGTH = 10000


# ── Pydantic models ──────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Wiadomość nie może być pusta.")
        if len(v) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Wiadomość jest za długa (max {MAX_MESSAGE_LENGTH} znaków).")
        return v.strip()


class UserCreateRequest(BaseModel):
    name: str
    avatar: Optional[str] = "👤"
    system_prompt: Optional[str] = ""

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Imię nie może być puste.")
        return v.strip()


class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    avatar: Optional[str] = None
    system_prompt: Optional[str] = None
    thinking_budget: Optional[int] = None


class SettingsUpdateRequest(BaseModel):
    ai_engine: Optional[str] = None
    gemini_model: Optional[str] = None
    gemini_api_key: Optional[str] = None   # NEW
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    system_prompt: Optional[str] = None
    grocy_url: Optional[str] = None        # NEW
    grocy_api_key: Optional[str] = None    # NEW
    language: Optional[str] = None         # NEW

    @field_validator("ai_engine")
    @classmethod
    def validate_engine(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("gemini", "ollama"):
            raise ValueError("ai_engine musi być 'gemini' lub 'ollama'.")
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("en", "pl"):
            raise ValueError("language must be 'en' or 'pl'.")
        return v


# ── Startup ──────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Inicjalizuj pliki danych przy starcie."""
    cfg_module.init_settings()
    usr_module.init_users()


# ── Strony HTML ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend nie znaleziony.")
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.get("/lite", response_class=HTMLResponse)
async def lite_app():
    """Wersja bez chatu — tylko Przepisy/Zakupy/Spiżarnia."""
    lite_path = STATIC_DIR / "lite.html"
    if not lite_path.exists():
        raise HTTPException(status_code=404, detail="Lite frontend nie znaleziony.")
    return HTMLResponse(content=lite_path.read_text(encoding="utf-8"))


# ── Chat ─────────────────────────────────────────────────────

@app.post("/chat")
async def chat(request: ChatRequest):
    """Endpoint SSE do chatu z AI (Gemini lub Ollama)."""
    session_id = request.session_id or str(uuid.uuid4())

    async def event_generator():
        try:
            async for chunk in gem.chat_stream(session_id, request.message, request.user_id):
                yield chunk
        except Exception as e:
            error_json = json.dumps({"error": f"Wewnętrzny błąd serwera: {str(e)[:200]}"})
            yield f"data: {error_json}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Health ───────────────────────────────────────────────────

@app.get("/health")
async def health():
    grocy_ok = await grocy.check_connectivity()
    settings = await cfg_module.get_settings()
    grocy_url = settings.get("grocy_url") or os.getenv("GROCY_BASE_URL", "")
    return JSONResponse({
        "status": "ok",
        "grocy": "connected" if grocy_ok else "disconnected",
        "grocy_url": grocy_url or "not configured",
    })


# ── Przepisy ─────────────────────────────────────────────────

@app.get("/api/recipes-panel")
async def recipes_panel(session_id: str = Query(default="")):
    session_recipes = gem.get_session_recipes(session_id) if session_id else []

    grocy_recipes = []
    try:
        grocy_recipes_raw = await grocy.get_recipes()
        for r in grocy_recipes_raw:
            recipe_id = r.get("id")
            recipe_data = {
                "id": recipe_id,
                "name": r.get("name", "Przepis"),
                "meal_type": r.get("type", ""),
                "calories": None,
                "description": r.get("description", ""),
                "ingredients": [],
            }
            try:
                ingredients = await grocy.get_recipe_ingredients_enriched(recipe_id)
                recipe_data["ingredients"] = ingredients or []
            except Exception:
                pass
            grocy_recipes.append(recipe_data)
    except Exception:
        grocy_recipes = []

    return JSONResponse({
        "session_recipes": session_recipes,
        "grocy_recipes": grocy_recipes,
    })


# ── Usuwanie przepisów ──────────────────────────────────────

@app.delete("/api/recipes/{recipe_id}")
async def delete_recipe_endpoint(recipe_id: int):
    """Usuwa przepis z Grocy po ID."""
    # Sprawdź czy istnieje
    try:
        recipes = await grocy.get_recipes()
        exists = any(str(r.get("id")) == str(recipe_id) for r in recipes)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Przepis {recipe_id} nie istnieje.")
    except HTTPException:
        raise
    except Exception:
        pass  # Jeśli nie możemy sprawdzić — próbujemy usunąć

    try:
        ok = await grocy.delete_recipe(recipe_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Przepis {recipe_id} nie istnieje lub już usunięty.")
        return JSONResponse({"deleted": recipe_id})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd usuwania przepisu: {str(e)}")


# ── Export ───────────────────────────────────────────────────

@app.get("/export/meal-plan", response_class=HTMLResponse)
async def export_meal_plan(session_id: str = Query(default="")):
    session_recipes = gem.get_session_recipes(session_id) if session_id else []

    shopping_items = []
    try:
        shopping_items = await grocy.get_shopping_list()
    except Exception:
        pass

    products_map: dict = {}
    try:
        products = await grocy.get_products()
        products_map = {str(p["id"]): p["name"] for p in products}
    except Exception:
        pass

    recipes_html = ""
    for r in session_recipes:
        ingredients_list = ""
        for ing in r.get("ingredients", []):
            ingredients_list += f"<li>{ing}</li>"
        recipes_html += f"""
        <div class="recipe-print">
            <h3>{r.get('name', 'Przepis')}</h3>
            {f'<p class="meal-type">{r.get("meal_type", "")}</p>' if r.get("meal_type") else ''}
            {f'<ul>{ingredients_list}</ul>' if ingredients_list else ''}
            {f'<p class="description">{r.get("description", "")[:500]}</p>' if r.get("description") else ''}
        </div>
        """

    shopping_html = ""
    for item in shopping_items:
        pid = str(item.get("product_id", ""))
        pname = products_map.get(pid, f"Produkt #{pid}")
        amount = item.get("amount", "")
        shopping_html += f"<li>{pname} — {amount}</li>"

    html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <title>Jadłospis — Dietetyk AI</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
        h1 {{ color: #333; border-bottom: 2px solid #333; padding-bottom: 10px; }}
        h2 {{ color: #333; margin-top: 30px; }}
        h3 {{ color: #333; }}
        .recipe-print {{ margin-bottom: 20px; padding: 15px; border: 1px solid #ddd; border-radius: 8px; }}
        .meal-type {{ color: #666; font-style: italic; }}
        .description {{ color: #555; }}
        ul {{ margin: 10px 0; padding-left: 20px; }}
        li {{ margin: 4px 0; }}
        .shopping-list {{ background: #f9f9f9; padding: 15px; border-radius: 8px; }}
        @media print {{
            body {{ margin: 10mm; }}
            .recipe-print {{ break-inside: avoid; }}
            @page {{ margin: 15mm; }}
        }}
    </style>
</head>
<body>
    <h1>Jadłospis — Dietetyk AI</h1>
    <h2>Przepisy ({len(session_recipes)})</h2>
    {recipes_html if recipes_html else '<p>Brak przepisów w tej sesji.</p>'}
    <h2>Lista zakupów</h2>
    <div class="shopping-list">
        {f'<ul>{shopping_html}</ul>' if shopping_html else '<p>Lista zakupów jest pusta.</p>'}
    </div>
    <script>
        window.onload = function() {{ setTimeout(function() {{ window.print(); }}, 300); }};
    </script>
</body>
</html>"""

    return HTMLResponse(content=html)


# ── Użytkownicy ──────────────────────────────────────────────

@app.get("/api/users")
async def get_users():
    """Zwraca listę użytkowników."""
    users = await usr_module.get_users()
    return JSONResponse(users)


@app.post("/api/users")
async def create_user(request: UserCreateRequest):
    """Tworzy nowego użytkownika."""
    try:
        user = await usr_module.create_user(
            name=request.name,
            avatar=request.avatar or "👤",
            system_prompt=request.system_prompt or "",
        )
        return JSONResponse(user, status_code=201)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/users/{user_id}")
async def update_user(user_id: str, request: UserUpdateRequest):
    """Aktualizuje użytkownika."""
    patch = {k: v for k, v in request.model_dump().items() if v is not None}
    try:
        updated = await usr_module.update_user(user_id, patch)
        return JSONResponse(updated)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: str):
    """Usuwa użytkownika."""
    try:
        ok = await usr_module.delete_user(user_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Użytkownik nie znaleziony.")
        return JSONResponse({"deleted": user_id})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Ustawienia ───────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    """Zwraca aktualne ustawienia."""
    settings = await cfg_module.get_settings()
    return JSONResponse(settings)


@app.put("/api/settings")
async def update_settings(request: SettingsUpdateRequest):
    """Zapisuje ustawienia."""
    patch = {k: v for k, v in request.model_dump().items() if v is not None}
    updated = await cfg_module.update_settings(patch)
    return JSONResponse(updated)


@app.get("/api/ollama/models")
async def get_ollama_models():
    """Zwraca listę dostępnych modeli z Ollama."""
    try:
        settings = await cfg_module.get_settings()
        ollama_url = settings.get("ollama_url", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{ollama_url}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            return JSONResponse({"models": models})
    except Exception:
        return JSONResponse({"models": [], "error": "Ollama niedostępna"})


# ── Historia sesji ───────────────────────────────────────────

@app.get("/api/sessions")
async def get_sessions(user_id: str = Query(default=None)):
    sessions = await sess.list_sessions(user_id=user_id)
    return JSONResponse(sessions)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    data = await sess.get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Sesja nie znaleziona.")
    return JSONResponse(data)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    ok = await sess.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sesja nie znaleziona.")
    return JSONResponse({"deleted": session_id})


# ── Graceful shutdown ────────────────────────────────────────

def _handle_shutdown(*args):
    gem._sessions.clear()


signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)

# ===== v4: Zakupy i Spiżarnia =====

class ShoppingAddRequest(BaseModel):
    product_name: str
    amount: float = 1.0
    unit: str = "szt."

    @field_validator("product_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Nazwa produktu nie może być pusta.")
        return v.strip()


@app.get("/api/shopping-list")
async def get_shopping_list_endpoint():
    """Pobierz listę zakupów z rozwiązanymi nazwami."""
    try:
        items = await grocy.get_shopping_list_enriched()
        return JSONResponse(items)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd pobierania listy zakupów: {str(e)}")


@app.post("/api/shopping-list")
async def add_to_shopping_list_endpoint(request: ShoppingAddRequest):
    """Dodaj wpis do listy zakupów."""
    try:
        result = await grocy.add_to_shopping_list_by_name(request.product_name, request.amount, request.unit)
        return JSONResponse(result, status_code=201)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd dodawania do listy zakupów: {str(e)}")


@app.post("/api/shopping-list/complete-done")
async def complete_done_shopping():
    """Przenieś wszystkie done=1 wpisy z listy zakupów do spiżarni."""
    items = await grocy.get_shopping_list_enriched()
    done_items = [i for i in items if i.get("done")]

    added_to_pantry = []
    failed = []

    for item in done_items:
        name = item.get("name", "")
        amount = float(item.get("amount") or 1)
        unit = item.get("unit") or "szt."
        item_id = item.get("id")

        if not name or not item_id:
            continue

        try:
            await grocy.add_to_pantry(name, amount, unit)
            await grocy.remove_from_shopping_list(item_id)
            added_to_pantry.append({"name": name, "amount": amount, "unit": unit})
        except Exception as e:
            failed.append({"name": name, "error": str(e)})

    return JSONResponse({
        "moved_to_pantry": len(added_to_pantry),
        "items": added_to_pantry,
        "failed": failed,
    })


@app.delete("/api/shopping-list/{item_id}")
async def delete_shopping_list_item(item_id: int):
    """Usuń wpis z listy zakupów."""
    try:
        ok = await grocy.remove_from_shopping_list(item_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Wpis {item_id} nie istnieje.")
        return JSONResponse({"deleted": item_id})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd usuwania: {str(e)}")


@app.get("/api/products/search")
async def search_products_endpoint(q: str = ""):
    """Wyszukaj produkty fuzzy matchingiem (max 5 wyników)."""
    if len(q) < 2:
        return JSONResponse([])
    try:
        results = await grocy.search_products(q)
        return JSONResponse(results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd wyszukiwania: {str(e)}")


@app.get("/api/pantry")
async def get_pantry_endpoint():
    """Pobierz aktualny stan spiżarni."""
    try:
        items = await grocy.get_pantry()
        return JSONResponse(items)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd pobierania spiżarni: {str(e)}")

# ===== v7: Edycja spiżarni, listy zakupów, przepisów =====

class PantryAddRequest(BaseModel):
    product_name: str
    amount: float = 1.0
    unit: str = "szt."

    @field_validator("product_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Nazwa produktu nie może być pusta.")
        return v.strip()


class PantryUpdateRequest(BaseModel):
    amount: float


class ShoppingDoneRequest(BaseModel):
    done: bool


class RecipeUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class RecipeIngredientRequest(BaseModel):
    product_name: str
    amount: float = 1.0
    unit: str = "szt."

    @field_validator("product_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Nazwa produktu nie może być pusta.")
        return v.strip()


@app.post("/api/pantry")
async def add_to_pantry_endpoint(request: PantryAddRequest):
    """Dodaj produkt do spiżarni."""
    try:
        result = await grocy.add_to_pantry(request.product_name, request.amount, request.unit)
        return JSONResponse(result, status_code=201)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd dodawania do spiżarni: {str(e)}")


@app.put("/api/pantry/{product_id}")
async def update_pantry_item_endpoint(product_id: int, request: PantryUpdateRequest):
    """Zaktualizuj ilość produktu w spiżarni."""
    try:
        result = await grocy.update_pantry_item(product_id, request.amount)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd aktualizacji spiżarni: {str(e)}")


@app.delete("/api/pantry/{product_id}")
async def remove_from_pantry_endpoint(product_id: int):
    """Usuń produkt ze spiżarni ORAZ z rejestru Grocy."""
    try:
        ok = await grocy.remove_from_pantry(product_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Produkt {product_id} nie istnieje.")
        return JSONResponse({"deleted": product_id})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd usuwania z spiżarni: {str(e)}")


@app.put("/api/shopping-list/{item_id}/done")
async def toggle_shopping_done_endpoint(item_id: int, request: ShoppingDoneRequest):
    """Oznacz wpis na liście zakupów jako kupiony lub nie."""
    try:
        result = await grocy.toggle_shopping_item_done(item_id, request.done)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd oznaczania zakupu: {str(e)}")


@app.get("/api/recipes/{recipe_id}")
async def get_recipe_endpoint(recipe_id: int):
    """Pobierz jeden przepis z składnikami."""
    try:
        data = await grocy.get_recipe(recipe_id)
        return JSONResponse(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd pobierania przepisu: {str(e)}")


@app.put("/api/recipes/{recipe_id}")
async def update_recipe_endpoint(recipe_id: int, request: RecipeUpdateRequest):
    """Zaktualizuj nazwę i opis przepisu."""
    try:
        result = await grocy.update_recipe(recipe_id, name=request.name, description=request.description)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd aktualizacji przepisu: {str(e)}")


@app.post("/api/recipes/{recipe_id}/ingredients")
async def add_recipe_ingredient_endpoint(recipe_id: int, request: RecipeIngredientRequest):
    """Dodaj składnik do przepisu."""
    try:
        result = await grocy.add_recipe_ingredient(recipe_id, request.product_name, request.amount, request.unit)
        return JSONResponse(result, status_code=201)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd dodawania składnika: {str(e)}")


@app.delete("/api/recipes/{recipe_id}/ingredients/{pos_id}")
async def delete_recipe_ingredient_endpoint(recipe_id: int, pos_id: int):
    """Usuń składnik z przepisu."""
    try:
        ok = await grocy.delete_recipe_ingredient(pos_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Składnik {pos_id} nie istnieje.")
        return JSONResponse({"deleted": pos_id})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd usuwania składnika: {str(e)}")

class ShoppingAmountRequest(BaseModel):
    amount: float


@app.put("/api/shopping-list/{item_id}/amount")
async def update_shopping_item_amount(item_id: int, request: ShoppingAmountRequest):
    """Zaktualizuj ilość wpisu na liście zakupów."""
    try:
        r = await grocy._grocy_request("put", f"objects/shopping_list/{item_id}", json={"amount": request.amount})
        r.raise_for_status()
        return JSONResponse({"success": True, "id": item_id, "amount": request.amount})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd aktualizacji ilości: {str(e)}")


# ===== v8: Naprawa jednostek + Dodaj brakujące do zakupów =====

@app.post("/api/admin/fix-units")
async def fix_existing_product_units():
    """
    Jednorazowa naprawa — iteruje przez wszystkie produkty w Grocy
    i aktualizuje qu_id_stock na podstawie nazwy produktu przez parse_ingredient.
    Produkty z qu_id_stock != Piece (2) są pomijane.
    """
    try:
        products = await grocy.get_products()
        fixed = []
        for p in products:
            if str(p.get("qu_id_stock")) != "2":  # już ma właściwą jednostkę
                continue
            parsed = grocy.parse_ingredient(p["name"])
            if parsed["unit"] != "szt.":
                qu_id = await grocy.get_qu_id_by_name(parsed["unit"])
                if qu_id and str(qu_id) != "2":
                    ok = await grocy._update_product_unit(int(p["id"]), qu_id)
                    if ok:
                        fixed.append({"name": p["name"], "unit": parsed["unit"]})
        return JSONResponse({"fixed": len(fixed), "products": fixed})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd naprawy jednostek: {str(e)}")


@app.post("/api/recipes/{recipe_id}/add-missing-to-shopping")
async def add_missing_to_shopping(recipe_id: int):
    """Dodaj brakujące składniki przepisu do listy zakupów."""
    try:
        ingredients = await grocy.get_recipe_ingredients_enriched(recipe_id)
        added = []
        already_have = []
        for ing in ingredients:
            name = ing.get("name") or ing.get("product_name", "")
            if not name:
                continue
            available = await grocy.is_product_available(name)
            if available:
                already_have.append(name)
            else:
                amount = ing.get("amount", 1)
                unit = ing.get("unit", "szt.")
                await grocy.add_to_shopping_list_by_name(name, amount, unit)
                added.append({"name": name, "amount": amount, "unit": unit})
        return JSONResponse({"added": added, "already_have": already_have})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd dodawania brakujących składników: {str(e)}")
