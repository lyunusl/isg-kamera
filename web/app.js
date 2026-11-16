'use strict';
/* İSG Kamera interface: a single page; data comes from the /api/... endpoints. Texts go through T() (web/i18n.js). */

const S = { status: null, catalog: null, days: 7, events: {}, analytics: { mode: 'heat', layer: 'person', camera: '', category: '', compare: false }, drawing: null, lastJob: null };
const $ = (s, k = document) => k.querySelector(s);
const h = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const MONTHS = [N_('Jan'), N_('Feb'), N_('Mar'), N_('Apr'), N_('May'), N_('Jun'), N_('Jul'), N_('Aug'), N_('Sep'), N_('Oct'), N_('Nov'), N_('Dec')];
const LEVELS = { critical: N_('Critical'), high: N_('High'), medium: N_('Medium'), low: N_('Low') };
const STATUS_NAMES = { open: N_('Open'), assigned: N_('Assigned'), resolved: N_('Resolved'), false_alarm: N_('False alarm') };
const TASK_STATUS_NAMES = { open: N_('Open'), in_progress: N_('In progress'), closed: N_('Closed') };
const translateTable = table => Object.entries(table).map(([k, v]) => [k, T(v)]);
const decimal = v => (I18N.lang === 'tr' ? String(v).replace('.', ',') : String(v));  // decimal comma in Turkish
const percent = v => T('{n}%', { n: decimal(v) });
const plural = (n, one, many) => T(n === 1 ? one : many, { n });  // singular / plural (one and many are N_-marked)
translateDom();

async function api(path, body) {
  const r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({ error: T('Could not read the response.') }));
  if (!r.ok || j.error) throw new Error(j.error || T('Request failed.'));
  return j;
}
function fmtDate(iso, seconds) {
  if (!iso) return '–';
  const [day, time] = iso.split('T');
  const [y, m, d] = day.split('-');
  const today = new Date().toISOString().slice(0, 10) === day || localDay() === day;
  return (today ? T('Today') : T('{month} {day}', { day: +d, month: T(MONTHS[+m - 1]) })) + ' ' + (time || '').slice(0, seconds ? 8 : 5);
}
function localDay() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function fmtDuration(sec) {
  sec = Math.round(sec || 0);
  if (sec < 60) return T('{n} s', { n: sec });
  if (sec < 3600) return T('{n} min', { n: Math.floor(sec / 60) }) + (sec % 60 ? ' ' + T('{n} s', { n: sec % 60 }) : '');
  return T('{h} h {m} min', { h: Math.floor(sec / 3600), m: Math.floor(sec % 3600 / 60) });
}
function toast(text) {
  const e = document.createElement('div');
  e.className = 'toast'; e.textContent = text; document.body.appendChild(e);
  setTimeout(() => e.remove(), 4200);
}
/* localStorage read with the v0.1.0 key as fallback (copied to the new key once found). */
function storageGet(key, legacyKey) {
  let v = localStorage.getItem(key);
  if (v == null && legacyKey) { v = localStorage.getItem(legacyKey); if (v != null) localStorage.setItem(key, v); }
  return v;
}
const icon = d => `<svg viewBox="0 0 24 24">${d}</svg>`;
const ICONS = {
  control: icon('<rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="5" rx="2"/><rect x="13" y="11" width="8" height="10" rx="2"/><rect x="3" y="14" width="8" height="7" rx="2"/>'),
  alert: icon('<path d="M6 9a6 6 0 0112 0c0 6 2.5 7 2.5 7h-17S6 15 6 9z"/><path d="M10 20a2 2 0 004 0"/>'),
  analytics: icon('<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>'),
  action: icon('<rect x="3" y="3" width="18" height="18" rx="4"/><path d="M8 12l3 3 5-6"/>'),
  report: icon('<path d="M4 20V10M10 20V4M16 20v-7M21 20H3"/>'),
  rule: icon('<path d="M12 3l7 3v6c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/>'),
  help: icon('<circle cx="12" cy="12" r="9"/><path d="M9.3 9.3a2.8 2.8 0 115 1.7c-.9 1-2.3 1.4-2.3 3"/><path d="M12 17.2v.1"/>'),
  settings: icon('<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>'),
};
const PAGES = [['', 'control', N_('Control')], ['alerts', 'alert', N_('Alerts')], ['analytics', 'analytics', N_('Analytics')], ['actions', 'action', N_('Action')],
  ['reports', 'report', N_('Reports')], ['rules', 'rule', N_('Rules')]];

function drawRail() {
  const path = route().path;
  const open = (S.status?.cameras || []).reduce((t, k) => t + (k.open || 0), 0);
  const lang = l => `<button type="button" data-action="lang" data-lang="${l}" class="${I18N.lang === l ? 'selected' : ''}" aria-pressed="${I18N.lang === l}">${l.toUpperCase()}</button>`;
  $('#rail').innerHTML = `<div class="brand" title="İSG Kamera"><svg viewBox="0 0 32 32" width="26" height="26"><path d="M16 5l9 3.4v6.8c0 5.6-3.8 9.6-9 11.8-5.2-2.2-9-6.2-9-11.8V8.4z" fill="none" stroke="#ef4b1c" stroke-width="2.6" stroke-linejoin="round"/><circle cx="16" cy="15" r="3" fill="#111318"/></svg></div>`
    + PAGES.map(([p, i, name]) => `<a href="#/${p}" class="${path === p ? 'selected' : ''}">${ICONS[i]}${T(name)}${p === 'alerts' && open ? `<span class="badge">${open > 99 ? '99+' : open}</span>` : ''}</a>`).join('')
    + `<div class="spacer"></div><a href="#" data-action="tour" title="${T('Guided tour: shows the screens and how to add a camera, step by step')}">${ICONS.help}${T('Tour')}</a><a href="#/settings" class="${path === 'settings' || path.startsWith('camera') ? 'selected' : ''}">${ICONS.settings}${T('Settings')}${S.status?.job?.status === 'running' ? '<span class="badge">•</span>' : ''}</a>`
    + `<div class="lang" role="group" aria-label="${T('Language')}">${lang('en')}${lang('tr')}</div>`;
}
/* Hash routes of v0.1.0 (bookmarks) and their query parameters, rewritten once to the new names. */
const LEGACY_ROUTES = { uyarilar: 'alerts', analitik: 'analytics', aksiyonlar: 'actions', raporlar: 'reports', kurallar: 'rules', ayarlar: 'settings', kamera: 'camera' };
const LEGACY_QUERY = { gun: 'days', kamera: 'camera', kategori: 'category', duzey: 'level', durum: 'status', vardiya: 'shift', ciddi: 'serious', sirala: 'sort' };
function route() {
  const raw = location.hash.replace(/^#\/?/, '');
  let [path, query] = raw.split('?');
  path = path || '';
  const parts = path.split('/');
  if (Object.prototype.hasOwnProperty.call(LEGACY_ROUTES, parts[0])) {
    parts[0] = LEGACY_ROUTES[parts[0]];
    if (parts[0] === 'camera' && parts[1] === 'yeni') parts[1] = 'new';
    const q = new URLSearchParams();
    for (const [k, v] of new URLSearchParams(query || '')) q.set(LEGACY_QUERY[k] || k, v);
    path = parts.join('/'); query = q.toString();
    history.replaceState(null, '', '#/' + path + (query ? '?' + query : ''));
  }
  return { path, q: Object.fromEntries(new URLSearchParams(query || '')) };
}
function go(path, q) { const s = new URLSearchParams(q || {}).toString(); location.hash = '#/' + path + (s ? '?' + s : ''); }
const periodSelect = () => `<select class="chip" data-change="period">${[[1, T('Today')], [7, T('Last {n} days', { n: 7 })], [30, T('Last {n} days', { n: 30 })], [90, T('Last {n} days', { n: 90 })]].map(([d, a]) => `<option value="${d}" ${S.days === d ? 'selected' : ''}>${a}</option>`).join('')}</select>`;
const pageHeader = (title, sub, right = '') => `<div class="top"><h1>${h(title)}<small>${sub}</small></h1>${right}</div>`;
const bars = (values, color, max) => {
  const m = max || Math.max(1, ...values.map(v => v || 0));
  return `<div class="bars">${values.map(v => `<i style="${v == null ? '' : `height:${Math.max(6, 100 * v / m)}%;background:${color}`}" title="${v ?? '–'}"></i>`).join('')}</div>`;
};
const levelBadge = o => `<span class="badge ${o.level}">${T(LEVELS[o.level])}</span>${o.serious ? ` <span class="badge serious" title="${T('Serious injury or fatality potential')}">${T('Serious risk')}</span>` : ''}`;
function alertRow(o, selected) {
  S.events[o.id] = o;
  return `<div class="alert ${selected ? 'selected' : ''}" data-action="event-open" data-id="${o.id}">${o.snapshot ? `<img src="/output/${h(o.snapshot)}" loading="lazy" alt="">` : '<div class="none"></div>'}
    <div class="body"><div>${levelBadge(o)} ${o.status !== 'open' ? `<span class="badge grey">${T(STATUS_NAMES[o.status])}</span>` : ''}</div>
    <div class="head">${h(o.rule_name)}</div><div class="sub">${h(o.camera_name)}${o.zone ? ' · ' + h(o.zone) : ''} · ${fmtDate(o.time, true)} · ${fmtDuration(o.duration_s)}</div></div>
    <div style="text-align:right"><div class="caption">${T('Risk')}</div><b style="font-size:18px">${o.score}</b></div></div>`;
}

/* ------------------------------------------------------------ Control Room */
async function pageControl() {
  const d = await api(`/api/summary?days=${S.days}`);
  const s = d.series;
  let body;
  if (!d.cameras.length) body = emptyState();
  else {
    body = `<div class="grid four" style="margin-bottom:14px">
      <div class="card"><div class="caption">${T('Safety score')}</div><div class="number">${d.score ?? '–'}<small>/100</small></div>${bars(s.map(x => x.score), 'var(--green)', 100)}</div>
      <div class="card"><div class="caption">${T('PPE compliance')}</div><div class="number">${d.compliance == null ? '–' : percent(d.compliance)}</div>${d.compliance == null ? `<div class="note" style="margin-top:10px">${T('No people were seen on cameras with a PPE rule enabled.')}</div>` : bars(s.map(x => x.compliance), '#6fb89b', 100)}</div>
      <div class="card"><div class="caption">${T('Monitored time')}</div><div class="number">${fmtDuration(d.monitored_s)}</div>${bars(s.map(x => x.monitored_s), '#f3b08a')}</div>
      <div class="card"><div class="caption">${T('Serious-risk events')}</div><div class="number">${d.serious}<small>${plural(d.events, N_('of {n} event'), N_('of {n} events'))}</small></div>${bars(s.map(x => x.serious), 'var(--accent)')}</div>
    </div>
    <div class="grid two">
      <div class="card"><div style="display:flex;align-items:center;margin-bottom:12px"><div class="caption" style="flex:1">${T('Cameras')} · ${d.cameras.length}</div><a class="btn small" href="#/settings">${T('Add / configure cameras')}</a></div>
        <div class="cameras">${d.cameras.map(k => `<div class="cam" data-action="camera-open" data-code="${h(k.code)}">${k.last_frame || k.setup_frame ? `<img src="${h(k.last_frame || k.setup_frame)}" alt="">` : `<div class="empty">${T('No image yet.<br>Start the analysis in Settings.')}</div>`}
          ${k.open ? `<div class="open">${T('{n} open', { n: k.open })}</div>` : ''}<div class="name"><span>${h(k.name)}</span>${k.last_analysis ? `<span>${fmtDate(k.last_analysis)}</span>` : ''}</div></div>`).join('')}</div>
        <div class="note" style="margin-top:10px">${T("Faces of people detected in the images are masked; no identity recognition is done. Each tile is the camera's most recently analyzed frame.")}</div></div>
      <div class="card"><div style="display:flex;align-items:center;margin-bottom:12px"><div class="caption" style="flex:1"><span style="color:var(--accent)">●</span> ${T('Open alerts')} · ${d.open}</div><a class="btn small" href="#/alerts">${T('All')}</a></div>
        <div class="list">${d.live.length ? d.live.map(o => alertRow(o)).join('') : `<div class="note">${T('No open alerts in this period.')}</div>`}</div></div>
    </div>`;
  }
  $('#page').innerHTML = pageHeader(T('Control Room'), `${h(d.site)} · ${T('riskiest alerts first')}`, periodSelect()) + modelWarning() + body;
}
/* ------------------------------------------------------------ AugeLab Studio setup wizard
   Step by step: install Studio → AugeLab account → license code → ready. The Studio window never opens:
   İSG Kamera uses Studio headless (the license code is also registered headless with Studio's own command).
   While the installation runs, the AugeLab account page opens inside the dialog (people without an account sign up then).
   Signing in with an existing account is not done in the frame: Google/Microsoft sign-in does not work in a frame; the
   license code is fetched in a separate window. */
const WIZ = { dismissed: false, offline: 0, hasAccount: false, timer: null, code: '', error: '', autoTest: false };
try { WIZ.hasAccount = storageGet('isg-augelab-account', 'isg-augelab-hesap') === '1'; } catch (x) { /* storage disabled */ }
const WIZ_INSTALL_STAGES = ['downloading', 'vc', 'installing', 'window'];
const wizMb = b => decimal((b / 1048576).toFixed(1));
function wizAccountUrl() { return S.status?.account_url || 'https://account.augelab.com/'; }
function wizTesting(d) { return d?.job?.type === 'studio-test' && d?.job?.status === 'running'; }
function wizStep(d) {
  const k = d?.studio_setup || {};
  const installing = (d?.job?.type === 'studio-install' && d?.job?.status === 'running') || WIZ_INSTALL_STAGES.includes(k.stage);
  if (!d?.studio_installed || installing) return 0;
  if (d?.studio?.connected && !wizTesting(d)) return 3;
  if (!WIZ.hasAccount && !d?.studio?.license_saved && !wizTesting(d)) return 1;
  return 2;
}
function wizAccount(step, d) {
  const k = d?.studio_setup || {};
  const show = !WIZ.hasAccount && (step === 1 || (step === 0 && WIZ_INSTALL_STAGES.includes(k.stage)));
  $('#wiz-account').hidden = !show;
  $('#wizard').classList.toggle('wide', show);
  if (!show) return;
  const url = wizAccountUrl() + '?p=signup';
  const frame = $('#wiz-iframe');
  if (frame.dataset.url !== url) {
    frame.src = url; frame.dataset.url = url;
    $('#wiz-frame').classList.remove('full'); $('#wiz-top').textContent = T('Show the top of the page');
    $('#wiz-scroll').classList.remove('gone');
  }
  $('#wiz-account-link').href = url;
  $('#wiz-account-text').innerHTML = step === 0
    ? T('<b>If you have no AugeLab account, create one here while the installation runs.</b> You need it for the license code.')
    : T('<b>Create your AugeLab account here</b> (confirm it with the link sent to your email). If you already have an account, click “I have an account”.');
}
function wizLastTest(d) {
  // If the last "test Studio" job ended with an error, its reason (no license, broken installation...).
  const job = d?.job;
  if (job?.type !== 'studio-test' || job.status === 'running' || d?.studio?.connected) return '';
  return job.status === 'error' ? (job.lines || []).slice(-1)[0] || T('License not found.') : '';
}
function wizDraw() {
  const d = S.status;
  if (!d || !$('#wizard').open) return;
  const k = d.studio_setup || {};
  const step = wizStep(d);
  document.querySelectorAll('#wizard .wiz-steps li').forEach((li, i) => { li.className = i < step ? 'done' : i === step ? 'current' : ''; });
  let body = '', action = '';
  if (step === 0 && WIZ_INSTALL_STAGES.includes(k.stage)) {
    // Sub-steps of the "install Studio" step, checked off as they finish.
    const order = { downloading: 0, vc: 1, installing: 2, window: 2 }[k.stage];
    const pct = k.total ? Math.round(100 * k.downloaded / k.total) : 0;
    const download = k.total ? `<div class="wiz-bar"><i style="width:${pct}%"></i></div><div class="wiz-wait">${h(k.what)} · ${wizMb(k.downloaded)} / ${wizMb(k.total)} MB · ${T('{n}%', { n: pct })}</div>` : '';
    const elapsed = `${Math.floor((k.elapsed || 0) / 60)}:${String((k.elapsed || 0) % 60).padStart(2, '0')}`;
    const line = (i, text, extra = '') => `<li style="list-style:none;margin-left:-20px">${i < order ? '✓' : i === order ? '▶' : '○'} ${i === order ? `<b>${text}</b>` : text}${i === order ? extra : ''}</li>`;
    body = `<h2>${T('Installing AugeLab Studio')}</h2><ol class="todo">
      ${line(0, T('Download the AugeLab installer'), k.stage === 'downloading' ? download : '')}
      ${line(1, T('Install the Visual C++ component (Microsoft)'), k.stage === 'vc' ? (download || `<div class="wiz-wait"><span class="wiz-spin"></span>${T('Installing. If Windows asks for permission, click <b>Yes</b>.')}</div>`) : '')}
      ${line(2, k.stage === 'window' ? T('Install AugeLab Studio in the setup window') : T('Install AugeLab Studio (takes a few minutes)'),
        `<div class="wiz-wait"><span class="wiz-spin"></span>${k.stage === 'window' ? T('Complete the steps in the setup window.') + ' ' : ''}${elapsed}</div>`)}
    </ol>`;
  } else if (step === 0) {
    body = `<h2>${T('AugeLab Studio is not installed')}</h2><p>${T('İSG Kamera analyzes the images with <b>AugeLab Studio</b> on this computer; images are not sent to the internet. Studio runs in the background; its window does not open.')}</p>
      <p>${T('İSG Kamera will download AugeLab Studio from AugeLab and install it on this computer; if the Visual C++ component is missing, it installs that from Microsoft too.')}</p>
      ${k.stage === 'error' && k.error ? `<p class="error">${h(k.error)}</p>` : ''}`;
    action = `<button class="btn main" data-action="studio-install" data-type="install"${d.job?.status === 'running' ? ' disabled' : ''}>${k.stage === 'error' ? T('Try again') : T('Download and install')} →</button>`;
  } else if (step === 1) {
    body = `<h2>${T('AugeLab account')}</h2><p>${T('AugeLab Studio is installed. The license code that activates Studio is shown in your AugeLab account; the free Community plan is enough.')}</p>`;
    action = `<button class="btn main" id="wiz-account-done" type="button">${T('My account is ready')} →</button>`;
  } else if (step === 2) {
    const testing = wizTesting(d);
    const error = WIZ.error || wizLastTest(d);
    body = `<h2>${T('License code')}</h2>
      <ol class="todo">
        <li>${T('Sign in to your AugeLab account (Google, Microsoft or email) and copy the license code.')}
          <div style="margin-top:8px"><button class="btn" data-action="account-open">${T('Get my license code')} ↗</button></div></li>
        <li>${T('Paste the code here. İSG Kamera gives the code only to AugeLab Studio; it does not store it.')}
          <div class="wiz-code"><input id="wiz-code" autocomplete="off" spellcheck="false" placeholder="${T('License code')}" value="${h(WIZ.code)}"${testing ? ' disabled' : ''}></div></li>
      </ol>
      ${testing ? `<div class="wiz-wait"><span class="wiz-spin"></span>${T('Checking the license…')}</div>` : ''}
      ${error ? `<p class="error">${h(error)}</p>` : ''}
      ${d.studio?.license_saved || testing ? '' : `<p class="faded" style="font-size:13px">${T('If Studio is already activated on this computer, {link} without entering a code.', { link: `<a href="#" id="wiz-test">${T('check the license')}</a>` })}</p>`}`;
    action = `<button class="btn main" id="wiz-save" type="button"${testing ? ' disabled' : ''}>${T('Save')} →</button>`;
  } else {
    body = `<h2>${T('Ready')}</h2><p>${T('AugeLab Studio {version} is ready to run in the background; its window does not open. You can add your cameras and start the analysis, or run the sample analysis from Settings.', { version: h(d.studio?.version || '') })}</p>`;
    action = `<button class="btn main" data-action="wizard-close">${T('Finish')}</button>`;
  }
  const focused = document.activeElement?.id === 'wiz-code';
  $('#wiz-body').innerHTML = body; $('#wiz-action').innerHTML = action;
  if (step === 2) {
    const input = $('#wiz-code');
    input.oninput = () => { WIZ.code = input.value; WIZ.error = ''; };
    input.onkeydown = e => { if (e.key === 'Enter') $('#wiz-save').click(); };
    if (focused) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
    $('#wiz-save').onclick = wizSave;
    const test = $('#wiz-test');
    if (test) test.onclick = async e => { e.preventDefault(); WIZ.error = ''; try { await api('/api/studio/test', {}); } catch (x) { WIZ.error = x.message; } await fetchStatus(); };
  }
  if (step === 1) $('#wiz-account-done').onclick = () => $('#wiz-have-account').click();
  // If the license code was entered in the installer, Studio is tested once automatically (no window opens).
  if (step === 2 && d.studio?.license_saved && d.studio?.ready !== true && !wizTesting(d) && !WIZ.autoTest && d.job?.status !== 'running') {
    WIZ.autoTest = true;
    api('/api/studio/test', {}).then(fetchStatus).catch(x => { WIZ.error = x.message; wizDraw(); });
  }
  wizAccount(step, d);
  $('#wiz-later').style.visibility = step === 3 ? 'hidden' : 'visible';
}
async function wizSave() {
  const code = (WIZ.code || '').trim();
  if (!code) { WIZ.error = T('Paste the license code.'); wizDraw(); return; }
  WIZ.error = '';
  $('#wiz-save').disabled = true;
  try {
    await api('/api/studio/license', { code });
    WIZ.code = '';  // the code is not kept in the page either
  } catch (x) {
    WIZ.error = x.message;
  }
  await fetchStatus();
}
function openWizard() {
  WIZ.dismissed = false;
  if (!$('#wizard').open) $('#wizard').showModal();
  wizDraw();
  clearInterval(WIZ.timer);
  WIZ.timer = setInterval(async () => { try { await fetchStatus(); } catch (x) { /* the program may have closed */ } }, 1500);
}
function closeWizard() { $('#wizard').close(); clearInterval(WIZ.timer); }
function watchWizard() {
  // If Studio is not ready (not installed, no license entered, test not passed) the wizard opens by itself (it waits while the tour is open).
  const connected = !!S.status?.studio?.connected;
  WIZ.offline = connected ? 0 : WIZ.offline + 1;
  if (!connected && WIZ.offline >= 2 && !WIZ.dismissed && !$('#wizard').open && !document.getElementById('tour')) openWizard();
  wizDraw();
}
$('#wiz-later').onclick = () => { WIZ.dismissed = true; closeWizard(); };
$('#wiz-have-account').onclick = () => { WIZ.hasAccount = true; try { localStorage.setItem('isg-augelab-account', '1'); } catch (x) { /* storage disabled */ } wizDraw(); };
$('#wiz-top').onclick = () => { const full = $('#wiz-frame').classList.toggle('full'); $('#wiz-top').textContent = full ? T('Bring the form to the front') : T('Show the top of the page'); };
$('#wiz-frame').addEventListener('mouseenter', () => setTimeout(() => $('#wiz-scroll').classList.add('gone'), 4000));
$('#wizard').addEventListener('cancel', () => { WIZ.dismissed = true; clearInterval(WIZ.timer); });

let autoTestDone = false;
function studioAutoTest(d) {
  // If Studio is installed but not tested yet (e.g. the license code was entered in the installer), a test starts once automatically.
  if (autoTestDone || !d?.studio_installed || d?.studio?.installed === false || d?.studio?.ready != null || d?.job?.status === 'running') return;
  autoTestDone = true;
  api('/api/studio/test', {}).then(fetchStatus).catch(() => { /* a job may be running; it is retried from the wizard */ });
}
function studioWarning() {
  // A short strip at the top of the page while Studio is not connected: the steps are in the setup wizard (above).
  const d = S.status;
  if (d?.studio?.connected) return '<div data-studio-setup hidden></div>';
  const installing = d?.job?.type === 'studio-install' && d?.job?.status === 'running';
  const testing = d?.job?.type === 'studio-test' && d?.job?.status === 'running';
  if (testing || (d?.studio_installed && d?.studio?.ready == null && !installing)) {
    return '<div data-studio-setup class="alert-strip"><b>' + T('Checking the license') + '</b> ' + T('Please wait.') + '</div>';
  }
  const title = installing ? T('Installing AugeLab Studio') : d?.studio_installed ? T('License not found') : T('AugeLab Studio is not installed');
  const extra = d?.studio_installed && d?.studio?.error ? h(d.studio.error) + ' ' : T('Analysis needs AugeLab Studio (it runs in the background; its window does not open); the setup wizard guides you step by step.') + ' ';
  return '<div data-studio-setup class="alert-strip"><b>' + title + '</b> ' + extra
    + '<button class="btn small main" data-action="wizard-open">' + T('Setup wizard') + '</button></div>';
}
function lockWarning() {
  const k = S.status?.model_lock;
  if (k?.community) {
    if (k.status === 'verified_in_studio_run') return '';
    return '<div class="alert-strip" data-model-lock><b>' + T('Model package') + '</b> '
      + h(k.message || T('Could not read the local Studio license.')) + '</div>';
  }
  if (k?.licensed) {
    const active = k.status === 'active';
    const message = active
      ? T("This device's model license is valid until {date}.", { date: new Date(k.expires * 1000).toLocaleString(I18N.lang === 'tr' ? 'tr-TR' : 'en-US') })
      : (k.message || T('A model license is required.')) + ' ' + T('Download the device request to get a license.');
    return '<div class="alert-strip" data-model-lock><b>' + T('Model license') + '</b> ' + h(message)
      + ' <button class="btn small" data-action="model-device-request">' + T('Download device request') + '</button>'
      + ' <button class="btn small main" data-action="model-license">' + T('Upload model.lic') + '</button></div>';
  }
  if (!k?.encrypted || k.status === 'active') return '';
  const text = k.status === 'key_missing'
    ? T('To start the analysis, enter the activation code once for this Windows user.')
    : (k.message || T('Cannot open the models.'));
  const input = k.package
    ? ' <span style="display:inline-flex;gap:6px;align-items:center;margin-left:6px"><input id="activation-code" type="text" autocomplete="off" spellcheck="false" placeholder="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX" style="width:270px">'
      + '<button class="btn small main" data-action="model-activate">' + T('Activate') + '</button></span>'
    : ' ' + T('Key package (anahtar-paketi.json) not found; download the program again.');
  return '<div class="alert-strip" data-model-lock><b>' + (k.status === 'key_missing' ? T('Models are not activated.') : T('Cannot open the models.')) + '</b> ' + h(text) + input + '</div>';
}
function modelWarning() {
  const m = S.status?.models;
  return studioWarning() + lockWarning() + (m && !m.object ? '<div class="alert-strip"><b>' + T('PPE and vehicle model not installed.') + '</b> ' + T('Hard hat, vest, gloves, glasses, safety shoes and forklift rules do not work; zone, people count, proximity and running rules work.') + '</div>' : '');
}
function emptyState() {
  return `<div class="empty-state"><h3>${T('No cameras yet')}</h3><p>${T('Add an IP camera, recorder, USB camera or video file; draw the zones and choose the rules.')}</p>
    <p><a class="btn main" href="#/camera/new">${T('Add camera')}</a> ${S.status?.has_samples ? `<button class="btn" data-action="sample">${T('Run sample analysis')}</button>` : ''}</p></div>`;
}

/* ------------------------------------------------------------ Alerts */
async function pageAlerts(q) {
  const p = new URLSearchParams({ days: S.days });
  for (const a of ['camera', 'category', 'level', 'status', 'shift', 'serious', 'sort']) if (q[a]) p.set(a, q[a]);
  const d = await api('/api/events?' + p);
  const sel = (field, label, options) => `<select class="chip" data-change="filter" data-field="${field}"><option value="">${label}</option>${options.map(([v, a]) => `<option value="${h(v)}" ${q[field] === String(v) ? 'selected' : ''}>${h(a)}</option>`).join('')}</select>`;
  const filters = sel('camera', T('All cameras'), S.status.cameras.map(k => [k.code, k.name]))
    + sel('category', T('All categories'), Object.entries(S.catalog.categories))
    + sel('level', T('All levels'), translateTable(LEVELS)) + sel('status', T('All statuses'), translateTable(STATUS_NAMES))
    + sel('shift', T('All shifts'), S.status.settings.shifts.map(v => [v.name, v.name]))
    + sel('sort', T('Newest first'), [['score', T('By risk')]]) + periodSelect();
  $('#page').innerHTML = pageHeader(T('Alerts'), plural(d.total, N_('{n} event · every alert comes with a clip, risk score, location and status'), N_('{n} events · every alert comes with a clip, risk score, location and status')), '')
    + `<div class="top" style="margin-top:-6px">${filters}</div>`
    + (d.events.length ? `<div class="list">${d.events.map(o => alertRow(o, q.id == o.id)).join('')}</div>` : `<div class="empty-state"><h3>${T('No alerts')}</h3><p>${T('No events match these filters.')}</p></div>`);
  if (q.id && S.events[q.id]) eventDrawer(S.events[q.id]);
}
function eventDrawer(o) {
  $('#overlay').innerHTML = `<div class="drawer form"><div style="display:flex;gap:10px;align-items:flex-start;margin-bottom:12px"><div style="flex:1">
      <div>${levelBadge(o)} <span class="badge grey">${T(STATUS_NAMES[o.status])}</span></div><h2 style="margin-top:6px">${h(o.rule_name)}</h2>
      <div class="note">${h(o.camera_name)}${o.zone ? ' · ' + h(o.zone) : ''} · ${fmtDate(o.time, true)}${o.shift ? ' · ' + T('{shift} shift', { shift: h(o.shift) }) : ''}</div></div>
      <button class="btn small" data-action="overlay-close">${T('Close')}</button></div>
    ${o.clip ? `<video src="/output/${h(o.clip)}" controls autoplay muted loop playsinline></video>` : (o.snapshot ? `<img class="large" src="/output/${h(o.snapshot)}" alt="">` : '')}
    <div class="info"><div><span class="caption">${T('Risk score')}</span><b>${o.score} / 25</b><span class="note">${T('severity {severity} × likelihood {likelihood}', { severity: o.severity, likelihood: o.likelihood })}</span></div>
      <div><span class="caption">${T('Duration')}</span><b>${fmtDuration(o.duration_s)}</b><span class="note">${plural(o.people, N_('{n} person'), N_('{n} people'))}</span></div>
      <div><span class="caption">${T('Event no.')}</span><b>#${o.id}</b><span class="note">${S.catalog.categories[o.category] || o.category}</span></div></div>
    <p>${h(o.description || '')}</p>
    <label>${T('Assignee')}</label><input id="event-assignee" value="${h(o.assignee || '')}" placeholder="${T('name or team')}">
    <label>${T('Note')}</label><textarea id="event-note" placeholder="${T('what was done, what was seen')}">${h(o.notes || '')}</textarea>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
      <button class="btn" data-action="event-status" data-id="${o.id}" data-status="assigned">${T('Take on')}</button>
      <button class="btn" data-action="event-status" data-id="${o.id}" data-status="resolved">${T('Resolved')}</button>
      <button class="btn" data-action="event-status" data-id="${o.id}" data-status="false_alarm">${T('False alarm')}</button>
      ${o.status !== 'open' ? `<button class="btn" data-action="event-status" data-id="${o.id}" data-status="open">${T('Reopen')}</button>` : ''}
      <button class="btn main" data-action="task-new" data-event="${o.id}">${T('Create task')}</button></div>
    ${(o.tasks || []).length ? `<p class="note">${T('This event is linked to {tasks}.', { tasks: o.tasks.map(g => `<a href="#/actions">${T('task #{n}', { n: g })}</a>`).join(', ') })}</p>` : ''}
    <p class="note">${T('An event marked "False alarm" is left out of the safety score and reports.')}</p></div>`;
}

/* ------------------------------------------------------------ Visual Analytics */
function heatColor(t) {
  const r = [[31, 111, 120], [242, 179, 61], [239, 75, 28]];
  const a = t < .5 ? 0 : 1, f = t < .5 ? t * 2 : (t - .5) * 2;
  return 'rgb(' + r[a].map((v, i) => Math.round(v + (r[a + 1][i] - v) * f)).join(',') + ')';
}
function drawScene(d, mode, layer) {
  if (!d.last_frame) return `<div class="stage"><div class="placeholder">${T('No analyzed images from this camera in this period.')}</div></div>`;
  const g = d.grid;
  let inner = '';
  if (mode === 'heat' && g.cols) {
    const cells = g[layer] || [];
    const m = Math.max(0, ...cells.flat());
    const gw = 1000 / g.cols, gh = 1000 / g.rows;
    if (m > 0) cells.forEach((row, y) => row.forEach((v, x) => {
      if (v <= 0) return;
      const t = Math.pow(v / m, .55);
      inner += `<rect x="${x * gw}" y="${y * gh}" width="${gw}" height="${gh}" fill="${heatColor(t)}" opacity="${(.22 + .58 * t).toFixed(2)}"><title>${layer === 'event' ? T('total risk score {n}', { n: v }) : fmtDuration(v)}</title></rect>`;
    }));
  } else if (mode === 'paths') {
    for (const z of d.tracks) {
      if (layer !== 'event' && z.s !== layer) continue;
      const color = z.s === 'vehicle' ? '#5aa2ff' : '#ff8a4c';
      inner += `<polyline points="${z.p.map(p => `${p[0] * 1000},${p[1] * 1000}`).join(' ')}" fill="none" stroke="${color}" stroke-width="2" stroke-opacity=".7" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
      const s = z.p[z.p.length - 1];
      inner += `<line x1="${s[0] * 1000}" y1="${s[1] * 1000}" x2="${s[0] * 1000}" y2="${s[1] * 1000}" stroke="${color}" stroke-width="8" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
    }
  }
  for (const b of d.zones) inner += `<polygon points="${b.poly.map(p => `${p[0] * 1000},${p[1] * 1000}`).join(' ')}" fill="none" stroke="#ffd166" stroke-width="1.5" stroke-dasharray="6 5" vector-effect="non-scaling-stroke"><title>${h(b.name)}</title></polygon>`;
  if (layer === 'event' || mode === 'paths') for (const n of d.points) inner += `<line x1="${n.x * 1000}" y1="${n.y * 1000}" x2="${n.x * 1000}" y2="${n.y * 1000}" stroke="#fff" stroke-width="${6 + n.score / 3}" stroke-opacity=".9" stroke-linecap="round" vector-effect="non-scaling-stroke"><title>${h(n.rule_name)} · ${T('risk {n}', { n: n.score })}</title></line>`;
  return `<div class="stage"><img src="${h(d.last_frame)}" alt=""><svg viewBox="0 0 1000 1000" preserveAspectRatio="none">${inner}</svg></div>`;
}
function analyticsStrip(d) {
  const s = d.counts;
  return `<div class="strip"><span class="caption">${T('Figures')}</span><span>${T('People/day')}<b>${s.people_per_day}</b></span><span>${T('Peak occupancy')}<b>${s.peak_people}</b></span><span>${T('Vehicles/day')}<b>${s.vehicles_per_day}</b></span>
    <span>${T('Busiest hour')}<b>${s.busiest_hour || '–'}</b></span><span>${T('PPE compliance')}<b style="color:${s.compliance != null && s.compliance < 80 ? 'var(--accent)' : 'inherit'}">${s.compliance == null ? '–' : percent(s.compliance)}</b></span>
    <span>${T('Events')}<b>${s.events}</b></span><span class="scale">${T('LOW')}<i></i>${T('HIGH')}</span></div>`;
}
async function pageAnalytics(q) {
  const A = S.analytics;
  const cameras = S.status.cameras;
  if (!cameras.length) { $('#page').innerHTML = pageHeader(T('Visual Analytics'), T('heat map and paths')) + emptyState(); return; }
  if (q.camera) A.camera = q.camera;
  if (!cameras.some(k => k.code === A.camera)) A.camera = (cameras.find(k => k.last_analysis) || cameras[0]).code;
  const query = extra => '/api/analytics?' + new URLSearchParams({ camera: A.camera, category: A.category, ...extra });
  const d = await api(query({ days: S.days }));
  let before = null;
  if (A.compare) {  // the previous period of equal length
    const days = S.days, to = new Date(Date.now() - days * 864e5), from = new Date(Date.now() - (2 * days - 1) * 864e5);
    const f = x => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`;
    before = await api(query({ from: f(from), to: f(to) }));
  }
  const button = (field, value, label) => `<button class="${A[field] === value ? 'selected' : ''}" data-action="analytics-select" data-field="${field}" data-value="${value}">${label}</button>`;
  const maxHour = Math.max(1, ...d.hourly.map(x => x.person_s));
  const eventCount = n => plural(n, N_('{n} event'), N_('{n} events'));
  $('#page').innerHTML = pageHeader(T('Visual Analytics'), T('where movement and risk concentrate on site'), periodSelect())
    + `<div class="card"><div class="top" style="margin-bottom:10px">
        <select class="chip" data-change="analytics-camera">${cameras.map(k => `<option value="${h(k.code)}" ${k.code === A.camera ? 'selected' : ''}>${h(k.name)}</option>`).join('')}</select>
        <select class="chip" data-change="analytics-category"><option value="">${T('All alerts')}</option><option value="serious" ${A.category === 'serious' ? 'selected' : ''}>${T('Serious risk')}</option>${Object.entries(S.catalog.categories).map(([k, a]) => `<option value="${k}" ${A.category === k ? 'selected' : ''}>${h(a)}</option>`).join('')}</select>
        <label class="chip" style="cursor:pointer"><input type="checkbox" data-change="analytics-compare" ${A.compare ? 'checked' : ''}> ${T('Compare with the previous period')}</label></div>
      <div class="tabs">${button('mode', 'heat', T('Heat map'))}${button('mode', 'paths', T('Paths'))}
        <span style="flex:1"></span>${button('layer', 'person', T('People'))}${button('layer', 'vehicle', T('Vehicles'))}${button('layer', 'event', T('Event risk'))}</div>
      ${before ? `<div class="grid" style="grid-template-columns:1fr 1fr"><div><div class="caption" style="margin-bottom:6px">${T('Before')} · ${before.from.slice(0, 10)} – ${before.to.slice(0, 10)} · ${eventCount(before.counts.events)}</div>${drawScene({ ...before, last_frame: before.last_frame || d.last_frame, zones: d.zones }, A.mode, A.layer)}</div>
          <div><div class="caption" style="margin-bottom:6px">${T('After')} · ${d.from.slice(0, 10)} – ${d.to.slice(0, 10)} · ${eventCount(d.counts.events)}</div>${drawScene(d, A.mode, A.layer)}</div></div>` : drawScene(d, A.mode, A.layer)}
      ${analyticsStrip(d)}
      ${d.counts.riskiest_share ? `<p class="note" style="margin:8px 2px 0">${T('The riskiest cell carries <b>{pct}%</b> of the total event risk in this view.', { pct: d.counts.riskiest_share })}</p>` : ''}</div>
    <div class="grid two" style="margin-top:14px"><div class="card"><div class="caption">${T('People density by hour')}</div>
        <div class="bars" style="height:90px">${d.hourly.map(x => `<i style="height:${Math.max(3, 100 * x.person_s / maxHour)}%;background:${x.events ? 'var(--accent)' : '#f3b08a'}" title="${String(x.hour).padStart(2, '0')}:00 · ${T('{time} person-time', { time: fmtDuration(x.person_s) })} · ${eventCount(x.events)}"></i>`).join('')}</div>
        <div style="display:flex;justify-content:space-between" class="caption"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div>
        <p class="note">${T('Dark bar: an event occurred in that hour. Figures are AI estimates; broken tracks can make the people count come out higher than it really is.')}</p></div>
      <div class="card"><div class="caption" style="margin-bottom:6px">${T('Events by rule on this camera')}</div>${d.rules.length ? `<table>${d.rules.map(k => `<tr><td>${h(k.name)}</td><td class="s"><b>${k.n}</b></td></tr>`).join('')}</table>` : `<div class="note">${T('No events.')}</div>`}
        <p style="margin-top:12px"><button class="btn small" data-action="task-new" data-camera="${h(A.camera)}">${T('Create task from this camera')}</button></p></div></div>`;
}

/* ------------------------------------------------------------ Actions */
async function pageActions() {
  const d = await api('/api/tasks');
  const card = g => {
    const m = Math.max(1, ...g.weeks.map(x => x.n));
    const verification = g.verification ? { verified: `<span class="badge green">${T('Camera verified: no repeat since closing')}</span>`, recurred: `<span class="badge red">${plural(g.verification.events_after, N_('Repeated {n} time after closing'), N_('Repeated {n} times after closing'))}</span>`, pending: `<span class="badge grey">${T('Waiting for new footage to verify')}</span>` }[g.verification.status] : '';
    const due = g.due_date ? (I18N.lang === 'tr' ? `${g.due_date.slice(8, 10)}.${g.due_date.slice(5, 7)}` : `${g.due_date.slice(5, 7)}/${g.due_date.slice(8, 10)}`) : '';
    return `<div class="task"><div style="display:flex;gap:10px;align-items:flex-start"><div style="flex:1"><h3>${h(g.title)}</h3>
        <div class="note">${g.owner ? T('Owner: {name}', { name: h(g.owner) }) + ' · ' : ''}${plural(g.events.length, N_('linked to {n} event'), N_('linked to {n} events'))} · ${T('opened {date}', { date: fmtDate(g.created) })}</div></div>
        ${g.due_date ? `<span class="badge ${g.overdue ? 'critical' : 'yellow'}">${T('Due {date}', { date: due })}</span>` : ''}
        <span class="badge ${g.status === 'closed' ? 'green' : g.status === 'in_progress' ? 'yellow' : 'grey'}">${T(TASK_STATUS_NAMES[g.status])}</span></div>
      ${g.description ? `<p style="margin:8px 0 0">${h(g.description)}</p>` : ''}
      ${g.events.length ? `<div style="display:flex;gap:6px;margin-top:10px;overflow:auto">${g.events.slice(0, 6).map(o => o.snapshot ? `<img src="/output/${h(o.snapshot)}" style="height:54px;border-radius:7px;cursor:pointer" data-action="event-goto" data-id="${o.id}" title="${h(o.rule_name)} · ${fmtDate(o.time)}">` : '').join('')}</div>` : ''}
      ${g.weeks.length ? `<div class="caption" style="margin-top:12px">${T('Same rule · weekly events')}</div><div class="bars" style="height:54px">${g.weeks.map(x => `<i style="height:${Math.max(6, 100 * x.n / m)}%;background:${g.closed && x.week > g.closed.slice(0, 10) ? '#6fb89b' : 'var(--accent)'}" title="${T(x.n === 1 ? N_('week of {week}: {n} event') : N_('week of {week}: {n} events'), { week: x.week, n: x.n })}"></i>`).join('')}</div>` : ''}
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px">${verification}<span style="flex:1"></span>
        ${g.status !== 'in_progress' && g.status !== 'closed' ? `<button class="btn small" data-action="task-status" data-id="${g.id}" data-status="in_progress">${T('Start')}</button>` : ''}
        ${g.status !== 'closed' ? `<button class="btn small main" data-action="task-status" data-id="${g.id}" data-status="closed">${T('Close')}</button>` : `<button class="btn small" data-action="task-status" data-id="${g.id}" data-status="open">${T('Reopen')}</button>`}
        <button class="btn small" data-action="task-edit" data-id="${g.id}">${T('Edit')}</button><button class="btn small danger" data-action="task-delete" data-id="${g.id}">${T('Delete')}</button></div></div>`;
  };
  S.tasks = Object.fromEntries(d.tasks.map(g => [g.id, g]));
  $('#page').innerHTML = pageHeader(T('Actions'), T('a next step with an owner and a due date for every finding'), `<button class="btn main" data-action="task-new">${T('New task')}</button>`)
    + (d.tasks.length ? `<div class="list" style="gap:12px">${d.tasks.map(card).join('')}</div>`
      : `<div class="empty-state"><h3>${T('No tasks yet')}</h3><p>${T('Open an alert and click <b>Create task</b>, or add a new task here. If the same rule fires again on the same camera after the task is closed, it shows up here.')}</p></div>`);
}
function taskDialog(g, eventId) {
  const o = eventId ? S.events[eventId] : null;
  g = g || { title: o ? `${o.rule_name}${o.zone ? ' · ' + o.zone : ''}` : '', description: '', owner: '', due_date: '', status: 'open', camera: o?.camera || route().q.camera || '', rule: o?.rule || '', zone: o?.zone || '' };
  $('#overlay').innerHTML = `<div class="backdrop"><div class="dialog form"><h2 style="margin:0 0 4px">${g.id ? T('Edit task') : T('New task')}</h2>
    ${o ? `<div class="note">${T('Event #{id} is linked to this task and becomes "assigned".', { id: o.id })}</div>` : ''}
    <label>${T('Title')}</label><input id="task-title" value="${h(g.title)}" placeholder="${T('e.g. Repaint the pedestrian walkway in the loading area')}">
    <label>${T('Description')}</label><textarea id="task-description">${h(g.description || '')}</textarea>
    <div class="row"><div><label>${T('Owner')}</label><input id="task-owner" value="${h(g.owner || '')}"></div><div><label>${T('Due date')}</label><input id="task-due" type="date" value="${h(g.due_date || '')}"></div></div>
    <div class="row"><div><label>${T('Camera')}</label><select id="task-camera"><option value="">–</option>${S.status.cameras.map(k => `<option value="${h(k.code)}" ${g.camera === k.code ? 'selected' : ''}>${h(k.name)}</option>`).join('')}</select></div>
      <div><label>${T('Rule to watch')}</label><select id="task-rule"><option value="">–</option>${S.catalog.rules.filter(k => k.status !== 'off').map(k => `<option value="${k.id}" ${g.rule === k.id ? 'selected' : ''}>${h(k.name)}</option>`).join('')}</select></div></div>
    <p class="note">${T('If a camera and a rule are chosen and that rule is seen again on that camera after the task is closed, the task is marked "repeated".')}</p>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-action="overlay-close">${T('Cancel')}</button>
      <button class="btn main" data-action="task-save" data-id="${g.id || ''}" data-event="${eventId || ''}" data-status="${g.status}" data-zone="${h(g.zone || '')}">${T('Save')}</button></div></div></div>`;
  $('#task-title').focus();
}

/* ------------------------------------------------------------ Reports */
function lineChart(points, min = 0, max = 100) {
  const filled = points.map((v, i) => [i, v]).filter(x => x[1] != null);
  if (!filled.length) return `<div class="note" style="padding:30px 0">${T('No footage to compute a score from in this period.')}</div>`;
  const n = Math.max(1, points.length - 1), X = i => 20 + 960 * i / n, Y = v => 180 - 160 * (v - min) / (max - min);
  const path = filled.map(([i, v], j) => `${j ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 1000 200" style="width:100%;height:190px">${[0, 50, 100].map(v => `<line x1="20" x2="980" y1="${Y(v)}" y2="${Y(v)}" stroke="#eeeae0"/><text x="0" y="${Y(v) + 4}" font-size="11" fill="#9a9ea8">${v}</text>`).join('')}
    <path d="${path}" fill="none" stroke="#ef4b1c" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>${filled.map(([i, v]) => `<circle cx="${X(i)}" cy="${Y(v)}" r="${filled.length > 40 ? 2 : 4}" fill="#fff" stroke="#ef4b1c" stroke-width="2"><title>${v}</title></circle>`).join('')}</svg>`;
}
async function pageReports() {
  const d = await api(`/api/report?days=${Math.max(S.days, 7)}`);
  const delta = d.score != null && d.previous_score != null ? d.score - d.previous_score : null;
  const table = (title, rows, extra) => `<div class="card"><div class="caption" style="margin-bottom:6px">${title}</div>${rows.length ? `<table><tr><th></th><th class="s">${T('Events')}</th><th class="s">${T('Serious')}</th><th class="s">${T('Risk')}</th><th style="width:28%"></th>${extra ? `<th class="s">${T('Score')}</th>` : ''}</tr>
    ${rows.slice(0, 12).map(r => `<tr><td>${h(r.name)}</td><td class="s">${r.n}</td><td class="s">${r.serious || ''}</td><td class="s"><b>${r.risk}</b></td><td><div class="bar"><i style="width:${100 * r.risk / Math.max(1, rows[0].risk)}%"></i></div></td>${extra ? `<td class="s">${r.score ?? '–'}</td>` : ''}</tr>`).join('')}</table>` : `<div class="note">${T('No events.')}</div>`}</div>`;
  $('#page').innerHTML = pageHeader(T('Reports'), `${h(d.site)} · ${d.from.slice(0, 10)} – ${d.to.slice(0, 10)}`, periodSelect() + `<a class="btn" href="/api/report.xlsx">${T('Excel report (30 days)')}</a>`)
    + `<div class="grid two" style="margin-bottom:14px"><div class="card"><div class="caption">${T('Safety score trend')}${d.weekly ? ' · ' + T('weekly') : ' · ' + T('daily')}</div>${lineChart(d.trend.map(e => e.score))}</div>
      <div class="card"><div class="caption">${T('Safety score')}</div><div class="number" style="font-size:54px">${d.score ?? '–'}<small>/100</small>${delta != null ? `<span class="delta ${delta < 0 ? 'bad' : ''}">${delta >= 0 ? '▲ +' : '▼ '}${T('{n} vs. the previous period', { n: delta })}</span>` : ''}</div>
        <p class="note">${T('Score = 100 × (1 − weighted violation time / monitored person-time). Violation time counts each event as duration × people × severity/5; false alarms are excluded.')}</p>
        <div class="info" style="grid-template-columns:repeat(2,1fr)"><div><span class="caption">${T('Events')}</span><b>${d.events}</b><span class="note">${T('{n} serious risk', { n: d.serious })}</span></div><div><span class="caption">${T('Open / resolved')}</span><b>${d.open} / ${d.resolved}</b></div>
          <div><span class="caption">${T('Monitored time')}</span><b>${fmtDuration(d.monitored_s)}</b><span class="note">${T('{n} person-hours', { n: decimal(d.person_hours) })}</span></div><div><span class="caption">${T('Avg. response')}</span><b>${d.avg_response_min == null ? '–' : T('{n} min', { n: decimal(d.avg_response_min) })}</b><span class="note">${T('take on or resolve')}</span></div></div></div></div>
    <div class="grid" style="grid-template-columns:repeat(2,minmax(0,1fr))">${table(T('By rule'), d.rules)}${table(T('By camera'), d.cameras, true)}${table(T('By shift'), d.shifts)}${table(T('By zone'), d.zones)}</div>`;
}

/* ------------------------------------------------------------ Rules */
async function pageRules() {
  const K = S.catalog;
  const status = { ready: `<span class="badge green">${T('Passed testing')}</span>`, limited: `<span class="badge yellow">${T('Limited')}</span>`, weak: `<span class="badge red">${T('Unreliable')}</span>`, experimental: `<span class="badge yellow">${T('Experimental')}</span>`, off: `<span class="badge grey">${T('Not available')}</span>` };
  const validation = k => k.validation ? `<div class="note">${k.validation.expected ? T('{caught}/{expected} caught', { caught: k.validation.caught, expected: k.validation.expected }) : ''}${k.validation.expected && k.validation.checked ? ' · ' : ''}${k.validation.checked ? T('{wrong}/{checked} false alarms', { wrong: k.validation.false_alarms, checked: k.validation.checked }) : ''}</div>` : '';
  const count = s => K.rules.filter(k => k.status === s).length;
  const missing = K.rules.filter(k => k.missing_model).length;
  $('#page').innerHTML = pageHeader(T('Rules'), T('{n} rules · {ready} passed testing · {limited} limited · {weak} unreliable · {off} not available', { n: K.rules.length, ready: count('ready'), limited: count('limited'), weak: count('weak'), off: count('off') }))
    + (K.rules.some(k => k.missing_model) ? `<div class="alert-strip"><b>${plural(missing, N_('{n} rule does not work: model file missing'), N_('{n} rules do not work: model file missing'))}</b> (${[...new Set(K.rules.filter(k => k.missing_model).map(k => h(k.missing_model)))].join(', ')}). ${T('Put the file in the <code>engine/models</code> folder, refresh this page and restart any running analysis or monitoring (a running scene fixes its rules when it starts). Until then these rules are not evaluated, even if they are enabled in the camera settings.')}</div>` : '')
    + `<div class="alert-strip">${T('Each rule was tested on scenes where the situation appears and where it does not (AI-generated clips and images; under each rule you can see in how many scenes it caught the situation and in how many it gave a false alarm). <b>Not tested with real camera footage</b>: watch your own camera for a few days and check the false alarms. Rules are enabled per camera in the camera settings.')}</div>`
    + `<div class="grid" style="grid-template-columns:repeat(2,minmax(0,1fr))">${Object.entries(K.categories).map(([id, name]) => `<div class="card"><div class="caption">${h(name)} · ${K.rules.filter(k => k.category === id).length}</div>
      ${K.rules.filter(k => k.category === id).map(k => `<div class="rule"><div style="flex:1"><div class="name">${h(k.name)} ${k.serious ? `<span class="badge serious">${T('Serious risk')}</span>` : ''}</div><div class="desc">${h(k.description)}</div></div><div style="text-align:right;white-space:nowrap">${k.missing_model ? `<span class="badge red">${T('Model file missing')}</span>` : status[k.status]}<div class="note">${k.missing_model ? h(k.missing_model) + ' · ' : ''}${T('severity {n}/5', { n: k.severity })}</div>${validation(k)}</div></div>`).join('')}</div>`).join('')}</div>`;
}

/* ------------------------------------------------------------ Settings */
function jobCard() {
  const i = S.status.job;
  if (!i) return `<div class="note">${T('No job has run yet.')}</div>`;
  const name = { running: T('running'), done: T('finished'), error: T('error'), stopped: T('stopped') }[i.status];
  return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><b>${h(i.title)}</b><span class="badge ${i.status === 'running' ? 'yellow' : i.status === 'error' ? 'red' : 'green'}">${name}</span><span class="note">${fmtDuration(i.elapsed_s)}</span><span style="flex:1"></span>
    ${i.status === 'running' ? `<button class="btn small danger" data-action="job-stop">${i.stopping ? T('Stopping…') : T('Stop')}</button>` : ''}</div><div class="record" id="record">${h(i.lines.join('\n'))}</div>`;
}
async function pageSettings() {
  const d = S.status, g = d.settings;
  const cam = k => `<tr><td><b>${h(k.name)}</b><div class="note">${{ rtsp: T('IP camera / recorder'), onvif: 'ONVIF', http: T('HTTP stream'), usb: T('USB camera'), file: T('video file') }[k.type] || k.type} · ${plural(k.zones, N_('{n} zone'), N_('{n} zones'))} · ${plural(k.rules, N_('{n} rule'), N_('{n} rules'))}${k.sample ? ' · ' + T('sample') : ''}${k.last_analysis ? ' · ' + T('last analysis {date}', { date: fmtDate(k.last_analysis) }) : ''}</div></td>
    <td style="white-space:nowrap;text-align:right">${k.needs_password ? `<input type="password" placeholder="${T('camera password')}" autocomplete="off" data-password="${h(k.code)}" style="width:130px;border:1px solid var(--line2);border-radius:8px;padding:5px 8px">` : ''}
      <button class="btn small" data-action="job" data-type="test" data-code="${h(k.code)}">${T('Test connection')}</button>
      ${k.type === 'file' ? `<button class="btn small main" data-action="job" data-type="video" data-code="${h(k.code)}">${T('Analyze video')}</button>` : `<button class="btn small main" data-action="job" data-type="monitor" data-code="${h(k.code)}">${T('Start monitoring')}</button>`}
      <a class="btn small" href="#/camera/${h(k.code)}">${T('Edit')}</a><button class="btn small danger" data-action="camera-delete" data-code="${h(k.code)}" data-name="${h(k.name)}">${T('Delete')}</button></td></tr>`;
  $('#page').innerHTML = pageHeader(T('Settings'), `İSG Kamera ${h(d.version)} · AugeLab Studio ${d.studio?.connected ? T('{version} ready (in the background)', { version: h(d.studio.version || '') }) : T('not ready')} · ${T('everything runs on this computer')}`, `<a class="btn" href="https://account.augelab.com/purchase" target="_blank" rel="noopener noreferrer">${T('View plans')}</a><button class="btn" data-action="open-folder">${T('Data folder')}</button><button class="btn danger" data-action="shutdown">${T('Quit the program')}</button>`)
    + modelWarning()
    + `<div class="grid two"><div class="card" id="out-cameras"><div style="display:flex;align-items:center;margin-bottom:8px"><div class="caption" style="flex:1">${T('Cameras')}</div>
        ${d.has_samples ? `<button class="btn small" data-action="sample">${T('Run sample analysis')}</button> ` : ''}<a class="btn small main" href="#/camera/new" style="margin-left:6px">${T('Add camera')}</a></div>
        ${d.cameras.length ? `<table>${d.cameras.map(cam).join('')}</table>` : `<div class="note">${T('No cameras yet.')}</div>`}
        <p class="note">${T('Analysis runs in AugeLab Studio: a scene is set up in Studio for each camera ({chain}) and events show up here as soon as they start. <b>Start monitoring</b> watches a live camera until it is stopped; one job runs at a time. The camera password is not saved; it is also removed from the Studio scene when monitoring ends.', { chain: `ISG Video ${T('or')} Camera IP → ISG Detection → ISG Pose → ISG Rules` })}</p></div>
      <div class="card"><div class="caption" style="margin-bottom:8px">${T('Job')}</div><div id="job-card">${jobCard()}</div></div></div>
    <div class="grid two" style="margin-top:14px"><div class="card form"><div class="caption">${T('Site and shifts')}</div>
        <label>${T('Site name')}</label><input id="site" value="${h(g.site)}">
        <label>${T('Shifts (name, start, end)')}</label><div id="shifts">${g.shifts.map(v => `<div class="row" style="margin-bottom:6px"><input value="${h(v.name)}"><input type="time" value="${v.start}"><input type="time" value="${v.end}"></div>`).join('')}</div>
        <p><button class="btn main" data-action="settings-save">${T('Save')}</button></p>
        <label for="language-select">${T('Language')}</label><select id="language-select" data-change="lang">${[['en', 'English'], ['tr', 'Türkçe']].map(([l, a]) => `<option value="${l}" ${I18N.lang === l ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      <div class="card"><div style="display:flex;align-items:center;margin-bottom:6px"><div class="caption" style="flex:1">${T('Outputs: PLC, messaging, notifications')}</div><button class="btn small main" data-action="output-dialog" data-i="-1">${T('Add output')}</button></div>
        ${g.outputs.length ? `<table>${g.outputs.map((b, i) => `<tr><td><b>${h(b.name)}</b> ${b.enabled === false ? `<span class="badge grey">${T('disabled')}</span>` : ''}<div class="note">${h(S.catalog.output_types[b.type] || b.type)} · ${h(outputSummary(b))}</div></td>
          <td style="white-space:nowrap;text-align:right"><button class="btn small" data-action="output-test" data-i="${i}">${T('Test')}</button> <button class="btn small" data-action="output-dialog" data-i="${i}">${T('Edit')}</button> <button class="btn small danger" data-action="output-delete" data-i="${i}">${T('Delete')}</button></td></tr>`).join('')}</table>` : `<div class="note">${T('No outputs yet. New events can be sent to a PLC (Modbus TCP, OPC UA), an MQTT broker, a webhook URL, a Slack / Teams channel or email.')}</div>`}
        <p class="note" style="margin-bottom:0">${T('Outputs fire as soon as an event starts, when the panel receives that event from Studio (about one or two seconds). They are meant for warning lights, sirens, logging and notifications; <b>they cannot replace a machine safety stop</b>. Outputs marked "Send with Studio block" go out from the Modbus Write, Siemens S7 Write, OPC UA Write or MQTT Publish block in the camera\'s Studio scene. No images are sent; passwords are not saved but read from an environment variable.')}</p></div></div>
    <div class="card" style="margin-top:14px"><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px"><div class="caption" style="flex:1">${T('API for external systems (read-only)')}</div>
        <label class="chip" style="cursor:pointer"><input type="checkbox" data-change="network-access" ${g.network_access ? 'checked' : ''}> ${T('Let systems on the network read with a key')}</label>
        ${g.api_key ? `<button class="btn small" data-action="regenerate-key">${T('Renew key')}</button>` : ''}</div>
      <table>${Object.entries(S.catalog.api).map(([u, a]) => `<tr><td style="font-family:var(--mono);font-size:12.5px;white-space:nowrap">GET ${h(u)}</td><td class="note">${h(a)}</td></tr>`).join('')}</table>
      <p class="note">${T('Readable from this computer without a key: {url}.', { url: `<code>http://127.0.0.1:${d.listening.port}/api/v2/events</code>` })} ${g.network_access ? (d.listening.address === '0.0.0.0'
        ? T('From the network: {urls} · every request needs the <code>X-API-Key</code> header. While network access is on, the program listens on all network interfaces; from the network only these addresses can be read (event clips and frames included), the interface and settings do not open. The connection is not encrypted (HTTP): turn it on only in a local network you trust.', { urls: d.listening.network.map(ip => `<code>http://${h(ip)}:${d.listening.port}/api/v2/…</code>`).join(', ') || T("this computer's network address") })
        : '<b>' + T('Network access saved; close and reopen the program for it to take effect.') + '</b>') : (d.listening.address === '0.0.0.0' ? '<b>' + T('Network access turned off:') + '</b> ' + T('requests from the network are now refused; the program keeps listening on the network interfaces until it is reopened.') : T('Network access off: the program listens only on this computer.'))}</p>
      ${g.network_access && g.api_key ? `<div class="form"><label>${T('API key (entered into systems such as Power BI, Grafana or SCADA; do not share it with others)')}</label><input readonly value="${h(g.api_key)}" onfocus="this.select()" style="font-family:var(--mono)"></div>` : ''}</div>`;
  const k = $('#record'); if (k) k.scrollTop = k.scrollHeight;
}
/* Output form fields: [key, label, default/placeholder, kind ('number' | 'select' | 'list'), select options]. */
const OUTPUT_COMMON_FIELDS = [['username', N_('User name (leave empty if not needed)'), ''], ['password_env', N_('Name of the environment variable that holds the password'), N_('e.g. ISG_PASSWORD')]];
const OUTPUT_FIELDS = {
  webhook: [['url', N_('URL (the event is POSTed as JSON)'), 'https://…']],
  slack: [['url', N_('Incoming webhook URL'), 'https://hooks.slack.com/…']],
  teams: [['url', N_('Incoming webhook URL'), 'https://…']],
  mqtt: [['host', N_('MQTT broker'), N_('e.g. 10.0.0.5')], ['port', N_('Port'), 1883, 'number'], ['topic', N_('Topic'), 'isg/event'], ['qos', N_('QoS (0 or 1)'), 0, 'number'], ...OUTPUT_COMMON_FIELDS],
  modbus: [['host', N_('PLC address'), N_('e.g. 10.0.0.20')], ['port', N_('Port'), 502, 'number'], ['unit_id', N_('Unit (slave) ID'), 1, 'number'],
    ['target', N_('Write target'), 'coil', 'select', [['coil', N_('Coil (function 5)')], ['register', N_('Holding register (function 6)')]]],
    ['address', N_('Address (starts at 0; 40001 → 0)'), 0, 'number'], ['value', N_('Value to write (for a coil, 1 = on)'), 1, 'number'], ['pulse_s', N_('Reset after how many seconds (0 = no reset)'), 5, 'number']],
  opcua: [['url', N_('Server URL'), 'opc.tcp://10.0.0.20:4840'], ['node_id', N_('Node'), 'ns=2;s=IsgAlarm'], ['value', N_('Value to write (when sent from the panel)'), 'true'], ['pulse_s', N_('Reset after how many seconds (0 = no reset)'), 5, 'number'],
    ['data_type', N_('Node data type (the Studio block writes Risk 0-25)'), 'Float', 'select', [['Float', 'Float'], ['Double', 'Double'], ['Int16', 'Int16'], ['Int32', 'Int32'], ['UInt16', 'UInt16'], ['UInt32', 'UInt32'], ['Boolean', 'Boolean']]], ...OUTPUT_COMMON_FIELDS],
  s7: [['host', N_('PLC address'), N_('e.g. 10.0.0.30')], ['port', N_('Port'), 102, 'number'], ['rack', N_('Rack'), 0, 'number'], ['slot', N_('Slot'), 1, 'number'],
    ['db', N_('DB number'), 1, 'number'], ['byte', N_('Byte address'), 0, 'number'], ['bit', N_('Bit (0-7)'), 0, 'number']],
  email: [['host', N_('SMTP server'), N_('smtp.example.com')], ['port', N_('Port'), 587, 'number'], ['sender', N_('Sender address'), ''], ['recipients', N_('Recipients (comma-separated)'), '', 'list'], ...OUTPUT_COMMON_FIELDS],
};
function outputSummary(b) {
  const target = { s7: `${b.host} · DB${b.db}.DBX${b.byte}.${b.bit}`, webhook: b.url, slack: b.url, teams: b.url, mqtt: `${b.host}:${b.port} · ${b.topic}`, modbus: `${b.host}:${b.port} · ${b.target === 'register' ? T('register') : T('coil')} ${b.address}${b.pulse_s ? ` · ${T('{n} s pulse', { n: b.pulse_s })}` : ''}`,
    opcua: `${b.url} · ${b.node_id}`, email: `${b.host} → ${(b.recipients || []).join(', ')}` }[b.type] || '';
  const filter = [b.min_score ? T('risk ≥ {n}', { n: b.min_score }) : '', b.serious_only ? T('serious risk only') : '', (b.categories || []).length ? b.categories.map(k => S.catalog.categories[k] || k).join(', ') : '',
    (b.cameras || []).length ? plural(b.cameras.length, N_('{n} camera'), N_('{n} cameras')) : ''].filter(Boolean).join(' · ');
  return (b.type === 's7' ? T('not supported') + ' · ' : '') + target + (filter ? ' · ' + filter : ' · ' + T('all events'));
}
function outputDialog(i, type) {
  const old = i >= 0 ? S.status.settings.outputs[i] : null;
  const b = old && (!type || type === old.type) ? old : { type: type || 'modbus', name: '', enabled: true, min_score: 9, serious_only: false, categories: [], cameras: [] };
  const field = ([key, label, def, kind, options]) => {
    const value = b[key] ?? (kind === 'number' || kind === 'select' ? def : '');
    if (kind === 'select') return `<div><label>${T(label)}</label><select data-out="${key}">${options.map(([v, a]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${T(a)}</option>`).join('')}</select></div>`;
    return `<div><label>${T(label)}</label><input data-out="${key}" data-kind="${kind || ''}" ${kind === 'number' ? 'type="number"' : ''} value="${h(Array.isArray(value) ? value.join(', ') : value)}" placeholder="${h(kind === 'number' ? '' : T(def))}" autocomplete="off"></div>`;
  };
  const checks = (key, options) => `<div style="display:flex;gap:6px;flex-wrap:wrap">${options.map(([v, a]) => `<label class="chip" style="cursor:pointer;margin:0"><input type="checkbox" data-out-list="${key}" value="${h(v)}" ${(b[key] || []).includes(v) ? 'checked' : ''}>${h(a)}</label>`).join('')}</div>`;
  // Webhook/MQTT outputs migrated from v0.1.0 keep sending the old payload shape until this box is cleared.
  const legacy = 'legacy_payload' in b && (b.type === 'webhook' || b.type === 'mqtt')
    ? `<label class="chip" style="cursor:pointer;margin:10px 0 0"><input type="checkbox" data-out-check="legacy_payload" ${b.legacy_payload ? 'checked' : ''}> ${T('Send the v0.1.0 payload (Turkish field names)')}</label>` : '';
  $('#overlay').innerHTML = `<div class="backdrop"><div class="dialog form" style="width:min(640px,100%)"><h2 style="margin:0 0 4px">${old ? T('Edit output') : T('Add output')}</h2>
    <div class="row"><div><label>${T('Type')}</label><select data-change="output-type" data-i="${i}">${Object.entries(S.catalog.output_types).map(([t, a]) => `<option value="${t}" ${b.type === t ? 'selected' : ''}>${h(a)}</option>`).join('')}</select></div>
      <div><label>${T('Name')}</label><input data-out="name" value="${h(b.name || '')}" placeholder="${T('e.g. Press line warning light')}"></div></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 8px">${OUTPUT_FIELDS[b.type].map(field).join('')}</div>${legacy}
    <div class="caption" style="margin-top:14px">${T('Which events to send')}</div>
    <div class="row"><div><label>${T('Minimum risk score (0-25)')}</label><input data-out="min_score" data-kind="number" type="number" min="0" max="25" value="${b.min_score ?? 0}"></div>
      <div><label>&nbsp;</label><label class="chip" style="cursor:pointer;margin:0"><input type="checkbox" data-out-check="serious_only" ${b.serious_only ? 'checked' : ''}> ${T('Only serious-risk events')}</label></div>
      <div><label>&nbsp;</label><label class="chip" style="cursor:pointer;margin:0"><input type="checkbox" data-out-check="enabled" ${b.enabled !== false ? 'checked' : ''}> ${T('Enabled')}</label></div></div>
    <label>${T('Categories (all if none is selected)')}</label>${checks('categories', Object.entries(S.catalog.categories))}
    <label>${T('Cameras (all if none is selected)')}</label>${checks('cameras', S.status.cameras.map(k => [k.code, k.name])) || `<span class="note">${T('No cameras.')}</span>`}
    ${b.type === 's7' ? `<p class="note">${T('The Siemens S7 output is not supported in this version (AugeLab Studio runs in the background with a ready-made scene); this connection is not used.')}</p>` : ''}
    ${b.type === 'modbus' || b.type === 'opcua' || b.type === 's7' ? `<p class="note">${T('Do not use it in place of a safety stop.')}</p>` : ''}
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-action="overlay-close">${T('Cancel')}</button><button class="btn" data-action="output-test" data-i="form">${T('Send test message')}</button>
      <button class="btn main" data-action="output-save" data-i="${i}">${T('Save')}</button></div></div></div>`;
}
function outputForm() {
  const b = { type: $('[data-change="output-type"]').value };
  document.querySelectorAll('[data-out]').forEach(e => { const kind = e.dataset.kind; b[e.dataset.out] = kind === 'number' ? (e.value === '' ? undefined : +e.value) : kind === 'list' ? e.value.split(',').map(x => x.trim()).filter(Boolean) : e.value.trim(); });
  document.querySelectorAll('[data-out-check]').forEach(e => { b[e.dataset.outCheck] = e.checked; });
  for (const key of ['categories', 'cameras']) b[key] = [...document.querySelectorAll(`[data-out-list="${key}"]`)].filter(e => e.checked).map(e => e.value);
  return b;
}

/* ------------------------------------------------------------ Camera editing */
const RULE_FIELDS = { max: N_('max people'), min: N_('min people'), threshold: N_('threshold'), duration_s: N_('duration (s)') };
function isRuleOn(k, config) {
  if (config.rules) return config.rules.some(x => x.id === k.id && !x.disabled);
  const types = new Set((config.zones || []).map(b => b.type));
  if (k.off_by_default) return false;
  if (k.zone_type && !types.has(k.zone_type)) return false;
  if (k.mechanism === 'ppe' && !k.zone_type && !types.has('ppe') && !config.ppe_everywhere) return false;
  return true;
}
async function pageCamera(code) {
  const K = S.catalog;
  let config = { name: '', code: '', source: { type: 'rtsp', brand: 'hikvision', port: 554, channel: 1, stream: 'sub', username: 'admin' }, zones: [], ppe_everywhere: false };
  const isNew = code === 'new';
  if (!isNew) config = await api('/api/camera/' + encodeURIComponent(code));
  const info = S.status.cameras.find(k => k.code === code);
  S.drawing = { config, points: [], frame: info?.setup_frame || info?.last_frame || null };
  const k = config.source || {};
  const ruleValue = (r, field) => (config.rules || []).find(x => x.id === r.id)?.[field] ?? r[field];
  const streamSelect = id => `<select id="${id}"><option value="sub" ${k.stream !== 'main' ? 'selected' : ''}>${T('Substream (recommended)')}</option><option value="main" ${k.stream === 'main' ? 'selected' : ''}>${T('Main stream')}</option></select>`;
  $('#page').innerHTML = pageHeader(isNew ? T('Add camera') : config.name || code, T('connection, zones and rules'), `<a class="btn" href="#/settings">← ${T('Settings')}</a><button class="btn main" data-action="camera-save">${T('Save')}</button>`)
    + `<div class="grid two"><div><div class="card" id="cam-zones"><div class="caption" style="margin-bottom:8px">${T('Zones')}</div><div id="drawing-area"></div>
        <div class="form"><div class="row"><div><label>${T('Type of the zone being drawn')}</label><select id="zone-type">${Object.entries(K.zone_types).map(([t, a]) => `<option value="${t}">${h(a)}</option>`).join('')}</select></div>
          <div><label>${T('Zone name')}</label><input id="zone-name" placeholder="${T('e.g. Press line')}"></div><div style="flex:.6"><label>${T('Direction (°)')}</label><input id="zone-direction" type="number" placeholder="${T('vehicle lane only')}" title="${T('0 = right, 90 = down, 180 = left, 270 = up')}"></div></div>
          <p style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn main small" data-action="zone-close">${T('Close zone')}</button><button class="btn small" data-action="zone-undo">${T('Delete last point')}</button>
            <label class="btn small" style="cursor:pointer;margin:0">${T('Choose image')}<input type="file" accept="image/*" id="frame-file" hidden></label></p>
          <p class="note">${T('Click the corners of the zone on the image in order, then <b>Close zone</b>. If there is no frame, first save the camera and click <b>Test connection</b> in Settings: the captured frame (faces masked) opens here.')}</p></div>
        <div id="zone-list"></div></div></div>
      <div><div class="card form" id="cam-connection"><div class="caption">${T('Connection')}</div>
        <div class="row"><div><label>${T('Camera name')}</label><input id="cam-name" value="${h(config.name || '')}" placeholder="${T('e.g. Warehouse · Door 2')}"></div><div><label>${T('Short code')}</label><input id="cam-code" value="${h(config.code || (isNew ? '' : code))}" ${isNew ? '' : 'disabled'} placeholder="depo2"></div></div>
        <label>${T('Connection type')}</label><select id="cam-type" data-change="camera-type">${[['rtsp', T('IP camera / recorder – NVR, DVR (RTSP, by brand)')], ['onvif', T('ONVIF camera / recorder (the address is read from the device)')], ['http', T('HTTP stream (MJPEG)')], ['usb', T('USB camera')], ['file', T('Video file')]].map(([t, a]) => `<option value="${t}" ${k.type === t ? 'selected' : ''}>${a}</option>`).join('')}</select>
        <div data-type="rtsp"><label>${T('Brand')}</label><select id="cam-brand">${Object.entries(K.brands).map(([m, a]) => `<option value="${m}" ${k.brand === m && !k.url ? 'selected' : ''}>${h(a)}</option>`).join('')}<option value="" ${k.url ? 'selected' : ''}>${T('Other (full URL)')}</option></select>
          <div class="row"><div><label>${T('IP address')}</label><input id="cam-ip" value="${h(k.ip || '')}"></div><div style="flex:.5"><label>${T('Port')}</label><input id="cam-port" value="${h(k.port ?? 554)}"></div></div>
          <div class="row"><div><label>${T('Channel')}</label><input id="cam-channel" value="${h(k.channel ?? 1)}"></div><div><label>${T('Stream')}</label>${streamSelect('cam-stream')}</div></div>
          <p class="note" style="margin:6px 0 0">${T("On a recorder (NVR / DVR) the IP address is the device's address; the <b>channel</b> is the camera's position on the device. Each channel is added as a separate camera.")}</p>
          <div class="row" style="align-items:flex-end"><div style="flex:.5"><label>${T('Number of channels on the recorder')}</label><input id="cam-channel-count" type="number" min="2" max="64" placeholder="${T('e.g. 8')}"></div>
            <div><button class="btn small" data-action="nvr-add-channels">${T('Add the channels as separate cameras')}</button></div></div></div>
        <div data-type="rtsp http"><label>${T('Full URL ({password} instead of the password; leave empty if a brand is selected)')}</label><input id="cam-url" value="${h(k.url || '')}" placeholder="rtsp://{user}:{password}@IP:554/...">
          <label>${T('User name')}</label><input id="cam-username" value="${h(k.username || 'admin')}" autocomplete="off"></div>
        <div data-type="onvif"><div class="row"><div><label>${T('Device IP address (camera or recorder)')}</label><input id="onvif-ip" value="${h(k.ip || '')}"></div><div style="flex:.5"><label>${T('ONVIF port')}</label><input id="onvif-port" value="${h(k.type === 'onvif' ? k.port ?? 80 : 80)}" title="${T('Usually 80, 8000, 8080 or 8899')}"></div>
            <div style="flex:none;align-self:flex-end"><button class="btn small" data-action="onvif-discover">${T('Search the network')}</button></div></div>
          <div class="row"><div><label>${T('User name')}</label><input id="onvif-username" value="${h(k.username || 'admin')}" autocomplete="off"></div>
            <div style="flex:.5"><label>${T('Channel')}</label><input id="onvif-channel" type="number" min="1" value="${h(k.channel ?? 1)}"></div>
            <div><label>${T('Stream')}</label>${streamSelect('onvif-stream')}</div></div>
          <label>${T('Profile')}</label><select id="onvif-profile"><option value="">${T('Automatic (single-channel camera only): the selected stream')}</option>${k.type === 'onvif' && k.profile ? `<option value="${h(k.profile)}" data-name="${h(k.profile_name || '')}" data-source="${h(k.video_source || '')}" selected>${h(k.profile_name || k.profile)}</option>` : ''}</select>
          <div class="row" style="align-items:flex-end;margin-top:8px"><div><label>${T('Password (only for listing; not saved)')}</label><input id="onvif-password" type="password" autocomplete="off"></div>
            <div style="flex:none"><button class="btn small" data-action="onvif-profiles">${T('List channels and profiles')}</button></div></div>
          <div id="onvif-result" style="margin-top:8px"></div>
          <p class="note">${T('With ONVIF the stream address is requested from the device; no brand needs to be selected. On a recorder each channel is added as a separate camera. On a recorder (several channels) you must choose the profile from the list: the camera always connects to the same physical channel through the tokens of the selected profile and video source; a multi-channel camera saved without a profile does not connect. Bulk adding saves the tokens automatically. ONVIF must be enabled on the device and allowed for this user.')}</p></div>
        <div data-type="usb"><label>${T('USB camera number')}</label><input id="cam-index" value="${h(k.index ?? 0)}"></div>
        <div data-type="file"><label>${T('Video file (full path or relative to the data folder)')}</label><input id="cam-path" value="${h(k.path || '')}" placeholder="videos/recording.mp4">
          <label>${T('Recording start time (optional)')}</label><input id="cam-start-time" type="datetime-local" value="${h((k.start_time || '').slice(0, 16))}"></div>
        <p class="note">${T('The password is not entered or saved here; when connecting, it is typed into the password box on the Settings page.')}</p></div>
      <div class="card form" id="cam-rules" style="margin-top:14px"><div class="caption">${T('Rules')}</div>
        <label style="display:flex;gap:8px;align-items:center;color:var(--text)"><input type="checkbox" id="cam-ppe-everywhere" ${config.ppe_everywhere ? 'checked' : ''}> ${T('PPE rules apply to the whole image (no zone needed)')}</label>
        <label>${T('Authorized clothing color (for the machine-zone rule; you can choose more than one)')}</label>
        <div style="display:flex;gap:6px;flex-wrap:wrap">${Object.entries(K.colors).map(([r, v]) => `<label class="chip" style="cursor:pointer;margin:0"><input type="checkbox" data-color="${r}" ${(config.authorized_colors || []).includes(r) ? 'checked' : ''}><i style="display:inline-block;width:12px;height:12px;border-radius:3px;background:${v.hex};border:1px solid #0002"></i>${h(v.name)}</label>`).join('')}</div>
        ${Object.entries(K.categories).map(([cat, name]) => { const rs = K.rules.filter(r => r.category === cat && r.status !== 'off'); return rs.length ? `<div class="caption" style="margin-top:14px">${h(name)}</div>` + rs.map(r => `<div class="rule"><input type="checkbox" data-rule="${r.id}" ${isRuleOn(r, config) ? 'checked' : ''} ${r.missing_model ? `disabled title="${T('Model file missing: this rule is not evaluated')}"` : ''} style="margin-top:4px">
          <div style="flex:1"><div class="name">${h(r.name)} ${r.missing_model ? `<span class="badge red">${T('Model file missing')}</span> ` : ''}${r.status === 'weak' ? `<span class="badge red">${T('Unreliable')}</span>` : r.status === 'limited' ? `<span class="badge yellow">${T('Limited')}</span>` : ''}</div><div class="desc">${r.zone_type ? T('Zone: {type}', { type: h(K.zone_types[r.zone_type]) }) + ' · ' : ''}${h(r.description)}</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:4px">${Object.entries(RULE_FIELDS).filter(([a]) => r[a] != null).map(([a, e]) => `<span class="note">${T(e)} <input type="number" step="any" data-rule-field="${r.id}:${a}" value="${ruleValue(r, a)}" style="width:64px;padding:2px 6px;border-radius:6px"></span>`).join('')}</div></div></div>`).join('') : ''; }).join('')}</div></div></div>`;
  showCameraType(); drawZones();
}
function showCameraType() {
  const t = $('#cam-type').value;
  document.querySelectorAll('[data-type]').forEach(e => { if (e.tagName === 'DIV') e.style.display = e.dataset.type.split(' ').includes(t) ? '' : 'none'; });
}
function drawZones() {
  const C = S.drawing, K = S.catalog;
  const poly = (b, i) => `<polygon points="${b.poly.map(p => `${p[0] * 1000},${p[1] * 1000}`).join(' ')}" fill="rgba(255,209,102,.16)" stroke="#ffd166" stroke-width="2" vector-effect="non-scaling-stroke"><title>${h(b.name)}</title></polygon>`;
  const open = C.points.length ? `<polyline points="${C.points.map(p => `${p[0] * 1000},${p[1] * 1000}`).join(' ')}" fill="none" stroke="#ef4b1c" stroke-width="2.5" vector-effect="non-scaling-stroke"/>`
    + C.points.map(p => `<line x1="${p[0] * 1000}" y1="${p[1] * 1000}" x2="${p[0] * 1000}" y2="${p[1] * 1000}" stroke="#ef4b1c" stroke-width="9" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`).join('') : '';
  $('#drawing-area').innerHTML = `<div class="stage drawing" id="drawing">${C.frame ? `<img src="${h(C.frame)}" alt="" style="filter:none">` : `<div class="placeholder">${T('No frame. Save the camera and click <b>Test connection</b>, or choose an image below.<br>You can also draw a zone without a frame by clicking its corners.')}</div>`}
    <svg viewBox="0 0 1000 1000" preserveAspectRatio="none">${C.config.zones.map(poly).join('')}${open}</svg></div>`;
  $('#zone-list').innerHTML = C.config.zones.length ? `<table style="margin-top:8px">${C.config.zones.map((b, i) => `<tr><td><b>${h(b.name)}</b> <span class="note">${h(K.zone_types[b.type] || b.type)}${b.direction_deg != null ? ' · ' + T('direction {n}°', { n: b.direction_deg }) : ''}</span></td><td class="s"><button class="btn small danger" data-action="zone-delete" data-i="${i}">${T('Delete')}</button></td></tr>`).join('')}</table>` : '';
}
function cameraForm() {
  const C = S.drawing, v = id => $('#' + id)?.value.trim() ?? '';
  const type = v('cam-type');
  const source = { type };
  if (type === 'rtsp') {
    if (v('cam-url')) Object.assign(source, { url: v('cam-url') });
    else Object.assign(source, { brand: v('cam-brand') || 'hikvision', ip: v('cam-ip'), port: +v('cam-port') || 554, channel: +v('cam-channel') || 1, stream: v('cam-stream') });
    source.username = v('cam-username') || 'admin';
  } else if (type === 'onvif') {
    Object.assign(source, { ip: v('onvif-ip'), port: +v('onvif-port') || 80, username: v('onvif-username') || 'admin', channel: +v('onvif-channel') || 1, stream: v('onvif-stream') });
    const selected = $('#onvif-profile').selectedOptions[0];
    if (selected?.value) Object.assign(source, { profile: selected.value, profile_name: selected.dataset.name || selected.textContent }, selected.dataset.source ? { video_source: selected.dataset.source } : {});
  } else if (type === 'http') Object.assign(source, { url: v('cam-url'), username: v('cam-username') || 'admin' });
  else if (type === 'usb') source.index = +v('cam-index') || 0;
  else { source.path = v('cam-path'); if (v('cam-start-time')) source.start_time = v('cam-start-time') + ':00'; }
  const rules = [];
  document.querySelectorAll('[data-rule]').forEach(e => {
    if (!e.checked) return;
    const r = { id: e.dataset.rule };
    document.querySelectorAll(`[data-rule-field^="${r.id}:"]`).forEach(a => { const x = parseFloat(a.value); if (!isNaN(x)) r[a.dataset.ruleField.split(':')[1]] = x; });
    rules.push(r);
  });
  const colors = [...document.querySelectorAll('[data-color]')].filter(e => e.checked).map(e => e.dataset.color);
  const config = { ...C.config, name: v('cam-name') || v('cam-code'), code: v('cam-code'), source, rules, ppe_everywhere: $('#cam-ppe-everywhere').checked };
  if (colors.length) config.authorized_colors = colors; else delete config.authorized_colors;
  return config;
}

/* ------------------------------------------------------------ events (click / change) */
const ACTIONS = {
  'studio-install': async e => {
    const type = e.dataset.type;
    if (type === 'install' && !confirm(T('Download and run the official AugeLab Studio installer?'))) return;
    await api('/api/studio/' + type, {});
    await fetchStatus();
    await render();
  },
  'wizard-open': () => openWizard(),
  'wizard-close': () => closeWizard(),
  'lang': e => { if (e.dataset.lang !== I18N.lang) return setLanguage(e.dataset.lang); },
  'account-open': () => {
    const url = S.status?.account_url || 'https://account.augelab.com/';
    if (!window.open(url, 'augelab-account', 'width=560,height=820')) window.open(url, '_blank');
  },
  'model-activate': async () => {
    const code = ($('#activation-code')?.value || '').trim();
    if (!code) { toast(T('Enter the activation code.')); return; }
    await api('/api/model/activate', { code });
    await fetchStatus();
    toast(T('Models activated.'));
    await render();
  },
  'model-device-request': async () => {
    const request = await api('/api/model/device-request', {});
    const url = URL.createObjectURL(new Blob([JSON.stringify(request)], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'model-cihaz-istegi.json';  // v0.1.0 file name kept: licensing instructions refer to model-cihaz-istegi*.json
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  'model-license': () => {
    const pick = document.createElement('input');
    pick.type = 'file';
    pick.accept = '.lic';
    pick.addEventListener('change', async () => {
      const file = pick.files[0];
      if (!file) return;
      if (file.size > 16384) { toast(T('Choose a valid model.lic file.')); return; }
      try {
        await api('/api/model/license', { file: await file.text() });
        await fetchStatus();
        toast(T('Model license verified.'));
        await render();
      } catch (e) { toast(e.message); }
    }, { once: true });
    pick.click();
  },
  'overlay-close': () => { $('#overlay').innerHTML = ''; const k = route(); if (k.q.id) { delete k.q.id; history.replaceState(null, '', '#/' + k.path + (Object.keys(k.q).length ? '?' + new URLSearchParams(k.q) : '')); } },
  'event-open': e => { const k = route(); if (k.path === 'alerts') { go('alerts', { ...k.q, id: e.dataset.id }); } else eventDrawer(S.events[e.dataset.id]); },
  'event-goto': e => go('alerts', { id: e.dataset.id, days: 90 }),
  'camera-open': e => go('analytics', { camera: e.dataset.code }),
  'event-status': async e => {
    await api('/api/event/status', { id: +e.dataset.id, status: e.dataset.status, assignee: $('#event-assignee')?.value || null, note: $('#event-note')?.value ?? null });
    toast(T('Event: {status}', { status: T(STATUS_NAMES[e.dataset.status]).toLowerCase() })); $('#overlay').innerHTML = ''; await fetchStatus(); render();
  },
  'task-new': e => taskDialog(null, e.dataset.event),
  'task-edit': e => taskDialog(S.tasks[e.dataset.id]),
  'task-save': async e => {
    const g = { id: e.dataset.id ? +e.dataset.id : undefined, title: $('#task-title').value, description: $('#task-description').value, owner: $('#task-owner').value, due_date: $('#task-due').value,
      camera: $('#task-camera').value, rule: $('#task-rule').value, zone: e.dataset.zone, status: e.dataset.status, events: e.dataset.event ? [+e.dataset.event] : [] };
    await api('/api/task', g); $('#overlay').innerHTML = ''; toast(T('Task saved.')); await fetchStatus();
    if (route().path === 'actions') render(); else go('actions');
  },
  'task-status': async e => { const g = S.tasks[e.dataset.id]; await api('/api/task', { ...g, events: [], status: e.dataset.status }); render(); },
  'task-delete': async e => { if (confirm(T('Delete the task? Linked events are not deleted.'))) { await api('/api/task/delete', { id: +e.dataset.id }); render(); } },
  'analytics-select': e => { S.analytics[e.dataset.field] = e.dataset.value; render(); },
  'sample': async () => { await api('/api/job/sample', {}); await fetchStatus(); go('settings'); },
  'job': async e => {
    const password = document.querySelector(`[data-password="${e.dataset.code}"]`);
    await api('/api/job/' + e.dataset.type, { code: e.dataset.code, password: password ? password.value : undefined, minutes: +($('#minutes')?.value || 5) });
    await fetchStatus(); render();
  },
  'job-stop': async () => { await api('/api/job/stop', {}); await fetchStatus(); render(); },
  'camera-delete': async e => { if (confirm(T('Delete camera "{name}" with all its events and clips? This cannot be undone.', { name: e.dataset.name }))) { await api('/api/camera/delete', { code: e.dataset.code }); await fetchStatus(); render(); } },
  'open-folder': () => api('/api/open-data-folder', {}),
  'shutdown': async () => { if (confirm(T('Close the program? Monitoring stops if it is running.'))) { await api('/api/shutdown', {}); document.body.innerHTML = `<div style="padding:40px;font:16px Segoe UI">${T('İSG Kamera has been closed. You can close this tab.')}</div>`; } },
  'settings-save': async () => {
    const shifts = [...document.querySelectorAll('#shifts .row')].map(s => { const [a, b, c] = s.querySelectorAll('input'); return { name: a.value, start: b.value, end: c.value }; });
    await api('/api/settings', { site: $('#site').value, shifts }); toast(T('Saved.')); await fetchStatus();
  },
  'output-dialog': e => outputDialog(+e.dataset.i),
  'output-save': async e => {
    const list = [...S.status.settings.outputs], i = +e.dataset.i, b = outputForm();
    // fields the form does not show (headers, tls, legacy_payload...) are kept when the type stays the same
    if (i >= 0) list[i] = list[i] && list[i].type === b.type ? { ...list[i], ...b } : b; else list.push(b);
    await api('/api/settings', { outputs: list }); $('#overlay').innerHTML = ''; toast(T('Output saved.')); await fetchStatus(); render();
  },
  'output-delete': async e => {
    const list = [...S.status.settings.outputs];
    if (!confirm(T('Delete output "{name}"?', { name: list[+e.dataset.i].name }))) return;
    list.splice(+e.dataset.i, 1); await api('/api/settings', { outputs: list }); await fetchStatus(); render();
  },
  'output-test': async e => {
    const b = e.dataset.i === 'form' ? outputForm() : S.status.settings.outputs[+e.dataset.i];
    toast(T('Sending test message…'));
    const r = await api('/api/output/test', { output: b });
    toast(r.ok ? `${b.name || T('Output')}: ${r.message}` : T('Could not send: {message}', { message: r.message }));
  },
  'regenerate-key': async () => { if (confirm(T('Generate a new key? Systems using the old key will no longer be able to read.'))) { await api('/api/settings', { regenerate_key: true }); await fetchStatus(); render(); } },
  'zone-close': () => {
    const C = S.drawing;
    if (C.points.length < 3) return toast(T('A zone needs at least three corners.'));
    const type = $('#zone-type').value, same = C.config.zones.filter(b => b.type === type).length;
    const b = { name: $('#zone-name').value.trim() || `${S.catalog.zone_types[type].split(' (')[0]} ${same + 1}`, type, poly: C.points.map(p => [+p[0].toFixed(4), +p[1].toFixed(4)]) };
    if ($('#zone-direction').value !== '') b.direction_deg = +$('#zone-direction').value;
    C.config.zones.push(b); C.points = []; $('#zone-name').value = ''; drawZones();
  },
  'zone-undo': () => { S.drawing.points.pop(); drawZones(); },
  'zone-delete': e => { S.drawing.config.zones.splice(+e.dataset.i, 1); drawZones(); },
  'camera-save': async () => {
    const config = cameraForm();
    if (!config.code) return toast(T('Enter a short code (e.g. depo2).'));
    const r = await api('/api/camera', { camera: config });
    toast([T('Camera saved.'), ...(r.notes || [])].join(' ')); await fetchStatus(); go('settings');
  },
};
/* Recorder channels: the connection in the form is saved as a separate camera for each channel (zones and rules are added later). */
async function addChannels(channels, profiles) {
  const base = cameraForm();
  if (!base.code) return toast(T('Enter the short code first (e.g. nvr1): the channels are added as nvr1-ch1, nvr1-ch2…'));
  if (!base.source.ip) return toast(T("Enter the recorder's IP address first."));
  if (!channels.length || channels.length > 64) return toast(T('The number of channels must be between 2 and 64.'));
  const codes = channels.map(n => `${base.code}-ch${n}`);
  const existing = codes.filter(k => (S.status.cameras || []).some(c => c.code === k));
  if (existing.length) return toast(T('Cameras with these codes already exist: {codes}. They are not overwritten; enter a different short code.', { codes: existing.join(', ') }));
  if (!confirm(T('{n} channels will be added as separate cameras: {first} … {last}. The rules checked in the form are copied to every channel; zones start empty and are drawn on each camera separately. Continue?', { n: channels.length, first: codes[0], last: codes[codes.length - 1] }))) return;
  try {  // the server builds the channel settings (with profile and source tokens for ONVIF) and creates all of them in one step: either all are added or none
    const r = await api('/api/camera/channels', { base, channels, ...(profiles ? { profiles } : {}) });
    toast(plural(r.codes.length, N_('{n} channel added.'), N_('{n} channels added.')));
  } catch (x) { await fetchStatus(); return toast(x.message); }
  await fetchStatus(); go('settings');
}
Object.assign(ACTIONS, {
  'nvr-add-channels': () => { const n = +$('#cam-channel-count').value; return addChannels(n >= 2 && n <= 64 ? Array.from({ length: n }, (_, i) => i + 1) : []); },
  'onvif-discover': async () => {
    $('#onvif-result').innerHTML = `<div class="note">${T('Searching the network for ONVIF devices (about 3 seconds)…')}</div>`;
    const d = await api('/api/onvif/discover', {}).catch(x => ({ error: x.message }));
    $('#onvif-result').innerHTML = d.error ? `<div class="note">${h(d.error)}</div>` : d.devices.length
      ? `<table>${d.devices.map(c => `<tr><td><b>${h(c.ip)}</b>:${c.port}<div class="note">${h([c.name, c.model].filter(Boolean).join(' · ') || T('ONVIF device'))}</div></td><td style="text-align:right"><button class="btn small" data-action="onvif-select-device" data-ip="${h(c.ip)}" data-port="${c.port}">${T('Select')}</button></td></tr>`).join('')}</table>`
      : `<div class="note">${T('No ONVIF device answered on this network. If the device is on another network or has discovery turned off, enter its IP address manually.')}</div>`;
  },
  'onvif-select-device': e => { $('#onvif-ip').value = e.dataset.ip; $('#onvif-port').value = e.dataset.port; $('#onvif-result').innerHTML = `<div class="note">${T('Device selected. Enter the user name and password and list the channels.')}</div>`; },
  'onvif-profiles': async () => {
    $('#onvif-result').innerHTML = `<div class="note">${T('Connecting to the device…')}</div>`;
    const d = await api('/api/onvif/profiles', { ip: $('#onvif-ip').value.trim(), port: +$('#onvif-port').value || 80, username: $('#onvif-username').value.trim() || 'admin', password: $('#onvif-password').value }).catch(x => ({ error: x.message }));
    if (d.error) { $('#onvif-result').innerHTML = `<div class="alert-strip" style="margin:0">${h(d.error)}</div>`; return; }
    S.onvifProfiles = d.profiles;
    const channels = [...new Set(d.profiles.map(p => p.channel))];
    const device = [d.device?.manufacturer, d.device?.model].filter(Boolean).join(' ');
    $('#onvif-result').innerHTML = `<div class="note" style="margin-bottom:4px">${h(device || T('Device'))} · ${plural(channels.length, N_('{n} channel'), N_('{n} channels'))} · ${plural(d.profiles.length, N_('{n} profile'), N_('{n} profiles'))}</div>
      <table>${d.profiles.map(p => `<tr><td>${T('Channel {n}', { n: p.channel })}</td><td><b>${h(p.name || p.token)}</b><div class="note">${h(p.encoding)} ${p.width}×${p.height}</div></td>
        <td style="text-align:right"><button class="btn small" data-action="onvif-select-profile" data-token="${h(p.token)}" data-channel="${p.channel}" data-source="${h(p.source)}" data-name="${h(`${T('Channel {n}', { n: p.channel })} · ${p.name || p.token} · ${p.encoding} ${p.width}×${p.height}`)}">${T('Select')}</button></td></tr>`).join('')}</table>
      ${channels.length > 1 ? `<p style="margin:8px 0 0"><button class="btn small" data-action="onvif-add-channels" data-channels="${channels.join(',')}">${T('Add {n} channels as separate cameras', { n: channels.length })}</button></p>` : ''}`;
  },
  'onvif-select-profile': e => {
    const s = $('#onvif-profile');
    [...s.options].filter(o => o.value).forEach(o => o.remove());
    const o = new Option(e.dataset.name, e.dataset.token, true, true);
    o.dataset.name = e.dataset.name;
    o.dataset.source = e.dataset.source || '';
    s.add(o);
    $('#onvif-channel').value = e.dataset.channel;
    toast(T('Profile selected. When you click Save, the camera connects with this profile.'));
  },
  'onvif-add-channels': e => addChannels(e.dataset.channels.split(',').map(Number), S.onvifProfiles || []),
});
document.addEventListener('click', async ev => {
  const e = ev.target.closest('[data-action]');
  if (e) { ev.preventDefault(); try { await ACTIONS[e.dataset.action](e); } catch (x) { toast(x.message); } return; }
  const c = ev.target.closest('#drawing');
  if (c && S.drawing) {
    const r = c.getBoundingClientRect();
    S.drawing.points.push([Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))]);
    drawZones();
  }
});
document.addEventListener('change', ev => {
  const e = ev.target, d = e.dataset.change;
  if (d === 'period') { S.days = +e.value; render(); }
  else if (d === 'filter') { const k = route(); if (e.value) k.q[e.dataset.field] = e.value; else delete k.q[e.dataset.field]; delete k.q.id; go('alerts', k.q); }
  else if (d === 'analytics-camera') { S.analytics.camera = e.value; history.replaceState(null, '', '#/analytics'); render(); }
  else if (d === 'analytics-category') { S.analytics.category = e.value; render(); }
  else if (d === 'analytics-compare') { S.analytics.compare = e.checked; render(); }
  else if (d === 'camera-type') showCameraType();
  else if (d === 'output-type') outputDialog(+e.dataset.i, e.value);
  else if (d === 'network-access') api('/api/settings', { network_access: e.checked }).then(fetchStatus).then(render).catch(x => toast(x.message));
  else if (d === 'lang') setLanguage(e.value).catch(x => toast(x.message));
  else if (e.id === 'frame-file' && e.files[0]) { const o = new FileReader(); o.onload = () => { S.drawing.frame = o.result; drawZones(); }; o.readAsDataURL(e.files[0]); }
});
document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && $('#overlay').innerHTML) ACTIONS['overlay-close'](); });

/* ------------------------------------------------------------ routing */
let licenseWarningDismissed = false;
function licenseWarning(studio) {
  const limit = studio?.community_fps_limit;
  const warning = $('#license-warning');
  if (limit !== true) {
    warning.hidden = true;
    if (limit === false) licenseWarningDismissed = false;
    return;
  }
  warning.hidden = licenseWarningDismissed;
}
$('#license-warning-close').addEventListener('click', () => {
  licenseWarningDismissed = true;
  licenseWarning(S.status?.studio);
});
let statusRequest = null;
function fetchStatus() {
  // One status request at a time: if one is in flight, wait for it (so polls do not pile up).
  if (!statusRequest) statusRequest = refreshStatus().finally(() => { statusRequest = null; });
  return statusRequest;
}
async function refreshStatus() {
  S.status = await api('/api/status');
  drawRail();
  licenseWarning(S.status.studio);
  studioAutoTest(S.status);
  document.querySelectorAll('[data-studio-setup]').forEach(e => { e.outerHTML = studioWarning(); });
  watchWizard();
}
async function render() {
  const { path, q } = route();
  drawRail();
  if (!q.id) $('#overlay').innerHTML = '';
  try {
    if (path === '') await pageControl();
    else if (path === 'alerts') await pageAlerts(q);
    else if (path === 'analytics') await pageAnalytics(q);
    else if (path === 'actions') await pageActions();
    else if (path === 'reports') await pageReports();
    else if (path === 'rules') await pageRules();
    else if (path === 'settings') await pageSettings();
    else if (path.startsWith('camera/')) await pageCamera(decodeURIComponent(path.slice(7)));
    else go('');
  } catch (x) { $('#page').innerHTML = `<div class="empty-state"><h3>${T('Could not open the page')}</h3><p>${h(x.message)}</p></div>`; }
}
async function heartbeat() {
  try {
    const before = S.status?.job;
    await fetchStatus();
    const now = S.status.job, path = route().path;
    if (path === 'settings' && now) { const k = $('#job-card'); if (k) { k.innerHTML = jobCard(); const y = $('#record'); if (y) y.scrollTop = y.scrollHeight; } }
    if (before?.status === 'running' && now?.status !== 'running' && !path.startsWith('camera/')) {
      toast(`${now.title}: ${{ done: T('finished'), error: T('failed'), stopped: T('stopped') }[now.status] || now.status}.`);
      if (!document.querySelector('.drawer, .backdrop')) render();
    }
  } catch (x) { /* the program may have closed */ }
}
window.addEventListener('hashchange', render);
(async () => {
  try {
    [S.status, S.catalog] = await Promise.all([api('/api/status'), api('/api/catalog')]);
    licenseWarning(S.status.studio);
    await render();
    setInterval(heartbeat, 2500);
  } catch (x) { $('#page').innerHTML = `<div class="empty-state"><h3>${T('The program is not responding')}</h3><p>${h(x.message)}</p></div>`; }
})();
