/* ── State ──────────────────────────────────────────────────────────────────── */
const state = {
  jobs: [],
  domains: [],
  filters: {
    search: '',
    location: '',
    domain: 'all',
    positionType: 'all',
    sort: 'deadline',
    hideApplied: false
  },
  selected: new Set(),          // job ids currently selected
  expandedDescriptions: new Set(),
  feeds: []
};

// Track which notes textarea is focused to avoid losing input on re-render
let focusedNoteId = null;
let focusedNoteValue = null;
let noteSaveTimeout = null;

/* ── API helpers ────────────────────────────────────────────────────────────── */
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

/* ── Toast ──────────────────────────────────────────────────────────────────── */
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

/* ── Deadline helpers ───────────────────────────────────────────────────────── */
function deadlineInfo(deadline) {
  if (!deadline) return { cls: 'deadline-none', label: 'No deadline', days: Infinity };
  const now = new Date(); now.setHours(0,0,0,0);
  const d = new Date(deadline); d.setHours(0,0,0,0);
  const days = Math.round((d - now) / 86400000);
  if (days < 0)  return { cls: 'deadline-past',   label: `Closed ${-days}d ago`, days };
  if (days === 0) return { cls: 'deadline-urgent', label: 'Today!', days };
  if (days <= 14) return { cls: 'deadline-urgent', label: `${days}d left`, days };
  if (days <= 30) return { cls: 'deadline-soon',   label: `${days}d left`, days };
  return { cls: 'deadline-ok', label: d.toLocaleDateString('en-GB', { day:'numeric', month:'short', year:'numeric' }), days };
}

/* ── Position type badge HTML ───────────────────────────────────────────────── */
function postypeBadge(type) {
  const labels = { postdoc: 'Postdoc', phd: 'PhD', other: 'Other' };
  const t = type || 'other';
  return `<span class="postype-badge postype-${t}">${labels[t] || t}</span>`;
}

/* ── Domain label helper ────────────────────────────────────────────────────── */
function domainLabel(d) {
  return d.length <= 4 ? d.toUpperCase() : d.charAt(0).toUpperCase() + d.slice(1);
}

/* ── Chip HTML ──────────────────────────────────────────────────────────────── */
function chipHTML(domain) {
  const known = state.domains.includes(domain);
  const cls = known ? `chip-${domain}` : 'chip-other';
  return `<span class="chip ${cls}">${domainLabel(domain)}</span>`;
}

/* ── Build domain UI from config ────────────────────────────────────────────── */
function buildDomainUI(domains) {
  // Sidebar filter buttons (keep the "All" button, append domain buttons)
  const filterContainer = document.getElementById('domain-filters');
  filterContainer.innerHTML = '<button class="domain-toggle active-all" data-domain="all">All</button>';
  domains.forEach(d => {
    const btn = document.createElement('button');
    btn.className = 'domain-toggle';
    btn.dataset.domain = d;
    btn.textContent = domainLabel(d);
    filterContainer.appendChild(btn);
  });

  // Manual form checkboxes
  document.getElementById('m-domain-checks').innerHTML =
    domains.map(d => `<label><input type="checkbox" id="m-${d}" /> ${domainLabel(d)}</label>`).join('');

  // Preview/edit modal checkboxes
  document.getElementById('p-domain-checks').innerHTML =
    domains.map(d => `<label><input type="checkbox" id="p-${d}" /> ${domainLabel(d)}</label>`).join('');
}

/* ── Stars HTML ─────────────────────────────────────────────────────────────── */
function starsHTML(id, affinity) {
  return Array.from({ length: 5 }, (_, i) => {
    const n = i + 1;
    const filled = n <= affinity ? 'filled' : '';
    return `<button class="star ${filled}" data-id="${id}" data-star="${n}" title="Affinity ${n}/5">★</button>`;
  }).join('');
}

/* ── Render job card ────────────────────────────────────────────────────────── */
function renderCard(job) {
  const dl = deadlineInfo(job.deadline);
  const tags = (job.domains || []).map(chipHTML).join('') || chipHTML('other');
  const stars = starsHTML(job.id, job.affinity || 0);
  const expanded = state.expandedDescriptions.has(job.id);
  const desc = job.description || '';
  const truncated = desc.length > 250 && !expanded;
  const displayDesc = truncated ? desc.substring(0, 250) + '…' : desc;

  // Preserve in-progress note value
  const noteVal = (focusedNoteId === job.id && focusedNoteValue !== null)
    ? focusedNoteValue
    : (job.notes || '');

  const salaryBadge = job.salary ? `<span class="salary-badge">💰 ${job.salary}</span>` : '';

  const isSelected = state.selected.has(job.id);
  const div = document.createElement('div');
  div.className = `job-card${job.applied ? ' applied' : ''}${isSelected ? ' selected' : ''}`;
  div.dataset.id = job.id;
  div.innerHTML = `
    <input type="checkbox" class="card-select-cb" data-id="${job.id}" ${isSelected ? 'checked' : ''} title="Select" />
    <div class="card-top">
      <div class="card-main">
        <div class="card-title">
          ${job.url
            ? `<a href="${escHtml(job.url)}" target="_blank" rel="noopener">${escHtml(job.title)}</a>`
            : escHtml(job.title)}
        </div>
        <div class="card-meta">
          ${job.institution ? `<span class="card-institution">${escHtml(job.institution)}</span>` : ''}
          ${job.institution && job.location ? '<span class="sep">·</span>' : ''}
          ${job.location ? `<span class="card-location">${escHtml(job.location)}</span>` : ''}
          ${salaryBadge}
        </div>
        <div class="card-tags">${postypeBadge(job.positionType)}${tags}</div>
        <span class="deadline-badge ${dl.cls}">📅 ${dl.label}</span>
      </div>
      <div class="star-rating">${stars}</div>
    </div>

    <div class="card-controls">
      <label class="applied-check">
        <input type="checkbox" data-id="${job.id}" class="applied-cb" ${job.applied ? 'checked' : ''} />
        Applied${job.appliedAt ? ` (${new Date(job.appliedAt).toLocaleDateString('en-GB')})` : ''}
      </label>
      <div class="card-actions">
        <button class="btn-secondary btn-sm edit-btn" data-id="${job.id}" title="Edit">✏️</button>
        <button class="btn-icon btn-sm delete-btn" data-id="${job.id}" title="Delete">🗑</button>
      </div>
    </div>

    <div class="notes-area">
      <div class="notes-label">Notes</div>
      <textarea class="notes-ta" data-id="${job.id}" rows="2" placeholder="Add notes…">${escHtml(noteVal)}</textarea>
    </div>

    ${desc ? `
    <div>
      <button class="btn-expand expand-btn" data-id="${job.id}">
        ${expanded ? '▲ Show less' : '▼ Show description'}
      </button>
      ${expanded ? `<div class="description">${escHtml(displayDesc)}</div>` : ''}
    </div>` : ''}
  `;
  return div;
}

function escHtml(str) {
  return String(str || '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

/* ── Render all ─────────────────────────────────────────────────────────────── */
function render() {
  const { search, location, domain, positionType, sort, hideApplied } = state.filters;
  let jobs = [...state.jobs];

  if (search) {
    const q = search.toLowerCase();
    jobs = jobs.filter(j =>
      (j.title + ' ' + j.institution + ' ' + j.location + ' ' + j.notes).toLowerCase().includes(q)
    );
  }
  if (location) {
    const loc = location.toLowerCase();
    jobs = jobs.filter(j => j.location.toLowerCase().includes(loc));
  }
  if (domain !== 'all') {
    jobs = jobs.filter(j => j.domains.includes(domain));
  }
  if (positionType !== 'all') {
    jobs = jobs.filter(j => (j.positionType || 'other') === positionType);
  }
  if (hideApplied) {
    jobs = jobs.filter(j => !j.applied);
  }

  const nullLast = d => d ? new Date(d) : new Date('9999-12-31');
  if (sort === 'deadline')  jobs.sort((a,b) => nullLast(a.deadline) - nullLast(b.deadline));
  if (sort === 'affinity')  jobs.sort((a,b) => b.affinity - a.affinity);
  if (sort === 'added')     jobs.sort((a,b) => new Date(b.addedAt) - new Date(a.addedAt));

  const container = document.getElementById('jobs-container');
  container.innerHTML = '';

  if (jobs.length === 0) {
    container.innerHTML = `<div class="empty-state">
      <div class="empty-icon">🔭</div>
      ${state.jobs.length === 0
        ? 'No jobs yet. Use the panel above to fetch or add jobs.'
        : 'No jobs match the current filters.'}
    </div>`;
  } else {
    jobs.forEach(j => container.appendChild(renderCard(j)));
  }

  // Restore focus if a note was being edited
  if (focusedNoteId) {
    const ta = container.querySelector(`.notes-ta[data-id="${focusedNoteId}"]`);
    if (ta) {
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }
  }

  updateStats(jobs);
}

function updateStats(filtered) {
  const all = state.jobs;
  const applied = all.filter(j => j.applied).length;
  const urgent = all.filter(j => { const d = deadlineInfo(j.deadline); return d.days >= 0 && d.days <= 14; }).length;
  document.getElementById('header-stats').textContent =
    `${filtered.length} job${filtered.length !== 1 ? 's' : ''} shown`;
  document.getElementById('sidebar-stats').innerHTML = `
    Total: <strong>${all.length}</strong><br>
    Applied: <strong>${applied}</strong><br>
    Deadline ≤ 14d: <strong style="color:var(--red)">${urgent}</strong><br>
    Unrated: <strong>${all.filter(j => !j.affinity).length}</strong>
  `;
}

/* ── Load jobs from server ──────────────────────────────────────────────────── */
async function loadJobs() {
  try {
    const data = await api('GET', '/api/jobs');
    state.jobs = data.jobs;
    render();
  } catch (e) {
    toast('Failed to load jobs: ' + e.message, 'error');
  }
}

/* ── Load feeds list ────────────────────────────────────────────────────────── */
async function loadFeeds() {
  try {
    const feeds = await api('GET', '/api/feeds');
    state.feeds = feeds;
    const sel = document.getElementById('feed-source');
    sel.innerHTML = feeds.map(f => `<option value="${f.id}" data-loc="${f.supportsLocation ? '1' : '0'}">${escHtml(f.name)}</option>`).join('');
    updateFeedLocationVisibility();
  } catch {}
}

function updateFeedLocationVisibility() {
  const sel = document.getElementById('feed-source');
  const opt = sel.options[sel.selectedIndex];
  const supported = opt && opt.dataset.loc === '1';
  const grp = document.getElementById('feed-location-group');
  grp.style.opacity = supported ? '1' : '0.35';
  document.getElementById('feed-location').disabled = !supported;
}

/* ── Filter event listeners ─────────────────────────────────────────────────── */
let searchDebounce = null;
document.getElementById('search-input').addEventListener('input', e => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => { state.filters.search = e.target.value; render(); }, 200);
});

document.getElementById('location-input').addEventListener('input', e => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => { state.filters.location = e.target.value; render(); }, 200);
});

document.getElementById('sort-select').addEventListener('change', e => {
  state.filters.sort = e.target.value; render();
});

document.getElementById('hide-applied-toggle').addEventListener('change', e => {
  state.filters.hideApplied = e.target.checked; render();
});

document.getElementById('domain-filters').addEventListener('click', e => {
  const btn = e.target.closest('.domain-toggle');
  if (!btn) return;
  document.querySelectorAll('.domain-toggle').forEach(b => { b.className = 'domain-toggle'; });
  const d = btn.dataset.domain;
  btn.className = `domain-toggle active-${d}`;
  state.filters.domain = d;
  render();
});

document.querySelectorAll('.postype-toggle').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.postype-toggle').forEach(b => { b.className = 'postype-toggle'; });
    const t = btn.dataset.postype;
    btn.className = `postype-toggle active-${t}`;
    state.filters.positionType = t;
    render();
  });
});

/* ── Fetch panel toggle ─────────────────────────────────────────────────────── */
document.getElementById('fetch-panel-toggle').addEventListener('click', () => {
  const body = document.getElementById('fetch-panel-body');
  const caret = document.getElementById('fetch-caret');
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  caret.textContent = open ? '▼' : '▲';
});

/* ── Tabs ───────────────────────────────────────────────────────────────────── */
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
  });
});

/* ── Feed source change → toggle location field ─────────────────────────────── */
document.getElementById('feed-source').addEventListener('change', updateFeedLocationVisibility);

/* ── Fetch from feed ────────────────────────────────────────────────────────── */
document.getElementById('fetch-feed-btn').addEventListener('click', async () => {
  const source    = document.getElementById('feed-source').value;
  const keywords  = document.getElementById('feed-keywords').value.trim();
  const location  = document.getElementById('feed-location').value.trim();
  const customUrl = document.getElementById('feed-custom-url').value.trim();
  const spinner   = document.getElementById('feed-spinner');
  const errEl     = document.getElementById('feed-error');
  errEl.style.display = 'none';
  spinner.style.display = 'inline';
  document.getElementById('fetch-feed-btn').disabled = true;

  try {
    const data = await api('POST', '/api/fetch/feed', { source, keywords, location, customUrl });
    openImportModal(data.items, data.feedTitle || 'Feed Results');
  } catch (e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
    toast('Fetch failed: ' + e.message, 'error');
  } finally {
    spinner.style.display = 'none';
    document.getElementById('fetch-feed-btn').disabled = false;
  }
});

/* ── Fetch from URL ─────────────────────────────────────────────────────────── */
document.getElementById('fetch-url-btn').addEventListener('click', async () => {
  const url = document.getElementById('url-input').value.trim();
  if (!url) return toast('Enter a URL first', 'error');
  const spinner = document.getElementById('url-spinner');
  const errEl = document.getElementById('url-error');
  errEl.style.display = 'none';
  spinner.style.display = 'block';
  document.getElementById('fetch-url-btn').disabled = true;

  try {
    const job = await api('POST', '/api/fetch/url', { url });
    job.url = job.url || url;
    openPreviewModal(job);
    document.getElementById('url-input').value = '';
  } catch (e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
    toast('Fetch failed: ' + e.message, 'error');
  } finally {
    spinner.style.display = 'none';
    document.getElementById('fetch-url-btn').disabled = false;
  }
});

/* ── Manual add ─────────────────────────────────────────────────────────────── */
document.getElementById('manual-add-btn').addEventListener('click', async () => {
  const title = document.getElementById('m-title').value.trim();
  if (!title) return toast('Title is required', 'error');

  const domains = state.domains.filter(d => document.getElementById(`m-${d}`)?.checked);

  const job = {
    title,
    institution: document.getElementById('m-institution').value.trim(),
    location: document.getElementById('m-location').value.trim(),
    deadline: document.getElementById('m-deadline').value || null,
    url: document.getElementById('m-url').value.trim(),
    salary: document.getElementById('m-salary').value.trim(),
    description: document.getElementById('m-description').value.trim(),
    domains,
    positionType: document.getElementById('m-postype').value,
    source: 'manual'
  };

  try {
    const added = await api('POST', '/api/jobs', job);
    state.jobs.push(added);
    render();
    toast('Job added!', 'success');
    // Clear form
    ['m-title','m-institution','m-location','m-url','m-salary','m-description'].forEach(id => {
      document.getElementById(id).value = '';
    });
    document.getElementById('m-deadline').value = '';
    document.getElementById('m-postype').value = 'postdoc';
    state.domains.forEach(d => { const el = document.getElementById(`m-${d}`); if (el) el.checked = false; });
  } catch (e) {
    toast('Error: ' + e.message, 'error');
  }
});

/* ── Import modal ───────────────────────────────────────────────────────────── */
let importItems = [];
let importSort  = { col: null, dir: 1 }; // dir: 1=asc, -1=desc

function renderImportTable() {
  const sorted = [...importItems];
  if (importSort.col) {
    const col = importSort.col;
    const dir = importSort.dir;
    sorted.sort((a, b) => {
      let va = (a[col] || '').toString().toLowerCase();
      let vb = (b[col] || '').toString().toLowerCase();
      if (col === 'deadline') {
        va = a.deadline || '9999-12-31';
        vb = b.deadline || '9999-12-31';
      }
      if (va < vb) return -dir;
      if (va > vb) return  dir;
      return 0;
    });
  }

  // Update header indicators
  document.querySelectorAll('.import-table th.sortable').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.col === importSort.col) {
      th.classList.add(importSort.dir === 1 ? 'sort-asc' : 'sort-desc');
    }
  });

  const tbody = document.getElementById('import-table-body');
  tbody.innerHTML = '';
  sorted.forEach((item) => {
    const origIdx = importItems.indexOf(item);
    const dl  = deadlineInfo(item.deadline);
    const tags = (item.domains || []).map(chipHTML).join('') || '<span style="color:var(--muted)">—</span>';
    const tr  = document.createElement('tr');
    if (item.alreadyAdded) tr.className = 'already-added';
    tr.innerHTML = `
      <td><input type="checkbox" class="import-cb" data-idx="${origIdx}" ${item.alreadyAdded ? 'disabled' : ''} /></td>
      <td class="import-title">
        <a href="${escHtml(item.url || '#')}" target="_blank" rel="noopener">${escHtml(item.title)}</a>
        ${item.alreadyAdded ? '<span class="already-badge">✓ Added</span>' : ''}
        ${item.institution ? `<div style="color:var(--muted);font-size:12px;margin-top:2px;">${escHtml(item.institution)}</div>` : ''}
      </td>
      <td>${escHtml(item.location || '—')}</td>
      <td><span class="deadline-badge ${dl.cls}" style="font-size:11px;">${dl.label}</span></td>
      <td>${postypeBadge(item.positionType)}</td>
      <td>${tags}</td>
    `;
    tbody.appendChild(tr);
  });
}

function openImportModal(items, title) {
  importItems = items;
  importSort  = { col: null, dir: 1 };
  document.getElementById('import-modal-title').textContent = title;
  document.getElementById('import-select-all').checked = false;
  renderImportTable();
  document.getElementById('import-modal').style.display = 'flex';
}

document.querySelector('.import-table thead').addEventListener('click', e => {
  const th = e.target.closest('th.sortable');
  if (!th) return;
  const col = th.dataset.col;
  if (importSort.col === col) {
    importSort.dir *= -1;
  } else {
    importSort.col = col;
    importSort.dir = 1;
  }
  renderImportTable();
});

document.getElementById('import-select-all').addEventListener('change', e => {
  document.querySelectorAll('.import-cb:not(:disabled)').forEach(cb => {
    cb.checked = e.target.checked;
  });
});

document.getElementById('import-modal-close').addEventListener('click', () => {
  document.getElementById('import-modal').style.display = 'none';
});
document.getElementById('import-cancel-btn').addEventListener('click', () => {
  document.getElementById('import-modal').style.display = 'none';
});

document.getElementById('import-confirm-btn').addEventListener('click', async () => {
  const selected = [];
  document.querySelectorAll('.import-cb:checked').forEach(cb => {
    selected.push(importItems[parseInt(cb.dataset.idx)]);
  });
  if (!selected.length) return toast('Select at least one job', 'error');

  try {
    const result = await api('POST', '/api/jobs/bulk', { jobs: selected });
    state.jobs.push(...result.jobs);
    render();
    document.getElementById('import-modal').style.display = 'none';
    toast(`${result.added} job${result.added !== 1 ? 's' : ''} imported!`, 'success');
  } catch (e) {
    toast('Import failed: ' + e.message, 'error');
  }
});

/* ── Preview modal ──────────────────────────────────────────────────────────── */
let previewSource = 'url';

function openPreviewModal(job, source = 'url') {
  previewSource = source;
  document.getElementById('preview-modal-title').textContent = 'Add Job';
  document.getElementById('p-title').value = job.title || '';
  document.getElementById('p-institution').value = job.institution || '';
  document.getElementById('p-location').value = job.location || '';
  document.getElementById('p-deadline').value = job.deadline || '';
  document.getElementById('p-url').value = job.url || '';
  document.getElementById('p-salary').value = job.salary || '';
  document.getElementById('p-description').value = job.description || '';
  document.getElementById('p-postype').value = job.positionType || 'postdoc';
  state.domains.forEach(d => {
    const el = document.getElementById(`p-${d}`);
    if (el) el.checked = (job.domains || []).includes(d);
  });
  document.getElementById('preview-modal').style.display = 'flex';
}

document.getElementById('preview-modal-close').addEventListener('click', () => {
  document.getElementById('preview-modal').style.display = 'none';
});
document.getElementById('preview-cancel-btn').addEventListener('click', () => {
  document.getElementById('preview-modal').style.display = 'none';
});

document.getElementById('preview-save-btn').addEventListener('click', async () => {
  const title = document.getElementById('p-title').value.trim();
  if (!title) return toast('Title is required', 'error');

  const domains = state.domains.filter(d => document.getElementById(`p-${d}`)?.checked);

  const job = {
    title,
    institution: document.getElementById('p-institution').value.trim(),
    location: document.getElementById('p-location').value.trim(),
    deadline: document.getElementById('p-deadline').value || null,
    url: document.getElementById('p-url').value.trim(),
    salary: document.getElementById('p-salary').value.trim(),
    description: document.getElementById('p-description').value.trim(),
    domains,
    positionType: document.getElementById('p-postype').value,
    source: previewSource
  };

  try {
    const added = await api('POST', '/api/jobs', job);
    state.jobs.push(added);
    render();
    document.getElementById('preview-modal').style.display = 'none';
    toast('Job added!', 'success');
  } catch (e) {
    toast('Error: ' + e.message, 'error');
  }
});

/* ── Edit job ───────────────────────────────────────────────────────────────── */
async function openEditModal(id) {
  const job = state.jobs.find(j => j.id === id);
  if (!job) return;
  openPreviewModal(job, job.source || 'manual');
  document.getElementById('preview-modal-title').textContent = 'Edit Job';

  // Override save to PATCH instead of POST
  const saveBtn = document.getElementById('preview-save-btn');
  const newSave = saveBtn.cloneNode(true);
  saveBtn.parentNode.replaceChild(newSave, saveBtn);

  newSave.addEventListener('click', async () => {
    const domains = state.domains.filter(d => document.getElementById(`p-${d}`)?.checked);

    const updates = {
      title: document.getElementById('p-title').value.trim(),
      institution: document.getElementById('p-institution').value.trim(),
      location: document.getElementById('p-location').value.trim(),
      deadline: document.getElementById('p-deadline').value || null,
      url: document.getElementById('p-url').value.trim(),
      salary: document.getElementById('p-salary').value.trim(),
      description: document.getElementById('p-description').value.trim(),
      domains,
      positionType: document.getElementById('p-postype').value,
    };

    try {
      const updated = await api('PATCH', `/api/jobs/${id}`, updates);
      const idx = state.jobs.findIndex(j => j.id === id);
      if (idx !== -1) state.jobs[idx] = updated;
      render();
      document.getElementById('preview-modal').style.display = 'none';
      toast('Job updated!', 'success');
    } catch (e) {
      toast('Error: ' + e.message, 'error');
    }
  });
}

/* ── Job card event delegation ──────────────────────────────────────────────── */
document.getElementById('jobs-container').addEventListener('click', async e => {
  const id = e.target.dataset.id;

  // Stars
  if (e.target.classList.contains('star')) {
    const star = parseInt(e.target.dataset.star);
    const job = state.jobs.find(j => j.id === id);
    const newAffinity = job.affinity === star ? 0 : star;
    try {
      const updated = await api('PATCH', `/api/jobs/${id}`, { affinity: newAffinity });
      const idx = state.jobs.findIndex(j => j.id === id);
      if (idx !== -1) state.jobs[idx] = updated;
      // Re-render just the stars
      const card = document.querySelector(`.job-card[data-id="${id}"]`);
      if (card) {
        card.querySelector('.star-rating').innerHTML = starsHTML(id, newAffinity);
      }
    } catch (err) { toast('Error: ' + err.message, 'error'); }
  }

  // Applied checkbox
  if (e.target.classList.contains('applied-cb')) {
    const applied = e.target.checked;
    try {
      const updated = await api('PATCH', `/api/jobs/${id}`, { applied });
      const idx = state.jobs.findIndex(j => j.id === id);
      if (idx !== -1) state.jobs[idx] = updated;
      render();
    } catch (err) { toast('Error: ' + err.message, 'error'); e.target.checked = !applied; }
  }

  // Delete
  if (e.target.classList.contains('delete-btn')) {
    if (!confirm('Remove this job?')) return;
    try {
      await api('DELETE', `/api/jobs/${id}`);
      state.jobs = state.jobs.filter(j => j.id !== id);
      render();
      toast('Job removed', 'info');
    } catch (err) { toast('Error: ' + err.message, 'error'); }
  }

  // Edit
  if (e.target.classList.contains('edit-btn')) {
    openEditModal(id);
  }

  // Expand description
  if (e.target.classList.contains('expand-btn')) {
    if (state.expandedDescriptions.has(id)) {
      state.expandedDescriptions.delete(id);
    } else {
      state.expandedDescriptions.add(id);
    }
    render();
  }
});

/* ── Notes autosave ─────────────────────────────────────────────────────────── */
document.getElementById('jobs-container').addEventListener('focus', e => {
  if (e.target.classList.contains('notes-ta')) {
    focusedNoteId = e.target.dataset.id;
    focusedNoteValue = e.target.value;
  }
}, true);

document.getElementById('jobs-container').addEventListener('input', e => {
  if (e.target.classList.contains('notes-ta')) {
    focusedNoteValue = e.target.value;
    const id = e.target.dataset.id;
    clearTimeout(noteSaveTimeout);
    noteSaveTimeout = setTimeout(async () => {
      try {
        const updated = await api('PATCH', `/api/jobs/${id}`, { notes: focusedNoteValue });
        const idx = state.jobs.findIndex(j => j.id === id);
        if (idx !== -1) state.jobs[idx] = updated;
      } catch {}
    }, 800);
  }
});

document.getElementById('jobs-container').addEventListener('blur', e => {
  if (e.target.classList.contains('notes-ta')) {
    focusedNoteId = null;
    focusedNoteValue = null;
  }
}, true);

/* ── Bulk selection ─────────────────────────────────────────────────────────── */
function updateBulkBar() {
  const count = state.selected.size;
  const bar = document.getElementById('bulk-bar');
  bar.style.display = count > 0 ? 'flex' : 'none';
  document.getElementById('bulk-count').textContent =
    `${count} job${count !== 1 ? 's' : ''} selected`;
}

// Checkbox clicks on job cards
document.getElementById('jobs-container').addEventListener('change', e => {
  if (e.target.classList.contains('card-select-cb')) {
    const id = e.target.dataset.id;
    if (e.target.checked) {
      state.selected.add(id);
    } else {
      state.selected.delete(id);
    }
    const card = document.querySelector(`.job-card[data-id="${id}"]`);
    if (card) card.classList.toggle('selected', e.target.checked);
    updateBulkBar();
  }
});

document.getElementById('bulk-select-all-btn').addEventListener('click', () => {
  document.querySelectorAll('.card-select-cb').forEach(cb => {
    state.selected.add(cb.dataset.id);
    cb.checked = true;
    const card = document.querySelector(`.job-card[data-id="${cb.dataset.id}"]`);
    if (card) card.classList.add('selected');
  });
  updateBulkBar();
});

document.getElementById('bulk-deselect-btn').addEventListener('click', () => {
  state.selected.clear();
  document.querySelectorAll('.card-select-cb').forEach(cb => {
    cb.checked = false;
    const card = document.querySelector(`.job-card[data-id="${cb.dataset.id}"]`);
    if (card) card.classList.remove('selected');
  });
  updateBulkBar();
});

document.getElementById('bulk-delete-btn').addEventListener('click', async () => {
  const count = state.selected.size;
  if (!count) return;
  if (!confirm(`Delete ${count} selected job${count !== 1 ? 's' : ''}?`)) return;
  try {
    await api('POST', '/api/jobs/bulk-delete', { ids: [...state.selected] });
    state.jobs = state.jobs.filter(j => !state.selected.has(j.id));
    state.selected.clear();
    updateBulkBar();
    render();
    toast(`${count} job${count !== 1 ? 's' : ''} deleted`, 'info');
  } catch (e) {
    toast('Error: ' + e.message, 'error');
  }
});

/* ── Close modals on overlay click ─────────────────────────────────────────── */
['import-modal', 'preview-modal'].forEach(id => {
  document.getElementById(id).addEventListener('click', e => {
    if (e.target.id === id) document.getElementById(id).style.display = 'none';
  });
});

/* ── Init ───────────────────────────────────────────────────────────────────── */
async function loadConfig() {
  try {
    const cfg = await api('GET', '/api/config');
    document.title = cfg.title || document.title;
    if (cfg.defaultKeywords) document.getElementById('feed-keywords').value = cfg.defaultKeywords;
    if (cfg.defaultLocation) document.getElementById('feed-location').value  = cfg.defaultLocation;
    state.domains = cfg.domains || [];
    buildDomainUI(state.domains);
  } catch {}
}

loadConfig();
loadFeeds();
loadJobs();
