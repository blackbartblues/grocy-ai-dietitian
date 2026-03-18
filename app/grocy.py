"""
Grocy API client z fuzzy matchingiem i konsolidacją listy zakupów.
"""
import os
import asyncio
import re
import unicodedata
import httpx
from typing import Optional

import settings as cfg_module

GROCY_BASE_URL = os.getenv("GROCY_BASE_URL", "")
GROCY_API_KEY = os.getenv("GROCY_API_KEY", "")

HEADERS = {
    "GROCY-API-KEY": GROCY_API_KEY,
    "Content-Type": "application/json",
}

TIMEOUT = 10.0


async def _get_grocy_config_async() -> tuple[str, str]:
    """Returns (base_url, api_key) — settings file takes priority over env vars."""
    s = await cfg_module.get_settings()
    url = s.get("grocy_url") or os.getenv("GROCY_BASE_URL", "")
    key = s.get("grocy_api_key") or os.getenv("GROCY_API_KEY", "")
    return url, key


async def _grocy_request(method: str, path: str, **kwargs):
    """Helper for all Grocy API requests with dynamic config."""
    url, key = await _get_grocy_config_async()
    headers = {"GROCY-API-KEY": key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await getattr(client, method)(f"{url}/api/{path}", headers=headers, **kwargs)
        r.raise_for_status()
        return r

# Cache dla jednostek i lokalizacji
_default_qu_id: Optional[int] = None
_default_location_id: Optional[int] = None

# ===== v6: Parsowanie jednostek miary =====

# Mapowanie wzorców na nazwy jednostek (zgodne z nazwami w Grocy)
UNIT_MAP = {
    # gramy
    r'\b(g|gr|gram|gramy|gramow)\b': 'g',
    # mililitry
    r'\b(ml|mililitr|mililitrow)\b': 'ml',
    # lyzka (bez polskich znakow dla zgodnosci z Grocy)
    r'\b(lyzka|lyzki|lyzek|lyzke|lyzk\.?|łyżka|łyżki|łyżek|łyżkę|łyżk\.?)\b': 'lyzka',
    # lyzeczka
    r'\b(lyzeczka|lyzeczki|lyzeczek|lyzeczke|lyzecz\.?|łyżeczka|łyżeczki|łyżeczek|łyżeczkę|łyżecz\.?)\b': 'lyzeczka',
    # szczypta
    r'\b(szczypta|szczypty|szczypcie|szcz\.?)\b': 'szczypta',
    # sztuki (domyslna)
    r'\b(szt\.?|sztuka|sztuki|sztuk|kawałek|kawalek|kawałki|kawałkow|zabek|ząbek|ząbki|ząbków|porcja|porcje|porcji)\b': 'szt.',
}


def parse_ingredient(text: str) -> dict:
    """
    Parsuje string skladnika i zwraca {name, amount, unit}.
    Przyklady:
      "60g platków owsianych"  -> {name: "platki owsiane", amount: 60, unit: "g"}
      "1 lyzka miodu"          -> {name: "miod", amount: 1, unit: "lyzka"}
      "szczypta cynamonu"      -> {name: "cynamon", amount: 1, unit: "szczypta"}
      "2 zabki czosnku"        -> {name: "czosnek", amount: 2, unit: "szt."}
      "3 jaja"                 -> {name: "jaja", amount: 3, unit: "szt."}
    """
    text = text.strip()
    amount = 1.0
    unit = 'szt.'
    name = text

    # Szukaj liczby na poczatku (np. "60g", "1.5 lyzki", "200")
    number_match = re.match(r'^(\d+[.,]?\d*)\s*', text)
    if number_match:
        amount = float(number_match.group(1).replace(',', '.'))
        text = text[number_match.end():].strip()

    # Szukaj jednostki
    for pattern, unit_name in UNIT_MAP.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            unit = unit_name
            # Usun jednostke z nazwy
            name = re.sub(pattern, '', text, flags=re.IGNORECASE).strip()
            break
    else:
        name = text

    # Wyczysc nazwe z nadmiarowych spacji i przecinkow
    name = re.sub(r'\s+', ' ', name).strip(' ,')

    return {"name": name, "amount": amount, "unit": unit}


def _normalize_unit_name(name: str) -> str:
    """Normalizuj nazwe jednostki: usun polskie znaki (w tym l-stroke), zamien na lowercase."""
    # Najpierw jawne mapowanie polskich liter nieobjetych przez NFKD decomposition
    _pl_map = str.maketrans('łŁąĄćĆęĘńŃóÓśŚźŹżŻ', 'lLaAcCeEnNoOsSzZzZ')
    name = name.translate(_pl_map)
    # Nastepnie usuniecie pozostalych znakow diakrytycznych przez NFKD
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_str = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_str.lower().strip()


# Mapa normalizacji typowych nazw jednostek przekazywanych przez AI
_UNIT_ALIASES: dict[str, list[str]] = {
    'g':         ['g', 'gr', 'gram', 'gramy', 'gramow', 'grams'],
    'ml':        ['ml', 'mililitr', 'mililitrow', 'milliliter'],
    'lyzka':     ['lyzka', 'lyzki', 'lyzek', 'lyzke', 'lyzk', 'tablespoon', 'tbsp'],
    'lyzeczka':  ['lyzeczka', 'lyzeczki', 'lyzeczek', 'lyzeczke', 'lyzecz', 'teaspoon', 'tsp'],
    'szczypta':  ['szczypta', 'szczypty', 'szczypcie', 'szcz', 'pinch'],
    'szt.':      ['szt.', 'szt', 'sztuka', 'sztuki', 'sztuk', 'pcs'],
}

# Odwrocona mapa: alias -> canonical Grocy name
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _aliases in _UNIT_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias] = _canonical


async def get_qu_id_by_name(unit_name: str) -> int:
    """Znajdz ID jednostki po nazwie. Normalizuje polskie znaki i aliasy. Fallback na 'g'."""
    try:
        r = await _grocy_request("get", "objects/quantity_units")
        units = r.json()

        normalized = _normalize_unit_name(unit_name)

        # 1. Szukaj dokladnego dopasowania (nazwa w Grocy)
        for u in units:
            if u['name'].lower().strip() == normalized:
                return u['id']

        # 2. Sprawdz mape aliasow — znormalizowany alias -> canonical -> szukaj w Grocy
        canonical = _ALIAS_TO_CANONICAL.get(normalized)
        if canonical:
            for u in units:
                if u['name'].lower().strip() == canonical.lower().strip():
                    return u['id']

        # 3. Fallback — szukaj 'g'
        for u in units:
            if u['name'].lower() in ('g', 'gram', 'gramy'):
                return u['id']

        # 4. Ostatni fallback — pierwsza jednostka
        return units[0]['id'] if units else 2
    except Exception:
        return await get_default_qu_id()


def format_description_for_grocy(text: str) -> str:
    """Konwertuj plain text z newlines na HTML dla Grocy."""
    if not text:
        return ""
    # Zamień podwójne newline na nowy akapit
    paragraphs = text.strip().split('\n\n')
    if len(paragraphs) > 1:
        return ''.join(f'<p>{p.replace(chr(10), "<br>")}</p>' for p in paragraphs if p.strip())
    # Pojedyncze newline → <br>
    return text.replace('\n', '<br>')


async def get_default_qu_id() -> int:
    """Pobierz ID domyślnej jednostki miary dynamicznie."""
    global _default_qu_id
    if _default_qu_id is not None:
        return _default_qu_id
    try:
        r = await _grocy_request("get", "objects/quantity_units")
        units = r.json()
        # Priorytet: szukaj 'g', potem 'szt', potem 'piece', potem pierwszą dostępną
        for u in units:
            if u['name'].lower() in ('g', 'gram', 'gramy'):
                _default_qu_id = u['id']
                return _default_qu_id
        for u in units:
            if u['name'].lower() in ('szt', 'szt.', 'piece', 'stück', 'sztuka'):
                _default_qu_id = u['id']
                return _default_qu_id
        _default_qu_id = units[0]['id'] if units else 2
        return _default_qu_id
    except Exception:
        return 2


async def get_default_location_id() -> int:
    """Pobierz ID domyślnej lokalizacji dynamicznie."""
    global _default_location_id
    if _default_location_id is not None:
        return _default_location_id
    try:
        r = await _grocy_request("get", "objects/locations")
        locations = r.json()
        _default_location_id = locations[0]['id'] if locations else 2
        return _default_location_id
    except Exception:
        return 2


def _normalize(text: str) -> str:
    """Normalizuje tekst: lowercase + usunięcie diakrytyków dla porównań."""
    return unicodedata.normalize("NFD", text.lower().strip()).encode("ascii", "ignore").decode("ascii")


def find_product(name: str, products: list) -> Optional[dict]:
    """
    Szuka produktu po nazwie z fuzzy matchingiem.
    Przykład: 'pekkan' znajdzie 'orzechy pekkan', 'migdały' znajdzie 'migdały blanszowane'
    """
    if not products:
        return None

    name_lower = name.lower().strip()
    name_norm = _normalize(name)
    name_tokens = set(name_lower.split())
    # Tylko słowa > 2 znaki — wyklucza przyimki "z", "w", "i", "a" itp.
    name_tokens_meaningful = {w for w in name_tokens if len(w) > 2}

    # 1. Dokładne dopasowanie (case-insensitive)
    for p in products:
        if p["name"].lower() == name_lower:
            return p

    # 2. Dokładne dopasowanie po zbiorze znaczących tokenów (identyczny zestaw słów)
    # "pierś z indyka" != "oliwa z oliwek" bo {"pierś","indyka"} != {"oliwa","oliwek"}
    if name_tokens_meaningful:
        for p in products:
            p_tokens = {w for w in p["name"].lower().split() if len(w) > 2}
            if name_tokens_meaningful == p_tokens:
                return p

    # 3. Wszystkie znaczące słowa z zapytania zawarte w tokenach produktu
    if name_tokens_meaningful:
        for p in products:
            p_tokens = {w for w in p["name"].lower().split() if len(w) > 2}
            if name_tokens_meaningful.issubset(p_tokens):
                return p

    # 4. Fallback znormalizowany: przynajmniej jedno słowo > 4 znaki pasuje
    long_words = [_normalize(w) for w in name_tokens if len(w) > 4]
    if long_words:
        for p in products:
            p_norm = _normalize(p["name"])
            p_words = p_norm.split()
            if any(w in p_words for w in long_words):
                return p

    return None


def consolidate_shopping_list(ingredients: list) -> list:
    """
    Łączy ilości tego samego produktu z całego tygodnia.
    Input: [{"name": "marchew", "amount": 150, "unit": "g"}, ...]
    Output: [{"name": "marchew", "amount": 350, "unit": "g"}, ...]
    """
    consolidated: dict = {}
    for item in ingredients:
        key = item["name"].lower().strip()
        if key in consolidated:
            if consolidated[key]["unit"] == item.get("unit", ""):
                consolidated[key] = {
                    **consolidated[key],
                    "amount": consolidated[key]["amount"] + item["amount"],
                }
        else:
            consolidated[key] = dict(item)
    return list(consolidated.values())


async def get_recipes() -> list:
    """Zwraca listę wszystkich przepisów."""
    try:
        r = await _grocy_request("get", "objects/recipes")
        return r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd pobierania przepisów z Grocy: {e}") from e


async def get_recipe_details(recipe_id: int) -> list:
    """Zwraca składniki konkretnego przepisu."""
    try:
        r = await _grocy_request("get", "objects/recipes_pos", params={"query[]": f"recipe_id={recipe_id}"})
        return r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd pobierania składników przepisu {recipe_id}: {e}") from e


async def get_stock() -> list:
    """Zwraca aktualny stan spiżarni."""
    try:
        r = await _grocy_request("get", "stock")
        return r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd pobierania spiżarni z Grocy: {e}") from e


async def get_products() -> list:
    """Zwraca listę wszystkich produktów."""
    try:
        r = await _grocy_request("get", "objects/products")
        return r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd pobierania produktów z Grocy: {e}") from e


async def create_product(name: str, qu_id: Optional[int] = None) -> dict:
    """
    Tworzy nowy produkt w Grocy jeśli nie istnieje.
    ZAWSZE sprawdza fuzzy matching przed stworzeniem.
    Zwraca istniejący lub nowo stworzony produkt.
    v6: opcjonalny qu_id pozwala ustawic wlasciwa jednostke przy tworzeniu produktu.
    """
    try:
        products = await get_products()
        existing = find_product(name, products)
        if existing:
            return existing

        if qu_id is None:
            qu_id = await get_default_qu_id()
        loc_id = await get_default_location_id()
        r = await _grocy_request("post", "objects/products", json={
            "name": name,
            "location_id": loc_id,
            "qu_id_purchase": qu_id,
            "qu_id_stock": qu_id,
        })
        created = r.json()
        # Pobierz pełny obiekt produktu (response może zwrócić tylko created_object_id)
        product_id = created.get("created_object_id") or created.get("id")
        if product_id:
            r2 = await _grocy_request("get", f"objects/products/{product_id}")
            if r2.status_code == 200:
                return r2.json()
        return created
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd tworzenia produktu '{name}': {e}") from e


async def save_recipe(name: str, description: str, servings: int, ingredients: list = None, nutrition: dict = None, meal_type: str = None, author_name: str = None, author_avatar: str = None) -> dict:
    """
    Zapisuje nowy przepis do Grocy.
    Jeśli podano ingredients, automatycznie dodaje składniki.
    Zwraca dict z id przepisu, ingredients_added i ingredients_failed.
    """
    if ingredients is None:
        ingredients = []

    if nutrition is None:
        nutrition = {}

    # Zbuduj blok wartości odżywczych jeśli podano jakiekolwiek wartości
    nutrition_values = {k: v for k, v in nutrition.items() if v is not None}
    if nutrition_values:
        cal = nutrition_values.get("calories", "—")
        prot = nutrition_values.get("protein_g", "—")
        fat = nutrition_values.get("fat_g", "—")
        carbs = nutrition_values.get("carbs_g", "—")
        fiber = nutrition_values.get("fiber_g", "—")
        nutrition_block = f"\n\n---NUTRITION---\ncal:{cal}|prot:{prot}|fat:{fat}|carbs:{carbs}|fiber:{fiber}"
        description = description + nutrition_block

    if meal_type or author_name:
        meta_parts = []
        if meal_type:
            meta_parts.append(f"type:{meal_type}")
        if author_name:
            meta_parts.append(f"author:{author_name}")
        if author_avatar:
            meta_parts.append(f"avatar:{author_avatar}")
        description = description + f"\n\n---META---\n{'|'.join(meta_parts)}"

    # Konwertuj opis plain text na HTML dla Grocy
    description_html = format_description_for_grocy(description)

    try:
        r = await _grocy_request("post", "objects/recipes", json={
            "name": name,
            "description": description_html,
            "base_servings": servings,
        })
        recipe_data = r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Blad zapisywania przepisu '{name}': {e}") from e

    recipe_id = recipe_data.get("created_object_id") or recipe_data.get("id")
    print(f"[grocy] Przepis '{name}' zapisany z ID={recipe_id}, skladnikow={len(ingredients)}")

    ingredients_added = 0
    ingredients_failed = []

    for ing in ingredients:
        ing_name = ing.get("name", "").strip()
        if not ing_name:
            continue
        ing_amount = float(ing.get("amount", 1))
        ing_unit = ing.get("unit", "")
        try:
            print(f"[grocy] Dodaję składnik: '{ing_name}' amount={ing_amount} unit={ing_unit}")
            await add_ingredient_to_recipe(int(recipe_id), ing_name, ing_amount, ing_unit)
            ingredients_added += 1
            print(f"[grocy] Składnik '{ing_name}' dodany OK")
        except Exception as e:
            err_str = str(e)[:120]
            print(f"[grocy] ERROR dodawania składnika '{ing_name}': {err_str}")
            ingredients_failed.append(f"{ing_name}: {err_str}")

    print(f"[grocy] Składniki: {ingredients_added} dodanych, {len(ingredients_failed)} błędów")

    result = {
        "id": recipe_id,
        "grocy_id": recipe_id,
        "name": name,
        "ingredients_added": ingredients_added,
        "ingredients_failed": ingredients_failed,
    }
    if ingredients_failed:
        failed_list = ', '.join(ingredients_failed)
        result["warning"] = f"Przepis '{name}' zapisany w Grocy (ID: {recipe_id}). Nie dodano skladnikow: {failed_list}"
    return result


async def _get_product_stock_amount(product_id: int) -> float:
    """Zwraca aktualny stan magazynowy produktu (0 jesli brak)."""
    try:
        r = await _grocy_request("get", f"stock/products/{product_id}")
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return float(data.get("stock_amount", 0) or 0)
        return 0.0
    except Exception:
        return 0.0


async def _update_product_unit(product_id: int, qu_id: int) -> bool:
    """
    Aktualizuje qu_id_stock i qu_id_purchase produktu.
    Mozliwe tylko gdy produkt nie ma stanu magazynowego.
    Zwraca True jesli udalo sie zaktualizowac.
    """
    try:
        stock_amount = await _get_product_stock_amount(product_id)
        if stock_amount > 0:
            print(f"[grocy] Produkt {product_id} ma stan={stock_amount}, nie mozna zmienic jednostki")
            return False
        r = await _grocy_request("put", f"objects/products/{product_id}", json={"qu_id_stock": qu_id, "qu_id_purchase": qu_id})
        if r.status_code < 400:
            print(f"[grocy] Zaktualizowano jednostke produktu {product_id} -> qu_id={qu_id}")
            return True
        print(f"[grocy] Nie mozna zaktualizowac jednostki produktu {product_id}: {r.text[:100]}")
        return False
    except Exception as e:
        print(f"[grocy] Blad aktualizacji jednostki produktu {product_id}: {e}")
        return False


async def add_ingredient_to_recipe(
    recipe_id: int,
    product_name: str,
    amount: float,
    unit: str = "",
) -> dict:
    """
    Dodaje skladnik do przepisu.
    Najpierw upewnia sie ze produkt istnieje (tworzy jesli nie).
    Gdy produkt istnieje z inna jednostka i ma zerowy stan — aktualizuje jednostke.
    Grocy wymaga qu_id w recipes_pos == qu_id_stock produktu.
    """
    # Ustal qu_id z zadanej jednostki
    if unit and unit.strip():
        qu_id = await get_qu_id_by_name(unit.strip())
    else:
        qu_id = None

    # Stworz produkt z prawidlowa jednostka (nowe produkty dostana qu_id_stock=qu_id)
    product = await create_product(product_name, qu_id=qu_id)
    product_id = product.get("id") or product.get("created_object_id")
    if not product_id:
        raise RuntimeError(f"Nie mozna uzyskac ID produktu '{product_name}'. Odpowiedz: {product}")

    product_id = int(product_id)

    # Pobierz aktualne qu_id_stock produktu
    product_qu_id = product.get("qu_id_stock")

    if qu_id is not None:
        qu_id = int(qu_id)
        # Jesli produkt ma inna jednostke niz zadana — sprobuj zaktualizowac (jesli brak stanu)
        if product_qu_id and int(product_qu_id) != qu_id:
            updated = await _update_product_unit(product_id, qu_id)
            if not updated:
                # Nie mozna zmienic — uzyj qu_id_stock produktu (Grocy constraint)
                qu_id = int(product_qu_id)
                print(f"[grocy] Fallback na qu_id_stock={qu_id} produktu '{product_name}'")
    else:
        # Brak zadanej jednostki — uzyj qu_id_stock produktu
        if product_qu_id:
            qu_id = int(product_qu_id)
        else:
            qu_id = await get_default_qu_id()

    print(f"[grocy] recipes_pos: recipe_id={recipe_id}, product_id={product_id}, amount={amount}, qu_id={qu_id}")
    try:
        r = await _grocy_request("post", "objects/recipes_pos", json={
            "recipe_id": int(recipe_id),
            "product_id": int(product_id),
            "amount": float(amount),
            "qu_id": qu_id,
        })
        if r.status_code >= 400:
            print(f"[grocy] recipes_pos ERROR {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(
            f"Błąd dodawania składnika '{product_name}' do przepisu {recipe_id}: {e}"
        ) from e


async def add_to_shopping_list(
    product_name: str,
    amount: float,
    note: str = "",
) -> dict:
    """Dodaje produkt do listy zakupów (tworzy produkt jeśli nie istnieje)."""
    product = await create_product(product_name)
    product_id = product.get("id") or product.get("created_object_id")

    try:
        r = await _grocy_request("post", "stock/shoppinglist/add-product", json={
            "product_id": int(product_id),
            "product_amount": amount,
            "note": note,
        })
        r.raise_for_status()
        return {"success": True, "product": product_name, "amount": amount}
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd dodawania '{product_name}' do listy zakupów: {e}") from e


async def get_shopping_list() -> list:
    """Zwraca aktualną listę zakupów."""
    try:
        r = await _grocy_request("get", "objects/shopping_list")
        return r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd pobierania listy zakupów: {e}") from e


async def get_meal_plan() -> list:
    """Zwraca aktualny plan posiłków."""
    try:
        r = await _grocy_request("get", "meal-plan")
        return r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd pobierania planu posiłków: {e}") from e


async def save_meal_plan_entry(recipe_id: int, day: str, meal_type: str) -> dict:
    """Zapisuje wpis do planu posiłków Grocy."""
    try:
        r = await _grocy_request("post", "objects/meal_plan", json={
            "recipe_id": recipe_id,
            "day": day,
            "type": meal_type,
        })
        return r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd zapisywania planu posiłków: {e}") from e


async def check_connectivity() -> bool:
    """Sprawdza połączenie z Grocy."""
    try:
        url, key = await _get_grocy_config_async()
        headers = {"GROCY-API-KEY": key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{url}/api/system/info", headers=headers)
            return r.status_code == 200
    except Exception:
        return False


async def delete_recipe(recipe_id: int) -> bool:
    """Usuwa przepis z Grocy. Zwraca True jeśli sukces (204), False jeśli nie istnieje."""
    try:
        r = await _grocy_request("delete", f"objects/recipes/{recipe_id}")
        return r.status_code in (200, 204)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Blad usuwania przepisu {recipe_id}: {e}") from e

# ===== v4: Nowe funkcje =====

async def get_shopping_list_enriched() -> list:
    """Pobierz listę zakupów z rozwiązanymi nazwami produktów."""
    try:
        r = await _grocy_request("get", "objects/shopping_list")
        items = r.json()

        products_list = await get_products()
        products = {p["id"]: p for p in products_list}

        # v8: Pobierz mapę jednostek (id → name) dla poprawnego rozwiązywania jednostek
        try:
            ru = await _grocy_request("get", "objects/quantity_units")
            units_map = {u["id"]: u["name"] for u in ru.json()}
        except Exception:
            units_map = {}

        result = []
        for item in items:
            product = products.get(item.get("product_id"), {})
            pid = item.get("product_id", "?")
            # v8: Rozwiąż jednostkę przez: qu_id wpisu → qu_id_stock produktu → fallback "szt."
            qu_id_item = item.get("qu_id")
            qu_id_product = product.get("qu_id_stock")
            unit_name = (
                units_map.get(qu_id_item)
                or units_map.get(qu_id_product)
                or product.get("qu_id_stock_name")
                or "szt."
            )
            result.append({
                "id": item["id"],
                "product_id": item.get("product_id"),
                "name": product.get("name") or item.get("note") or f"Produkt #{pid}",
                "amount": item.get("amount", 1),
                "unit": unit_name,
                "note": item.get("note", ""),
                "done": item.get("done", 0) == 1,
            })
        return result
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd pobierania listy zakupów: {e}") from e


async def add_to_shopping_list_by_name(product_name: str, amount: float = 1, unit: str = "szt.") -> dict:
    """Dodaj produkt do listy zakupów z fuzzy matchingiem i poprawną jednostką."""
    try:
        # Rozwiąż qu_id dla podanej jednostki
        qu_id = await get_qu_id_by_name(unit) if unit and unit.strip() else None

        products_list = await get_products()
        existing = find_product(product_name, products_list)
        if existing:
            product_id = int(existing.get("id"))
            # Użyj qu_id z jednostki lub qu_id_stock produktu
            item_qu_id = qu_id or existing.get("qu_id_stock")
            payload = {"product_id": product_id, "amount": amount, "note": ""}
            if item_qu_id:
                payload["qu_id"] = item_qu_id
            r = await _grocy_request("post", "objects/shopping_list", json=payload)
            r.raise_for_status()
            return {"success": True, "name": existing["name"], "amount": amount, "unit": unit}
        else:
            # Produkt nie istnieje w bazie — dodaj przez notatkę z qu_id
            payload = {"amount": amount, "note": product_name}
            if qu_id:
                payload["qu_id"] = qu_id
            r = await _grocy_request("post", "objects/shopping_list", json=payload)
            r.raise_for_status()
            return {"success": True, "name": product_name, "amount": amount, "unit": unit}
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd dodawania do listy zakupów: {e}") from e


async def remove_from_shopping_list(item_id: int) -> bool:
    """Usuwa wpis z listy zakupów."""
    try:
        r = await _grocy_request("delete", f"objects/shopping_list/{item_id}")
        return r.status_code in (200, 204)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd usuwania z listy zakupów: {e}") from e


async def search_products(query: str) -> list:
    """Wyszukaj produkty fuzzy matchingiem. Zwraca max 5 wyników."""
    try:
        products_list = await get_products()
        query_lower = query.lower().strip()
        query_norm = _normalize(query)

        results = []
        for p in products_list:
            name = p.get("name", "")
            name_lower = name.lower()
            name_norm = _normalize(name)
            if (query_lower in name_lower
                    or query_norm in name_norm
                    or any(w in name_norm.split() for w in query_norm.split() if len(w) > 2)):
                results.append({"id": p["id"], "name": name})
            if len(results) >= 5:
                break
        return results
    except Exception as e:
        raise RuntimeError(f"Błąd wyszukiwania produktów: {e}") from e


async def get_pantry() -> list:
    """
    v9: Pobierz WSZYSTKIE produkty z bazy Grocy + ich aktualny stan stock.
    Produkty bez stock (lub amount=0) mają in_stock=False.
    """
    try:
        # 1. Pobierz wszystkie produkty z bazy
        products_raw = await get_products()

        # 2. Pobierz aktualny stock (tylko produkty z amount > 0)
        stock_data = await get_stock()

        # 3. Pobierz mapę jednostek
        try:
            ru = await _grocy_request("get", "objects/quantity_units")
            units_map = {u["id"]: u["name"] for u in ru.json()}
        except Exception:
            units_map = {}

        # 4. Zbuduj mapę: product_id → stock info
        stock_map = {}
        for s in stock_data:
            pid = s.get("product_id")
            if pid:
                stock_map[int(pid)] = {
                    "amount": float(s.get("amount", 0) or 0),
                }

        # 5. Połącz wszystkie produkty ze stanem stock
        result = []
        for p in products_raw:
            pid = int(p["id"])
            qu_id = p.get("qu_id_stock")
            unit_name = units_map.get(qu_id, "szt.") if qu_id else "szt."
            stock_entry = stock_map.get(pid)
            in_stock = stock_entry is not None and stock_entry["amount"] > 0
            amount = stock_entry["amount"] if stock_entry else 0.0

            result.append({
                "product_id": pid,
                "name": p["name"],
                "amount": amount,
                "unit": unit_name,
                "in_stock": in_stock,
            })

        # Sortuj: najpierw mamy (alfabetycznie), potem brakujące (alfabetycznie)
        result.sort(key=lambda x: (not x["in_stock"], x["name"].lower()))
        return result
    except Exception as e:
        raise RuntimeError(f"Błąd pobierania spiżarni: {e}") from e


async def is_product_available(product_name: str) -> bool:
    """
    Sprawdz czy produkt jest dostepny w spizarni.
    v6: jesli produkt istnieje w stock (nawet z amount=0 lub None) → True.
    Tylko jesli produktu w ogole nie ma w stock → False.
    Uzywamy _normalize do porownania bez diakrytykow.
    """
    try:
        stock = await get_stock()
        products_in_stock = {}
        for s in stock:
            product_info = s.get("product", {}) or {}
            name = product_info.get("name", "")
            if name:
                products_in_stock[name.lower()] = _normalize(name)

        product_lower = product_name.lower().strip()
        product_norm = _normalize(product_name)

        # 1. Dokladne dopasowanie (z diakrytykami)
        if product_lower in products_in_stock:
            return True

        # 2. Dopasowanie znormalizowane (bez diakrytykow)
        for orig_lower, norm in products_in_stock.items():
            if product_norm == norm:
                return True

        # 3. Fuzzy matching — substring lub tokeny (znormalizowane)
        for orig_lower, norm in products_in_stock.items():
            # Substring check (znormalizowany)
            if product_norm in norm or norm in product_norm:
                return True
            # Token matching (znormalizowany)
            p_tokens = set(product_norm.split())
            s_tokens = set(norm.split())
            long_common = [t for t in p_tokens if len(t) > 3 and t in s_tokens]
            if long_common:
                return True
        return False
    except Exception:
        return False


async def get_recipe_ingredients_enriched(recipe_id: int) -> list:
    """Zwraca składniki przepisu z rozwiązanymi nazwami produktów."""
    try:
        ingredients = await get_recipe_details(recipe_id)
        products_list = await get_products()
        products = {p["id"]: p for p in products_list}

        # v8: Pobierz mapę jednostek (id → name) bo qu_id_stock_name nie jest w odpowiedzi Grocy
        try:
            ru = await _grocy_request("get", "objects/quantity_units")
            units_map = {u["id"]: u["name"] for u in ru.json()}
        except Exception:
            units_map = {}

        result = []
        for ing in ingredients:
            product = products.get(ing.get("product_id"), {})
            pid = ing.get("product_id", "?")
            qu_id = product.get("qu_id_stock")
            unit_name = units_map.get(qu_id, "szt.") if qu_id else "szt."
            result.append({
                "name": product.get("name") or f"Produkt #{pid}",
                "amount": ing.get("amount", 1),
                "unit": unit_name,
            })
        return result
    except Exception as e:
        raise RuntimeError(f"Błąd pobierania składników przepisu {recipe_id}: {e}") from e

# ===== v7: Edycja spiżarni, listy zakupów, przepisów =====

async def add_to_pantry(product_name: str, amount: float, unit: str) -> dict:
    """
    Dodaj produkt do spiżarni.
    1. Znajdź lub utwórz produkt (find_product / create_product)
    2. Jeśli produkt istnieje i ma inną jednostkę → zaktualizuj qu_id_stock (v8)
    3. POST /api/stock/products/{id}/add z {amount, transaction_type: "purchase"}
    """
    qu_id = await get_qu_id_by_name(unit) if unit and unit.strip() else None

    # v8: Sprawdź czy produkt istnieje — jeśli tak, zaktualizuj jednostkę
    products_list = await get_products()
    existing = find_product(product_name, products_list)
    if existing:
        product_id = int(existing["id"])
        # Zaktualizuj jednostkę jeśli się różni (i qu_id jest znany)
        if qu_id and str(existing.get("qu_id_stock")) != str(qu_id):
            await _update_product_unit(product_id, qu_id)
        product = existing
    else:
        product = await create_product(product_name, qu_id=qu_id)
        product_id = product.get("id") or product.get("created_object_id")
        if not product_id:
            raise RuntimeError(f"Nie można uzyskać ID produktu '{product_name}'")
        product_id = int(product_id)

    try:
        r = await _grocy_request("post", f"stock/products/{product_id}/add", json={
            "amount": float(amount),
            "transaction_type": "purchase",
        })
        r.raise_for_status()
        return {
            "success": True,
            "product_id": product_id,
            "name": product.get("name", product_name),
            "amount": amount,
            "unit": unit,
        }
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd dodawania do spiżarni '{product_name}': {e}") from e


async def update_pantry_item(product_id: int, amount: float) -> dict:
    """Zaktualizuj ilość — usuń stary stock (consume) i dodaj nowy."""
    try:
        # Pobierz aktualny stan
        current = await _get_product_stock_amount(product_id)
        if current > 0:
            # Zredukuj do zera przez consume
            r = await _grocy_request("post", f"stock/products/{product_id}/consume", json={"amount": current, "transaction_type": "consume"})
            r.raise_for_status()
        # Dodaj nową ilość
        r2 = await _grocy_request("post", f"stock/products/{product_id}/add", json={"amount": float(amount), "transaction_type": "purchase"})
        r2.raise_for_status()
        return {"success": True, "product_id": product_id, "amount": amount}
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd aktualizacji spiżarni produktu {product_id}: {e}") from e


async def remove_from_pantry(product_id: int) -> bool:
    """
    Usuń produkt całkowicie — zarówno ze spiżarni jak i z rejestru Grocy.
    DELETE /api/objects/products/{id}  ← usuwa produkt z rejestru Grocy.
    Grocy automatycznie usuwa powiązane stock entries przy usunięciu produktu.
    """
    try:
        r = await _grocy_request("delete", f"objects/products/{product_id}")
        return r.status_code in (200, 204)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd usuwania produktu {product_id}: {e}") from e


async def toggle_shopping_item_done(item_id: int, done: bool) -> dict:
    """PUT /api/objects/shopping_list/{id} z {done: 1 lub 0}"""
    try:
        r = await _grocy_request("put", f"objects/shopping_list/{item_id}", json={"done": 1 if done else 0})
        r.raise_for_status()
        return {"success": True, "id": item_id, "done": done}
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd oznaczania zakupu {item_id}: {e}") from e


async def get_recipe(recipe_id: int) -> dict:
    """Pobierz przepis + jego składniki z nazwami produktów i jednostkami."""
    try:
        r = await _grocy_request("get", f"objects/recipes/{recipe_id}")
        recipe = r.json()

        # Pobierz składniki z nazwami
        ingredients = await get_recipe_ingredients_enriched_with_pos_id(recipe_id)

        return {
            "id": recipe.get("id"),
            "name": recipe.get("name", ""),
            "description": recipe.get("description", ""),
            "base_servings": recipe.get("base_servings", 1),
            "ingredients": ingredients,
        }
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd pobierania przepisu {recipe_id}: {e}") from e


async def get_recipe_ingredients_enriched_with_pos_id(recipe_id: int) -> list:
    """Zwraca składniki przepisu z pos_id, nazwami produktów i jednostkami."""
    try:
        ingredients = await get_recipe_details(recipe_id)
        products_list = await get_products()
        products = {p["id"]: p for p in products_list}

        # Pobierz nazwy jednostek
        r = await _grocy_request("get", "objects/quantity_units")
        units = {u["id"]: u["name"] for u in r.json()}

        result = []
        for ing in ingredients:
            product = products.get(ing.get("product_id"), {})
            pid = ing.get("product_id", "?")
            qu_id = ing.get("qu_id")
            # v8: qu_id_stock_name nie istnieje w Grocy API — użyj mapy jednostek
            qu_id_stock = product.get("qu_id_stock")
            unit_name = units.get(qu_id) or units.get(qu_id_stock) or "szt."
            result.append({
                "pos_id": ing.get("id"),
                "product_id": ing.get("product_id"),
                "name": product.get("name") or f"Produkt #{pid}",
                "amount": ing.get("amount", 1),
                "unit": unit_name,
            })
        return result
    except Exception as e:
        raise RuntimeError(f"Błąd pobierania składników przepisu {recipe_id}: {e}") from e


async def update_recipe(recipe_id: int, name: str = None, description: str = None) -> dict:
    """PUT /api/objects/recipes/{id}"""
    try:
        data = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = format_description_for_grocy(description)
        r = await _grocy_request("put", f"objects/recipes/{recipe_id}", json=data)
        r.raise_for_status()
        return {"success": True, "id": recipe_id}
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd aktualizacji przepisu {recipe_id}: {e}") from e


async def add_recipe_ingredient(recipe_id: int, product_name: str, amount: float, unit: str) -> dict:
    """Dodaj składnik do istniejącego przepisu."""
    result = await add_ingredient_to_recipe(recipe_id, product_name, amount, unit)
    return result


async def delete_recipe_ingredient(pos_id: int) -> bool:
    """DELETE /api/objects/recipes_pos/{id}"""
    try:
        r = await _grocy_request("delete", f"objects/recipes_pos/{pos_id}")
        return r.status_code in (200, 204)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Błąd usuwania składnika przepisu {pos_id}: {e}") from e
