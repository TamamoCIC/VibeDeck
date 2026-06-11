// ═══════════════════════════════════════════════════
// VibeDeck Settings SPA — all interactivity & API calls
// ═══════════════════════════════════════════════════

// ── Constants ──────────────────────────────────────
const PANELS = ['daemon', 'timing', 'appearance', 'theme', 'terminals', 'layouts', 'adapters', 'about'];
const TIMING_FIELDS = ['thinking_timeout_ms', 'activity_window_ms', 'fast_frame_interval_ms', 'slow_frame_interval_ms'];
const VALID_GRIDS = ['3x4', '3x5', '4x8'];

// ── Navigation ─────────────────────────────────────
function showPanel(name) {
  // Hide all panels
  document.querySelectorAll('[data-panel]').forEach(el => el.classList.remove('active'));
  // Deactivate all nav items
  document.querySelectorAll('[data-nav]').forEach(el => el.classList.remove('active'));
  // Show target panel
  const panel = document.querySelector(`[data-panel="${name}"]`);
  if (panel) panel.classList.add('active');
  const nav = document.querySelector(`[data-nav="${name}"]`);
  if (nav) nav.classList.add('active');
  // Update hash
  window.location.hash = '#settings/' + name;
  // Lazy load
  loadPanel(name);
}

function handleHash() {
  const m = window.location.hash.match(/^#settings\/(\w+)/);
  const panel = m ? m[1] : 'daemon';
  if (PANELS.indexOf(panel) !== -1) {
    showPanel(panel);
  } else {
    showPanel('daemon');
  }
}

function goBack() {
  // If inside an iframe, tell parent to clear the hash
  if (window.self !== window.top) {
    window.parent.location.hash = '';
    return;
  }
  // If we have history from the main SPA, go back
  if (document.referrer && document.referrer.includes(window.location.host)) {
    window.history.back();
  } else {
    window.location.href = '/';
  }
}

// ── Data Loading ───────────────────────────────────
async function loadPanel(name) {
  const loadingEl = document.querySelector(`[data-panel="${name}"] [data-loading]`);
  if (loadingEl && loadingEl.dataset.loaded === 'true') return; // Already loaded

  switch (name) {
    case 'daemon': await loadDaemon(); break;
    case 'timing': await loadTiming(); break;
    case 'appearance': await loadAppearance(); break;
    case 'theme': await loadTheme(); break;
    case 'terminals': await loadTerminals(); break;
    case 'layouts': await loadLayouts(); break;
    case 'adapters': await loadAdapters(); break;
    case 'about': await loadAbout(); break;
  }
  if (loadingEl) loadingEl.dataset.loaded = 'true';
}

async function loadDaemon() {
  const body = document.querySelector('[data-panel="daemon"] [data-body]');
  if (!body) return;
  body.innerHTML = '<p class="dim">Loading...</p>';
  try {
    const resp = await fetch('/api/config');
    if (!resp.ok) throw new Error('Failed to load config');
    const data = await resp.json();
    body.innerHTML = `
      <div class="field-row">
        <label>Port</label>
        <input type="number" id="cfgPort" value="${esc(data.port)}" min="1" max="65535" data-dirty="false">
      </div>
      <div class="field-row">
        <label>Expose (0.0.0.0)</label>
        <select id="cfgExpose" data-dirty="false">
          <option value="false" ${data.expose ? '' : 'selected'}>No (localhost only)</option>
          <option value="true" ${data.expose ? 'selected' : ''}>Yes (all interfaces)</option>
        </select>
      </div>
      <div class="field-row">
        <label>Auto-detect agents</label>
        <select id="cfgAutodetect" data-dirty="false">
          <option value="true" ${data.autodetect ? 'selected' : ''}>Enabled</option>
          <option value="false" ${data.autodetect ? '' : 'selected'}>Disabled</option>
        </select>
      </div>
      <div class="field-row">
        <label>Render mode</label>
        <select id="cfgRender" data-dirty="false">
          <option value="sim" ${(data.render||'sim') === 'sim' ? 'selected' : ''}>Simulator</option>
          <option value="hardware" ${data.render === 'hardware' ? 'selected' : ''}>Hardware</option>
        </select>
      </div>
    `;
    // Mark fields dirty on change
    body.querySelectorAll('input,select').forEach(el => {
      el.addEventListener('change', () => { el.dataset.dirty = 'true'; markDirty('daemon'); });
    });
    const timing = data.timing || {};
    if (timing) {
      const timingSection = document.createElement('div');
      timingSection.className = 'section';
      timingSection.innerHTML = '<h3>Timing (from daemon config)</h3>';
      TIMING_FIELDS.forEach(f => {
        timingSection.innerHTML += `
          <div class="field-row">
            <label>${esc(f.replace(/_/g,' '))}</label>
            <input type="number" id="cfgTiming_${esc(f)}" value="${esc(timing[f]||'')}" min="0" step="1" data-dirty="false">
          </div>`;
      });
      body.appendChild(timingSection);
      timingSection.querySelectorAll('input').forEach(el => {
        el.addEventListener('change', () => { el.dataset.dirty = 'true'; markDirty('daemon'); });
      });
    }
  } catch (e) {
    body.innerHTML = '<p class="dim error">Failed to load daemon config: ' + esc(e.message) + '</p>';
  }
}

async function loadTiming() {
  const body = document.querySelector('[data-panel="timing"] [data-body]');
  if (!body) return;
  body.innerHTML = '<p class="dim">Loading...</p>';
  try {
    const resp = await fetch('/api/timing');
    if (!resp.ok) throw new Error('Failed to load timing');
    const data = await resp.json();
    const timing = data.timing || {};
    let html = TIMING_FIELDS.map(f => {
      const label = f.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      const desc = timingDesc(f);
      return `
        <div class="field-row">
          <label title="${esc(desc)}">${esc(label)}</label>
          <input type="number" id="timing_${esc(f)}" value="${esc(timing[f]||'')}" min="0" step="1" data-dirty="false">
          <span class="field-help">${esc(desc)}</span>
        </div>`;
    }).join('');
    body.innerHTML = html;
    body.querySelectorAll('input').forEach(el => {
      el.addEventListener('change', () => { el.dataset.dirty = 'true'; markDirty('timing'); });
    });
  } catch (e) {
    body.innerHTML = '<p class="dim error">Failed to load timing: ' + esc(e.message) + '</p>';
  }
}

async function loadAppearance() {
  const body = document.querySelector('[data-panel="appearance"] [data-body]');
  if (!body) return;
  body.innerHTML = '<p class="dim">Loading adapters...</p>';
  try {
    // Get the list of adapters first
    const adaptersResp = await fetch('/api/adapters');
    if (!adaptersResp.ok) throw new Error('Failed to list adapters');
    const adaptersData = await adaptersResp.json();
    const adapters = adaptersData.adapters || [];
    if (adapters.length === 0) {
      body.innerHTML = '<p class="dim">No adapters registered.</p>';
      return;
    }
    let html = '<div class="appearance-tabs">';
    adapters.forEach((a, i) => {
      html += `<button class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="appearance_${esc(a.name)}" onclick="switchAppearanceTab('${esc(a.name)}')">${esc(a.name)}</button>`;
    });
    html += '</div>';
    adapters.forEach((a, i) => {
      html += `<div class="appearance-tab-content ${i === 0 ? 'active' : ''}" id="appearanceTab_${esc(a.name)}" data-adapter="${esc(a.name)}"><p class="dim">Loading...</p></div>`;
    });
    body.innerHTML = html;
    // Load each adapter's appearance
    for (const a of adapters) {
      await loadAdapterAppearance(a.name);
    }
  } catch (e) {
    body.innerHTML = '<p class="dim error">Failed to load appearance: ' + esc(e.message) + '</p>';
  }
}

async function loadAdapterAppearance(name) {
  const tabContent = document.getElementById('appearanceTab_' + name);
  if (!tabContent) return;
  try {
    const [resp, clipsResp] = await Promise.all([
      fetch('/api/adapters/' + encodeURIComponent(name) + '/appearance'),
      fetch('/api/clips'),
    ]);
    if (!resp.ok) throw new Error('Failed to load appearance for ' + name);
    const data = await resp.json();
    const appearance = data.appearance || {};
    const events = Object.keys(appearance);
    if (events.length === 0) {
      tabContent.innerHTML = '<p class="dim">No appearance events defined for this adapter.</p>';
      return;
    }
    // Build sprite clip options
    let spriteOpts = '<option value="none">none</option>';
    try {
      const clipsData = await clipsResp.json();
      const clips = clipsData.clips || [];
      clips.forEach(c => {
        spriteOpts += '<option value="' + esc(c.value) + '">' + esc(c.name) + '</option>';
      });
    } catch (_) { /* clips API unavailable — just show 'none' */ }

    let html = '';
    events.forEach(ev => {
      const e = appearance[ev] || {};
      // Build sprite options with the current value selected
      const spriteSelected = e.sprite || 'none';
      const spriteOptsSelected = spriteOpts.replace(
        'value="' + spriteSelected + '"',
        'value="' + spriteSelected + '" selected',
      );
      html += `
        <div class="appearance-row" data-event="${esc(ev)}" data-adapter="${esc(name)}">
          <div class="field-row">
            <label class="event-name">${esc(ev)}</label>
            <input type="text" class="appearance-icon" value="${esc(e.icon||'')}" maxlength="4" data-field="icon" placeholder="icon">
            <input type="color" class="appearance-color" value="${esc(e.color||'#000000')}" data-field="color">
            <select class="appearance-anim" data-field="animation">
              <option value="none" ${(e.animation||'none')==='none'?'selected':''}>none</option>
              <option value="pulse" ${e.animation==='pulse'?'selected':''}>pulse</option>
              <option value="crawl" ${e.animation==='crawl'?'selected':''}>crawl</option>
              <option value="blink" ${e.animation==='blink'?'selected':''}>blink</option>
              <option value="progress" ${e.animation==='progress'?'selected':''}>progress</option>
            </select>
            <select class="appearance-sprite" data-field="sprite">${spriteOptsSelected}</select>
            <input type="text" class="appearance-label" value="${esc(e.label||'')}" maxlength="12" data-field="label" placeholder="label">
            <input type="number" class="appearance-min-ms" value="${esc(e.min_display_ms||'')}" min="0" step="1" data-field="min_display_ms" placeholder="min ms">
          </div>
        </div>`;
    });
    tabContent.innerHTML = html;
    tabContent.querySelectorAll('input,select').forEach(el => {
      el.addEventListener('change', () => markDirty('appearance'));
    });
  } catch (e) {
    tabContent.innerHTML = '<p class="dim error">Failed: ' + esc(e.message) + '</p>';
  }
}

function switchAppearanceTab(name) {
  document.querySelectorAll('.appearance-tabs .tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.appearance-tab-content').forEach(c => c.classList.remove('active'));
  const btn = document.querySelector(`.appearance-tabs [data-tab="appearance_${esc(name)}"]`);
  if (btn) btn.classList.add('active');
  const tab = document.getElementById('appearanceTab_' + name);
  if (tab) tab.classList.add('active');
}

async function loadTheme() {
  const body = document.querySelector('[data-panel="theme"] [data-body]');
  if (!body) return;
  const rs = getComputedStyle(document.documentElement);
  const THEME_VARS = ['--bg','--panel','--surface','--border','--text','--text-dim','--accent','--danger','--warn','--info'];
  body.innerHTML = THEME_VARS.map(v => {
    const val = rs.getPropertyValue(v).trim();
    return `<div class="field-row">
      <label>${esc(v)}</label>
      <input type="color" class="theme-color" value="${esc(val)}" data-var="${esc(v)}" style="width:36px;height:28px">
      <input type="text" class="theme-text" value="${esc(val)}" data-var="${esc(v)}" style="flex:1">
    </div>`;
  }).join('');
  body.querySelectorAll('.theme-color').forEach(c => {
    const txt = body.querySelector(`.theme-text[data-var="${c.dataset.var}"]`);
    c.oninput = () => { txt.value = c.value; };
    txt.oninput = () => { c.value = txt.value; };
    c.addEventListener('change', () => markDirty('theme'));
    txt.addEventListener('change', () => markDirty('theme'));
  });
}

async function loadTerminals() {
  const body = document.querySelector('[data-panel="terminals"] [data-body]');
  if (!body) return;
  body.innerHTML = '<p class="dim">Loading...</p>';
  try {
    const resp = await fetch('/api/terminals');
    if (!resp.ok) throw new Error('Failed to load terminals');
    const data = await resp.json();
    const terminals = data.terminals || [];
    if (terminals.length === 0) {
      body.innerHTML = '<div class="empty-panel"><p class="dim">No terminals registered.</p></div>';
      return;
    }
    body.innerHTML = terminals.map(t => `
      <div class="card terminal-card" data-terminal-id="${esc(t.id)}">
        <div class="card-header">
          <span class="card-name">${esc(t.name || t.id)}</span>
          <span class="card-badge ${t.type}">${esc(t.type)}</span>
          <span class="card-badge grid-badge">${esc(t.grid)}</span>
        </div>
        <div class="card-body">
          <div class="card-row"><label>ID</label><code>${esc(t.id)}</code></div>
          <div class="card-row"><label>Token</label><code class="token-val">${esc(t.token)}</code>
            <button class="btn-sm" onclick="copyToken('${esc(t.token)}')" title="Copy token">📋</button>
          </div>
          <div class="card-row"><label>Widgets</label><span>${t.widget_count != null ? t.widget_count : '—'}</span></div>
          <div class="card-row"><label>Created</label><span>${t.created_at ? new Date(t.created_at).toLocaleString() : '—'}</span></div>
        </div>
        <div class="card-actions">
          <button class="btn-sm" onclick="renameTerminal('${esc(t.id)}')">✏️ Rename</button>
          <button class="btn-sm" onclick="changeTerminalGrid('${esc(t.id)}')">📐 Grid</button>
          ${t.id !== 'default' ? `<button class="btn-sm btn-danger" onclick="deleteTerminal('${esc(t.id)}')">🗑 Delete</button>` : ''}
        </div>
      </div>
    `).join('');
  } catch (e) {
    body.innerHTML = '<p class="dim error">Failed to load terminals: ' + esc(e.message) + '</p>';
  }
}

async function loadLayouts() {
  const body = document.querySelector('[data-panel="layouts"] [data-body]');
  if (!body) return;
  body.innerHTML = '<p class="dim">Loading...</p>';
  try {
    const resp = await fetch('/api/layouts');
    if (!resp.ok) throw new Error('Failed to load layouts');
    const data = await resp.json();
    const layouts = data.layouts || [];
    if (layouts.length === 0) {
      body.innerHTML =
        '<div class="empty-panel"><p class="dim">No saved layouts.</p>' +
        '<p class="dim" style="font-size:0.72rem">Save a layout from the main UI to see it here.</p></div>';
      return;
    }
    body.innerHTML = layouts.map(l => `
      <div class="card layout-card" data-layout-name="${esc(l.name)}">
        <div class="card-header">
          <span class="card-name">${esc(l.name)}</span>
        </div>
        <div class="card-actions">
          <button class="btn-sm" onclick="loadLayoutByName('${esc(l.name)}')">📂 Load</button>
          <button class="btn-sm" onclick="renameLayout('${esc(l.name)}')">✏️ Rename</button>
          <button class="btn-sm" onclick="exportLayout('${esc(l.name)}')">📥 Export</button>
          <button class="btn-sm btn-danger" onclick="deleteLayout('${esc(l.name)}')">🗑 Delete</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    body.innerHTML = '<p class="dim error">Failed to load layouts: ' + esc(e.message) + '</p>';
  }
}

async function loadAdapters() {
  const body = document.querySelector('[data-panel="adapters"] [data-body]');
  if (!body) return;
  body.innerHTML = '<p class="dim">Loading...</p>';
  try {
    const resp = await fetch('/api/adapters');
    if (!resp.ok) throw new Error('Failed to load adapters');
    const data = await resp.json();
    const adapters = data.adapters || [];
    if (adapters.length === 0) {
      body.innerHTML = '<p class="dim">No adapters registered.</p>';
      return;
    }
    body.innerHTML = adapters.map(a => `
      <div class="card adapter-card" data-adapter="${esc(a.name)}">
        <div class="card-header">
          <span class="card-name">${esc(a.name)}</span>
          <span class="card-badge ${a.enabled ? 'enabled' : 'disabled'}">${a.enabled ? 'Enabled' : 'Disabled'}</span>
        </div>
        <div class="card-body">
          <div class="card-row"><label>Class</label><code>${esc(a.class)}</code></div>
          <div class="card-row"><label>Module</label><code style="font-size:0.65rem">${esc(a.module)}</code></div>
        </div>
        <div class="card-actions">
          <button class="btn-sm" onclick="toggleAdapter('${esc(a.name)}', ${!a.enabled})">${a.enabled ? 'Disable' : 'Enable'}</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    body.innerHTML = '<p class="dim error">Failed to load adapters: ' + esc(e.message) + '</p>';
  }
}

async function loadAbout() {
  const body = document.querySelector('[data-panel="about"] [data-body]');
  if (!body) return;
  try {
    const resp = await fetch('/api/config');
    let version = '—', uptime = '—';
    if (resp.ok) {
      const data = await resp.json();
      version = '1.0.0'; // Static for now; could come from config
    }
    body.innerHTML = `
      <div class="about-section">
        <h2>🦞 VibeDeck</h2>
        <p class="dim">Real-time agent monitoring for your Stream Deck.</p>
        <table class="about-table">
          <tr><td>Version</td><td>${esc(version)}</td></tr>
          <tr><td>API Port</td><td>9734</td></tr>
          <tr><td>Built with</td><td>Python + aiohttp + Vanilla JS</td></tr>
          <tr><td>Repository</td><td><a href="https://github.com/TamamoGroup/VibeDeck" target="_blank">github.com/TamamoGroup/VibeDeck</a></td></tr>
        </table>
      </div>
    `;
  } catch (e) {
    body.innerHTML = '<p class="dim error">Failed to load about info: ' + esc(e.message) + '</p>';
  }
}

// ── Save / Submit ──────────────────────────────────
let dirtyPanels = {};

function markDirty(panel) {
  dirtyPanels[panel] = true;
  const indicator = document.querySelector(`[data-nav="${panel}"] .unsaved-dot`);
  if (indicator) indicator.style.display = 'inline-block';
}

function clearDirty(panel) {
  dirtyPanels[panel] = false;
  const indicator = document.querySelector(`[data-nav="${panel}"] .unsaved-dot`);
  if (indicator) indicator.style.display = 'none';
}

async function savePanel(panel) {
  switch (panel) {
    case 'daemon': await saveDaemon(); break;
    case 'timing': await saveTiming(); break;
    case 'appearance': await saveAppearance(); break;
    case 'theme': await saveTheme(); break;
  }
}

async function saveDaemon() {
  const body = {
    port: parseInt(document.getElementById('cfgPort')?.value) || 9734,
    expose: document.getElementById('cfgExpose')?.value === 'true',
    autodetect: document.getElementById('cfgAutodetect')?.value === 'true',
    render: document.getElementById('cfgRender')?.value || 'sim',
  };
  // Timing fields from daemon panel
  const timing = {};
  TIMING_FIELDS.forEach(f => {
    const el = document.getElementById('cfgTiming_' + f);
    if (el && el.dataset.dirty === 'true') timing[f] = parseInt(el.value) || 0;
  });
  if (Object.keys(timing).length > 0) body.timing = timing;

  try {
    const resp = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error('Save failed: ' + ((await resp.json()).error || resp.statusText));
    toast('Daemon config saved');
    clearDirty('daemon');
  } catch (e) {
    toast('Failed to save daemon config: ' + e.message, 'error');
  }
}

async function saveTiming() {
  const timing = {};
  TIMING_FIELDS.forEach(f => {
    const el = document.getElementById('timing_' + f);
    if (el) timing[f] = parseInt(el.value) || 0;
  });
  try {
    const resp = await fetch('/api/timing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ timing }),
    });
    if (!resp.ok) throw new Error('Save failed: ' + ((await resp.json()).error || resp.statusText));
    toast('Timing saved');
    clearDirty('timing');

    // Also push min_display_ms to each adapter's appearance if present
    const appearanceRows = document.querySelectorAll('[data-panel="appearance"] .appearance-row');
    const adaptersToSave = new Set();
    appearanceRows.forEach(row => {
      const adapter = row.dataset.adapter;
      if (adapter) adaptersToSave.add(adapter);
    });
    for (const adapter of adaptersToSave) {
      await saveAdapterAppearance(adapter);
    }
  } catch (e) {
    toast('Failed to save timing: ' + e.message, 'error');
  }
}

async function saveAppearance() {
  const activeTab = document.querySelector('.appearance-tab-content.active');
  if (!activeTab) return;
  const adapter = activeTab.dataset.adapter;
  if (!adapter) return;
  await saveAdapterAppearance(adapter);
}

async function saveAdapterAppearance(adapter) {
  const tabContent = document.getElementById('appearanceTab_' + adapter);
  if (!tabContent) return;
  const rows = tabContent.querySelectorAll('.appearance-row');
  const appearance = {};
  rows.forEach(row => {
    const eventName = row.dataset.event;
    if (!eventName) return;
    const entry = {};
    row.querySelectorAll('[data-field]').forEach(el => {
      entry[el.dataset.field] = el.value;
    });
    // Clean up — remove empty strings
    Object.keys(entry).forEach(k => {
      if (entry[k] === '' || entry[k] === null || entry[k] === undefined) delete entry[k];
    });
    if (Object.keys(entry).length > 0) appearance[eventName] = entry;
  });
  try {
    const resp = await fetch('/api/adapters/' + encodeURIComponent(adapter) + '/appearance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ appearance }),
    });
    if (!resp.ok) throw new Error('Save failed: ' + ((await resp.json()).error || resp.statusText));
    toast('Appearance saved for ' + adapter);
    clearDirty('appearance');
  } catch (e) {
    toast('Failed to save appearance: ' + e.message, 'error');
  }
}

async function saveTheme() {
  let css = ':root {\n';
  document.querySelectorAll('[data-panel="theme"] .theme-text').forEach(txt => {
    css += '  ' + txt.dataset.var + ': ' + txt.value + ';\n';
  });
  css += '}';
  try {
    const resp = await fetch('/api/theme', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ css }),
    });
    if (!resp.ok) throw new Error('Save failed');
    // Apply locally
    document.querySelectorAll('[data-panel="theme"] .theme-text').forEach(txt => {
      document.documentElement.style.setProperty(txt.dataset.var, txt.value);
    });
    toast('Theme saved');
    clearDirty('theme');
  } catch (e) {
    toast('Failed to save theme: ' + e.message, 'error');
  }
}

// ── Terminal Actions ──────────────────────────────
async function renameTerminal(id) {
  const name = prompt('New name for terminal ' + id + ':');
  if (!name || !name.trim()) return;
  try {
    const resp = await fetch('/api/terminals/' + encodeURIComponent(id) + '/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() }),
    });
    if (!resp.ok) throw new Error('Rename failed');
    toast('Terminal renamed');
    loadTerminals();
  } catch (e) {
    toast('Failed to rename terminal: ' + e.message, 'error');
  }
}

async function deleteTerminal(id) {
  if (!confirm('Are you sure you want to delete terminal "' + id + '"? This cannot be undone.')) return;
  try {
    const resp = await fetch('/api/terminals/' + encodeURIComponent(id), { method: 'DELETE' });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.error || resp.statusText);
    }
    toast('Terminal deleted');
    loadTerminals();
  } catch (e) {
    toast('Failed to delete terminal: ' + e.message, 'error');
  }
}

async function changeTerminalGrid(id) {
  const grid = prompt('New grid size for terminal ' + id + ' (3x4, 3x5, 4x8):');
  if (!grid || VALID_GRIDS.indexOf(grid.trim()) === -1) return;
  try {
    const resp = await fetch('/api/terminals/' + encodeURIComponent(id) + '/grid', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ grid: grid.trim() }),
    });
    if (!resp.ok) throw new Error('Grid change failed');
    toast('Grid changed to ' + grid.trim());
    loadTerminals();
  } catch (e) {
    toast('Failed to change grid: ' + e.message, 'error');
  }
}

function copyToken(token) {
  navigator.clipboard.writeText(token).then(() => {
    toast('Token copied to clipboard');
  }).catch(() => {
    toast('Failed to copy', 'error');
  });
}

async function registerNewTerminal() {
  const name = document.getElementById('newTermName')?.value?.trim() || 'My Terminal';
  const grid = document.getElementById('newTermGrid')?.value || '4x8';
  try {
    const resp = await fetch('/api/terminal/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, grid, type: 'virtual' }),
    });
    if (!resp.ok) throw new Error((await resp.json()).error || 'Registration failed');
    toast('New terminal registered');
    loadTerminals();
    // Clear form
    const nameEl = document.getElementById('newTermName');
    if (nameEl) nameEl.value = '';
  } catch (e) {
    toast('Failed to register terminal: ' + e.message, 'error');
  }
}

// ── Layout Actions ────────────────────────────────
async function deleteLayout(name) {
  if (!confirm('Delete layout "' + name + '"? This cannot be undone.')) return;
  try {
    const resp = await fetch('/api/layouts/' + encodeURIComponent(name), { method: 'DELETE' });
    if (!resp.ok) throw new Error('Delete failed');
    toast('Layout "' + name + '" deleted');
    loadLayouts();
  } catch (e) {
    toast('Failed to delete layout: ' + e.message, 'error');
  }
}

async function renameLayout(name) {
  const newName = prompt('New name for layout "' + name + '":');
  if (!newName || !newName.trim()) return;
  try {
    const resp = await fetch('/api/layouts/' + encodeURIComponent(name) + '/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_name: newName.trim() }),
    });
    if (!resp.ok) throw new Error((await resp.json()).error || 'Rename failed');
    toast('Layout renamed to "' + newName.trim() + '"');
    loadLayouts();
  } catch (e) {
    toast('Failed to rename layout: ' + e.message, 'error');
  }
}

async function loadLayoutByName(name) {
  try {
    const resp = await fetch('/api/layouts/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, terminal_id: 'default' }),
    });
    if (!resp.ok) throw new Error('Load failed');
    toast('Layout "' + name + '" loaded');
  } catch (e) {
    toast('Failed to load layout: ' + e.message, 'error');
  }
}

async function exportLayout(name) {
  try {
    // Try direct download from API first, then fall back to constructing path
    const resp = await fetch('/api/layout/' + encodeURIComponent(name) + '/file');
    if (resp.ok) {
      const blob = await resp.blob();
      downloadBlob(blob, name + '.yaml');
      toast('Layout "' + name + '" exported');
      return;
    }
  } catch (e) { /* fall through */ }

  // Fallback: trigger download via known path
  try {
    const resp = await fetch('/api/layouts');
    if (!resp.ok) throw new Error('Failed to list layouts');
    const data = await resp.json();
    const layout = (data.layouts || []).find(l => l.name === name);
    if (layout && layout.path) {
      const fileResp = await fetch('/api/layout/' + encodeURIComponent(name) + '/file');
      if (fileResp.ok) {
        const blob = await fileResp.blob();
        downloadBlob(blob, name + '.yaml');
        toast('Layout "' + name + '" exported');
        return;
      }
    }
    throw new Error('Export endpoint not available');
  } catch (e) {
    toast('Failed to export layout: ' + e.message, 'error');
  }
}

function triggerLayoutImport() {
  document.getElementById('layoutFileInput')?.click();
}

async function handleLayoutImport(file) {
  if (!file) return;
  try {
    const text = await file.text();
    const resp = await fetch('/api/layouts/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: file.name.replace(/\.yaml$/i, ''), yaml: text }),
    });
    if (resp.ok) {
      toast('Layout "' + file.name + '" imported');
      loadLayouts();
    } else {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || 'Import failed');
    }
  } catch (e) {
    // Fallback: no import endpoint, direct save
    try {
      const name = file.name.replace(/\.yaml$/i, '');
      const text = await file.text();
      const resp = await fetch('/api/config', {
        method: 'GET',
      });
      // If import endpoint doesn't exist, inform the user
      toast('Import endpoint not available. Save the layout from the main UI.', 'error');
    } catch (e2) {
      toast('Failed to import layout: ' + e.message, 'error');
    }
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── Adapter Actions ───────────────────────────────
async function toggleAdapter(name, enable) {
  try {
    const resp = await fetch('/api/adapters/' + encodeURIComponent(name) + '/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enable }),
    });
    if (!resp.ok) throw new Error('Toggle failed');
    toast((enable ? 'Enabled' : 'Disabled') + ' adapter ' + name);
    loadAdapters();
  } catch (e) {
    toast('Failed to toggle adapter: ' + e.message, 'error');
  }
}

// ── Utility ────────────────────────────────────────
function toast(msg, type) {
  type = type || 'success';
  const container = document.getElementById('toastContainer') || (() => {
    const c = document.createElement('div');
    c.id = 'toastContainer';
    c.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:999;display:flex;flex-direction:column-reverse;gap:8px;pointer-events:none';
    document.body.appendChild(c);
    return c;
  })();
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    el.addEventListener('animationend', () => el.remove());
  }, 2200);
}

function timingDesc(field) {
  const descs = {
    thinking_timeout_ms: 'Silence (ms) before "Thinking" state',
    activity_window_ms: 'Fast frame-rate window (ms) after event',
    fast_frame_interval_ms: 'Frame push interval (ms) when active (~30fps)',
    slow_frame_interval_ms: 'Frame push interval (ms) when idle (~1fps)',
  };
  return descs[field] || '';
}

function esc(s) {
  return (s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Initialization ─────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  handleHash();
  window.addEventListener('hashchange', handleHash);

  // Set up navigation click handlers
  document.querySelectorAll('[data-nav]').forEach(el => {
    el.addEventListener('click', () => showPanel(el.dataset.nav));
  });

  // Back button
  const backBtn = document.querySelector('[data-action="back"]');
  if (backBtn) backBtn.addEventListener('click', goBack);

  // Save buttons
  document.querySelectorAll('[data-action="save"]').forEach(el => {
    el.addEventListener('click', () => savePanel(el.dataset.panel));
  });

  // Import file input
  const fileInput = document.getElementById('layoutFileInput');
  if (fileInput) {
    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length > 0) handleLayoutImport(e.target.files[0]);
      e.target.value = '';
    });
  }

  // New terminal registration
  const registerBtn = document.querySelector('[data-action="register-terminal"]');
  if (registerBtn) registerBtn.addEventListener('click', registerNewTerminal);
});
