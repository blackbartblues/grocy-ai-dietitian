# Dietetyk AI — Feature Manifest

**Version: 2.0.0**
**Last updated: 2026-03-17**

This document is the authoritative inventory of all features, components, and design decisions.
Update this file whenever a significant feature is added, changed, or removed.

---

## Version History

| Version | Date | Summary |
|---------|------|---------|
| 1.0.0 | 2026-02-XX | Initial release: chat + basic Grocy integration |
| 1.1.0 | 2026-02-XX | Shopping list + pantry management |
| 1.2.0 | 2026-02-XX | Recipe editor, ingredients editing |
| 1.3.0 | 2026-02-XX | Multi-user support, per-user memory |
| 2.0.0 | 2026-03-17 | Full Material Design 3 redesign, multi-type tags, settings overhaul, two-column settings panel |

---

## Backend — Python / FastAPI

### `main.py`
- FastAPI app, `version="2.0.0"`
- CORS middleware (allow all origins)
- Pydantic request models with validators
- All endpoints documented in README
- Graceful shutdown: clears in-memory sessions on SIGTERM/SIGINT
- `MAX_MESSAGE_LENGTH = 10000` chars per message

### `gemini.py`
- Dual-engine: **Gemini** (google-genai) and **Ollama** (HTTP)
- Streaming via SSE (`text/event-stream`)
- `SESSION_TIMEOUT = 7200` sec (2 hours inactivity)
- In-memory session cache (`_sessions` dict)
- Function calling (tool use) for Grocy operations:
  - `get_recipes()`, `save_recipe()`, `delete_recipe()`
  - `get_stock()`, `add_to_pantry()`
  - `get_shopping_list()`, `add_to_shopping_list()`
  - `get_recipe_ingredients()`, `add_recipe_ingredient()`
  - `get_memory()`, `update_memory()`
- Per-user system prompt loaded from `users.json`
- Global BASE_SYSTEM_PROMPT (clinical dietitian, thyroid health focus)
- `get_session_recipes()` — recipes mentioned in a session (for export)

### `grocy.py`
- Full Grocy REST API client using `httpx`
- `GROCY_BASE_URL` and `GROCY_API_KEY` from environment variables
- `TIMEOUT = 15.0` seconds
- Product fuzzy search (normalized, accent-insensitive)
- Unit mapping: `g`, `ml`, `lyzka`, `lyzeczka`, `szczypta`, `szt.`
- Auto-creates products/units in Grocy if they don't exist
- Recipe metadata stored in Grocy description field:
  - `---NUTRITION---` block: kcal, protein, fat, carbs, fiber
  - `---META---` block: `type:sniadanie,kolacja|author:Name|avatar:🧌`
- `stripNutritionBlock()` / `stripMetaBlock()` — regex strip on save
- `parse_ingredient()` — parses "name amount unit" strings
- `check_connectivity()` — health check endpoint

### `sessions.py`
- Persistent sessions: `/data/sessions/{id}.json`
- `list_sessions(user_id=None)` — filter by user
- Atomic write with temp file + `os.replace()`
- Session has: `id`, `user_id`, `messages[]`, `created_at`, `updated_at`

### `memory.py`
- Per-user AI memory: `/data/memory/{user_id}.json`
- Free-form text blob updated by AI tool calls
- Loaded into system prompt context at session start

### `settings.py`
- Persistent settings: `/data/settings.json`
- Atomic write with asyncio.Lock
- Default settings:
  ```json
  {
    "ai_engine": "gemini",
    "gemini_model": "gemini-2.5-flash",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:14b",
    "system_prompt": ""
  }
  ```

### `users.py`
- Persistent users: `/data/users.json`
- Fields: `id`, `name`, `avatar`, `system_prompt`, `created_at`
- ID auto-generated from name (lowercase, ASCII)
- CRUD: get, create, update, delete

---

## Frontend — Vanilla JS

### `app.js` (~2200 lines, strict mode)

#### State variables (global)
| Variable | Type | Purpose |
|----------|------|---------|
| `currentUserId` | string\|null | Active user ID |
| `currentUserName` | string | Active user display name |
| `currentUserAvatar` | string | Active user avatar emoji |
| `_editingUserId` | string\|null | User being edited in settings |
| `_addingNewUser` | bool | Settings: add mode vs edit mode |
| `_settingsSection` | string | Active settings nav: 'ai'\|userId\|'new' |
| `_settingsUsers` | array | Cached users list for settings nav |
| `_editModalRecipeId` | int\|null | Recipe ID in editor modal |
| `_editModalIngredients` | array | Working copy of ingredients |
| `_pantryFilter` | string | 'all'\|'have'\|'missing' |
| `_recipeFilter` | string | 'all'\|meal type |
| `_recipeSort` | string | 'name'\|'type'\|'author' |
| `_pantryData` | array | Cached pantry items |
| `_shoppingData` | array | Cached shopping list |

#### Key functions
| Function | Description |
|----------|-------------|
| `initTheme()` / `toggleTheme()` | Dark/light theme, per-user localStorage key |
| `updateHeaderUser()` | Updates avatar+name in header |
| `loadSessions()` | Fetches sessions for current user, renders list |
| `startNewSession()` | Creates new session ID |
| `sendMessage()` | SSE chat stream, handles `[RECIPE_SAVED]` events |
| `renderMarkdown()` | Basic markdown → HTML (bold, italic, code, lists) |
| `loadRecipesPanel()` | Fetches and renders all recipe cards |
| `renderRecipeCard()` | Generates recipe card HTML |
| `openRecipeEditModal()` | Opens recipe editor, loads all fields |
| `saveRecipeEditModal()` | Saves recipe (name, desc, meta, nutrition) |
| `toggleModalAccordion()` | Opens one, closes other (ingredients/nutrition) |
| `loadShoppingList()` | Fetches and renders shopping list |
| `addShoppingItem()` | Add item by name with amount+unit |
| `toggleShoppingDone()` | Mark/unmark item as bought |
| `completeDoneShopping()` | Move all done → pantry |
| `loadPantry()` | Fetches and renders pantry |
| `addPantryItem()` | Add item to pantry |
| `loadUsersInSettings()` | Fetches users, calls renderSettingsNav() |
| `renderSettingsNav()` | Renders left nav in settings modal |
| `selectSettingsSection()` | Switches settings panel + switches active user |
| `saveActiveUserEdit()` | Save or create user profile |
| `deleteActiveUser()` | Delete user after confirmation |
| `parseMeta()` | Parses `---META---` block from recipe description |
| `stripMetaBlock()` | Removes META block from description |
| `stripNutritionBlock()` | Removes NUTRITION block from description |
| `escapeHtml()` | XSS prevention |

#### Meal types
```javascript
{ sniadanie, obiad, kolacja, przekaska }
```
Icons: `wb_sunny`, `restaurant`, `nightlight_round`, `apple`

#### Units (ingredient selector)
`szt.`, `g`, `ml`, `lyzka`, `lyzeczka`, `szczypta`

---

## Frontend — HTML/CSS

### `index.html`
- Full app: chat sidebar (left) + main tabs (right)
- Tabs: Chat / Przepisy / Zakupy / Spiżarnia
- Modals: recipe editor, settings
- Settings modal: two-column layout (nav left, content right)
- Google Fonts: Roboto (latin + latin-ext, weights 300/400/500/700)
- Material Icons Round (font ligatures, class `mi`)

### `lite.html`
- No chat — only Przepisy / Zakupy / Spiżarnia tabs
- Same modals as index.html
- Lightweight for tablet/kitchen use

### `style.css` (~4500+ lines)
- Material Design 3 design system (118 CSS variables)
  - `--md-primary`, `--md-surface-*`, `--md-outline-*`, etc.
- Dark theme default, light theme via `.theme-light` on `<body>`
- Per-user theme stored in localStorage
- Recipe modal: `min(96vw, 1200px)` wide, `min(96vh, 860px)` tall, 2-col grid `1fr 480px`
- Settings modal: `min(96vw, 1000px)` wide, `min(92vh, 780px)` tall, fixed height
- Accordion components: recipe editor, settings (old), settings nav sections
- Recipe cards: `.recipe-card-bottom` flex row (tags left, author right)
- Meal type chips: `.meal-chip` / `.meal-chip.active`
- Settings layout: `.settings-nav` (200px) + `.settings-content` (flex:1)
- `.tab-btn { text-transform: none }` — fixes Polish character rendering (Ż, Ź)

### `manifest.json` (PWA)
- App name, icons, theme color
- `display: standalone`

---

## Infrastructure

### `Dockerfile`
- Base: `python:3.11-slim`
- Installs requirements
- Runs uvicorn on port 7860

### `docker-compose.yml`
- Service: `dietetyk-ai`
- Port: `7860:7860`
- Volume: `dietetyk_data:/data`
- Environment: `GEMINI_API_KEY`, `GROCY_BASE_URL`, `GROCY_API_KEY`

### `.env` (not committed — see `.env.example`)
```env
GEMINI_API_KEY=
GROCY_BASE_URL=
GROCY_API_KEY=
```

---

## Known Limitations / Future Work

- [ ] i18n / internationalization — all UI currently in Polish; planned: English default + i18n system
- [ ] Gemini API key configurable in UI (currently env var only)
- [ ] Grocy URL/key configurable in UI (currently env var only)
- [ ] No authentication / access control (designed for LAN use)
- [ ] `lite.html` may drift from `index.html` — consider shared component approach
- [ ] Session auto-cleanup (old sessions never deleted automatically)
- [ ] No recipe image support
- [ ] Meal plan / calendar view not implemented
- [ ] Grocy meal plan sync (Grocy has meal plan feature, not yet connected)

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Vanilla JS (no framework) | Zero build step, fast load, easy to deploy in Docker |
| Grocy as recipe/pantry backend | Avoids building custom food database; Grocy has barcode scanning, product DB |
| Metadata in Grocy description field | Grocy API has no custom fields for recipes — `---META---` and `---NUTRITION---` blocks are our extension |
| SSE for chat streaming | Native browser support, no WebSocket complexity |
| JSON files for storage | Simple, no database dependency, Docker volume backup is trivial |
| Per-user system prompt | Different family members have different health profiles — AI adapts per user |
| IDs generated from names | Human-readable user IDs (e.g. `wojtek`, `justyna`) for easier debugging |
