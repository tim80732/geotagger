'use strict';

// ---------------------------------------------------------------- helpers
const $ = (s, el = document) => el.querySelector(s);
const PROPS = new Set(['value', 'checked', 'disabled', 'selected', 'indeterminate']);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (PROPS.has(k)) el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, opts = {}) {
  const o = { ...opts, headers: {} };
  if (o.body && !(o.body instanceof FormData)) {
    o.headers['Content-Type'] = 'application/json';
    o.body = JSON.stringify(o.body);
  }
  const r = await fetch('/api' + path, o);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

const nf = new Intl.NumberFormat('en-US');
const n = (x) => nf.format(x || 0);
function fmtLocal(dto, tz) {
  const m = /^(\d{4}):(\d\d):(\d\d) (\d\d:\d\d:\d\d)/.exec(dto || '');
  if (!m) return '—';
  return `${+m[2]}/${+m[3]} ${m[4]}${tz ? ' ' + tz : ''}`;
}
const fmtUtc = (u) => (u == null ? '—' : new Date(u * 1000).toISOString().slice(11, 19));
function tzMinutes(tz) {
  const m = /^([+-])(\d\d):(\d\d)$/.exec(tz || '');
  return m ? (m[1] === '-' ? -1 : 1) * (+m[2] * 60 + +m[3]) : 0;
}
function fmtInTz(utc, tz) {      // a UTC time shown on the photo's own clock
  const iso = new Date((utc + tzMinutes(tz) * 60) * 1000).toISOString();
  return `${+iso.slice(5, 7)}/${+iso.slice(8, 10)} ${iso.slice(11, 19)}`;
}
function fmtDur(s) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}
const fmtDist = (m) => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`);
const fmtClock = (ms) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const fmtWhen = (s) => new Date(s * 1000).toLocaleString([], { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

const ICON = {
  ok: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#1D5FD1" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  dash: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#5B6266" stroke-width="2.2" stroke-linecap="round"><path d="M6 12h12"/></svg>',
  warn: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#B45309" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none;margin-top:1px"><circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16h.01"/></svg>',
  search: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#5B6266" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>',
  expand: '<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6"/><path d="M10 20H4v-6"/><path d="M20 4l-7 7"/><path d="M4 20l7-7"/></svg>',
  upload: '<svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#5B6266" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg>',
};
const icon = (name) => h('span', { html: ICON[name], style: 'display:inline-flex' });

let SETTINGS = null;
let INFO = { roots: ['/photos'], exiftool: '' };
const rel = (p) => {
  for (const r of INFO.roots) if (p === r || p.startsWith(r + '/')) return p.slice(r.length) || '/';
  return p;
};

// ---------------------------------------------------------------- router
let cleanups = [];
const onLeave = (fn) => cleanups.push(fn);

async function render() {
  cleanups.forEach((f) => { try { f(); } catch (e) { /* ignore */ } });
  cleanups = [];
  const [, name = 'new', id] = (location.hash || '#/new').split('/');
  document.querySelectorAll('.nav-links a').forEach((a) =>
    a.classList.toggle('active', a.dataset.nav === (name === 'job' ? 'jobs' : name)));
  const view = $('#view');
  view.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
  try {
    if (name === 'job') await viewJob(view, +id);
    else if (name === 'jobs') await viewJobs(view);
    else if (name === 'settings') await viewSettings(view);
    else await viewNew(view);
  } catch (e) {
    view.replaceChildren(h('div', { class: 'note err' }, e.message));
  }
}

function steps(active, jobId) {
  const item = (num, label, href) => {
    const on = num === active;
    const inner = [h('b', {}, num), ' ', label];
    return h('li', {}, href && !on ? h('a', { href }, inner) : h('span', { class: on ? 'on' : '', 'aria-current': on ? 'step' : null }, inner));
  };
  return h('ol', { class: 'steps', 'aria-label': 'Steps' },
    item(1, 'Choose photos', '#/new'),
    item(2, 'Review dry run', jobId ? `#/job/${jobId}` : null),
    item(3, 'Write GPS', null));
}

function sideFoot() {
  let host = '';
  try { host = new URL(SETTINGS.dawarich_url).host; } catch (e) { host = 'not set'; }
  const src = SETTINGS.source === 'gpx' ? 'GPX files' : `Dawarich · ${host}`;
  $('#side-foot').replaceChildren(
    h('div', {}, h('span', { class: 'dot' + (SETTINGS.source !== 'gpx' && !SETTINGS.dawarich_key_set ? ' bad' : '') }), src),
    h('div', {}, 'Library · ' + INFO.roots.join(', ')),
    h('div', {}, INFO.exiftool ? `exiftool ${INFO.exiftool}` : ''));
}

// ---------------------------------------------------------------- 1. choose photos
const OPTION_KEYS = ['source', 'subfolders', 'max_gap_min', 'nearest_min', 'max_accuracy_m', 'warn_km', 'frozen_min',
  'has_gps', 'no_tz', 'assume_offset'];
let NEW = null;   // kept while moving between pages

async function viewNew(view) {
  if (!NEW) {
    NEW = { path: localStorage.getItem('gt.path') || '', selected: new Map(), types: SETTINGS.types,
      opts: Object.fromEntries(OPTION_KEYS.map((k) => [k, SETTINGS[k]])) };
  }
  const st = NEW;
  let listing;

  const pathInput = h('input', { id: 'path', class: 'input mono', type: 'text', style: 'flex:1 1 280px' });
  const listEl = h('div', { class: 'folders', role: 'list', 'aria-label': 'Folders' });
  const summaryEl = h('div', { class: 'card-pad' });
  const runBtn = h('button', { class: 'btn primary', type: 'button', onclick: run }, 'Run dry run');

  async function load(path) {
    try {
      listing = await api('/browse?path=' + encodeURIComponent(path || ''));
    } catch (e) {
      listing = await api('/browse');
    }
    st.path = listing.path;
    localStorage.setItem('gt.path', st.path);
    pathInput.value = listing.path;
    drawList();
  }

  const typeOk = (c) => (st.types !== 'raw' && c.jpg) || (st.types !== 'jpg' && c.raw);
  // counts for a folder: only its own files, or with its subfolders too
  const pick = (both) => (st.opts.subfolders ? both.deep : both.counts);
  function countText(c) {
    const parts = [];
    if (c.jpg) parts.push(`${n(c.jpg)} JPG`);
    if (c.raw) parts.push(`${n(c.raw)} RAW`);
    if (c.mov) parts.push(`${n(c.mov)} videos · not supported`);
    return parts.join(' · ') || 'no photos';
  }
  function folderRow(path, label, both, openable) {
    const sel = st.selected.has(path);
    const counts = pick(both);
    const usable = typeOk(counts);
    return h('div', { class: 'folder' + (sel ? ' sel' : ''), role: 'listitem' },
      h('input', { type: 'checkbox', checked: sel, disabled: !usable && !sel, 'aria-label': `Select ${label}`,
        onchange: (e) => { if (e.target.checked) st.selected.set(path, both); else st.selected.delete(path); drawList(); } }),
      openable ? h('button', { class: 'open', type: 'button', onclick: () => load(path) }, label)
        : h('span', { class: 'open', style: 'cursor:default' }, label),
      h('span', { class: 'counts' }, countText(counts)));
  }
  function drawList() {
    const rows = [];
    if (listing.parent) rows.push(h('div', { class: 'folder' }, h('span', { style: 'width:20px' }),
      h('button', { class: 'open', type: 'button', onclick: () => load(listing.parent) }, '.. (up)')));
    const here = { counts: listing.here, deep: listing.here_deep };
    const hereC = pick(here);
    if (hereC.jpg || hereC.raw) {
      rows.push(folderRow(listing.path, st.opts.subfolders && listing.dirs.length ? '(this folder and its subfolders)' : '(this folder)', here, false));
    }
    for (const d of listing.dirs) rows.push(folderRow(d.path, d.name, d, true));
    if (!listing.dirs.length && !hereC.jpg && !hereC.raw) rows.push(h('div', { class: 'folder muted' }, 'Empty folder'));
    listEl.replaceChildren(...rows);
    drawSummary();
  }
  function drawSummary() {
    let jpg = 0, raw = 0;
    for (const both of st.selected.values()) {
      const c = pick(both);
      jpg += st.types !== 'raw' ? c.jpg : 0; raw += st.types !== 'jpg' ? c.raw : 0;
    }
    runBtn.disabled = !(jpg + raw);
    summaryEl.replaceChildren(
      h('h2', {}, 'Selection'),
      h('div', { class: 'stats' },
        h('div', {}, h('div', { class: 'stat-l' }, 'JPG'), h('div', { class: 'stat-v' }, n(jpg))),
        h('div', {}, h('div', { class: 'stat-l' }, 'RAW'), h('div', { class: 'stat-v' }, n(raw)))),
      st.selected.size
        ? h('div', { class: 'pills' }, [...st.selected.keys()].map((p) =>
          h('span', { class: 'pill' }, rel(p), ' ',
            h('button', { type: 'button', 'aria-label': `Remove ${rel(p)}`, style: 'border:0;background:none;cursor:pointer;padding:0 0 0 4px',
              onclick: () => { st.selected.delete(p); drawList(); } }, '×'))))
        : h('p', { class: 'muted', style: 'margin:0' }, 'Tick one or more folders.'),
      h('div', { class: 'note' }, 'Timezones, camera GPS and frozen-GPS checks are shown after the dry run. A RAW and a JPG with the same name get the same position.'));
  }

  const segBtn = (val, label) => h('button', { type: 'button', 'aria-pressed': String(st.types === val),
    onclick: () => { st.types = val; view.querySelectorAll('.seg button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === val))); drawList(); },
    'data-v': val }, label);

  const o = st.opts;
  const num = (key, label, unit, hint) => h('div', { class: 'field' },
    h('label', { for: 'o-' + key }, label),
    h('div', { class: 'unit' }, h('input', { id: 'o-' + key, class: 'input num', type: 'number', min: '0', step: 'any', value: o[key],
      oninput: (e) => { o[key] = parseFloat(e.target.value) || 0; } }), unit),
    hint ? h('span', { class: 'hint' }, hint) : null);
  const sel = (key, label, options, hint, extra) => h('div', { class: 'field' },
    h('label', { for: 'o-' + key }, label),
    h('select', { id: 'o-' + key, class: 'input', onchange: (e) => { o[key] = e.target.value; tzField.style.display = o.no_tz === 'assume' ? '' : 'none'; } },
      options.map(([v, t]) => h('option', { value: v, selected: o[key] === v }, t))),
    hint ? h('span', { class: 'hint' }, hint) : null, extra || null);
  const tzField = h('div', { class: 'unit', style: o.no_tz === 'assume' ? '' : 'display:none' }, 'Assume',
    h('input', { class: 'input', style: 'width:110px', value: o.assume_offset, 'aria-label': 'Assumed timezone offset',
      oninput: (e) => { o.assume_offset = e.target.value.trim(); } }));

  async function run() {
    runBtn.disabled = true;
    try {
      const job = await api('/jobs', { method: 'POST', body: { folders: [...st.selected.keys()], types: st.types, options: o } });
      location.hash = `#/job/${job.id}`;
    } catch (e) {
      alert(e.message);
      runBtn.disabled = false;
    }
  }

  view.replaceChildren(
    steps(1),
    h('header', {}, h('h1', {}, 'Choose photos'),
      h('p', { class: 'sub' }, 'Pick folders and the file types to tag. Each photo is matched using its own EXIF timezone — no offsets to set.')),
    h('div', { class: 'row' },
      h('section', { class: 'card grow3', 'aria-labelledby': 'pick-h' },
        h('div', { class: 'card-pad' },
          h('h2', { id: 'pick-h' }, 'Photos'),
          h('form', { class: 'field', onsubmit: (e) => { e.preventDefault(); load(pathInput.value.trim()); } },
            h('label', { for: 'path' }, 'Folder'),
            h('div', { style: 'display:flex;flex-wrap:wrap;gap:8px' }, pathInput, h('button', { class: 'btn', type: 'submit' }, 'Open'))),
          h('div', { class: 'field' },
            h('span', { class: 'label', id: 'types-l' }, 'File types'),
            h('div', { class: 'seg', role: 'group', 'aria-labelledby': 'types-l' },
              segBtn('jpg', 'JPG'), segBtn('raw', 'RAW'), segBtn('both', 'JPG + RAW')),
            h('span', { class: 'hint' }, 'JPG: GPS written into the file. RAW: GPS written to a .xmp sidecar next to it — the raw file itself is never changed.')),
          h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!st.opts.subfolders,
            onchange: (e) => { st.opts.subfolders = e.target.checked; drawList(); } }), 'Include subfolders')),
        listEl),
      h('section', { class: 'card grow2' }, summaryEl)),
    h('section', { class: 'card', 'aria-labelledby': 'opt-h' },
      h('div', { class: 'card-pad' },
        h('div', { class: 'head' }, h('h2', { id: 'opt-h' }, 'Matching and writing'),
          h('span', { class: 'hint' }, 'Defaults come from ', h('a', { href: '#/settings' }, 'Settings'), ' · changes here apply to this job only')),
        h('div', { class: 'grid3' },
          num('max_gap_min', 'Max gap between track points', 'min', 'Longer gaps (a flight) aren’t bridged — those photos are listed for you to place.'),
          num('nearest_min', 'Nearest-point fallback', 'min', 'Further away than this → the photo is skipped.'),
          num('max_accuracy_m', 'Ignore points less accurate than', 'm'),
          sel('has_gps', 'Photos that already have GPS', [['replace', 'Replace with the track position'], ['keep', 'Keep — only tag photos without GPS']],
            '“Keep” is what Lightroom does. Photos you placed by hand are never replaced.'),
          sel('no_tz', 'Photos with no timezone', [['skip', 'Skip them'], ['assume', 'Assume an offset']], null, tzField),
          sel('source', 'Track source', [['dawarich', 'Dawarich'], ['gpx', 'Uploaded GPX files']]),
          num('warn_km', 'Warn when camera GPS is off by more than', 'km'),
          num('frozen_min', 'Flag camera GPS as frozen after', 'min at one spot')))),
    h('div', { class: 'actions', style: 'justify-content:flex-end' }, h('span', { class: 'muted' }, 'A dry run changes nothing.'), runBtn));

  await load(st.path);
}

// ---------------------------------------------------------------- 2/3. job
const JOBVIEW = {};   // per job: filter, offset, map view

async function viewJob(view, id) {
  const job = await api(`/jobs/${id}`);
  if (['queued', 'scanning'].includes(job.status) && !job.summary.total) return viewScanning(view, job);
  if (['failed', 'cancelled'].includes(job.status) && !job.summary.total) return viewScanning(view, job);
  return viewResults(view, job);
}

function folderLine(job) {
  return job.folders.map(rel).join(' + ');
}

function viewScanning(view, job) {
  const bar = h('div'), phase = h('p', { class: 'sub' }), stats = h('div', { class: 'stats' }), err = h('div');
  const kicker = h('div', { class: 'kicker' });
  const again = async () => { await api(`/jobs/${job.id}/rescan`, { method: 'POST' }); render(); };
  const cancelBtn = h('button', { class: 'btn danger', type: 'button', onclick: async () => {
    if (!confirm('Cancel this dry run? Nothing has been written, so you can run it again any time.')) return;
    cancelBtn.disabled = true;
    try { await api(`/jobs/${job.id}/cancel`, { method: 'POST' }); } catch (e) { alert(e.message); cancelBtn.disabled = false; }
  } }, 'Cancel dry run');
  function draw(j) {
    const live = ['scanning', 'queued'].includes(j.status);
    const pct = j.total ? (100 * j.done) / j.total : 0;
    bar.style.width = pct + '%';
    kicker.textContent = j.status === 'queued' ? 'Waiting' : live ? 'Running' : j.status === 'cancelled' ? 'Cancelled' : 'Failed';
    phase.textContent = j.phase || 'Starting';
    cancelBtn.hidden = !live;
    stats.replaceChildren(
      h('div', {}, h('div', { class: 'stat-l' }, 'Photos read'), h('div', { class: 'stat-v' }, `${n(j.done)} / ${n(j.total)}`)),
      h('div', {}, h('div', { class: 'stat-l' }, 'Time left'), h('div', { class: 'stat-v' }, j.eta_s != null ? `about ${fmtDur(j.eta_s)}` : '—')));
    const retry = h('div', { class: 'actions', style: 'gap:8px' },
      h('button', { class: 'btn small', type: 'button', onclick: again }, j.status === 'failed' ? 'Try again' : 'Run it again'),
      h('a', { class: 'btn small', href: '#/jobs' }, 'Back to jobs'));
    err.replaceChildren(j.status === 'failed'
      ? h('div', { class: 'note err', style: 'flex-direction:column' }, h('strong', {}, 'The dry run failed'), j.error || '', retry)
      : j.status === 'cancelled'
        ? h('div', { class: 'note', style: 'flex-direction:column' }, h('strong', {}, 'The dry run was cancelled'), 'Nothing was written to any photo.', retry)
        : '');
  }
  view.replaceChildren(
    steps(2, job.id),
    h('header', {}, h('h1', {}, 'Dry run'), h('p', { class: 'sub mono' }, folderLine(job))),
    h('section', { class: 'card run' }, h('div', { class: 'card-pad' },
      h('div', { class: 'head' }, h('div', {}, kicker, phase), cancelBtn),
      h('div', { class: 'progress', role: 'progressbar', 'aria-label': 'Dry run progress' }, bar), stats, err)));
  draw(job);
  if (!['scanning', 'queued'].includes(job.status)) return;
  const t = setInterval(async () => {
    const j = await api(`/jobs/${job.id}`);
    if (j.status !== 'scanning' && j.status !== 'queued') { clearInterval(t); render(); return; }
    draw(j);
  }, 1500);
  onLeave(() => clearInterval(t));
}

function resultOf(r) {
  if (r.write_error) return { t: r.write_error, c: 'c-warn' };
  const done = r.written && r.action === 'write' ? ' · in file' : '';
  if (r.manual) return { t: (r.action === 'skip' ? 'Excluded by you' : 'Placed by you') + done, c: '' };
  if (r.action === 'write') return { t: r.reason + done, c: 'c-accent' };
  return { t: r.reason, c: 'c-muted' };
}
function cameraOf(r, warnM) {
  if (r.cam_lat == null) return { t: '—', c: 'c-muted' };
  if (r.ours) return { t: 'written by Geotagger', c: 'c-muted' };
  const parts = [];
  if (r.frozen) parts.push('frozen');
  if (r.cam_diff_m != null) parts.push(`${fmtDist(r.cam_diff_m)} off`);
  parts.push(r.action === 'write' && !r.manual ? 'replaced' : r.manual && r.action === 'write' ? 'replaced' : 'kept');
  return { t: parts.join(' · '), c: r.frozen || (r.cam_diff_m || 0) > warnM ? 'c-warn' : 'c-muted' };
}

async function viewResults(view, job) {
  const id = job.id, s = job.summary, o = job.options;
  const vs = JOBVIEW[id] || (JOBVIEW[id] = { filter: 'all', offset: 0 });
  const busy = ['queued', 'scanning', 'writing'].includes(job.status);
  const warnM = (o.warn_km || 1) * 1000;
  const selected = new Set();
  let pending = null;

  // ---- running / finished card
  let topCard = null;
  if (job.status === 'writing' || (job.status === 'queued' && s.total) || job.status === 'paused') topCard = runningCard(job);
  else if (job.status === 'done') topCard = doneCard(job);
  else if (job.status === 'cancelled' || job.status === 'failed') {
    topCard = h('div', { class: 'note err' }, job.status === 'failed' ? `Writing failed: ${job.error || ''}` : 'Writing was cancelled. Files already written keep their new GPS; write again to finish the rest.');
  }

  // ---- header
  const toWrite = s.to_write;
  const kinds = [s.to_write_kind.jpg ? `${n(s.to_write_kind.jpg)} JPG` : null, s.to_write_kind.raw ? `${n(s.to_write_kind.raw)} RAW` : null].filter(Boolean).join(' + ');
  const writeBtn = h('button', { class: 'btn primary', type: 'button', disabled: busy || !toWrite, onclick: startWrite },
    toWrite ? `Write GPS · ${kinds}` : 'Nothing to write');
  async function startWrite() {
    const msg = `Write GPS to ${n(toWrite)} files?\n\nJPG: GPS goes into the file.\nRAW: into a .xmp sidecar next to it.`;
    if (!confirm(msg)) return;
    try { await api(`/jobs/${id}/write`, { method: 'POST' }); render(); } catch (e) { alert(e.message); }
  }

  // ---- tiles
  const tile = (label, value, sub, cls) => h('div', { class: 'tile ' + (cls || '') },
    h('span', { class: 'l' }, label), h('span', { class: 'v' }, n(value)), h('span', { class: 's' }, sub));
  const kindSub = [s.by_kind.jpg ? `${n(s.by_kind.jpg)} JPG` : '', s.by_kind.raw ? `${n(s.by_kind.raw)} RAW` : ''].filter(Boolean).join(' · ');
  const tiles = h('div', { class: 'tiles' },
    tile(job.status === 'done' ? 'Left to write' : 'Will be written', toWrite,
      s.written ? `${n(s.written)} already in the files` : `of ${n(s.total)} files (${kindSub})`, 'accent'),
    tile('In a track gap', s.gap, s.gap ? 'not tagged · place them on the map' : 'every photo had track nearby'),
    tile('Frozen camera GPS', s.frozen, s.frozen ? `same spot ${fmtLocal(s.frozen_from)} – ${fmtLocal(s.frozen_to).split(' ')[1]}` : 'none found', s.frozen ? 'warn' : ''),
    tile(`Camera GPS off by > ${o.warn_km} km`, s.off, s.worst && s.off ? `worst: ${s.worst.name}, ${fmtDist(s.worst.cam_diff_m)}` : `${n(s.camera_gps)} files have camera GPS`, s.off ? 'warn' : ''),
    tile('Placed by you', s.manual, 'never overwritten by later runs'));

  // ---- map
  const mapEl = h('div', { id: 'map' });
  const results = h('ul', { hidden: true });
  const searchIn = h('input', { type: 'search', placeholder: 'Search places', 'aria-label': 'Search places' });
  const searchBox = h('div', { class: 'search' }, h('div', { class: 'bar' }, icon('search'), searchIn), results);
  const selbar = h('div', { class: 'selbar', role: 'region', 'aria-label': 'Selected photos', hidden: true });

  // ---- table
  const chipsEl = h('div', { class: 'chips', role: 'group', 'aria-label': 'Filter' });
  const tbody = h('tbody');
  const pager = h('div', { class: 'pager' });
  const allBox = h('input', { type: 'checkbox', 'aria-label': 'Select all on this page', style: 'width:18px;height:18px;accent-color:#1D5FD1' });
  let rows = [];

  view.replaceChildren(
    steps(['writing', 'done', 'paused'].includes(job.status) ? 3 : 2, id),
    topCard || '',
    h('header', { class: 'head' },
      h('div', {}, h('h1', {}, job.status === 'done' ? 'GPS written' : 'Review dry run'),
        h('p', { class: 'sub' }, h('span', { class: 'mono' }, folderLine(job)), ' · ',
          job.status === 'ready' ? 'nothing has been written yet' : (job.phase || job.status))),
      h('div', { class: 'actions' },
        h('a', { class: 'btn', href: `/api/jobs/${id}/csv` }, 'Download CSV'),
        h('button', { class: 'btn', type: 'button', disabled: busy, title: 'Read the photos and the track again',
          onclick: async () => { await api(`/jobs/${id}/rescan`, { method: 'POST' }); render(); } }, 'Re-run dry run'),
        writeBtn)),
    tiles,
    h('section', { class: 'card', style: 'overflow:hidden', 'aria-labelledby': 'map-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'map-h' }, 'Map'),
        h('div', { class: 'legend' },
          h('span', {}, h('span', { class: 'sw-line' }), 'Track'),
          h('span', {}, h('span', { class: 'sw-dot' }), 'Photo position'),
          h('span', {}, h('span', { class: 'sw-ring' }), 'Frozen camera GPS'),
          h('span', {}, h('span', { class: 'sw-dia' }), 'Placed by you (drag to adjust)'))),
      h('div', { class: 'mapwrap' }, mapEl, searchBox),
      selbar),
    h('section', { class: 'card', 'aria-labelledby': 'tbl-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'tbl-h' }, 'Photos'), chipsEl),
      h('div', { class: 'tablewrap' }, h('table', { class: 'stack photos' },
        h('thead', {}, h('tr', {},
          h('th', { style: 'width:30px' }, h('label', { class: 'allsel' }, allBox, h('span', { class: 'sm-only' }, 'Select all on this page'))), h('th', {}, 'Photo'), h('th', {}, 'Taken (camera)'), h('th', {}, 'UTC'),
          h('th', {}, 'Result'), h('th', {}, 'New position'), h('th', {}, 'Gap'), h('th', {}, 'Camera GPS'))),
        tbody)),
      pager));

  // ---- map setup
  const map = L.map(mapEl, { preferCanvas: true, zoomControl: false, worldCopyJump: true });
  L.control.zoom({ position: 'topright' }).addTo(map);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' }).addTo(map);
  onLeave(() => { vs.view = { c: map.getCenter(), z: map.getZoom() }; map.remove(); });
  const ringLayer = L.layerGroup().addTo(map);
  const nearbyLayer = L.layerGroup().addTo(map);
  let pendingMarker = null;
  const md = await api(`/jobs/${id}/map`);
  const bounds = L.latLngBounds([]);
  for (const seg of md.segments) { L.polyline(seg, { color: '#1D5FD1', weight: 3, opacity: 0.8 }).addTo(map); seg.forEach((p) => bounds.extend(p)); }
  for (const f of md.frozen) {
    L.circleMarker([f.lat, f.lon], { radius: 9, color: '#B45309', weight: 3, fillColor: '#fff', fillOpacity: 1 })
      .bindTooltip(`Camera GPS frozen here · ${n(f.n)} files, ${fmtLocal(f.a)} – ${fmtLocal(f.b).split(' ')[1]}`).addTo(map);
  }
  const manualIcon = L.divIcon({ className: '', html: '<div class="pin-manual"></div>', iconSize: [18, 18], iconAnchor: [9, 9] });
  for (const [pid, lat, lon, manual, , name] of md.photos) {
    bounds.extend([lat, lon]);
    const pop = () => h('div', {}, h('img', { class: 'popthumb', src: `/api/thumb/${pid}`, alt: '' }), h('strong', {}, name));
    if (manual) {
      const m = L.marker([lat, lon], { icon: manualIcon, draggable: !busy, title: name }).addTo(map).bindPopup(pop);
      m.on('dragend', () => place([pid], m.getLatLng()));
    } else {
      L.circleMarker([lat, lon], { radius: 5, color: '#fff', weight: 1.5, fillColor: '#1D5FD1', fillOpacity: 1 }).addTo(map).bindPopup(pop);
    }
  }
  if (vs.view) map.setView(vs.view.c, vs.view.z);
  else if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
  else map.setView([30, 10], 2);
  setTimeout(() => map.invalidateSize(), 50);

  map.on('click', (e) => {
    if (!selected.size || busy) return;
    setPending(e.latlng);
  });
  function setPending(ll) {
    pending = ll;
    if (pendingMarker) pendingMarker.remove();
    pendingMarker = L.marker(ll, { icon: L.divIcon({ className: '', html: '<div class="pin-pending"></div>', iconSize: [22, 22], iconAnchor: [11, 11] }) }).addTo(map);
    drawSelbar();
  }

  // ---- place search
  let searchT = null;
  searchIn.addEventListener('input', () => {
    clearTimeout(searchT);
    const q = searchIn.value.trim();
    if (q.length < 2) { results.hidden = true; return; }
    searchT = setTimeout(async () => {
      try {
        const found = await api('/geocode?q=' + encodeURIComponent(q));
        results.replaceChildren(...found.map((f) => h('li', {}, h('button', { type: 'button', onclick: () => {
          results.hidden = true;
          map.flyTo([f.lat, f.lon], 15);
          if (selected.size) setPending(L.latLng(f.lat, f.lon));
        } }, h('span', {}, f.name), h('span', { class: 'd' }, f.detail)))));
        results.hidden = !found.length;
      } catch (e) { results.replaceChildren(h('li', { class: 'muted', style: 'padding:9px 14px' }, e.message)); results.hidden = false; }
    }, 350);
  });

  // ---- selection + placing
  async function place(ids, ll, mode = 'set') {
    try {
      vs.view = { c: map.getCenter(), z: map.getZoom() };
      await api(`/jobs/${id}/place`, { method: 'POST', body: { ids, mode, lat: ll ? ll.lat : null, lon: ll ? ll.lng : null } });
      render();
    } catch (e) { alert(e.message); }
  }
  function drawSelbar() {
    selbar.hidden = !selected.size;
    ringLayer.clearLayers();
    for (const r of rows) if (selected.has(r.id) && r.t_lat != null) {
      L.circleMarker([r.t_lat, r.t_lon], { radius: 10, color: '#16191A', weight: 2.5, fill: false }).addTo(ringLayer);
    }
    if (!selected.size) { if (pendingMarker) { pendingMarker.remove(); pendingMarker = null; } pending = null; nearbyLayer.clearLayers(); return; }
    const many = selected.size === 1 ? '1 photo selected' : `${n(selected.size)} photos selected`;
    selbar.replaceChildren(
      h('span', {}, h('strong', {}, many), busy ? ' · wait until writing has stopped to change positions'
        : pending ? ' · click “Place here” to use the marked spot' : ' · search a place or click the map to set the position'),
      h('div', { class: 'actions', style: 'gap:8px' },
        h('button', { class: 'btn', type: 'button', disabled: busy, onclick: () => place([...selected], null, 'track') }, 'Use track position'),
        h('button', { class: 'btn', type: 'button', disabled: busy, onclick: () => place([...selected], null, 'exclude') }, 'Don’t tag'),
        h('button', { class: 'btn', type: 'button', onclick: () => { selected.clear(); drawTable(); } }, 'Clear selection'),
        h('button', { class: 'btn light', type: 'button', disabled: busy || !pending, onclick: () => place([...selected], pending) }, 'Place here')));
  }

  // ---- filters + table
  const chipDefs = [['all', 'All', s.total], ['write', 'To tag', s.to_write + s.written], ['gap', 'In a track gap', s.gap],
    ['frozen', 'Frozen camera GPS', s.frozen], ['off', `Camera GPS > ${o.warn_km} km off`, s.off], ['manual', 'Placed by you', s.manual],
    ['skipped', 'Not tagged', s.skipped], ['errors', 'Errors', s.errors]];
  const warnChips = new Set(['frozen', 'off', 'errors']);
  function drawChips() {
    chipsEl.replaceChildren(...chipDefs.filter(([k, , c]) => k === 'all' || c || k === vs.filter).map(([k, label, c]) =>
      h('button', { type: 'button', 'aria-pressed': String(vs.filter === k), onclick: () => { vs.filter = k; vs.offset = 0; drawChips(); loadRows(); } },
        h('span', {}, label), h('span', { class: 'count' + (warnChips.has(k) && c ? ' warn' : '') }, n(c)))));
  }
  async function loadRows() {
    const data = await api(`/jobs/${id}/photos?filter=${vs.filter}&offset=${vs.offset}&limit=100`);
    rows = data.rows;
    drawTable();
    const a = data.total ? vs.offset + 1 : 0, b = Math.min(vs.offset + 100, data.total);
    pager.replaceChildren(
      h('span', {}, `Showing ${n(a)}–${n(b)} of ${n(data.total)} · tick photos to place them on the map`),
      h('div', { class: 'actions', style: 'gap:8px' },
        h('button', { class: 'btn small', type: 'button', disabled: vs.offset === 0, onclick: () => { vs.offset = Math.max(0, vs.offset - 100); loadRows(); } }, 'Previous'),
        h('button', { class: 'btn small', type: 'button', disabled: b >= data.total, onclick: () => { vs.offset += 100; loadRows(); } }, 'Next')));
  }
  function drawTable() {
    allBox.checked = rows.length > 0 && rows.every((r) => selected.has(r.id));
    allBox.onchange = () => { rows.forEach((r) => (allBox.checked ? selected.add(r.id) : selected.delete(r.id))); drawTable(); };
    tbody.replaceChildren(...rows.map((r, i) => {
      const res = resultOf(r), cam = cameraOf(r, warnM);
      const warnRow = r.frozen || (r.cam_diff_m || 0) > warnM;
      const tr = h('tr', { class: selected.has(r.id) ? 'sel' : warnRow ? 'warnrow' : '' },
        h('td', {}, h('input', { type: 'checkbox', checked: selected.has(r.id), 'aria-label': `Select ${r.name}`,
          style: 'width:18px;height:18px;accent-color:#1D5FD1',
          onclick: (e) => e.stopPropagation(),
          onchange: (e) => { if (e.target.checked) selected.add(r.id); else selected.delete(r.id); drawTable(); } })),
        h('td', {}, h('div', { class: 'namecell' },
          h('button', { class: 'thumbbtn', type: 'button', 'aria-label': `View ${r.name} full size`, title: 'View full size',
            onclick: (e) => { e.stopPropagation(); openViewer(vs.offset + i, e.currentTarget); } },
            h('img', { class: 'thumb', src: `/api/thumb/${r.id}`, alt: '', loading: 'lazy' }), h('span', { class: 'ex', html: ICON.expand })),
          h('span', { style: 'font-weight:500' }, r.name), h('span', { class: 'badge' }, r.kind.toUpperCase()))),
        h('td', { class: 'mono', 'data-label': 'Taken' }, fmtLocal(r.dto, r.tz)),
        h('td', { class: 'mono hide-sm' }, fmtUtc(r.utc)),
        h('td', { class: res.c, 'data-label': 'Result' }, h('div', { class: 'rescell' }, h('span', {}, res.t),
          inGap(r) ? h('button', { class: 'btn small', type: 'button', title: 'Show the closest GPS points before and after this photo',
            onclick: (e) => { e.stopPropagation(); showNearby(r); } }, 'Nearest GPS') : null)),
        h('td', { class: 'mono' + (r.t_lat == null ? ' hide-sm' : ''), 'data-label': 'Position' }, r.t_lat != null
          ? h('a', { href: `https://www.openstreetmap.org/?mlat=${r.t_lat}&mlon=${r.t_lon}#map=17/${r.t_lat}/${r.t_lon}`, target: '_blank', rel: 'noopener',
            onclick: (e) => e.stopPropagation() }, `${r.t_lat.toFixed(5)}, ${r.t_lon.toFixed(5)}`) : '—'),
        h('td', { class: 'mono hide-sm' }, r.gap_s != null ? fmtDur(r.gap_s) : '—'),
        h('td', { class: cam.c + (r.cam_lat == null ? ' hide-sm' : ''), 'data-label': 'Camera GPS' }, cam.t));
      tr.addEventListener('click', () => focusRow(r));
      return tr;
    }));
    drawSelbar();
  }

  const inGap = (r) => r.action === 'skip' && !r.manual && ['gap', 'outside'].includes(r.m_status);

  function focusRow(r) {
    if (inGap(r)) { showNearby(r); return; }
    const ll = r.t_lat != null ? [r.t_lat, r.t_lon] : r.cam_lat != null ? [r.cam_lat, r.cam_lon] : null;
    if (!ll) return;
    map.setView(ll, Math.max(map.getZoom(), 16));
    L.popup().setLatLng(ll).setContent(h('div', {}, h('img', { class: 'popthumb', src: `/api/thumb/${r.id}`, alt: '' }),
      h('strong', {}, r.name))).openOn(map);
    mapEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // ---- untagged photo: the closest GPS points on either side, as a starting point for placing it
  async function showNearby(r) {
    let data;
    try { data = await api(`/photo/${r.id}/nearby`); } catch (e) { alert(e.message); return; }
    nearbyLayer.clearLayers();
    const found = [[data.before, 'Last GPS point'], [data.after, 'Next GPS point']].filter(([p]) => p);
    if (!found.length) { alert('There are no GPS points before or after this photo.'); return; }
    for (const [p, which] of found) {
      const when = `${fmtInTz(p.utc, r.tz)} · ${fmtDur(Math.abs(p.diff_s))} ${p.diff_s < 0 ? 'before' : 'after'} the photo`;
      L.circleMarker([p.lat, p.lon], { radius: 8, color: '#16191A', weight: 2.5, fillColor: '#FFC53D', fillOpacity: 1 })
        .addTo(nearbyLayer).bindTooltip(`${which}: ${when}`, p === data.before
          ? { permanent: true, direction: 'left', offset: [-10, 0] } : { permanent: true, direction: 'right', offset: [10, 0] });
    }
    const pts = found.map(([p]) => [p.lat, p.lon]);
    if (pts.length === 2) L.polyline(pts, { color: '#16191A', weight: 2, dashArray: '6 6' }).addTo(nearbyLayer);
    map.closePopup();
    map.fitBounds(L.latLngBounds(pts), { padding: [90, 90], maxZoom: 16 });
    selected.clear();
    selected.add(r.id);
    drawTable();
    const closest = found.map(([p]) => p).sort((a, b) => Math.abs(a.diff_s) - Math.abs(b.diff_s))[0];
    setPending(L.latLng(closest.lat, closest.lon));
    mapEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // ---- full-size viewer: steps through the whole filtered list, not just this page
  function openViewer(start, trigger) {
    let idx = start, total = 0, cur = null, fit = true, seq = 0;
    const img = h('img', { alt: '' });
    const status = h('div', { class: 'v-status', role: 'status' }, 'Loading…');
    const title = h('div', { class: 'v-title' }), meta = h('div', { class: 'v-meta' });
    const prevB = h('button', { class: 'btn', type: 'button', onclick: () => go(idx - 1) }, '← Previous');
    const nextB = h('button', { class: 'btn', type: 'button', onclick: () => go(idx + 1) }, 'Next →');
    const fitB = h('button', { class: 'btn', type: 'button', onclick: toggleFit }, '100%');
    const mapB = h('button', { class: 'btn', type: 'button', onclick: () => { const r = cur; close(); if (r) focusRow(r); } }, 'Show on map');
    const closeB = h('button', { class: 'btn v-close', type: 'button', 'aria-label': 'Close viewer', onclick: () => close() }, '✕');
    const stage = h('div', { class: 'v-stage' }, img, status);
    const box = h('div', { class: 'viewer', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Photo viewer' },
      h('div', { class: 'v-bar' }, h('div', {}, title, meta), h('div', { class: 'v-actions' }, prevB, nextB, fitB, mapB, closeB)), stage);

    function toggleFit() {
      fit = !fit;
      stage.classList.toggle('actual', !fit);
      fitB.textContent = fit ? '100%' : 'Fit';
    }
    // swipe left/right on a phone, unless zoomed to 100% where dragging scrolls the photo
    let touchX = null;
    stage.addEventListener('touchstart', (e) => { touchX = fit && e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });
    stage.addEventListener('touchend', (e) => {
      if (touchX == null) return;
      const dx = e.changedTouches[0].clientX - touchX;
      touchX = null;
      if (Math.abs(dx) > 50) go(dx < 0 ? idx + 1 : idx - 1);
    });
    img.onload = () => { status.hidden = true; };
    img.onerror = () => { status.textContent = 'Could not load this photo.'; status.hidden = false; };

    async function go(i) {
      if (i < 0 || (total && i >= total)) return;
      const mine = ++seq;
      idx = i;
      status.textContent = 'Loading…';
      status.hidden = false;
      const off = Math.max(0, i - 1);
      const data = await api(`/jobs/${id}/photos?filter=${vs.filter}&offset=${off}&limit=3`);
      if (mine !== seq) return;                          // a newer step already started
      total = data.total;
      cur = data.rows[i - off];
      if (!cur) return;
      img.src = `/api/photo/${cur.id}/full`;
      const next = data.rows[i - off + 1];
      if (next) { const pre = new Image(); pre.src = `/api/photo/${next.id}/full`; }
      title.textContent = cur.name;
      meta.textContent = `${fmtLocal(cur.dto, cur.tz)} · ${resultOf(cur).t} · ${n(i + 1)} of ${n(total)}`;
      prevB.disabled = i === 0;
      nextB.disabled = i >= total - 1;
      mapB.textContent = inGap(cur) ? 'Nearest GPS' : 'Show on map';
    }
    function onKey(e) {
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(idx - 1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); go(idx + 1); }
      else if (e.key === 'Escape') { e.preventDefault(); close(); }
    }
    function close() {
      document.removeEventListener('keydown', onKey);
      box.remove();
      document.body.style.overflow = '';
      if (trigger && trigger.isConnected) trigger.focus();
    }
    document.addEventListener('keydown', onKey);
    onLeave(close);
    document.body.append(box);
    document.body.style.overflow = 'hidden';
    closeB.focus();
    go(start);
  }
  drawChips();
  await loadRows();

  // ---- live progress while writing
  if (busy || job.status === 'queued') {
    const t = setInterval(async () => {
      const j = await api(`/jobs/${id}`);
      if (j.status !== job.status) { clearInterval(t); vs.view = { c: map.getCenter(), z: map.getZoom() }; render(); return; }
      if (topCard && topCard.update) topCard.update(j);
    }, 1500);
    onLeave(() => clearInterval(t));
  }
}

function runningCard(job) {
  const bar = h('div'), stats = h('div', { class: 'grid3', style: 'grid-template-columns:repeat(auto-fit,minmax(160px,1fr))' });
  const kicker = h('div', { class: 'kicker' }), phase = h('span', { class: 'hint' }), err = h('div');
  const pauseBtn = h('button', { class: 'btn', type: 'button' });
  const cancelBtn = h('button', { class: 'btn danger', type: 'button', onclick: async () => {
    if (!confirm('Stop writing? Files already written keep their new GPS.')) return;
    await api(`/jobs/${job.id}/cancel`, { method: 'POST' });
  } }, 'Cancel');
  const card = h('section', { class: 'card run', 'aria-labelledby': 'run-h' }, h('div', { class: 'card-pad' },
    h('div', { class: 'head' }, h('div', {}, kicker, h('h2', { id: 'run-h', style: 'font-size:18px' }, 'Writing GPS · ', h('span', { class: 'mono' }, folderLine(job)))),
      h('div', { class: 'actions' }, pauseBtn, cancelBtn)),
    h('div', { class: 'progress', role: 'progressbar', 'aria-label': 'Files written' }, bar), stats, phase, err));
  card.update = (j) => {
    const paused = j.status === 'paused';
    kicker.textContent = paused ? 'Paused' : j.status === 'queued' ? 'Waiting' : 'Running';
    bar.style.width = (j.total ? (100 * j.done) / j.total : 0) + '%';
    const left = j.eta_s;
    stats.replaceChildren(
      h('div', {}, h('div', { class: 'stat-l' }, 'Files written'), h('div', { class: 'stat-v', style: 'font-size:20px' }, `${n(j.done)} / ${n(j.total)}`)),
      h('div', {}, h('div', { class: 'stat-l' }, 'Time left'), h('div', { class: 'stat-v', style: 'font-size:20px' }, paused ? '—' : left != null ? `about ${fmtDur(left)}` : 'estimating…')),
      h('div', {}, h('div', { class: 'stat-l' }, 'Done at'), h('div', { class: 'stat-v', style: 'font-size:20px' }, paused || left == null ? '—' : `about ${fmtClock(Date.now() + left * 1000)}`)),
      h('div', {}, h('div', { class: 'stat-l' }, 'Speed'), h('div', { class: 'stat-v', style: 'font-size:20px' }, j.rate ? `${j.rate.toFixed(1)} files/s` : '—')));
    phase.textContent = paused ? (j.phase || 'Paused') : 'The estimate follows the speed of the last two minutes. You can close this page — the job keeps running on the server.';
    err.replaceChildren(paused && j.error ? h('div', { class: 'note err', style: 'margin-top:12px' }, j.error) : '');
    pauseBtn.textContent = paused ? 'Resume' : 'Pause';
    pauseBtn.onclick = async () => { await api(`/jobs/${job.id}/${paused ? 'resume' : 'pause'}`, { method: 'POST' }); if (paused) render(); };
  };
  card.update(job);
  return card;
}

function doneCard(job) {
  const r = job.result || {}, s = job.summary;
  const took = r.write_started && job.finished ? ` in ${fmtDur(job.finished - r.write_started)}` : '';
  const li = (ic, text) => h('li', {}, icon(ic), text);
  return h('div', { style: 'display:flex;flex-direction:column;gap:16px' },
    h('section', { class: 'card' }, h('div', { class: 'card-pad' },
      h('h2', {}, `Finished${took}`),
      h('ul', { class: 'checks' },
        li('ok', `GPS in ${n(s.written)} files`),
        r.unchanged ? li('dash', `${n(r.unchanged)} already had this position, so they weren’t rewritten`) : null,
        li(r.mismatched ? 'warn' : 'ok', `Read back: ${n(r.verified)} match${r.mismatched ? `, ${n(r.mismatched)} don’t` : ''}`),
        li('ok', 'Owner, permissions and file dates unchanged'),
        li(s.errors ? 'warn' : 'dash', s.errors ? `${n(s.errors)} files had errors — see the Errors filter` : 'No write errors'),
        s.skipped ? li('dash', `${n(s.skipped)} not tagged (gaps, kept GPS or excluded)`) : null))),
    h('section', { class: 'card', style: 'background:var(--accent-bg);border-color:var(--accent-line)' }, h('div', { class: 'card-pad' },
      h('h2', {}, 'Next, in Lightroom'),
      h('ol', { style: 'margin:0;padding-left:22px;display:flex;flex-direction:column;gap:6px;line-height:1.5' },
        h('li', {}, 'Select the photos from ', h('span', { class: 'mono' }, folderLine(job)), '.'),
        h('li', {}, h('strong', {}, 'Metadata → Read Metadata from Files'), '. If you edited them in Lightroom, press Ctrl+S on them before the next write.'),
        h('li', {}, 'Check a few on the Map — no time zone offset needed.')))));
}

// ---------------------------------------------------------------- jobs list
async function viewJobs(view) {
  const draw = (jobs) => {
    const body = jobs.map((j) => {
      const c = j.counts || {};
      let st;
      if (j.status === 'writing' || j.status === 'scanning') {
        const pct = j.total ? Math.floor((100 * j.done) / j.total) : 0;
        st = h('span', { class: 'status run' }, `${j.status === 'writing' ? 'Writing' : 'Dry run'} · ${pct}%${j.eta_s != null ? ` · ~${fmtDur(j.eta_s)} left` : ''}`);
      } else {
        const map = { ready: ['Dry run ready', 'blue'], done: ['Written', 'blue'], paused: ['Paused', ''], queued: ['Waiting', ''],
          cancelled: ['Cancelled', 'bad'], failed: ['Failed', 'bad'] };
        const [t, cls] = map[j.status] || [j.status, ''];
        st = h('span', { class: 'status ' + cls }, t);
      }
      const busy = ['writing', 'scanning', 'queued'].includes(j.status);
      return h('tr', { onclick: () => { location.hash = `#/job/${j.id}`; } },
        h('td', { class: 'mono jobid' }, `#${j.id}`, ' ', h('span', { class: 'sm-only' }, st)),
        h('td', { 'data-label': 'Created' }, fmtWhen(j.created)),
        h('td', { class: 'mono', 'data-label': 'Folders' }, j.folders.map(rel).join(' + ')),
        h('td', { 'data-label': 'Files' }, n(c.total)),
        h('td', { 'data-label': 'Written' }, c.total ? `${n(c.done)} / ${n(c.tag)}` : '—'),
        h('td', { class: 'hide-sm' }, st.cloneNode(true)),
        h('td', { class: 'rowactions', style: 'text-align:right;white-space:nowrap' },
          h('a', { href: `#/job/${j.id}`, onclick: (e) => e.stopPropagation() }, 'Open'), ' ',
          busy ? null : h('button', { class: 'btn small danger', type: 'button', style: 'margin-left:12px',
            onclick: async (e) => { e.stopPropagation(); if (confirm(`Delete job #${j.id}? Photos are not touched.`)) { await api(`/jobs/${j.id}`, { method: 'DELETE' }); render(); } } }, 'Delete')));
    });
    // the list is redrawn every 2 s while a job runs; keep the table's sideways scroll where the user left it
    const oldWrap = view.querySelector('.tablewrap');
    const scrollX = oldWrap ? oldWrap.scrollLeft : 0;
    queueMicrotask(() => { const w = view.querySelector('.tablewrap'); if (w) w.scrollLeft = scrollX; });
    view.replaceChildren(
      h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Jobs'), h('p', { class: 'sub' }, 'Dry runs and writes. Deleting a job never touches photos.')),
        h('a', { class: 'btn primary', href: '#/new' }, 'New job')),
      h('section', { class: 'card' }, h('div', { class: 'tablewrap' }, h('table', { class: 'stack' },
        h('thead', {}, h('tr', {}, ['Job', 'Created', 'Folders', 'Files', 'Written / to tag', 'Status', ''].map((t) => h('th', {}, t)))),
        h('tbody', {}, body.length ? body : h('tr', {}, h('td', { colspan: 7, class: 'muted' }, 'No jobs yet.')))))));
  };
  let jobs = await api('/jobs');
  draw(jobs);
  const t = setInterval(async () => {
    if (!jobs.some((j) => ['writing', 'scanning', 'queued'].includes(j.status))) return;
    jobs = await api('/jobs');
    draw(jobs);
  }, 2000);
  onLeave(() => clearInterval(t));
}

// ---------------------------------------------------------------- settings
async function viewSettings(view) {
  const s = { ...(await api('/settings')) };
  const [libs, gpxList] = await Promise.all([api('/libraries'), api('/gpx')]);
  let key = '';
  const status = h('span', { role: 'status', class: 'muted' });
  const gpxEl = h('div');

  const radio = (val, title, desc) => h('label', { class: 'radio' + (s.source === val ? ' on' : '') },
    h('input', { type: 'radio', name: 'src', checked: s.source === val, onchange: () => {
      s.source = val; view.querySelectorAll('.radio').forEach((r) => r.classList.toggle('on', r.querySelector('input').checked)); } }),
    h('span', {}, h('span', { class: 't', style: 'display:block' }, title), h('span', { class: 'd' }, desc)));
  const num = (k, label, unit) => h('div', { class: 'field' }, h('label', { for: 's-' + k }, label),
    h('div', { class: 'unit' }, h('input', { id: 's-' + k, class: 'input num', type: 'number', min: '0', step: 'any', value: s[k],
      oninput: (e) => { s[k] = parseFloat(e.target.value) || 0; } }), unit));
  const sel = (k, label, opts) => h('div', { class: 'field' }, h('label', { for: 's-' + k }, label),
    h('select', { id: 's-' + k, class: 'input', onchange: (e) => { s[k] = e.target.value; } },
      opts.map(([v, t]) => h('option', { value: v, selected: s[k] === v }, t))));
  const keyIn = h('input', { id: 's-key', class: 'input mono', type: 'password', style: 'flex:1;min-width:0',
    placeholder: s.dawarich_key_set ? '•••••••• saved — leave empty to keep' : 'Paste the API key', oninput: (e) => { key = e.target.value.trim(); } });

  function drawGpx(list) {
    gpxEl.replaceChildren(list.length ? h('div', { class: 'tablewrap' }, h('table', {},
      h('thead', {}, h('tr', {}, ['File', 'Points', 'From', 'To', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, list.map((g) => h('tr', { style: 'cursor:default' },
        h('td', {}, g.name), h('td', {}, n(g.points)),
        h('td', { class: 'mono' }, g.first ? new Date(g.first * 1000).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : '—'),
        h('td', { class: 'mono' }, g.last ? new Date(g.last * 1000).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : '—'),
        h('td', { style: 'text-align:right' }, h('button', { class: 'btn small danger', type: 'button',
          onclick: async () => drawGpx(await api(`/gpx/${g.id}`, { method: 'DELETE' })) }, 'Remove'))))))) : '');
  }
  async function upload(files) {
    if (!files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    try { drawGpx(await api('/gpx', { method: 'POST', body: fd })); } catch (e) { alert(e.message); }
  }
  const fileIn = h('input', { type: 'file', accept: '.gpx', multiple: true, hidden: true, onchange: (e) => upload(e.target.files) });
  const drop = h('div', { class: 'drop' }, icon('upload'), h('span', {}, 'Drop .gpx files here (several at once is fine), or'),
    h('button', { class: 'btn small', type: 'button', onclick: () => fileIn.click() }, 'Choose files'), fileIn);
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); upload(e.dataTransfer.files); });

  async function test() {
    status.textContent = 'Testing…';
    const r = await api('/settings/test', { method: 'POST', body: { dawarich_url: s.dawarich_url, dawarich_key: key } });
    status.replaceChildren(r.ok ? h('span', { style: 'display:inline-flex;gap:8px;align-items:center;color:var(--ink)' }, icon('ok'),
      `Connected${r.version ? ` · Dawarich ${r.version}` : ''}${r.latest ? ` · latest point ${new Date(r.latest * 1000).toLocaleString([], { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}` : ''}`)
      : h('span', { class: 'c-warn' }, r.message || 'Could not connect'));
  }
  async function save() {
    const body = { ...s, dawarich_key: key };
    delete body.dawarich_key_set;
    SETTINGS = await api('/settings', { method: 'PUT', body });
    NEW = null;
    sideFoot();
    saveBtn.textContent = 'Saved';
    setTimeout(() => { saveBtn.textContent = 'Save settings'; }, 1500);
  }
  const saveBtn = h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Save settings');

  view.replaceChildren(
    h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Settings'),
      h('p', { class: 'sub' }, 'Where the track comes from, which photo libraries are available, and the defaults for new jobs.')), saveBtn),
    h('section', { class: 'card', 'aria-labelledby': 'src-h' }, h('div', { class: 'card-pad' },
      h('h2', { id: 'src-h' }, 'Track source'),
      h('div', { class: 'radios', role: 'radiogroup', 'aria-labelledby': 'src-h' },
        radio('dawarich', 'Dawarich API', 'Pulls the points for each job’s time range'),
        radio('gpx', 'GPX files', 'Fallback: upload a track from any app or device')),
      h('div', { class: 'grid2' },
        h('div', { class: 'field' }, h('label', { for: 's-url' }, 'Dawarich URL'),
          h('input', { id: 's-url', class: 'input mono', type: 'url', value: s.dawarich_url, oninput: (e) => { s.dawarich_url = e.target.value.trim(); } })),
        h('div', { class: 'field' }, h('label', { for: 's-key' }, 'API key'),
          h('div', { style: 'display:flex;gap:8px' }, keyIn,
            h('button', { class: 'btn', type: 'button', onclick: (e) => { keyIn.type = keyIn.type === 'password' ? 'text' : 'password'; e.target.textContent = keyIn.type === 'password' ? 'Show' : 'Hide'; } }, 'Show')),
          h('span', { class: 'hint' }, 'Dawarich → Account → API key. Can also be set as DAWARICH_API_KEY in the Unraid template.'))),
      h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', onclick: test }, 'Test connection'), status),
      h('div', { class: 'field' }, h('span', { class: 'label' }, 'GPX files'), drop, gpxEl,
        h('span', { class: 'hint' }, 'Times in GPX are UTC, so photos still match by their own EXIF timezone. Place search on the map uses Photon (' + s.photon_url + '), or OpenStreetMap’s Nominatim when Photon doesn’t answer.')))),
    h('section', { class: 'card', 'aria-labelledby': 'lib-h' },
      h('div', { class: 'card-pad', style: 'padding-bottom:6px' }, h('h2', { id: 'lib-h' }, 'Photo libraries'),
        h('span', { class: 'hint' }, 'Folders mounted into the container. Add more as paths in the Unraid template and list them in PHOTO_ROOTS.')),
      h('div', { class: 'tablewrap' }, h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, 'In the app'), h('th', {}, 'Status'))),
        h('tbody', {}, libs.map((l) => h('tr', { style: 'cursor:default' }, h('td', { class: 'mono' }, l.path),
          h('td', {}, h('span', { class: 'status ' + (l.exists && l.writable ? 'blue' : 'bad') },
            !l.exists ? 'Not mounted' : l.writable ? 'Read-write' : 'Read-only')))))))),
    h('section', { class: 'card', 'aria-labelledby': 'def-h' }, h('div', { class: 'card-pad' },
      h('h2', { id: 'def-h' }, 'Defaults for new jobs'),
      h('div', { class: 'grid3' },
        num('max_gap_min', 'Max gap between track points', 'min'),
        num('nearest_min', 'Nearest-point fallback', 'min'),
        num('max_accuracy_m', 'Ignore points less accurate than', 'm'),
        num('warn_km', 'Warn when camera GPS is off by more than', 'km'),
        num('frozen_min', 'Flag camera GPS as frozen after', 'min at one spot'),
        sel('types', 'File types', [['both', 'JPG + RAW'], ['jpg', 'JPG only'], ['raw', 'RAW only']]),
        sel('has_gps', 'Photos that already have GPS', [['replace', 'Replace with the track position'], ['keep', 'Keep — only tag photos without GPS']]),
        sel('no_tz', 'Photos with no timezone', [['skip', 'Skip them'], ['assume', 'Assume an offset']]),
        h('div', { class: 'field' }, h('span', { class: 'label' }, 'Folders'),
          h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!s.subfolders, onchange: (e) => { s.subfolders = e.target.checked; } }),
            'Include subfolders'))))));
  drawGpx(gpxList);
}

// ---------------------------------------------------------------- start
(async function start() {
  try {
    [SETTINGS, INFO] = await Promise.all([api('/settings'), api('/info')]);
  } catch (e) {
    $('#view').replaceChildren(h('div', { class: 'note err' }, 'Could not reach the Geotagger server: ' + e.message));
    return;
  }
  sideFoot();
  window.addEventListener('hashchange', render);
  render();
})();
