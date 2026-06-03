'use strict';

const REGION_INFO = {
  cn: { name: 'China',     flag: '🇨🇳' },
  ua: { name: 'Ukraine',   flag: '🇺🇦' },
  us: { name: 'USA',       flag: '🇺🇸' },
  tr: { name: 'Turkey',    flag: '🇹🇷' },
  ar: { name: 'Argentina', flag: '🇦🇷' },
  in: { name: 'India',     flag: '🇮🇳' },
  gb: { name: 'UK',        flag: '🇬🇧' },
  br: { name: 'Brazil',    flag: '🇧🇷' },
};

const CURRENCY_SYMBOL = {
  CNY: '¥', UAH: '₴', USD: '$', TRY: '₺',
  ARS: 'AR$', INR: '₹', GBP: '£', BRL: 'R$',
};

let pollTimer      = null;
let syncStart      = null;
let tableRefreshAt = 0;
let allGames       = [];
let activeFilter   = 'all';

// ── Init ──────────────────────────────────────────────────────────
async function init() {
  try {
    const s = await apiFetch('/api/settings');
    if (s.steam_id) {
      document.getElementById('inp-steam').value = s.steam_id;
      await checkAndLoad();
    } else {
      show('view-welcome');
    }
  } catch (_) {
    showError('Cannot reach backend', 'Make sure start.bat is running, then refresh this page.');
  }
}

async function checkAndLoad() {
  const status = await apiFetch('/api/status');

  if (status.game_count > 0) {
    const games = await apiFetch('/api/wishlist');
    renderTable(games);
    show('view-table');
    setSyncLabel(status.last_sync);
    if (status.is_syncing) {
      document.getElementById('sync-label').innerHTML = '<span class="spin ref-icon">⟳</span> Syncing…';
      schedulePoll();
    } else {
      clearPoll();
    }
    return;
  }

  if (status.is_syncing) {
    show('view-loading');
    updateLoadingText(status);
    schedulePoll();
    return;
  }
  if (status.last_error) { showError('Could not load wishlist', status.last_error); return; }

  show('view-loading');
  setLoadingText('Starting sync…', '');
  schedulePoll();
}

// ── Polling ───────────────────────────────────────────────────────
function schedulePoll() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const status = await apiFetch('/api/status');
      if (status.is_syncing) {
        updateLoadingText(status);
        const now = Date.now();
        if (status.game_count > 0 && now - tableRefreshAt > 30000) {
          tableRefreshAt = now;
          const games = await apiFetch('/api/wishlist');
          renderTable(games);
          show('view-table');
          document.getElementById('sync-label').innerHTML = '<span class="spin ref-icon">⟳</span> Syncing…';
        }
        return;
      }
      clearPoll();
      if (status.last_error) { showError('Could not load wishlist', status.last_error); return; }
      await checkAndLoad();
    } catch (_) {}
  }, 1500);
}

function clearPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

function setLoadingText(phase, progress) {
  document.getElementById('loading-phase').textContent = phase;
  document.getElementById('loading-progress').textContent = progress;
}

function updateLoadingText(status) {
  document.getElementById('loading-phase').textContent = status.phase || 'Syncing…';
  const bar = document.getElementById('progress-bar');

  if (status.total > 0) {
    if (status.done > 0 && !syncStart) syncStart = Date.now();
    const pct = Math.round((status.done / status.total) * 100);
    if (bar) bar.style.width = pct + '%';

    let timeStr = '';
    if (syncStart && status.done > 5) {
      const elapsed = (Date.now() - syncStart) / 1000;
      const rate    = status.done / elapsed;
      const remSec  = Math.ceil((status.total - status.done) / rate);
      if (remSec > 1) timeStr = remSec < 60 ? ` — ~${remSec}s left` : ` — ~${Math.ceil(remSec / 60)}min left`;
    }
    document.getElementById('loading-progress').textContent =
      `${status.done} / ${status.total} requests (${pct}%)${timeStr}`;

    if (status.done >= status.total) {
      setLoadingText('Finalizing…', '');
      clearPoll();
      setTimeout(checkAndLoad, 600);
    }
  } else {
    document.getElementById('loading-progress').textContent = '';
    if (bar) bar.style.width = '0%';
  }
}

// ── Render ────────────────────────────────────────────────────────
function renderTable(games) {
  allGames = games;
  updateStats();
  applyFilters();
}

function updateStats() {
  const total    = allGames.length;
  const discounted = allGames.filter(g => Object.values(g.prices).some(p => p.discount_pct > 0)).length;
  const upcoming = allGames.filter(g => g.is_upcoming).length;
  document.getElementById('stat-total').textContent    = total;
  document.getElementById('stat-disc').textContent     = discounted;
  document.getElementById('stat-upcoming').textContent = upcoming;
}

function setFilter(name, btn) {
  activeFilter = name;
  document.querySelectorAll('.fchip').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
  applyFilters();
}

function applyFilters() {
  let games = [...allGames];

  const search = (document.getElementById('search-input')?.value || '').toLowerCase().trim();
  const sort   = document.getElementById('sort-select')?.value || 'name';

  const clearBtn = document.getElementById('search-clear');
  if (clearBtn) clearBtn.classList.toggle('hidden', !search);

  if (search) games = games.filter(g => g.name.toLowerCase().includes(search));

  if (activeFilter === 'discounted') {
    games = games.filter(g => Object.values(g.prices).some(p => p.discount_pct > 0));
  } else if (activeFilter === 'upcoming') {
    games = games.filter(g => g.is_upcoming);
  }

  if (sort === 'price-asc') {
    games.sort((a, b) => (a.lowest_price_usd ?? Infinity) - (b.lowest_price_usd ?? Infinity));
  } else if (sort === 'price-desc') {
    games.sort((a, b) => {
      if (a.lowest_price_usd === null && b.lowest_price_usd === null) return 0;
      if (a.lowest_price_usd === null) return -1;   // unreleased on top
      if (b.lowest_price_usd === null) return 1;
      return b.lowest_price_usd - a.lowest_price_usd;
    });
  } else if (sort === 'discount') {
    const maxDisc = g => Math.max(0, ...Object.values(g.prices).map(p => p.discount_pct || 0));
    games.sort((a, b) => maxDisc(b) - maxDisc(a));
  } else {
    games.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  }

  renderRows(games);
}

function renderRows(games) {
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = '';

  const lbl = document.getElementById('game-count-label');
  if (lbl) lbl.textContent = games.length === allGames.length
    ? `${allGames.length} games`
    : `Showing ${games.length} of ${allGames.length} games`;

  if (games.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="no-results">No games match this filter.</td></tr>';
    return;
  }

  games.forEach(game => {
    const tr = document.createElement('tr');
    tr.dataset.appId = game.app_id;
    tr.innerHTML =
      `<td>${gameCell(game)}</td>` +
      `<td class="td-price">${priceCell(game.prices['cn'])}</td>` +
      `<td class="td-price">${priceCell(game.prices['ua'])}</td>` +
      `<td class="td-price">${lowestCell(game)}</td>`;
    tbody.appendChild(tr);
  });
}

// ── Variants dropdown (editions & bundles) ────────────────────────
const variantCache = {};

async function toggleVariants(ev, appId) {
  ev.stopPropagation();
  const btn  = ev.currentTarget;
  const row  = btn.closest('tr');
  const next = row.nextElementSibling;

  if (next && next.classList.contains('variant-row')) {
    next.remove();
    btn.classList.remove('open');
    return;
  }
  btn.classList.add('open');

  const vr = document.createElement('tr');
  vr.className = 'variant-row';
  vr.innerHTML = `<td colspan="4"><div class="variant-panel">
      <span class="chat-typing"><span></span><span></span><span></span></span> Loading editions…
    </div></td>`;
  row.after(vr);

  let variants = variantCache[appId];
  if (!variants) {
    try {
      variants = await apiFetch('/api/variants/' + appId);
      variantCache[appId] = variants;
    } catch (_) {
      vr.querySelector('.variant-panel').textContent = 'Could not load editions.';
      return;
    }
  }
  vr.querySelector('.variant-panel').innerHTML = renderVariants(variants);
}

function renderVariants(variants) {
  if (!variants || !variants.length)
    return '<div class="variant-empty">No editions or bundles found.</div>';

  const rows = variants.map(v => {
    const badge = v.kind === 'bundle'
      ? '<span class="vbadge vbadge-bundle">Bundle</span>'
      : '<span class="vbadge vbadge-edition">Edition</span>';
    return `<div class="vrow">
      <div class="vname">${badge}<span title="${esc(v.name)}">${esc(v.name)}</span></div>
      <div class="vcell">${variantPrice(v.prices['cn'])}</div>
      <div class="vcell">${variantPrice(v.prices['ua'])}</div>
      <div class="vcell">${variantLowest(v)}</div>
    </div>`;
  }).join('');

  return `<div class="variant-grid">
    <div class="vrow vhead"><div>Variant</div><div>China</div><div>Ukraine</div><div>Cheapest</div></div>
    ${rows}
  </div>`;
}

function variantPrice(p) {
  if (!p) return '<span class="p-na">N/A</span>';
  if (p.price_raw === 0) return '<span class="p-free">Free</span>';
  const sym = CURRENCY_SYMBOL[p.currency] || (p.currency + ' ');
  const native = sym + (p.price_raw / 100).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const disc = p.discount_pct > 0 ? `<span class="p-disc">-${p.discount_pct}%</span>` : '';
  return `<div class="vp">${disc}<span class="vp-usd">$${p.price_usd.toFixed(2)}</span><span class="vp-native">${native}</span></div>`;
}

function variantLowest(v) {
  if (!v.lowest_region) return '<span class="p-na">N/A</span>';
  const info = REGION_INFO[v.lowest_region] || { name: v.lowest_region, flag: '' };
  return `<div class="vp"><span class="vlow-badge">${info.flag} ${info.name}</span>` +
         `<span class="vp-usd">$${v.lowest_price_usd.toFixed(2)}</span></div>`;
}

function countdownText(days) {
  if (days == null) return '';
  if (days <= 0)   return 'out now';
  if (days === 1)  return 'tomorrow';
  if (days < 14)   return `in ${days} days`;
  if (days < 60)   return `in ${Math.round(days / 7)} weeks`;
  return `in ${Math.round(days / 30)} months`;
}

function gameCell(game) {
  const name = esc(game.name);
  let tags = '';
  if (game.is_upcoming) {
    const cd = countdownText(game.days_until_release);
    tags += `<span class="tag tag-upcoming">★ Unreleased${cd ? ' · ' + cd : ''}</span>`;
    if (game.release_date) tags += `<span class="tag tag-release">📅 ${esc(game.release_date)}</span>`;
  } else if (game.release_date) {
    tags += `<span class="tag tag-release">${esc(game.release_date)}</span>`;
  }
  const chevron = game.has_variants
    ? `<button class="variant-toggle" onclick="toggleVariants(event, ${game.app_id})"
               title="Show editions &amp; bundles">&#9662;</button>`
    : '';
  return `<div class="game-cell">
    <img class="game-thumb" src="${esc(game.header_image)}" alt="${name}" loading="lazy"
         onerror="this.style.visibility='hidden'">
    <div class="game-meta">
      <span class="game-name" title="${name}">${name}</span>
      ${tags ? `<div class="game-tags">${tags}</div>` : ''}
    </div>
    ${chevron}
  </div>`;
}

function priceCell(price) {
  if (!price) return '<span class="p-na">N/A</span>';
  if (price.price_raw === 0) return '<span class="p-free">Free</span>';
  const sym    = CURRENCY_SYMBOL[price.currency] || (price.currency + ' ');
  const native = sym + (price.price_raw / 100).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  const disc   = price.discount_pct > 0 ? `<span class="p-disc">-${price.discount_pct}%</span>` : '';
  return `<div class="price-cell">
    ${disc}
    <span class="p-main">$${price.price_usd.toFixed(2)}</span>
    <span class="p-sub">${native}</span>
  </div>`;
}

function lowestCell(game) {
  if (!game.lowest_region) return '<span class="p-na">N/A</span>';
  const r = game.lowest_region;
  const info = REGION_INFO[r] || { name: r.toUpperCase(), flag: '' };
  const p = game.prices[r];
  let nativeLine = '';
  if (p && p.price_raw > 0) {
    const sym = CURRENCY_SYMBOL[p.currency] || (p.currency + ' ');
    nativeLine = `<span class="p-sub">${sym}${(p.price_raw / 100).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}</span>`;
  }
  return `<div class="lowest-cell">
    <span class="lowest-badge">${info.flag} ${info.name}</span>
    <span class="p-main">$${game.lowest_price_usd.toFixed(2)}</span>
    ${nativeLine}
  </div>`;
}

// ── Actions ───────────────────────────────────────────────────────
async function triggerRefresh(force = false) {
  const btn = document.getElementById('btn-refresh');
  const icon = document.getElementById('refresh-icon');
  btn.disabled = true; icon.classList.add('spin');
  syncStart = null;
  try {
    const url = force ? '/api/refresh?force=true' : '/api/refresh';
    const res = await apiFetch(url, { method: 'POST' });

    if (res.fresh) {
      // Prices are still fresh — don't run the slow full sync
      const ago = res.hours < 1 ? 'less than an hour ago' : `${Math.round(res.hours)}h ago`;
      showToast(`Prices are up to date (synced ${ago}).`, true);
    } else if (res.ok) {
      hideToast();
      schedulePoll();
    } else {
      showToast(res.message || 'Already syncing…', false);
    }
  } catch (_) {}
  finally { setTimeout(() => { btn.disabled = false; icon.classList.remove('spin'); }, 1200); }
}

function forceRefresh() { hideToast(); triggerRefresh(true); }

let toastTimer = null;
function showToast(msg, withAction) {
  const t = document.getElementById('toast');
  document.getElementById('toast-msg').textContent = msg;
  document.getElementById('toast-action').classList.toggle('hidden', !withAction);
  t.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, withAction ? 7000 : 4000);
}
function hideToast() {
  document.getElementById('toast').classList.add('hidden');
  clearTimeout(toastTimer);
}

async function retrySync() {
  syncStart = null;
  try { await apiFetch('/api/refresh', { method: 'POST' }); } catch (_) {}
  show('view-loading');
  setLoadingText('Retrying sync…', '');
  schedulePoll();
}

async function saveSettings() {
  const val = document.getElementById('inp-steam').value.trim();
  if (!val) return;
  syncStart = null;
  await apiFetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ steam_id: val }),
  });
  closeSettings();
  show('view-loading');
  setLoadingText('Sync started…', '');
  schedulePoll();
}

function clearSearch() {
  const inp = document.getElementById('search-input');
  if (inp) { inp.value = ''; inp.focus(); }
  applyFilters();
}

function openSettings()  { document.getElementById('modal').classList.remove('hidden'); }
function closeSettings() { document.getElementById('modal').classList.add('hidden'); }

// ── Helpers ───────────────────────────────────────────────────────
function show(id) {
  ['view-welcome','view-loading','view-table','view-error'].forEach(v =>
    document.getElementById(v).classList.add('hidden'));
  document.getElementById(id).classList.remove('hidden');
}

function showError(title, msg) {
  document.getElementById('err-title').textContent = title;
  document.getElementById('err-msg').textContent   = msg;
  show('view-error');
}

function setSyncLabel(isoStr) {
  if (!isoStr) return;
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60000), hrs = Math.floor(mins / 60);
  const label = mins < 1 ? 'just now' : mins < 60 ? `${mins}m ago` : hrs < 24 ? `${hrs}h ago` : `${Math.floor(hrs/24)}d ago`;
  document.getElementById('sync-label').textContent = `Last synced: ${label}`;
}

async function apiFetch(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function esc(str) {
  const d = document.createElement('div');
  d.textContent = String(str);
  return d.innerHTML;
}

// ── Chat assistant ────────────────────────────────────────────────
let chatGreeted = false;

function toggleChat() {
  const drawer  = document.getElementById('chat-drawer');
  const overlay = document.getElementById('chat-overlay');
  const opening = drawer.classList.contains('hidden');
  drawer.classList.toggle('hidden');
  overlay.classList.toggle('hidden');
  if (opening) {
    if (!chatGreeted) {
      addChatMsg('bot',
        'Hi! Ask me about any Steam game\'s price. For example:<br>' +
        '<em>"Kingdom Come Deliverance price in China vs Ukraine"</em> or ' +
        '<em>"How much is Cyberpunk 2077 in Brazil?"</em>');
      chatGreeted = true;
    }
    setTimeout(() => document.getElementById('chat-input').focus(), 100);
  }
}

function addChatMsg(who, html, game) {
  const body = document.getElementById('chat-body');
  const wrap = document.createElement('div');
  wrap.className = `chat-msg chat-${who}`;
  let inner = '';
  if (game) {
    inner += `<img class="chat-thumb" src="${esc(game.header_image)}" alt=""
                   onerror="this.style.display='none'">`;
  }
  inner += `<div class="chat-bubble">${html}</div>`;
  wrap.innerHTML = inner;
  body.appendChild(wrap);
  body.scrollTop = body.scrollHeight;
  return wrap;
}

async function sendChat() {
  const inp = document.getElementById('chat-input');
  const msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  addChatMsg('user', esc(msg));

  const typing = addChatMsg('bot', '<span class="chat-typing"><span></span><span></span><span></span></span>');

  try {
    const res = await apiFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg }),
    });
    typing.remove();
    addChatMsg('bot', formatReply(res.reply), res.game);
  } catch (_) {
    typing.remove();
    addChatMsg('bot', '⚠ Something went wrong reaching the server. Try again.');
  }
}

// Render **bold**, *em*, and keep line breaks
function formatReply(text) {
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\n/g, '<br>');
}

document.addEventListener('DOMContentLoaded', init);
