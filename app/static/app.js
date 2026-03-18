/* ===== Dietetyk AI v2 — app.js ===== */
"use strict";

// === i18n System ===
let _i18n = {};
let _lang = localStorage.getItem('dietetyk_lang') || 'en';

async function loadTranslations(lang) {
  try {
    const res = await fetch(`/static/i18n/${lang}.json`);
    if (!res.ok) throw new Error('Not found');
    _i18n = await res.json();
    _lang = lang;
    localStorage.setItem('dietetyk_lang', lang);
  } catch {
    if (lang !== 'en') {
      await loadTranslations('en'); // fallback
    }
  }
}

function t(key, vars = {}) {
  const parts = key.split('.');
  let val = _i18n;
  for (const p of parts) {
    if (val && typeof val === 'object') val = val[p];
    else { val = key; break; }
  }
  if (typeof val !== 'string') return key;
  return Object.entries(vars).reduce((s, [k, v]) => s.replace(`{${k}}`, v), val);
}

async function setLanguage(lang) {
  await loadTranslations(lang);
  applyTranslations();
}
window.setLanguage = setLanguage;

function applyTranslations() {
  // Update elements with data-i18n attribute
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.textContent = t(key);
  });
  // Update placeholders
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
  });
}
// === end i18n ===

// ===== Stan globalny =====
let sessionId = localStorage.getItem("dietetyk_session_id") || generateUUID();
let currentUserId = localStorage.getItem("dietetyk_user_id") || null;
let currentUserName = localStorage.getItem("dietetyk_user_name") || null;
let currentUserAvatar = localStorage.getItem("dietetyk_user_avatar") || null;
let _editingUserId = null;
let _addingNewUser = false;
let _settingsUsers = [];
let _settingsSection = 'ai'; // 'ai' | userId | 'new'

// ===== DOM =====
const chatMessages = document.getElementById("chat-messages");
const messageInput = document.getElementById("message-input");
const btnSend = document.getElementById("btn-send");
const btnMic = document.getElementById("btn-mic");
const btnTts = document.getElementById("btn-tts");
const btnTheme = document.getElementById("btn-theme");
const btnSettings = document.getElementById("btn-settings");
const recipesPanel = document.getElementById("recipes-panel");
const sessionRecipesEl = document.getElementById("session-recipes");
const grocyRecipesEl = document.getElementById("grocy-recipes");
const sessionsList = document.getElementById("sessions-list");
const btnNewChat = document.getElementById("btn-new-chat");

// ===== Stan =====
let ttsEnabled = false;
let isRecording = false;
let isStreaming = false;
let recognition = null;
let recipesPollingInterval = null;

// ===== UUID =====
function generateUUID() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

// ===== Motyw =====
function initTheme() {
  const themeKey = currentUserId ? `dietetyk_theme_${currentUserId}` : "dietetyk_theme";
  const saved = localStorage.getItem(themeKey) || localStorage.getItem("dietetyk_theme") || "dark";
  applyTheme(saved);
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  if (btnTheme) btnTheme.innerHTML = `<span class="mi">${theme === "dark" ? "light_mode" : "dark_mode"}</span>`;
  btnTheme.title = theme === "dark" ? "Włącz jasny motyw" : "Włącz ciemny motyw";
  const toggle = document.getElementById("toggle-theme");
  if (toggle) toggle.checked = theme === "light";
  const themeKey = currentUserId ? `dietetyk_theme_${currentUserId}` : "dietetyk_theme";
  localStorage.setItem(themeKey, theme);
  localStorage.setItem("dietetyk_theme", theme); // fallback globalny
}

btnTheme.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  applyTheme(current === "dark" ? "light" : "dark");
});

// ===== Markdown =====
if (typeof marked !== "undefined") {
  marked.setOptions({ breaks: true, gfm: true });
}

function renderMarkdown(text) {
  if (typeof marked === "undefined") return escapeHtml(text);
  const raw = marked.parse(text);
  if (typeof DOMPurify !== "undefined") return DOMPurify.sanitize(raw);
  return raw;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ===== Wiadomości =====
function appendMessage(role, contentHtml, isHtml = false) {
  if (!chatMessages) return null;
  const msg = document.createElement("div");
  msg.className = `message ${role === "user" ? "user-message" : "assistant-message"}`;
  const contentDiv = document.createElement("div");
  contentDiv.className = "message-content";
  if (isHtml) {
    contentDiv.innerHTML = contentHtml;
  } else {
    contentDiv.textContent = contentHtml;
  }
  msg.appendChild(contentDiv);
  chatMessages.appendChild(msg);
  scrollToBottom();
  return contentDiv;
}

function appendTypingIndicator() {
  if (!chatMessages) return;
  const el = document.createElement("div");
  el.className = "message assistant-message";
  el.id = "typing-indicator";
  el.innerHTML = `
    <div class="typing-indicator">
      <div class="typing-dots">
        <span></span><span></span><span></span>
        <span style="width:auto;height:auto;background:none;border-radius:0;animation:none;margin-left:6px">Dietetyk pisze...</span>
      </div>
    </div>`;
  chatMessages.appendChild(el);
  scrollToBottom();
}

function removeTypingIndicator() {
  const el = document.getElementById("typing-indicator");
  if (el) el.remove();
}

function appendToolCall(toolName) {
  if (!chatMessages) return;
  const names = {
    get_recipes: "Sprawdzam przepisy",
    get_stock: "Sprawdzam spiżarnię",
    get_products: "Sprawdzam produkty",
    save_recipe: "Zapisuję przepis",
    add_ingredient_to_recipe: "Dodaję składnik",
    add_to_shopping_list: "Dodaję do listy zakupów",
    get_shopping_list: "Pobieram listę zakupów",
    get_meal_plan: "Sprawdzam plan posiłków",
    save_meal_plan_entry: "Zapisuję plan posiłków",
    update_memory: "Zapamiętuje informację",
    get_memory: "Wczytuję pamięć",
    get_recipe_details: "Pobieram szczegóły przepisu",
  };
  const label = names[toolName] || toolName;
  const el = document.createElement("div");
  el.className = "message assistant-message";
  el.innerHTML = `<div class="tool-call-indicator"><span class="mi mi-sm">settings</span> ${label}...</div>`;
  const typing = document.getElementById("typing-indicator");
  if (typing) {
    chatMessages.insertBefore(el, typing);
  } else {
    chatMessages.appendChild(el);
  }
  scrollToBottom();
}

function scrollToBottom() {
  if (!chatMessages) return;
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ===== Wyślij wiadomość =====
async function sendMessage() {
  const text = messageInput.value.trim();
  if (!text || isStreaming) return;

  appendMessage("user", text);
  messageInput.value = "";
  autoResizeTextarea();

  isStreaming = true;
  btnSend.disabled = true;
  appendTypingIndicator();

  let contentDiv = null;
  let fullText = "";

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        session_id: sessionId,
        user_id: currentUserId || null,
      }),
    });

    if (!response.ok) {
      removeTypingIndicator();
      appendMessage("assistant", "Błąd połączenia z serwerem. Spróbuj ponownie.");
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const dataStr = line.slice(6).trim();
        if (!dataStr) continue;

        let data;
        try {
          data = JSON.parse(dataStr);
        } catch {
          continue;
        }

        if (data.tool_call) {
          appendToolCall(data.tool_call);
        } else if (data.recipe_saved) {
          refreshRecipesPanel();
        } else if (data.chunk) {
          if (!contentDiv) {
            removeTypingIndicator();
            const msgEl = document.createElement("div");
            msgEl.className = "message assistant-message";
            contentDiv = document.createElement("div");
            contentDiv.className = "message-content";
            msgEl.appendChild(contentDiv);
            chatMessages.appendChild(msgEl);
          }
          fullText += data.chunk;
          contentDiv.innerHTML = renderMarkdown(fullText);
          scrollToBottom();
        } else if (data.error) {
          removeTypingIndicator();
          appendMessage("assistant", `⚠️ ${data.error}`);
        } else if (data.done) {
          removeTypingIndicator();
          if (ttsEnabled && fullText) speakText(fullText);
          loadSessions();
        }
      }
    }
  } catch {
    removeTypingIndicator();
    appendMessage("assistant", "Błąd połączenia. Sprawdź czy serwer działa.");
  } finally {
    isStreaming = false;
    btnSend.disabled = false;
    messageInput.focus();
  }
}

// ===== Input =====
messageInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

messageInput?.addEventListener("input", autoResizeTextarea);

function autoResizeTextarea() {
  if (!messageInput) return;
  messageInput.style.height = "auto";
  messageInput.style.height = Math.min(messageInput.scrollHeight, 150) + "px";
}

// ===== Czyszczenie wklejanego tekstu (Google Docs itp.) =====
function cleanPastedText(text) {
  return text
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    // Zamień tabulatory na spację
    .replace(/\t/g, " ")
    // Usuń znaki kontrolne (poza \n)
    .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, "")
    // Wielokrotne spacje → jedna (ale NIE newline)
    .replace(/[ ]{2,}/g, " ")
    // Max 2 kolejne puste linie (3+ newline → 2)
    .replace(/\n{3,}/g, "\n\n")
    // Trim każdej linii (usuń spacje na początku/końcu linii)
    .split("\n").map((l) => l.trim()).join("\n")
    .trim();
}

messageInput?.addEventListener("paste", (e) => {
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData("text/plain");
  const cleaned = cleanPastedText(text);
  const start = messageInput.selectionStart;
  const end = messageInput.selectionEnd;
  const current = messageInput.value;
  messageInput.value = current.slice(0, start) + cleaned + current.slice(end);
  messageInput.selectionStart = messageInput.selectionEnd = start + cleaned.length;
  autoResizeTextarea();
});

btnSend?.addEventListener("click", sendMessage);

// ===== STT =====
function setupSpeechRecognition() {
  if (!btnMic) return;
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    btnMic.style.display = "none";
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = "pl-PL";
  recognition.continuous = false;
  recognition.interimResults = false;

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    messageInput.value += (messageInput.value ? " " : "") + transcript;
    autoResizeTextarea();
  };

  recognition.onend = () => {
    isRecording = false;
    btnMic.classList.remove("recording");
    btnMic.title = "Dyktuj";
  };

  recognition.onerror = () => {
    isRecording = false;
    btnMic.classList.remove("recording");
  };

  btnMic.addEventListener("click", () => {
    if (!recognition) return;
    if (isRecording) {
      recognition.stop();
    } else {
      recognition.start();
      isRecording = true;
      btnMic.classList.add("recording");
      btnMic.title = "Zatrzymaj dyktowanie";
    }
  });
}

// ===== TTS =====
function setupTTS() {
  if (!btnTts) return;
  if (!("speechSynthesis" in window)) {
    btnTts.style.display = "none";
    return;
  }

  btnTts.addEventListener("click", () => {
    ttsEnabled = !ttsEnabled;
    btnTts.classList.toggle("active", ttsEnabled);
    btnTts.title = ttsEnabled ? "Wyłącz odczyt głosowy" : "Czytaj głośno";
    if (!ttsEnabled) window.speechSynthesis.cancel();
  });
}

function speakText(text) {
  if (!ttsEnabled || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const plain = text
    .replace(/#{1,6}\s+/g, "")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/`(.+?)`/g, "$1")
    .replace(/\[(.+?)\]\(.+?\)/g, "$1")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/\n{2,}/g, ". ")
    .trim();
  if (!plain) return;
  const utterance = new SpeechSynthesisUtterance(plain);
  utterance.lang = "pl-PL";
  const voices = window.speechSynthesis.getVoices();
  const polishVoice = voices.find((v) => v.lang.startsWith("pl") || v.name.toLowerCase().includes("polish"));
  if (polishVoice) utterance.voice = polishVoice;
  utterance.rate = 0.9;
  window.speechSynthesis.speak(utterance);
}

// ===== Panel przepisów =====

// ===== v9: Spiżarnia — wszystkie produkty, filtry, oznaczenie braków =====

let _pantryFilter = 'all'; // 'all' | 'have' | 'missing'
let _recipeFilter = 'all'; // 'all' | 'sniadanie' | 'obiad' | 'kolacja' | 'przekaska'
let _recipeSort = 'name'; // 'name' | 'type' | 'author'

function getStep(unit) {
  if (!unit) return 1;
  const u = unit.toLowerCase();
  if (u === 'ml') return 1;
  if (u === 'lyzka' || u === 'łyżka' || u === 'lyzeczka' || u === 'łyżeczka') return 0.5;
  if (u === 'szt.' || u === 'szt' || u === 'piece' || u === 'pieces' || u === 'pack' || u === 'packs') return 1;
  return 0.1;
}

function renderPantryItem(item) {
  const cls = item.in_stock ? '' : ' out-of-stock';
  const step = getStep(item.unit);
  const isInfinite = item.in_stock && item.amount >= 999;

  const amountDisplay = item.in_stock
    ? `<div class="qty-control">
         <button class="qty-btn qty-minus${isInfinite ? ' hidden' : ''}" onclick="adjustPantryQty(${item.product_id}, ${item.amount}, ${step}, -1, '${escapeHtml(item.unit)}')">−</button>
         <span class="qty-value${isInfinite ? ' qty-infinite' : ''}">${isInfinite ? '∞' : item.amount + ' ' + escapeHtml(item.unit)}</span>
         <button class="qty-btn qty-plus${isInfinite ? ' hidden' : ''}" onclick="adjustPantryQty(${item.product_id}, ${item.amount}, ${step}, 1, '${escapeHtml(item.unit)}')">+</button>
       </div>`
    : `<button class="qty-btn qty-plus qty-add" onclick="quickAddToPantry(${item.product_id}, '${escapeHtml(item.name)}', '${escapeHtml(item.unit)}')">+ Dodaj</button>`;

  const infiniteBtn = item.in_stock
    ? `<button class="btn-infinite${isInfinite ? ' active' : ''}" title="${isInfinite ? 'Ustaw normalną ilość' : 'Ustaw jako zawsze dostępny'}" onclick="toggleInfinite(${item.product_id}, ${isInfinite})">∞</button>`
    : '';

  return `
    <div class="list-item${cls}" data-product-id="${item.product_id}">
      <span class="item-name">${escapeHtml(item.name)}</span>
      ${amountDisplay}
      <div class="item-actions">
        ${infiniteBtn}
        <button class="btn-item-delete" onclick="deletePantryItem(${item.product_id}, '${escapeHtml(item.name)}')"><span class="mi mi-sm">delete_outline</span></button>
      </div>
    </div>`;
}

async function adjustPantryQty(productId, currentAmount, step, direction, unit) {
  const newAmount = Math.max(0, currentAmount + step * direction);
  try {
    await fetch(`/api/pantry/${productId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount: newAmount }),
    });
    await loadPantry();
  } catch { /* silent */ }
}

async function quickAddToPantry(productId, name, unit) {
  try {
    await fetch('/api/pantry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_name: name, amount: 1, unit }),
    });
    await loadPantry();
  } catch { /* silent */ }
}


async function addMissingToShopping(grocyId, btn) {
  if (!grocyId) return;
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class=\mi mi-sm\>hourglass_empty</span>';
  try {
    const res = await fetch(`/api/recipes/${grocyId}/add-missing-to-shopping`, { method: 'POST' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    const added = (data.added || []).map(i => i.name);
    const have = data.already_have || [];
    const msg = added.length
      ? 'Dodano do zakupów: ' + added.join(', ') + (have.length ? '. Masz już: ' + have.join(', ') : '')
      : (have.length ? 'Masz już wszystkie składniki!' : 'Brak składników do dodania');
    btn.innerHTML = '<span class=\mi mi-sm\>check</span>';
    setTimeout(() => { btn.innerHTML = orig; btn.disabled = false; }, 2500);
    if (document.getElementById('chat-messages') && document.getElementById('chat-messages').style.display !== 'none') {
      appendMessage('assistant', msg);
    }
    if (typeof loadShoppingList === 'function') loadShoppingList();
  } catch {
    btn.innerHTML = orig;
    btn.disabled = false;
  }
}
async function toggleInfinite(productId, isCurrentlyInfinite) {
  const newAmount = isCurrentlyInfinite ? 1 : 9999;
  try {
    await fetch(`/api/pantry/${productId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount: newAmount }),
    });
    await loadPantry();
  } catch { /* silent */ }
}

function renderPantryList(data, filter, query) {
  let filtered = data;
  if (filter === 'have') filtered = data.filter(i => i.in_stock);
  if (filter === 'missing') filtered = data.filter(i => !i.in_stock);
  if (query) filtered = filtered.filter(i => i.name.toLowerCase().includes(query.toLowerCase()));
  return filtered;
}

let _pantryData = [];
let _pantryDebounceTimer = null;
let _pantrySelectedProductId = null;

async function loadPantry() {
  const listEl = document.getElementById('pantry-list');
  if (!listEl) return;

  listEl.innerHTML = `<p class="empty-state">${t('common.loading')}</p>`;
  try {
    const res = await fetch('/api/pantry');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    _pantryData = await res.json();
  } catch {
    listEl.innerHTML = `<p class="empty-state">${t('common.error_generic')}</p>`;
    return;
  }

  renderCurrentPantry();
}

function renderCurrentPantry() {
  const listEl = document.getElementById('pantry-list');
  if (!listEl || !_pantryData.length) return;
  const query = (document.getElementById('pantry-search') || {}).value || '';
  const filtered = renderPantryList(_pantryData, _pantryFilter, query);
  if (!filtered.length) {
    listEl.innerHTML = `<p class="empty-state">${t('pantry.empty')}</p>`;
    return;
  }
  listEl.innerHTML = filtered.map(renderPantryItem).join('');
}

function startPantryEdit(btn, productId, currentAmount, unit) {
  const item = btn.closest('.list-item') || btn.closest('.pantry-item');
  if (!item) return;
  const qtyControl = item.querySelector('.qty-control');
  const actionsDiv = item.querySelector('.item-actions');

  if (qtyControl) {
    qtyControl.innerHTML = `
      <input type="number" class="pantry-inline-input" value="${currentAmount}" min="0" step="0.1" style="width:60px" />
      <span style="margin-left:2px">${escapeHtml(unit)}</span>
    `;
  }
  if (actionsDiv) {
    actionsDiv.innerHTML = `
      <button class="qty-btn" onclick="savePantryEdit(this, ${productId})"><span class="mi mi-sm">check</span></button>
      <button class="qty-btn" onclick="loadPantry()"><span class="mi mi-sm">close</span></button>
    `;
  }
}

async function savePantryEdit(btn, productId) {
  const item = btn.closest('.list-item') || btn.closest('.pantry-item');
  if (!item) return;
  const input = item.querySelector('.pantry-inline-input');
  const amount = parseFloat(input.value);
  if (isNaN(amount) || amount < 0) return;

  try {
    btn.disabled = true;
    const res = await fetch(`/api/pantry/${productId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
    if (res.ok) {
      await loadPantry();
    }
  } catch { /* silent */ } finally {
    btn.disabled = false;
  }
}

async function deletePantryItem(productId, name) {
  if (!productId) return;
  try {
    const res = await fetch(`/api/pantry/${productId}`, { method: 'DELETE' });
    if (res.ok) {
      await loadPantry();
    }
  } catch { /* silent */ }
}

function initPantry() {
  const searchEl = document.getElementById('pantry-search');
  const btnRefresh = document.getElementById('btn-pantry-refresh');
  const nameInput = document.getElementById('pantry-add-name');
  const btnAdd = document.getElementById('btn-pantry-add');
  const suggestionsEl = document.getElementById('pantry-suggestions');

  if (searchEl) {
    searchEl.addEventListener('input', () => renderCurrentPantry());
  }

  if (btnRefresh) {
    btnRefresh.addEventListener('click', () => loadPantry());
  }

  // v9: Filter buttons
  document.querySelectorAll('.pantry-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.pantry-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _pantryFilter = btn.dataset.filter;
      renderCurrentPantry();
    });
  });

  if (!nameInput) return;

  // Sync step attribute on amount input when unit changes
  const pantryUnitEl = document.getElementById('pantry-add-unit');
  const pantryAmountEl = document.getElementById('pantry-add-amount');
  function syncPantryStep() {
    if (pantryAmountEl && pantryUnitEl) {
      pantryAmountEl.step = getStep(pantryUnitEl.value);
    }
  }
  if (pantryUnitEl) pantryUnitEl.addEventListener('change', syncPantryStep);

  nameInput.addEventListener('input', () => {
    clearTimeout(_pantryDebounceTimer);
    _pantrySelectedProductId = null;
    const q = nameInput.value.trim();
    if (q.length < 2) { hidePantrySuggestions(); return; }
    _pantryDebounceTimer = setTimeout(() => fetchPantrySuggestions(q), 300);
  });

  nameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hidePantrySuggestions();
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#pantry-add-name') && !e.target.closest('#pantry-suggestions')) hidePantrySuggestions();
  });

  if (btnAdd) btnAdd.addEventListener('click', addPantryItem);

  async function fetchPantrySuggestions(q) {
    if (!suggestionsEl) return;
    try {
      const res = await fetch(`/api/products/search?q=${encodeURIComponent(q)}`);
      const items = await res.json();
      if (!items.length) { hidePantrySuggestions(); return; }
      suggestionsEl.innerHTML = items.map(it =>
        `<div class="suggestion-item" data-id="${it.id}" data-name="${escapeHtml(it.name)}">${escapeHtml(it.name)}</div>`
      ).join('');
      suggestionsEl.classList.remove('hidden');
      suggestionsEl.querySelectorAll('.suggestion-item').forEach(el => {
        el.addEventListener('click', () => {
          nameInput.value = el.dataset.name;
          _pantrySelectedProductId = el.dataset.id;
          hidePantrySuggestions();
        });
      });
    } catch { hidePantrySuggestions(); }
  }

  function hidePantrySuggestions() {
    if (suggestionsEl) suggestionsEl.classList.add('hidden');
  }

  async function addPantryItem() {
    const name = nameInput.value.trim();
    const amountEl = document.getElementById('pantry-add-amount');
    const unitEl = document.getElementById('pantry-add-unit');
    const amount = parseFloat(amountEl ? amountEl.value : 1) || 1;
    const unit = unitEl ? unitEl.value : 'szt.';
    if (!name) return;
    try {
      btnAdd.disabled = true;
      const res = await fetch('/api/pantry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_name: name, amount, unit }),
      });
      if (res.ok) {
        nameInput.value = '';
        if (amountEl) amountEl.value = 1;
        _pantrySelectedProductId = null;
        await loadPantry();
      }
    } catch { /* silent */ } finally {
      btnAdd.disabled = false;
    }
  }
}

window.startPantryEdit = startPantryEdit;
window.savePantryEdit = savePantryEdit;
window.deletePantryItem = deletePantryItem;
window.adjustPantryQty = adjustPantryQty;
window.quickAddToPantry = quickAddToPantry;

// ===== v11: Lista zakupów — filtry, +/- qty, natychmiastowe odświeżanie =====

let _shoppingData = [];
let _shoppingFilter = 'all'; // 'all' | 'pending' | 'done'

function renderShoppingItem(item) {
  const doneCls = item.done ? ' done' : '';
  const step = getStep(item.unit);
  return `
    <div class="list-item${doneCls}" data-id="${item.id}">
      <button class="done-btn${item.done ? ' checked' : ''}" onclick="toggleShoppingDone(${item.id}, ${!item.done})"><span class="mi mi-sm">${item.done ? 'check_circle' : 'radio_button_unchecked'}</span></button>
      <span class="item-name">${escapeHtml(item.name)}</span>
      <div class="qty-control">
        <button class="qty-btn qty-minus" onclick="adjustShoppingQty(${item.id}, ${item.amount}, ${step}, -1)">−</button>
        <span class="qty-value">${item.amount} ${escapeHtml(item.unit || '')}</span>
        <button class="qty-btn qty-plus" onclick="adjustShoppingQty(${item.id}, ${item.amount}, ${step}, 1)">+</button>
      </div>
      <div class="item-actions">
        <button class="btn-item-delete" onclick="deleteShoppingItem(${item.id}, '${escapeHtml(item.name)}')"><span class="mi mi-sm">delete_outline</span></button>
      </div>
    </div>`;
}

function renderCurrentShoppingList() {
  const listEl = document.getElementById('shopping-list');
  if (!listEl) return;
  const query = (document.getElementById('shopping-search') || {}).value || '';
  let filtered = _shoppingData;
  if (_shoppingFilter === 'pending') filtered = filtered.filter(i => !i.done);
  if (_shoppingFilter === 'done') filtered = filtered.filter(i => i.done);
  if (query) filtered = filtered.filter(i => i.name.toLowerCase().includes(query.toLowerCase()));
  if (!filtered.length) {
    listEl.innerHTML = `<p class="empty-state">${t('shopping.empty')}</p>`;
    return;
  }
  listEl.innerHTML = filtered.map(renderShoppingItem).join('');
}

async function loadShoppingList() {
  const listEl = document.getElementById('shopping-list');
  if (!listEl) return;
  try {
    const res = await fetch('/api/shopping-list');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    _shoppingData = await res.json();
  } catch (e) {
    listEl.innerHTML = `<p class="empty-state">${t('common.error_generic')}</p>`;
    return;
  }
  renderCurrentShoppingList();
}

async function toggleShoppingDone(itemId, done) {
  try {
    const res = await fetch(`/api/shopping-list/${itemId}/done`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ done }),
    });
    if (res.ok) {
      await loadShoppingList();
    }
  } catch { /* silent */ }
}

async function adjustShoppingQty(itemId, currentAmount, step, direction) {
  const newAmount = Math.max(0, currentAmount + step * direction);
  try {
    await fetch(`/api/shopping-list/${itemId}/amount`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount: newAmount }),
    });
    await loadShoppingList();
  } catch { /* silent */ }
}

window.toggleShoppingDone = toggleShoppingDone;
window.adjustShoppingQty = adjustShoppingQty;

// ===== v7: Przepisy — edycja inline =====

function parseRecipeSteps(desc) {
  if (!desc) return '';
  // Spróbuj wykryć numerowane kroki: "1. tekst 2. tekst" lub "1) tekst"
  const stepPattern = /(?:^|\s)(\d+)[.)]\s+/g;
  const matches = [...desc.matchAll(stepPattern)];

  if (matches.length < 2) {
    // Brak kroków numerowanych — zwykły paragraf
    const hasHtml = /<br|<p|<\/p>/i.test(desc);
    const sanitized = hasHtml
      ? (typeof DOMPurify !== "undefined" ? DOMPurify.sanitize(desc) : escapeHtml(desc))
      : escapeHtml(desc).replace(/\n/g, '<br>');
    return `<h4>Sposób przygotowania</h4><div class="recipe-description">${sanitized}</div>`;
  }

  // Podziel tekst na kroki
  const steps = [];
  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].index + matches[i][0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index : desc.length;
    const stepText = desc.slice(start, end).trim();
    if (stepText) steps.push(stepText);
  }

  if (!steps.length) {
    return `<h4>Sposób przygotowania</h4><div class="recipe-description">${escapeHtml(desc)}</div>`;
  }

  const stepsHtml = steps.map((step, i) =>
    `<div class="recipe-step">
      <div class="recipe-step-num">${i + 1}</div>
      <div class="recipe-step-text">${escapeHtml(step)}</div>
    </div>`
  ).join('');

  return `<h4>Sposób przygotowania</h4><div class="recipe-steps">${stepsHtml}</div>`;
}

window.toggleIngredients = function(cardId) {
  const list = document.getElementById(cardId + '-ing-list');
  const icon = document.querySelector('#' + cardId + '-ing-acc .ing-toggle-icon');
  if (!list) return;
  list.classList.toggle('hidden');
  if (icon) icon.textContent = list.classList.contains('hidden') ? 'chevron_right' : 'expand_more';
};

function parseNutrition(desc) {
  if (!desc) return null;
  const match = desc.match(/---NUTRITION---(?:<br\s*\/?>|\n)cal:([\d.—]+)\|prot:([\d.—]+)\|fat:([\d.—]+)\|carbs:([\d.—]+)\|fiber:([\d.—]+)/);
  if (!match) return null;
  return { calories: match[1], protein: match[2], fat: match[3], carbs: match[4], fiber: match[5] };
}

function stripNutritionBlock(desc) {
  if (!desc) return desc;
  desc = desc.replace(/<p>---NUTRITION---<br\s*\/?>.*?<\/p>/s, '');
  desc = desc.replace(/\n*---NUTRITION---[\s\S]*/g, '');
  return desc.trim();
}

function parseMeta(desc) {
  if (!desc) return {};
  const match = desc.match(/---META---(?:<br\s*\/?>|\n)([^\n<]+)/);
  if (!match) return {};
  const result = {};
  match[1].split('|').forEach(pair => {
    const [k, ...v] = pair.split(':');
    if (k) result[k.trim()] = v.join(':').trim();
  });
  return result;
}

function stripMetaBlock(desc) {
  if (!desc) return desc;
  desc = desc.replace(/<p>---META---<br\s*\/?>.*?<\/p>/s, '');
  desc = desc.replace(/\n*---META---[\s\S]*/g, '');
  return desc.trim();
}

function renderRecipeCard(recipe, isSession) {
  const mealTypesMap = { breakfast: 'Śniadanie', lunch: 'Obiad', dinner: 'Kolacja', snack: 'Przekąska', '': '' };
  const mealLabel = mealTypesMap[recipe.meal_type] || '';
  const calories = recipe.calories ? `${recipe.calories} kcal` : '';
  const recipeMeta = [mealLabel, calories].filter(Boolean).join(' · ');

  const cardId = `recipe-${isSession ? 's' : 'g'}-${recipe.id}`;

  const ingredientsHtml = recipe.ingredients && recipe.ingredients.length
    ? `<div class="recipe-ingredients-accordion" id="${cardId}-ing-acc">
        <button class="recipe-ing-toggle" onclick="toggleIngredients('${cardId}')">
          <span class="mi mi-sm ing-toggle-icon">chevron_right</span> SKŁADNIKI
          <span class="ing-count">(${recipe.ingredients.length})</span>
        </button>
        <div class="recipe-ing-list hidden" id="${cardId}-ing-list" data-card-id="${cardId}">
          <ul>${recipe.ingredients.map((ing) => {
            if (typeof ing === 'string') return `<li>${escapeHtml(ing)}</li>`;
            const amt = ing.amount ? `${ing.amount} ${ing.unit || ''}`.trim() : '';
            const name = ing.name || ing.product_name || JSON.stringify(ing);
            const posId = ing.pos_id;
            const deleteBtn = posId && !isSession
              ? `<button class="btn-delete-ingredient" onclick="deleteRecipeIngredient(${recipe.id}, ${posId}, event)" title="Usuń składnik"><span class="mi mi-sm">delete_outline</span></button>`
              : '';
            return `<li>${escapeHtml(name)}${amt ? ' — ' + escapeHtml(amt) : ''} ${deleteBtn}</li>`;
          }).join('')}</ul>
        </div>
      </div>` : '';

  const nutrition = parseNutrition(recipe.description);
  const metaData = parseMeta(recipe.description);
  const mealTypes = (metaData.type || recipe.meal_type || '').split(',').map(t => t.trim()).filter(Boolean);
  const authorName = metaData.author || '';
  const authorAvatar = metaData.avatar || '';
  const cleanDesc = stripMetaBlock(stripNutritionBlock(recipe.description));
  const descHtml = cleanDesc ? parseRecipeSteps(cleanDesc) : '';

  const grocy_id = !isSession && recipe.id ? recipe.id : (recipe.grocy_id || null);
  const deleteBtn = grocy_id
    ? `<button class="icon-btn" onclick="deleteRecipe(${grocy_id}, '${escapeHtml(recipe.name)}', '${cardId}')" title="Usuń przepis"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg></button>`
    : '';

  const editBtn = grocy_id && !isSession
    ? `<button class="icon-btn" onclick="openRecipeEditModal(${grocy_id})" title="Edytuj przepis"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg></button>`
    : '';

  const nutritionHtml = nutrition ? `
  <div class="recipe-nutrition">
    <span class="recipe-nutrition-item" title="Kalorie">🔥 ${nutrition.calories} kcal</span>
    <span class="recipe-nutrition-item" title="Białko">🥩 ${nutrition.protein}g</span>
    <span class="recipe-nutrition-item" title="Tłuszcz">🫒 ${nutrition.fat}g</span>
    <span class="recipe-nutrition-item" title="Węglowodany">🌾 ${nutrition.carbs}g</span>
    <span class="recipe-nutrition-item" title="Błonnik">🌿 ${nutrition.fiber}g</span>
    <span style="font-size:0.7rem;color:var(--text-muted)">/ porcja</span>
  </div>` : '';

  const mealTypeIcons = { sniadanie: 'wb_sunny', obiad: 'restaurant', kolacja: 'nightlight_round', przekaska: 'apple' };
  const mealTypeNames = { sniadanie: 'Śniadanie', obiad: 'Obiad', kolacja: 'Kolacja', przekaska: 'Przekąska' };
  // Generuj tagi dla WSZYSTKICH typów
  const metaTagHtml = mealTypes.map(t =>
    `<span class="recipe-tag recipe-tag-${t}"><span class="mi mi-sm">${mealTypeIcons[t] || 'label'}</span> ${mealTypeNames[t] || t}</span>`
  ).join('');
  const authorHtml = authorName
    ? `<span class="recipe-author"><span class="mi mi-sm">person</span> ${escapeHtml(authorName)}</span>`
    : '';

  return `
    <div class="recipe-card" id="${cardId}" data-recipe-id="${cardId}" data-grocy-id="${grocy_id || ''}">
      <div class="recipe-card-header" onclick="toggleRecipeCard('${cardId}')">
        <div class="recipe-card-info">
            <div class="recipe-name" id="${cardId}-name">${escapeHtml(recipe.name)}</div>
            <div class="recipe-card-bottom">
              <div class="recipe-card-tags">${metaTagHtml}</div>
              ${authorHtml ? `<div class="recipe-card-author">${authorHtml}</div>` : ''}
            </div>
            ${recipeMeta ? `<div class="recipe-meta">${escapeHtml(recipeMeta)}</div>` : ''}
        </div>
        <div class="recipe-card-actions" onclick="event.stopPropagation()">
          ${editBtn}
          ${grocy_id ? `<button class="icon-btn" onclick="addMissingToShopping(${grocy_id}, this)" title="Brakujące składniki do zakupów"><span class="mi mi-sm">add_shopping_cart</span></button>` : ''}
          ${deleteBtn}
        </div>
      </div>
      <div class="recipe-card-body" id="${cardId}-body">
        <div id="${cardId}-view" class="recipe-view">

          ${nutritionHtml}
          ${ingredientsHtml}

          <div class="recipe-description-pane" id="${cardId}-desc">
            ${descHtml}
            ${!ingredientsHtml && !descHtml ? '<p style="color:var(--text-muted);font-style:italic">Brak szczegółów</p>' : ''}
          </div>

        </div>

      </div>
    </div>`;
}

async function startRecipeEdit(cardId, grocyId) {
  return openRecipeEditModal(grocyId);
}

async function saveRecipeEdit(cardId, grocyId) {
  const nameInput = document.getElementById(`${cardId}-edit-name`);
  const descInput = document.getElementById(`${cardId}-edit-desc`);
  if (!nameInput) return;

  const name = nameInput.value.trim();
  const description = descInput ? descInput.value.trim() : '';

  try {
    const res = await fetch(`/api/recipes/${grocyId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    });
    if (res.ok) {
      await refreshRecipesPanel();
    }
  } catch { /* silent */ }
}

function cancelRecipeEdit(cardId) {
  const viewDiv = document.getElementById(`${cardId}-view`);
  const editDiv = document.getElementById(`${cardId}-edit`);
  if (viewDiv) viewDiv.style.display = 'block';
  if (editDiv) editDiv.style.display = 'none';
}

function initIngredientAutocomplete(cardId) {
  const form = document.getElementById(`${cardId}-add-ing`);
  if (!form || form.dataset.autocompleteInit) return;
  form.dataset.autocompleteInit = '1';

  const nameEl = form.querySelector('.add-ing-name');
  const suggestionsEl = form.querySelector('.add-ing-suggestions');
  const unitEl = form.querySelector('.add-ing-unit');
  const amountEl = form.querySelector('.add-ing-amount');
  if (!nameEl || !suggestionsEl) return;

  let debounceTimer = null;

  function hideSuggestions() {
    suggestionsEl.classList.add('hidden');
    suggestionsEl.innerHTML = '';
  }

  async function fetchIngSuggestions(q) {
    try {
      const res = await fetch(`/api/products/search?q=${encodeURIComponent(q)}`);
      const items = await res.json();
      if (!items.length) { hideSuggestions(); return; }
      suggestionsEl.innerHTML = items.map(it =>
        `<div class="suggestion-item" data-name="${escapeHtml(it.name)}" data-unit="${escapeHtml(it.unit || '')}">${escapeHtml(it.name)}</div>`
      ).join('');
      suggestionsEl.classList.remove('hidden');
      suggestionsEl.querySelectorAll('.suggestion-item').forEach(el => {
        el.addEventListener('mousedown', (e) => {
          e.preventDefault();
          nameEl.value = el.dataset.name;
          // Ustaw jednostkę jeśli produkt ma jednostkę
          if (unitEl && el.dataset.unit) {
            const unit = el.dataset.unit.toLowerCase();
            const opt = Array.from(unitEl.options).find(o => o.value.toLowerCase() === unit || o.text.toLowerCase() === unit);
            if (opt) unitEl.value = opt.value;
            if (amountEl) amountEl.step = getStep(unitEl.value);
          }
          hideSuggestions();
        });
      });
    } catch { hideSuggestions(); }
  }

  nameEl.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const q = nameEl.value.trim();
    if (q.length < 2) { hideSuggestions(); return; }
    debounceTimer = setTimeout(() => fetchIngSuggestions(q), 300);
  });

  nameEl.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hideSuggestions();
  });

  document.addEventListener('click', (e) => {
    if (!form.contains(e.target)) hideSuggestions();
  }, { capture: false });
}

function initAllIngredientAutocompletes() {
  document.querySelectorAll('.add-ingredient-form').forEach(form => {
    const cardId = form.id.replace('-add-ing', '');
    initIngredientAutocomplete(cardId);
  });
}

async function addRecipeIngredient(grocyId, cardId, event) {
  event.stopPropagation();
  const form = document.getElementById(`${cardId}-add-ing`);
  if (!form) return;

  const nameEl = form.querySelector('.add-ing-name');
  const amountEl = form.querySelector('.add-ing-amount');
  const unitEl = form.querySelector('.add-ing-unit');

  const name = nameEl ? nameEl.value.trim() : '';
  const amount = parseFloat(amountEl ? amountEl.value : 1) || 1;
  const unit = unitEl ? unitEl.value : 'szt.';
  if (!name) return;

  try {
    const res = await fetch(`/api/recipes/${grocyId}/ingredients`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_name: name, amount, unit }),
    });
    if (res.ok) {
      if (nameEl) nameEl.value = '';
      if (amountEl) amountEl.value = 1;
      await refreshRecipesPanel();
    }
  } catch { /* silent */ }
}

async function deleteRecipeIngredient(grocyId, posId, event) {
  event.stopPropagation();
  try {
    const res = await fetch(`/api/recipes/${grocyId}/ingredients/${posId}`, { method: 'DELETE' });
    if (res.ok) {
      await refreshRecipesPanel();
    }
  } catch { /* silent */ }
}


// ===== Modal edycji przepisu =====
let _editModalRecipeId = null;
let _editModalIngredients = []; // kopia składników do edycji
let _editModalIngDebounce = null;

async function openRecipeEditModal(grocyId) {
  try {
    const res = await fetch(`/api/recipes/${grocyId}`);
    if (!res.ok) return;
    const data = await res.json();

    _editModalRecipeId = grocyId;
    _editModalIngredients = (data.ingredients || []).map(ing => ({ ...ing }));

    document.getElementById('recipe-modal-name').value = data.name || '';
    const rawDescFull = (data.description || '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '').trim();
    const rawDesc = stripMetaBlock(stripNutritionBlock(rawDescFull));
    document.getElementById('recipe-modal-desc').value = rawDesc;

    const nutrition = parseNutrition(rawDescFull);
    document.getElementById('recipe-modal-cal').value = nutrition?.calories !== '—' ? nutrition?.calories || '' : '';
    document.getElementById('recipe-modal-prot').value = nutrition?.protein !== '—' ? nutrition?.protein || '' : '';
    document.getElementById('recipe-modal-fat').value = nutrition?.fat !== '—' ? nutrition?.fat || '' : '';
    document.getElementById('recipe-modal-carbs').value = nutrition?.carbs !== '—' ? nutrition?.carbs || '' : '';
    document.getElementById('recipe-modal-fiber').value = nutrition?.fiber !== '—' ? nutrition?.fiber || '' : '';

    // Ładuj meal_type (multi-select chips) i autora
    const metaData = parseMeta(rawDescFull);
    const selectedTypes = (metaData.type || '').split(',').map(t => t.trim()).filter(Boolean);
    document.querySelectorAll('#recipe-modal-meal-chips .meal-chip').forEach(chip => {
      chip.classList.toggle('active', selectedTypes.includes(chip.dataset.value));
    });

    const authorInput = document.getElementById('recipe-modal-author');
    if (authorInput) {
      authorInput.value = metaData.author || currentUserName || '';
    }

    renderModalIngredients();
    initModalIngAutocomplete();

    document.getElementById('recipe-edit-modal').classList.remove('hidden');
    document.getElementById('recipe-modal-name').focus();
  } catch { /* silent */ }
}

function renderModalIngredients() {
  const container = document.getElementById('recipe-modal-ingredients-list');
  if (!container) return;
  if (!_editModalIngredients.length) {
    container.innerHTML = `<p style="color:var(--text-muted);font-style:italic;font-size:0.85rem">${t('recipe_editor.ingredients_label')}</p>`;
    return;
  }
  container.innerHTML = _editModalIngredients.map((ing, idx) => {
    const units = ['szt.','g','ml','lyzka','lyzeczka','szczypta'];
    const unitOptions = units.map(u => `<option value="${u}" ${ing.unit === u ? 'selected' : ''}>${u === 'lyzka' ? 'łyżka' : u === 'lyzeczka' ? 'łyżeczka' : u}</option>`).join('');
    return `
      <div class="recipe-modal-ing-row" data-idx="${idx}">
        <span class="recipe-modal-ing-name">${escapeHtml(ing.product_name || ing.name || '')}</span>
        <input type="number" class="recipe-modal-ing-amount" value="${ing.amount || 1}" min="0" step="0.1"
          oninput="updateModalIng(${idx}, 'amount', this.value)" />
        <select class="recipe-modal-ing-unit-sel" onchange="updateModalIng(${idx}, 'unit', this.value)">
          ${unitOptions}
        </select>
        <button class="recipe-modal-ing-del" onclick="deleteModalIng(${idx})" title="Usuń składnik"><span class="mi mi-sm">delete_outline</span></button>
      </div>`;
  }).join('');
}

function updateModalIng(idx, field, value) {
  if (_editModalIngredients[idx]) {
    _editModalIngredients[idx][field] = field === 'amount' ? parseFloat(value) || 1 : value;
  }
}

function deleteModalIng(idx) {
  _editModalIngredients.splice(idx, 1);
  renderModalIngredients();
}

function initModalIngAutocomplete() {
  const nameEl = document.getElementById('recipe-modal-ing-name');
  const suggestionsEl = document.getElementById('recipe-modal-ing-suggestions');
  const unitEl = document.getElementById('recipe-modal-ing-unit');
  const amountEl = document.getElementById('recipe-modal-ing-amount');
  const addBtn = document.getElementById('recipe-modal-ing-add-btn');
  if (!nameEl || nameEl.dataset.init) return;
  nameEl.dataset.init = '1';

  nameEl.addEventListener('input', () => {
    clearTimeout(_editModalIngDebounce);
    const q = nameEl.value.trim();
    if (q.length < 2) { suggestionsEl.classList.add('hidden'); return; }
    _editModalIngDebounce = setTimeout(async () => {
      try {
        const res = await fetch(`/api/products/search?q=${encodeURIComponent(q)}`);
        const items = await res.json();
        if (!items.length) { suggestionsEl.classList.add('hidden'); return; }
        suggestionsEl.innerHTML = items.map(it =>
          `<div class="suggestion-item" data-name="${escapeHtml(it.name)}" data-unit="${escapeHtml(it.unit||'')}">${escapeHtml(it.name)}</div>`
        ).join('');
        suggestionsEl.classList.remove('hidden');
        suggestionsEl.querySelectorAll('.suggestion-item').forEach(el => {
          el.addEventListener('mousedown', e => {
            e.preventDefault();
            nameEl.value = el.dataset.name;
            if (unitEl && el.dataset.unit) {
              const u = el.dataset.unit.toLowerCase();
              const opt = Array.from(unitEl.options).find(o => o.value.toLowerCase() === u || o.text.toLowerCase() === u);
              if (opt) { unitEl.value = opt.value; if (amountEl) amountEl.step = getStep(unitEl.value); }
            }
            suggestionsEl.classList.add('hidden');
          });
        });
      } catch { suggestionsEl.classList.add('hidden'); }
    }, 300);
  });

  nameEl.addEventListener('keydown', e => { if (e.key === 'Escape') suggestionsEl.classList.add('hidden'); });

  if (addBtn) {
    addBtn.addEventListener('click', () => {
      const name = nameEl.value.trim();
      if (!name) return;
      const amount = parseFloat(amountEl ? amountEl.value : 1) || 1;
      const unit = unitEl ? unitEl.value : 'szt.';
      _editModalIngredients.push({ product_name: name, amount, unit, pos_id: null });
      renderModalIngredients();
      nameEl.value = '';
      if (amountEl) amountEl.value = 1;
      suggestionsEl.classList.add('hidden');
    });
  }
}

async function saveRecipeEditModal() {
  const name = document.getElementById('recipe-modal-name').value.trim();
  const description = document.getElementById('recipe-modal-desc').value.trim();
  if (!name || !_editModalRecipeId) return;

  const cal = document.getElementById('recipe-modal-cal').value;
  const prot = document.getElementById('recipe-modal-prot').value;
  const fat = document.getElementById('recipe-modal-fat').value;
  const carbs = document.getElementById('recipe-modal-carbs').value;
  const fiber = document.getElementById('recipe-modal-fiber').value;
  const selectedChips = document.querySelectorAll('#recipe-modal-meal-chips .meal-chip.active');
  const mealTypeVal = Array.from(selectedChips).map(c => c.dataset.value).join(',');
  const authorVal = (document.getElementById('recipe-modal-author') || {}).value || '';
  let descWithNutrition = description;
  if (cal || prot || fat || carbs || fiber) {
    descWithNutrition += `\n\n---NUTRITION---\ncal:${cal||'—'}|prot:${prot||'—'}|fat:${fat||'—'}|carbs:${carbs||'—'}|fiber:${fiber||'—'}`;
  }
  const authorToSave = authorVal || currentUserName || '';
  if (mealTypeVal || authorToSave) {
    const metaParts = [];
    if (mealTypeVal) metaParts.push(`type:${mealTypeVal}`);
    if (authorToSave) metaParts.push(`author:${authorToSave}`);
    if (currentUserAvatar) metaParts.push(`avatar:${currentUserAvatar}`);
    if (metaParts.length) descWithNutrition += `\n\n---META---\n${metaParts.join('|')}`;
  }

  const saveBtn = document.getElementById('recipe-modal-save-btn');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.innerHTML = `<span class="mi mi-sm">hourglass_empty</span> ${t('common.loading')}`; }

  try {
    // 1. Zapisz nazwę i opis
    const resRecipe = await fetch(`/api/recipes/${_editModalRecipeId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description: descWithNutrition }),
    });
    if (!resRecipe.ok) throw new Error('Błąd zapisu przepisu');

    // 2. Pobierz aktualne składniki z serwera żeby znać pos_id
    const resData = await fetch(`/api/recipes/${_editModalRecipeId}`);
    const serverData = resData.ok ? await resData.json() : { ingredients: [] };
    const serverIngs = serverData.ingredients || [];

    // 3. Usuń składniki których nie ma w _editModalIngredients (po pos_id)
    const keptPosIds = new Set(_editModalIngredients.filter(i => i.pos_id).map(i => String(i.pos_id)));
    for (const sIng of serverIngs) {
      if (sIng.pos_id && !keptPosIds.has(String(sIng.pos_id))) {
        await fetch(`/api/recipes/${_editModalRecipeId}/ingredients/${sIng.pos_id}`, { method: 'DELETE' });
      }
    }

    // 4. Dodaj nowe składniki (bez pos_id)
    for (const ing of _editModalIngredients) {
      if (!ing.pos_id) {
        await fetch(`/api/recipes/${_editModalRecipeId}/ingredients`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_name: ing.product_name || ing.name, amount: ing.amount, unit: ing.unit }),
        });
      }
    }

    closeRecipeEditModal();
    await refreshRecipesPanel();
  } catch (e) {
    alert(t('common.error_generic') + ' ' + e.message);
  } finally {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = `<span class="mi mi-sm">save</span> ${t('recipe_editor.save')}`; }
  }
}

function closeRecipeEditModal(event) {
  if (event && event.target !== document.getElementById('recipe-edit-modal')) return;
  document.getElementById('recipe-edit-modal').classList.add('hidden');
  _editModalRecipeId = null;
  _editModalIngredients = [];
  const nameEl = document.getElementById('recipe-modal-ing-name');
  if (nameEl) delete nameEl.dataset.init;
}

function toggleModalAccordion(openId, closeId) {
  const openEl = document.getElementById(openId);
  const closeEl = document.getElementById(closeId);
  if (!openEl) return;

  const openBody = openEl.querySelector('.recipe-modal-accordion-body');
  const openBtn = openEl.querySelector('.recipe-modal-accordion-btn');
  const openArrow = openEl.querySelector('.accordion-arrow');

  if (openBody.style.display === 'none') {
    // Otwórz ten
    openBody.style.display = '';
    openBtn.classList.add('active');
    if (openArrow) openArrow.textContent = 'expand_less';

    // Zamknij drugi
    if (closeEl) {
      const closeBody = closeEl.querySelector('.recipe-modal-accordion-body');
      const closeBtn = closeEl.querySelector('.recipe-modal-accordion-btn');
      const closeArrow = closeEl.querySelector('.accordion-arrow');
      if (closeBody) closeBody.style.display = 'none';
      if (closeBtn) closeBtn.classList.remove('active');
      if (closeArrow) closeArrow.textContent = 'expand_more';
    }
  }
}
window.toggleModalAccordion = toggleModalAccordion;

function toggleSettingsAccordion(openId, closeId) {
  const openEl = document.getElementById(openId);
  const closeEl = document.getElementById(closeId);
  if (!openEl) return;
  const openBody = openEl.querySelector('.settings-accordion-body');
  const openBtn = openEl.querySelector('.settings-accordion-btn');
  const openArrow = openEl.querySelector('.settings-acc-arrow');
  if (!openBody || openBody.style.display !== 'none') return; // już otwarty
  openBody.style.display = '';
  openBtn.classList.add('active');
  if (openArrow) openArrow.textContent = 'expand_less';
  if (closeEl) {
    const closeBody = closeEl.querySelector('.settings-accordion-body');
    const closeBtn = closeEl.querySelector('.settings-accordion-btn');
    const closeArrow = closeEl.querySelector('.settings-acc-arrow');
    if (closeBody) closeBody.style.display = 'none';
    if (closeBtn) closeBtn.classList.remove('active');
    if (closeArrow) closeArrow.textContent = 'expand_more';
  }
}
window.toggleSettingsAccordion = toggleSettingsAccordion;

window.openRecipeEditModal = openRecipeEditModal;
window.closeRecipeEditModal = closeRecipeEditModal;
window.saveRecipeEditModal = saveRecipeEditModal;
window.deleteModalIng = deleteModalIng;
window.updateModalIng = updateModalIng;

window.startRecipeEdit = startRecipeEdit;
window.saveRecipeEdit = saveRecipeEdit;
window.cancelRecipeEdit = cancelRecipeEdit;
window.addRecipeIngredient = addRecipeIngredient;
window.deleteRecipeIngredient = deleteRecipeIngredient;

function toggleRecipeCard(cardId) {
  const body = document.getElementById(`${cardId}-body`);
  if (body) body.classList.toggle("open");
}


async function refreshRecipesPanel() {
  try {
    // Zapamiętaj rozwinięte karty przed przebudowaniem listy
    const expanded = new Set(
      Array.from(document.querySelectorAll(".recipe-card-body.open"))
        .map((el) => el.id.replace("-body", ""))
    );

    // Zapamiętaj otwarte accordion składników (lista BEZ klasy "hidden")
    const openIngredients = new Set(
      Array.from(document.querySelectorAll(".recipe-ing-list:not(.hidden)"))
        .map((el) => el.dataset.cardId)
        .filter(Boolean)
    );

    const resp = await fetch(`/api/recipes-panel?session_id=${sessionId}`);
    if (!resp.ok) return;
    const data = await resp.json();

    if (data.session_recipes && data.session_recipes.length > 0) {
      sessionRecipesEl.innerHTML = data.session_recipes.map((r) => renderRecipeCard(r, true)).join("");
    } else {
      sessionRecipesEl.innerHTML = `<p class="empty-state">${t('recipes.empty')}</p>`;
    }

    if (data.grocy_recipes && data.grocy_recipes.length > 0) {
      let grocyRecipes = data.grocy_recipes || [];
      // Filtruj
      if (_recipeFilter !== 'all') {
        grocyRecipes = grocyRecipes.filter(r => {
          const rm = parseMeta(r.description || '');
          const t = rm.type || r.meal_type || '';
          return t === _recipeFilter;
        });
      }
      // Sortuj
      if (_recipeSort === 'name') {
        grocyRecipes = [...grocyRecipes].sort((a, b) => (a.name || '').localeCompare(b.name || '', 'pl'));
      } else if (_recipeSort === 'type') {
        const typeOrder = { sniadanie: 0, obiad: 1, kolacja: 2, przekaska: 3 };
        grocyRecipes = [...grocyRecipes].sort((a, b) => {
          const ma = parseMeta(a.description || ''); const mb = parseMeta(b.description || '');
          return (typeOrder[ma.type || a.meal_type || ''] ?? 9) - (typeOrder[mb.type || b.meal_type || ''] ?? 9);
        });
      } else if (_recipeSort === 'author') {
        grocyRecipes = [...grocyRecipes].sort((a, b) => {
          const ma = parseMeta(a.description || ''); const mb = parseMeta(b.description || '');
          return (ma.author || '').localeCompare(mb.author || '', 'pl');
        });
      }
      grocyRecipesEl.innerHTML = grocyRecipes.map((r) => renderRecipeCard(r, false)).join("");
    } else {
      grocyRecipesEl.innerHTML = `<p class="empty-state">${t('recipes.empty')}</p>`;
    }

    // Przywróć stan rozwinięcia kart
    expanded.forEach((cardId) => {
      const body = document.getElementById(`${cardId}-body`);
      if (body) body.classList.add("open");
    });

    // Inicjalizuj autocomplete dla nowo wyrenderowanych formularzy
    initAllIngredientAutocompletes();

    // Przywróć stan accordion składników
    openIngredients.forEach((cardId) => {
      const list = document.querySelector(`.recipe-ing-list[data-card-id="${cardId}"]`);
      const icon = document.querySelector(`#${cardId} .ing-toggle-icon`);
      if (list) {
        list.classList.remove("hidden");
        if (icon) icon.textContent = "expand_more";
      }
    });
  } catch { /* silent */ }
}

function startRecipesPolling() {
  refreshRecipesPanel();
}

// ===== Historia sesji =====
function formatSessionDate(isoString) {
  if (!isoString) return "";
  const d = new Date(isoString);
  const now = new Date();
  const diffDays = Math.floor((now - d) / 86400000);
  if (diffDays === 0) return "Dziś, " + d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  if (diffDays === 1) return "Wczoraj";
  if (diffDays < 7) return d.toLocaleDateString("pl-PL", { weekday: "long" });
  return d.toLocaleDateString("pl-PL", { day: "numeric", month: "short" });
}

async function loadSessions() {
  try {
    const userId = currentUserId || '';
    const res = await fetch(`/api/sessions${userId ? '?user_id=' + encodeURIComponent(userId) : ''}`);
    if (!res.ok) return;
    const sessions = await res.json();
    renderSessions(sessions);
  } catch { /* silent */ }
}

function renderSessions(sessions) {
  if (!sessionsList) return;
  if (!sessions || sessions.length === 0) {
    sessionsList.innerHTML = `<p class="empty-state">${t('chat.empty_state')}</p>`;
    return;
  }
  sessionsList.innerHTML = sessions.map((s) => `
    <div class="session-item ${s.id === sessionId ? "active" : ""}"
         data-id="${s.id}" onclick="loadSessionHistory('${s.id}')">
      <div class="session-info">
        <div class="session-title"><span class="mi mi-sm">chat_bubble_outline</span> ${DOMPurify ? DOMPurify.sanitize(s.title || t('chat.session_default')) : escapeHtml(s.title || t('chat.session_default'))}</div>
        <div class="session-meta">${formatSessionDate(s.updated_at)} · ${s.message_count || 0} wiad.</div>
      </div>
      <button class="session-delete" title="Usuń rozmowę"
              onclick="deleteSession(event, '${s.id}')"><span class="mi mi-sm">close</span></button>
    </div>
  `).join("");
}

async function loadSessionHistory(id) {
  if (id === sessionId) return;
  try {
    const res = await fetch(`/api/sessions/${id}`);
    if (!res.ok) return;
    const data = await res.json();

    sessionId = id;
    localStorage.setItem("dietetyk_session_id", sessionId);

    chatMessages.innerHTML = "";
    (data.messages || []).forEach((msg) => {
      if (msg.role === "assistant") {
        const el = document.createElement("div");
        el.className = "message assistant-message";
        const content = document.createElement("div");
        content.className = "message-content";
        content.innerHTML = renderMarkdown(msg.content || "");
        el.appendChild(content);
        chatMessages.appendChild(el);
      } else {
        appendMessage("user", msg.content || "");
      }
    });

    document.querySelectorAll(".session-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.id === id);
    });

    await refreshRecipesPanel();
  } catch { /* silent */ }
}

async function deleteSession(event, id) {
  event.stopPropagation();
  try {
    await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    if (id === sessionId) startNewChat();
    await loadSessions();
  } catch { /* silent */ }
}

// ===== Nowa sesja + wybór użytkownika =====
function startNewChat() {
  sessionId = generateUUID();
  localStorage.setItem("dietetyk_session_id", sessionId);

  chatMessages.innerHTML = `
    <div class="message assistant-message">
      <div class="message-content">
        <p>Cześć! 👋 Nowa rozmowa. Czym mogę Ci dziś pomóc?</p>
      </div>
    </div>`;

  document.querySelectorAll(".session-item").forEach((el) => el.classList.remove("active"));
  showUserSelectModal();
}

if (btnNewChat) {
  btnNewChat.addEventListener("click", () => {
    startNewChat();
    loadSessions();
  });
}

// ===== Header: aktywny użytkownik =====
function updateHeaderUser() {
  const headerUser = document.getElementById("header-user");
  const headerAvatar = document.getElementById("header-user-avatar");
  const headerName = document.getElementById("header-user-name");

  if (currentUserId && currentUserName) {
    headerUser.style.display = "flex";
    headerAvatar.textContent = currentUserAvatar || "👤";
    headerName.textContent = currentUserName;
  } else {
    headerUser.style.display = "none";
  }
}

document.getElementById("header-user").addEventListener("click", showUserSelectModal);

// ===== Modal ustawień =====
const modalSettings = document.getElementById("modal-settings");
const btnCloseSettings = document.getElementById("btn-close-settings");

btnSettings.addEventListener("click", () => {
  openSettingsModal();
});

btnCloseSettings.addEventListener("click", () => {
  modalSettings.classList.add("hidden");
});

modalSettings.addEventListener("click", (e) => {
  if (e.target === modalSettings) modalSettings.classList.add("hidden");
});

async function openSettingsModal() {
  modalSettings.classList.remove("hidden");
  await loadUsersInSettings();
  selectSettingsSection(_settingsSection || 'ai');
  await loadSettingsInModal();
}

// ===== Użytkownicy w ustawieniach =====
async function loadUsersInSettings() {
  try {
    const res = await fetch("/api/users");
    _settingsUsers = await res.json();
  } catch {
    _settingsUsers = [];
  }
  renderSettingsNav();
}

function renderSettingsNav() {
  // Aktualizuj aktywność linku AI
  const navAi = document.getElementById('settings-nav-ai');
  if (navAi) navAi.classList.toggle('active', _settingsSection === 'ai');

  // Renderuj listę użytkowników w nawigacji
  const navUsers = document.getElementById('settings-nav-users');
  if (navUsers) {
    navUsers.innerHTML = _settingsUsers.map(u => `
      <button class="settings-nav-item ${_settingsSection === u.id ? 'active' : ''}" onclick="selectSettingsSection('${u.id}')">
        <div class="settings-nav-item-avatar">${escapeHtml(u.avatar || '👤')}</div>
        <span>${escapeHtml(u.name)}</span>
        ${u.id === currentUserId ? '<span class="mi mi-sm" style="margin-left:auto;color:var(--md-primary)">check_circle</span>' : ''}
      </button>
    `).join('');
  }

  // Aktualizuj "Dodaj" aktywność
  const addBtn = document.querySelector('.settings-nav-item-add');
  if (addBtn) addBtn.classList.toggle('active', _settingsSection === 'new');

  const navConn = document.getElementById('settings-nav-connections');
  if (navConn) navConn.classList.toggle('active', _settingsSection === 'connections');
}

function selectSettingsSection(section) {
  _settingsSection = section;
  renderSettingsNav();

  // Ukryj wszystkie panele
  document.querySelectorAll('.settings-panel').forEach(p => p.classList.add('hidden'));

  if (section === 'ai') {
    document.getElementById('settings-panel-ai')?.classList.remove('hidden');
    // Załaduj system prompt
    loadSettingsInModal();

  } else if (section === 'connections') {
    document.getElementById('settings-panel-connections')?.classList.remove('hidden');
    loadConnectionSettings();

  } else if (section === 'new') {
    _editingUserId = null;
    _addingNewUser = true;
    const panel = document.getElementById('settings-panel-user');
    panel?.classList.remove('hidden');
    const title = document.getElementById('settings-panel-user-title');
    if (title) title.textContent = t('settings.user_title_new');
    document.getElementById('edit-user-name-active').value = '';
    document.getElementById('edit-user-avatar-active').value = '';
    document.getElementById('edit-user-prompt-active').value = '';
    const saveBtn = document.getElementById('settings-editor-save-btn');
    if (saveBtn) saveBtn.innerHTML = `<span class="mi mi-sm">person_add</span> ${t('settings.user_add')}`;
    const deleteBtn = document.getElementById('settings-editor-delete-btn');
    if (deleteBtn) deleteBtn.classList.add('hidden');

  } else {
    // Edycja istniejącego użytkownika
    const u = _settingsUsers.find(x => x.id === section);
    if (!u) return;

    // Przełącz aktywnego użytkownika
    currentUserId = u.id;
    currentUserName = u.name;
    currentUserAvatar = u.avatar || '👤';
    localStorage.setItem('dietetyk_user_id', u.id);
    localStorage.setItem('dietetyk_user_name', currentUserName);
    localStorage.setItem('dietetyk_user_avatar', currentUserAvatar);
    updateHeaderUser();
    loadSessions();

    _editingUserId = u.id;
    _addingNewUser = false;
    const panel = document.getElementById('settings-panel-user');
    panel?.classList.remove('hidden');
    const title = document.getElementById('settings-panel-user-title');
    if (title) title.textContent = t('settings.user_title_edit');
    document.getElementById('edit-user-name-active').value = u.name;
    document.getElementById('edit-user-avatar-active').value = u.avatar || '';
    document.getElementById('edit-user-prompt-active').value = u.system_prompt || '';
    const saveBtn = document.getElementById('settings-editor-save-btn');
    if (saveBtn) saveBtn.innerHTML = `<span class="mi mi-sm">save</span> ${t('settings.user_save')}`;
    const deleteBtn = document.getElementById('settings-editor-delete-btn');
    if (deleteBtn) deleteBtn.classList.remove('hidden');

    renderSettingsNav();
  }
}
window.selectSettingsSection = selectSettingsSection;

function selectUserForEdit(userId) {
  selectSettingsSection(userId);
}
window.selectUserForEdit = selectUserForEdit;

async function saveActiveUserEdit() {
  const name = document.getElementById('edit-user-name-active')?.value.trim();
  const avatar = document.getElementById('edit-user-avatar-active')?.value.trim();
  const systemPrompt = document.getElementById('edit-user-prompt-active')?.value;
  if (!name) { alert(t('common.empty_name')); return; }

  if (_addingNewUser) {
    // Tryb dodawania nowego użytkownika
    try {
      const res = await fetch('/api/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, avatar: avatar || '👤', system_prompt: systemPrompt }),
      });
      if (!res.ok) { const e = await res.json(); alert(e.detail || t('common.error_generic')); return; }
      const newUser = await res.json();
      _addingNewUser = false;
      _settingsSection = newUser.id || 'ai';
      await loadUsersInSettings();
      selectSettingsSection(_settingsSection);
    } catch { alert(t('common.error_generic')); }
  } else {
    // Tryb edycji istniejącego
    if (!_editingUserId) return;
    try {
      const res = await fetch(`/api/users/${_editingUserId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, avatar: avatar || '👤', system_prompt: systemPrompt }),
      });
      if (!res.ok) { const e = await res.json(); alert(e.detail || t('common.error_generic')); return; }
      if (_editingUserId === currentUserId) {
        currentUserName = name;
        currentUserAvatar = avatar || '👤';
        localStorage.setItem('dietetyk_user_name', currentUserName);
        localStorage.setItem('dietetyk_user_avatar', currentUserAvatar);
        updateHeaderUser();
      }
      await loadUsersInSettings();
      renderSettingsNav();
    } catch { alert(t('common.error_generic')); }
  }
}
window.saveActiveUserEdit = saveActiveUserEdit;

async function deleteActiveUser() {
  if (!_editingUserId) return;
  if (!confirm(t('settings.user_delete_confirm'))) return;
  try {
    const res = await fetch(`/api/users/${_editingUserId}`, { method: 'DELETE' });
    if (!res.ok) { const e = await res.json(); alert(e.detail || t('common.error_generic')); return; }
    if (currentUserId === _editingUserId) {
      currentUserId = null; currentUserName = null; currentUserAvatar = null;
      localStorage.removeItem('dietetyk_user_id');
      localStorage.removeItem('dietetyk_user_name');
      localStorage.removeItem('dietetyk_user_avatar');
      updateHeaderUser();
    }
    _editingUserId = null;
    _settingsSection = 'ai';
    await loadUsersInSettings();
    selectSettingsSection('ai');
  } catch { alert(t('common.error_generic')); }
}
window.deleteActiveUser = deleteActiveUser;

function toggleAddUserForm() {
  const form = document.getElementById('add-user-form');
  if (form) form.classList.toggle('hidden');
}
window.toggleAddUserForm = toggleAddUserForm;

function openAddUserForm() {
  selectSettingsSection('new');
}
window.openAddUserForm = openAddUserForm;

async function deleteUserFromSettings(userId, totalCount) {
  if (totalCount <= 1) {
    alert(t('common.error_generic'));
    return;
  }
  try {
    const res = await fetch(`/api/users/${userId}`, { method: "DELETE" });
    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || t('common.error_generic'));
      return;
    }
    if (currentUserId === userId) {
      currentUserId = null;
      currentUserName = null;
      currentUserAvatar = null;
      localStorage.removeItem("dietetyk_user_id");
      localStorage.removeItem("dietetyk_user_name");
      localStorage.removeItem("dietetyk_user_avatar");
      updateHeaderUser();
    }
    await loadUsersInSettings();
  } catch {
    alert(t('common.error_generic'));
  }
}

document.getElementById("btn-add-user")?.addEventListener("click", async () => {
  const name = document.getElementById("new-user-name")?.value.trim();
  const avatar = document.getElementById("new-user-avatar")?.value.trim() || "👤";
  const prompt = document.getElementById("new-user-prompt")?.value.trim();

  if (!name) {
    alert(t('common.empty_name'));
    return;
  }

  try {
    const res = await fetch("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, avatar, system_prompt: prompt }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || t('common.error_generic'));
      return;
    }
    if (document.getElementById("new-user-name")) document.getElementById("new-user-name").value = "";
    if (document.getElementById("new-user-avatar")) document.getElementById("new-user-avatar").value = "";
    if (document.getElementById("new-user-prompt")) document.getElementById("new-user-prompt").value = "";
    await loadUsersInSettings();
  } catch {
    alert(t('common.error_generic'));
  }
});

// ===== Ustawienia AI =====
async function loadSettingsInModal() {
  try {
    const res = await fetch("/api/settings");
    const settings = await res.json();

    const engine = settings.ai_engine || "gemini";
    document.querySelectorAll(".radio-option").forEach((opt) => {
      opt.classList.toggle("selected", opt.querySelector("input").value === engine);
    });
    document.querySelectorAll('input[name="ai-engine"]').forEach((r) => {
      r.checked = r.value === engine;
    });

    const geminiModel = document.getElementById("gemini-model");
    if (geminiModel) geminiModel.value = settings.gemini_model || "gemini-2.0-flash";

    const ollamaUrl = document.getElementById("ollama-url");
    if (ollamaUrl) ollamaUrl.value = settings.ollama_url || "http://192.168.1.28:11434";

    const systemPromptEl = document.getElementById("system-prompt-edit");
    if (systemPromptEl) systemPromptEl.value = settings.system_prompt || "";

    const geminiKeyEl = document.getElementById("settings-gemini-api-key");
    if (geminiKeyEl) geminiKeyEl.value = settings.gemini_api_key || "";

    const chatModelSelect = document.getElementById("chat-model-select");
    if (chatModelSelect) chatModelSelect.value = settings.gemini_model || "gemini-2.0-flash";

    toggleEngineOptions(engine);

    if (engine === "ollama") {
      await loadOllamaModels(settings.ollama_model);
    }
  } catch { /* silent */ }
}

function toggleEngineOptions(engine) {
  const geminiOpts = document.getElementById("gemini-options");
  const ollamaOpts = document.getElementById("ollama-options");
  if (geminiOpts) geminiOpts.style.display = engine === "gemini" ? "block" : "none";
  if (ollamaOpts) ollamaOpts.style.display = engine === "ollama" ? "block" : "none";
}

document.querySelectorAll('input[name="ai-engine"]').forEach((radio) => {
  radio.addEventListener("change", (e) => {
    const val = e.target.value;
    document.querySelectorAll(".radio-option").forEach((opt) => {
      opt.classList.toggle("selected", opt.querySelector("input").value === val);
    });
    toggleEngineOptions(val);
    if (val === "ollama") loadOllamaModels();
  });
});

async function loadOllamaModels(currentModel = null) {
  const select = document.getElementById("ollama-model");
  const dot = document.getElementById("ollama-status-dot");
  const text = document.getElementById("ollama-status-text");

  if (!select) return;
  select.innerHTML = '<option value="">Ładowanie...</option>';

  try {
    const res = await fetch("/api/ollama/models");
    const data = await res.json();

    if (data.error || !data.models || data.models.length === 0) {
      select.innerHTML = '<option value="">Brak modeli</option>';
      if (dot) { dot.className = "status-dot offline"; }
      if (text) text.textContent = "Ollama niedostępna";
    } else {
      select.innerHTML = data.models.map((m) =>
        `<option value="${escapeHtml(m)}" ${m === currentModel ? "selected" : ""}>${escapeHtml(m)}</option>`
      ).join("");
      if (dot) { dot.className = "status-dot online"; }
      if (text) text.textContent = `Dostępna (${data.models.length} modeli)`;
    }
  } catch {
    select.innerHTML = '<option value="">Błąd połączenia</option>';
    if (dot) { dot.className = "status-dot offline"; }
    if (text) text.textContent = "Błąd połączenia z Ollama";
  }
}

document.getElementById("btn-save-settings").addEventListener("click", async () => {
  const engine = document.querySelector('input[name="ai-engine"]:checked')?.value || "gemini";
  const geminiModel = document.getElementById("gemini-model")?.value || "gemini-2.0-flash";
  const geminiApiKey = document.getElementById("settings-gemini-api-key")?.value?.trim() || "";
  const ollamaUrl = document.getElementById("ollama-url")?.value?.trim() || "http://192.168.1.28:11434";
  const ollamaModel = document.getElementById("ollama-model")?.value || "qwen2.5:14b";

  try {
    const res = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ai_engine: engine, gemini_model: geminiModel, gemini_api_key: geminiApiKey, ollama_url: ollamaUrl, ollama_model: ollamaModel }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || t('common.error_generic'));
      return;
    }
    const chatModelSelect = document.getElementById("chat-model-select");
    if (chatModelSelect) chatModelSelect.value = geminiModel;
    modalSettings.classList.add("hidden");
  } catch {
    alert(t('common.error_generic'));
  }
});

// ===== System prompt handlers =====
document.getElementById("btn-save-system-prompt")?.addEventListener("click", async () => {
  const prompt = document.getElementById("system-prompt-edit")?.value || "";
  try {
    const res = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system_prompt: prompt }),
    });
    if (!res.ok) { alert(t('common.error_generic')); return; }
    alert(t('settings.saved'));
  } catch { alert(t('common.error_generic')); }
});

document.getElementById("btn-reset-system-prompt")?.addEventListener("click", async () => {
  if (!confirm(t('settings.save_prompt'))) return;
  document.getElementById("system-prompt-edit").value = "";
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ system_prompt: "" }),
  });
});

// ===== Chat model select (ZMIANA 4) =====
const chatModelSelect = document.getElementById("chat-model-select");
chatModelSelect?.addEventListener("change", async () => {
  const model = chatModelSelect.value;
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ gemini_model: model }),
  });
  const geminiModelEl = document.getElementById("gemini-model");
  if (geminiModelEl) geminiModelEl.value = model;
});

// ===== Toggle motyw w modalu =====
const toggleThemeSwitch = document.getElementById("toggle-theme");
if (toggleThemeSwitch) {
  toggleThemeSwitch.addEventListener("change", () => {
    applyTheme(toggleThemeSwitch.checked ? "light" : "dark");
  });
}

// ===== Modal wyboru użytkownika =====
const modalUserSelect = document.getElementById("modal-user-select");
const btnCloseUserSelect = document.getElementById("btn-close-user-select");

btnCloseUserSelect.addEventListener("click", () => {
  modalUserSelect.classList.add("hidden");
});

modalUserSelect.addEventListener("click", (e) => {
  if (e.target === modalUserSelect) modalUserSelect.classList.add("hidden");
});

async function showUserSelectModal() {
  const grid = document.getElementById("user-select-grid");
  try {
    const res = await fetch("/api/users");
    const users = await res.json();

    if (!users || users.length === 0) {
      modalUserSelect.classList.add("hidden");
      return;
    }

    grid.innerHTML = users.map((u) => `
      <div class="user-select-card" onclick="selectUser('${u.id}', '${escapeHtml(u.name)}', '${escapeHtml(u.avatar || "👤")}')">
        <span class="user-select-avatar">${escapeHtml(u.avatar || "👤")}</span>
        <span class="user-select-name">${escapeHtml(u.name)}</span>
      </div>
    `).join("");

    modalUserSelect.classList.remove("hidden");
  } catch {
    // Jeśli błąd — nie pokazuj modala
  }
}

function selectUser(userId, userName, userAvatar) {
  currentUserId = userId;
  currentUserName = userName;
  currentUserAvatar = userAvatar;
  localStorage.setItem("dietetyk_user_id", userId);
  localStorage.setItem("dietetyk_user_name", userName);
  localStorage.setItem("dietetyk_user_avatar", userAvatar);
  // Załaduj motyw dla tego użytkownika
  const userThemeKey = `dietetyk_theme_${userId}`;
  const userTheme = localStorage.getItem(userThemeKey) || localStorage.getItem("dietetyk_theme") || "dark";
  applyTheme(userTheme);
  updateHeaderUser();
  loadSessions(); // odśwież historię dla nowego użytkownika
  modalUserSelect.classList.add("hidden");
  // Powiadom lite.html o zmianie użytkownika
  document.dispatchEvent(new CustomEvent('userSelected', { detail: { userId, userName, userAvatar } }));
}

// ===== Init =====
async function init() {
  await loadTranslations(_lang);
  applyTranslations();
  initTheme();
  setupSpeechRecognition();
  setupTTS();
  startRecipesPolling();
  const _modalSaveBtn = document.getElementById('recipe-modal-save-btn');
  if (_modalSaveBtn) _modalSaveBtn.addEventListener('click', saveRecipeEditModal);

  // Load selected model on startup
  try {
    const res = await fetch('/api/settings');
    if (res.ok) {
      const settings = await res.json();
      const chatModelSelect = document.getElementById('chat-model-select');
      if (chatModelSelect && settings.gemini_model) {
        chatModelSelect.value = settings.gemini_model;
      }
    }
  } catch { /* silent */ }

  // Escape key closes recipe edit modal
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      const modal = document.getElementById('recipe-edit-modal');
      if (modal && !modal.classList.contains('hidden')) closeRecipeEditModal();
    }
  });
  loadSessions();
  updateHeaderUser();
  messageInput?.focus();

  // Refresh session list every 10 seconds
  setInterval(loadSessions, 10000);
}
init();

// v4: Inicjalizacja zakładek i nowych funkcji
function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');

  if (!tabBtns.length) return;

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;

      tabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      tabContents.forEach(tc => {
        if (tc.id === 'tab-' + target) {
          tc.classList.remove('hidden');
        } else {
          tc.classList.add('hidden');
        }
      });

      if (target === 'shopping') loadShoppingList();
      if (target === 'pantry') loadPantry();
    });
  });

  // Filtry przepisów
  const recipeFilterSelect = document.getElementById('recipe-filter-select');
  if (recipeFilterSelect) {
    recipeFilterSelect.addEventListener('change', () => {
      _recipeFilter = recipeFilterSelect.value;
      refreshRecipesPanel();
    });
  }
  // Sort przepisów
  const recipeSortSelect = document.getElementById('recipe-sort-select');
  if (recipeSortSelect) {
    recipeSortSelect.addEventListener('change', () => {
      _recipeSort = recipeSortSelect.value;
      refreshRecipesPanel();
    });
  }

  // Meal type chips — toggle multi-select
  document.querySelectorAll('#recipe-modal-meal-chips .meal-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      chip.classList.toggle('active');
    });
  });
}

initTabs();
initShoppingForm();
initPantry();


async function deleteShoppingItem(itemId, name) {
  try {
    const res = await fetch(`/api/shopping-list/${itemId}`, { method: "DELETE" });
    if (res.ok) {
      await loadShoppingList();
    }
  } catch { /* silent */ }
}
window.deleteShoppingItem = deleteShoppingItem;

function initShoppingForm() {
  let _selectedProductId = null;
  let _shoppingDebounceTimer = null;
  const input = document.getElementById('shopping-input');
  const suggestionsEl = document.getElementById('shopping-suggestions');
  const btnAdd = document.getElementById('btn-shopping-add');
  const btnRefresh = document.getElementById('btn-shopping-refresh');
  const searchEl = document.getElementById('shopping-search');
  if (!input) return;

  // Sync step attribute on amount input when unit changes
  const shoppingUnitEl = document.getElementById('shopping-unit');
  const shoppingAmountEl = document.getElementById('shopping-amount');
  function syncShoppingStep() {
    if (shoppingAmountEl && shoppingUnitEl) {
      shoppingAmountEl.step = getStep(shoppingUnitEl.value);
    }
  }
  if (shoppingUnitEl) shoppingUnitEl.addEventListener('change', syncShoppingStep);

  // Search
  if (searchEl) {
    searchEl.addEventListener('input', () => renderCurrentShoppingList());
  }

  // Filter buttons
  document.querySelectorAll('#tab-shopping .list-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#tab-shopping .list-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _shoppingFilter = btn.dataset.filter;
      renderCurrentShoppingList();
    });
  });

  input.addEventListener('input', () => {
    clearTimeout(_shoppingDebounceTimer);
    _selectedProductId = null;
    const q = input.value.trim();
    if (q.length < 2) {
      hideSuggestions();
      return;
    }
    _shoppingDebounceTimer = setTimeout(() => fetchSuggestions(q), 300);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { addShoppingItem(); return; }
    if (e.key === 'Escape') hideSuggestions();
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#shopping-input') && !e.target.closest('#shopping-suggestions')) hideSuggestions();
  });

  if (btnAdd) btnAdd.addEventListener('click', addShoppingItem);
  if (btnRefresh) btnRefresh.addEventListener('click', loadShoppingList);

  const btnCompleteShopping = document.getElementById('btn-complete-shopping');
  if (btnCompleteShopping) {
    btnCompleteShopping.addEventListener('click', async () => {
      btnCompleteShopping.disabled = true;
      btnCompleteShopping.innerHTML = `<span class="mi mi-sm">hourglass_empty</span> ${t('common.loading')}`;
      try {
        const res = await fetch('/api/shopping-list/complete-done', { method: 'POST' });
        const data = await res.json();
        if (data.moved_to_pantry > 0) {
          btnCompleteShopping.innerHTML = `<span class="mi mi-sm">check_circle</span> +${data.moved_to_pantry}`;
          await loadShoppingList();
          await loadPantry();
        } else {
          btnCompleteShopping.innerHTML = `<span class="mi mi-sm">error_outline</span> ${t('shopping.empty')}`;
        }
      } catch {
        btnCompleteShopping.innerHTML = `<span class="mi mi-sm">error_outline</span> ${t('common.error_generic')}`;
      } finally {
        setTimeout(() => {
          btnCompleteShopping.disabled = false;
          btnCompleteShopping.innerHTML = `<span class="mi mi-sm">check_circle</span> ${t('shopping.complete_done')}`;
        }, 3000);
      }
    });
  }

  async function fetchSuggestions(q) {
    try {
      const res = await fetch(`/api/products/search?q=${encodeURIComponent(q)}`);
      const items = await res.json();
      if (!items.length) { hideSuggestions(); return; }
      suggestionsEl.innerHTML = items.map(it =>
        `<div class="suggestion-item" data-id="${it.id}" data-name="${escapeHtml(it.name)}">${escapeHtml(it.name)}</div>`
      ).join('');
      suggestionsEl.classList.remove('hidden');
      suggestionsEl.querySelectorAll('.suggestion-item').forEach(el => {
        el.addEventListener('click', () => {
          input.value = el.dataset.name;
          _selectedProductId = el.dataset.id;
          hideSuggestions();
        });
      });
    } catch { hideSuggestions(); }
  }

  function hideSuggestions() {
    if (suggestionsEl) suggestionsEl.classList.add('hidden');
  }

  async function addShoppingItem() {
    const name = input.value.trim();
    if (!name) return;
    const amountEl = document.getElementById('shopping-amount');
    const unitEl = document.getElementById('shopping-unit');
    const amount = parseFloat(amountEl ? amountEl.value : 1) || 1;
    const unit = unitEl ? unitEl.value : 'szt.';
    try {
      btnAdd.disabled = true;
      const res = await fetch('/api/shopping-list', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_name: name, amount, unit }),
      });
      if (res.ok) {
        input.value = '';
        if (amountEl) amountEl.value = 1;
        _selectedProductId = null;
        await loadShoppingList();  // natychmiastowe odswiezenie
      }
    } catch { /* silent */ } finally {
      btnAdd.disabled = false;
      input.focus();
    }
  }
}

// ===== Usuwanie przepisu z Grocy =====
async function deleteRecipe(grocy_id, name, cardId) {
  try {
    const res = await fetch(`/api/recipes/${grocy_id}`, { method: "DELETE" });
    if (res.ok) {
      const card = document.getElementById(cardId);
      if (card) card.remove();
    } else if (res.status === 404) {
      const card = document.getElementById(cardId);
      if (card) card.remove();
    } else {
      alert(t('common.error_generic'));
    }
  } catch {
    alert(t('common.error_generic'));
  }
}

// ===== Globals for inline onclick =====
window.toggleRecipeCard = toggleRecipeCard;
window.loadSessionHistory = loadSessionHistory;
window.deleteSession = deleteSession;
window.selectUser = selectUser;
window.showUserSelectModal = showUserSelectModal;
window.toggleTheme = function() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  applyTheme(current === 'dark' ? 'light' : 'dark');
};
window.deleteUserFromSettings = deleteUserFromSettings;
window.deleteRecipe = deleteRecipe;

// ===== Resizable Columns =====
function initResizableColumns() {
  const historyPanel = document.getElementById('history-panel');
  const recipesPanel = document.getElementById('recipes-panel');
  const resizeHistory = document.getElementById('resize-history');
  const resizeRecipes = document.getElementById('resize-recipes');

  if (!historyPanel || !recipesPanel || !resizeHistory || !resizeRecipes) return;

  // Load saved widths
  const savedHistoryW = localStorage.getItem('dietetyk_history_width');
  const savedRecipesW = localStorage.getItem('dietetyk_recipes_width');
  if (savedHistoryW) { historyPanel.style.width = savedHistoryW + 'px'; historyPanel.style.flexShrink = '0'; }
  if (savedRecipesW) { recipesPanel.style.width = savedRecipesW + 'px'; recipesPanel.style.flexShrink = '0'; }

  function startDrag(e, panel, saveKey, isRight) {
    e.preventDefault();
    e.stopPropagation();
    const handle = e.currentTarget;
    handle.classList.add('dragging');
    // Disable text selection during drag
    document.body.style.userSelect = 'none';
    document.body.style.webkitUserSelect = 'none';
    document.body.style.cursor = 'col-resize';

    const startX = e.clientX;
    const startW = panel.getBoundingClientRect().width;

    function onMove(ev) {
      ev.preventDefault();
      const delta = isRight ? startX - ev.clientX : ev.clientX - startX;
      const minW = isRight ? 160 : 120;
      const maxW = isRight ? 600 : 400;
      const newW = Math.min(maxW, Math.max(minW, startW + delta));
      panel.style.width = newW + 'px';
      panel.style.minWidth = newW + 'px';
      panel.style.flexShrink = '0';
    }

    function onUp() {
      handle.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.body.style.webkitUserSelect = '';
      document.body.style.cursor = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      const finalW = Math.round(panel.getBoundingClientRect().width);
      localStorage.setItem(saveKey, finalW);
    }

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  resizeHistory.addEventListener('mousedown', e =>
    startDrag(e, historyPanel, 'dietetyk_history_width', false));
  resizeRecipes.addEventListener('mousedown', e =>
    startDrag(e, recipesPanel, 'dietetyk_recipes_width', true));
}

// ===== Connection Settings =====

async function loadConnectionSettings() {
  try {
    const res = await fetch('/api/settings');
    const s = await res.json();
    const urlEl = document.getElementById('settings-grocy-url');
    const keyEl = document.getElementById('settings-grocy-api-key');
    const geminiKeyEl = document.getElementById('settings-gemini-api-key');
    if (urlEl) urlEl.value = s.grocy_url || '';
    if (keyEl) keyEl.value = s.grocy_api_key || '';
    if (geminiKeyEl) geminiKeyEl.value = s.gemini_api_key || '';
  } catch {}
}

async function saveConnectionSettings() {
  const url = document.getElementById('settings-grocy-url')?.value.trim();
  const key = document.getElementById('settings-grocy-api-key')?.value.trim();
  const geminiKey = document.getElementById('settings-gemini-api-key')?.value.trim();
  try {
    await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ grocy_url: url, grocy_api_key: key, gemini_api_key: geminiKey }),
    });
    const statusEl = document.getElementById('grocy-connection-status');
    if (statusEl) {
      statusEl.innerHTML = `<span style="color:var(--md-primary)"><span class="mi mi-sm">check_circle</span> ${t('settings.saved')}</span>`;
      setTimeout(() => { statusEl.innerHTML = ''; }, 3000);
    }
  } catch {
    alert('Save error.');
  }
}
window.saveConnectionSettings = saveConnectionSettings;

async function testGrocyConnection() {
  const statusEl = document.getElementById('grocy-connection-status');
  if (statusEl) statusEl.innerHTML = '<span class="mi mi-sm" style="animation:spin 1s linear infinite">refresh</span> Testing...';
  try {
    await saveConnectionSettings();
    const res = await fetch('/health');
    const data = await res.json();
    if (statusEl) {
      if (data.grocy === 'connected') {
        statusEl.innerHTML = '<span style="color:var(--md-primary)"><span class="mi mi-sm">check_circle</span> Connected to ' + escapeHtml(data.grocy_url || 'Grocy') + '</span>';
      } else {
        statusEl.innerHTML = '<span style="color:var(--md-error,#f28b82)"><span class="mi mi-sm">error</span> Cannot connect. Check URL and API key.</span>';
      }
    }
  } catch {
    if (statusEl) statusEl.innerHTML = '<span style="color:var(--md-error,#f28b82)"><span class="mi mi-sm">error</span> Connection test failed.</span>';
  }
}
window.testGrocyConnection = testGrocyConnection;

function toggleGeminiKeyVisibility() {
  const input = document.getElementById('settings-gemini-api-key');
  const icon = document.getElementById('gemini-key-eye-icon');
  if (!input) return;
  input.type = input.type === 'password' ? 'text' : 'password';
  if (icon) icon.textContent = input.type === 'password' ? 'visibility' : 'visibility_off';
}
window.toggleGeminiKeyVisibility = toggleGeminiKeyVisibility;

function toggleGrocyKeyVisibility() {
  const input = document.getElementById('settings-grocy-api-key');
  const icon = document.getElementById('grocy-key-eye-icon');
  if (!input) return;
  input.type = input.type === 'password' ? 'text' : 'password';
  if (icon) icon.textContent = input.type === 'password' ? 'visibility' : 'visibility_off';
}
window.toggleGrocyKeyVisibility = toggleGrocyKeyVisibility;

// Uruchom po pełnym załadowaniu DOM
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initResizableColumns);
} else {
  initResizableColumns();
}

