'use strict';
/* Guided tour: starts by itself on first launch and can be reopened with the "Tour" button in the left rail.
   Each step goes to a screen, highlights the relevant element and explains what it does. While the tour is open the page
   cannot be clicked: no setting changes, no analysis starts. Uses S, ACTIONS, route, go and h from app.js. */

const TOUR_STORAGE_KEY = 'isg-tour-seen';
const LEGACY_TOUR_STORAGE_KEY = 'isg-rehber-goruldu';  // v0.1.0 key, read as fallback
const TOUR = { open: false, i: 0, target: null, scrolled: false, timer: null, prevFocus: null };
const TOUR_BACKDROP = ['#rail', '#page', '#overlay'];  // sections made inert while the tour is open (no clicks, no focus)

/* The tour shows the settings page of the first camera if there is one (so zones and rules are filled in); otherwise the empty "Add camera" form. */
const tourRuleRow = id => document.querySelector(`#cam-rules input[data-rule="${id}"]`)?.closest('.rule');
const tourCamera = () => 'camera/' + ((S.status?.cameras || [])[0]?.code || 'new');

/* path: screen to go to (current screen if omitted) · target: element to highlight: a selector or a function returning the element
   (tried in order; if none exists the card stays in the middle). title/text are N_-marked and translated when shown. */
const TOUR_STEPS = [
  { title: N_('Welcome to İSG Kamera'),
    text: N_('İSG Kamera looks for workplace safety hazards in the footage of your security cameras: a missing hard hat or vest, entry into a restricted zone, vehicle–pedestrian proximity and more. This short tour shows the screens and how a camera is added. No setting changes during the tour.') },
  { target: ['#rail'], title: N_('Moving between screens'),
    text: N_('Use the rail on the left to switch screens. The number on Alerts shows the open alerts waiting to be reviewed.') },
  { path: '', target: ['#page .grid.four', '#page .empty-state', '#page .top'], title: N_('Control Room'),
    text: N_('A summary of the day: safety score, personal protective equipment (PPE) compliance, monitored time and serious-risk events. You can change the period at the top right.') },
  { path: '', target: ['#page .cameras', '#page .empty-state', '#page .top'], title: N_('Cameras'),
    text: N_("Each tile is a camera's most recently analyzed frame; the faces of detected people are masked. Click a tile to open that camera's heat map.") },
  { path: 'alerts', target: ['#page .alert', '#page .list', '#page .top'], title: N_('Alerts'),
    text: N_('Every event found is listed here. Click an alert to open its clip, risk score and location; you can take the alert on, or mark it as resolved or as a false alarm.') },
  { path: 'analytics', target: ['#page .stage', '#page .top'], title: N_('Visual Analytics'),
    text: N_('A heat map and paths over the camera image: where people and vehicles spend the most time and where events cluster. You can compare it side by side with the previous period.') },
  { path: 'actions', target: ['#page .task', '#page .top'], title: N_('Actions'),
    text: N_('From an alert you open a task with an owner and a due date. If the same rule is seen again after the task is closed, the task is marked "repeated".') },
  { path: 'reports', target: ['#page .card', '#page .top'], title: N_('Reports'),
    text: N_('The safety score trend and how events break down by rule, camera, shift and zone. The Excel report is downloaded here.') },
  { path: 'rules', target: ['#page .rule', '#page .top'], title: N_('Rules'),
    text: N_('All the rules the program knows. The badge on the right shows how the rule did in testing: "Passed testing" or "Limited". This screen is only a list; a rule is enabled in the settings of each camera.') },
  { path: 'settings', target: ['#page .top h1', '#page .top'], title: N_('Settings and AugeLab Studio'),
    text: N_('Image processing runs in AugeLab Studio. It should say "connected" here; if it does not, open Studio and start the connection with AI Tools → AI Chat Window → Settings → MCP configuration → Start.') },
  { path: 'settings', target: ['button[data-action="sample"]', '#out-cameras'], title: N_('Try the sample first'),
    text: N_('"Run sample analysis" analyzes the four sample clips that come with the program in Studio. It takes a few minutes; the events and their clips then appear in Alerts.') },
  { path: 'settings', target: ['a[href="#/camera/new"]', '#out-cameras'], title: N_('Adding a camera'),
    text: N_('For your own camera or video recording, click "Add camera". The page that opens has three parts to fill in: connection, zones and rules. The next steps walk through this page, above all how rules are added.') },
  { path: tourCamera, target: ['#cam-connection'], title: N_('1 · Connection'),
    text: N_('Give the camera a name and a short code, then choose the connection type: IP camera or recorder (RTSP by brand, or ONVIF), HTTP stream or video file. On a recorder each channel is added as a separate camera. The camera password is not entered or saved here.') },
  { path: tourCamera, target: ['#drawing-area', '#cam-zones'], title: N_('2 · Zones'),
    text: N_('Click the corners of the zone on the image in order, choose its type (restricted zone, pedestrian walkway, vehicle lane, PPE-required area…) and click "Close zone". If there is no image, save the camera and click "Test connection" in Settings: the captured frame opens here.') },
  { path: tourCamera, target: ['#cam-rules'], title: N_('3 · Rules'),
    text: N_('Rules are chosen separately for each camera. The list is grouped: personal protection, area control, behavior, vehicles, housekeeping and ergonomics. The next steps show how a rule is enabled and tuned.') },
  { path: tourCamera, target: [() => tourRuleRow('hard_hat'), '#cam-rules .rule'], title: N_('Turning a rule on and off'),
    text: N_('Each row is a rule. Check the box on the left and the rule runs on this camera; uncheck it and it is off. The description under its name says what the rule looks for and where it can be wrong.') },
  { path: tourCamera, target: [() => document.querySelector('#cam-rules [data-rule-field]')?.closest('.rule'), '#cam-rules .rule'], title: N_('Duration and threshold'),
    text: N_('The boxes under the description are the rule\'s values. "duration (s)": an event starts once the situation lasts this long; shortening it adds false alarms, lengthening it misses short situations. Some rules also have a "threshold" (e.g. proximity) or "max / min people".') },
  { path: tourCamera, target: [() => tourRuleRow('restricted_zone'), () => [...document.querySelectorAll('#cam-rules .rule')].find(e => e.textContent.includes(T('Zone: {type}', { type: '' }))), '#cam-rules'], title: N_('Rules that need a zone'),
    text: N_('Rules whose description starts with "Zone: …" only work in a zone of that type. For example, for "Entry into restricted zone" first draw a zone of type "Restricted zone" on the image on the left. Without a zone of that type the rule produces no events, even if it is checked.') },
  { path: tourCamera, target: [() => document.querySelector('#cam-ppe-everywhere')?.closest('label'), '#cam-rules'], title: N_('Where do the PPE rules apply?'),
    text: N_('Personal protection rules such as hard hat, vest and gloves are checked in zones drawn with the type "PPE-required area". If this box is checked, no zone is needed: the whole image is checked.') },
  { path: tourCamera, target: [() => document.querySelector('#cam-rules [data-color]')?.closest('div'), '#cam-rules'], title: N_('Authorized clothing color'),
    text: N_('The "Unauthorized person in machine zone" rule looks at the clothing color of whoever enters the zone. Choose the color worn by authorized staff here (e.g. a fluorescent yellow vest); an event starts when someone not wearing that color enters the zone.') },
  { path: tourCamera, target: [() => [...document.querySelectorAll('#cam-rules .rule')].find(e => e.querySelector('.badge.yellow, .badge.red')), '#cam-rules'], title: N_('Badges'),
    text: N_('A rule with the "Limited" badge works, but missed some situations in testing; read its description before enabling it. A rule with the "Model file missing" badge is not evaluated. Rules without a badge passed testing.') },
  { path: tourCamera, target: ['[data-action="camera-save"]'], title: N_('Save'),
    text: N_('Click Save and the camera is added to the list in Settings. You can reopen the same page later with "Edit".') },
  { path: 'settings', target: ['#out-cameras tr', '#out-cameras'], title: N_('Starting the analysis'),
    text: N_('"Test connection" grabs a frame from the camera. For a video file, "Analyze video" processes the recording from start to end; for a live camera, "Start monitoring" watches until it is stopped. One job runs at a time.') },
  { path: 'settings', target: ['#job-card'], title: N_('Job status'),
    text: N_('The steps of the running job, and any error message, are shown here. When the job finishes, the events appear in Alerts and in the Control Room.') },
  { path: '', title: N_('You are ready'),
    text: N_('To get started, run the sample analysis from Settings or add your first camera. You can reopen this tour at any time with the "Tour" button at the bottom left.') },
];

function tourSeen() {
  try {
    if (localStorage.getItem(TOUR_STORAGE_KEY) === '1') return true;
    if (localStorage.getItem(LEGACY_TOUR_STORAGE_KEY) === '1') { markTourSeen(); return true; }
    return false;
  } catch (x) { return true; }
}
function markTourSeen() { try { localStorage.setItem(TOUR_STORAGE_KEY, '1'); } catch (x) { /* storage disabled: the tour counts as seen for this session only */ } }

function openTour(i = 0) {
  if (!document.getElementById('tour')) {
    const d = document.createElement('div');
    d.id = 'tour';
    d.setAttribute('role', 'dialog');
    d.setAttribute('aria-modal', 'true');
    d.setAttribute('aria-label', T('Guided tour'));
    d.innerHTML = '<div class="tour-spot none"></div><div class="tour-card"></div>';
    document.body.appendChild(d);
  }
  $('#overlay').innerHTML = '';
  if (!TOUR.open) TOUR.prevFocus = document.activeElement;
  TOUR_BACKDROP.forEach(s => { const e = document.querySelector(s); if (e) e.inert = true; });
  TOUR.open = true;
  clearInterval(TOUR.timer);
  TOUR.timer = setInterval(placeTour, 200);  // the page may be redrawn: the target is looked up again each time
  showTourStep(i);
}

function closeTour() {
  TOUR.open = false;
  clearInterval(TOUR.timer);
  document.getElementById('tour')?.remove();
  TOUR_BACKDROP.forEach(s => { const e = document.querySelector(s); if (e) e.inert = false; });
  // focus returns to where it was before the tour; if that element was redrawn, to the Tour button in the left rail
  (TOUR.prevFocus && document.contains(TOUR.prevFocus) ? TOUR.prevFocus : document.querySelector('#rail a[data-action="tour"]'))?.focus({ preventScroll: true });
  TOUR.prevFocus = null;
  markTourSeen();
}

function showTourStep(i) {
  TOUR.i = Math.max(0, Math.min(TOUR_STEPS.length - 1, i));
  TOUR.target = null;
  TOUR.scrolled = false;
  const a = TOUR_STEPS[TOUR.i], last = TOUR.i === TOUR_STEPS.length - 1;
  const path = typeof a.path === 'function' ? a.path() : a.path;
  if (path != null && route().path !== path) go(path);
  $('#tour .tour-card').innerHTML = `<button class="tour-close" data-action="tour-close" title="${T('Close tour (Esc)')}" aria-label="${T('Close tour')}">×</button>
    <h3>${h(T(a.title))}</h3><p>${h(T(a.text))}</p>
    <div class="tour-foot"><span class="tour-count">${TOUR.i + 1} / ${TOUR_STEPS.length}</span>
      ${TOUR.i ? `<button class="btn small" data-action="tour-back">${T('Back')}</button>` : `<button class="btn small" data-action="tour-close">${T('Skip tour')}</button>`}
      <button class="btn small main" data-action="${last ? 'tour-close' : 'tour-next'}" id="tour-next">${last ? T('Finish') : T('Next')}</button></div>`;
  $('#tour-next').focus({ preventScroll: true });
  placeTour();
}

function tourTarget() {
  const a = TOUR_STEPS[TOUR.i];
  const path = typeof a.path === 'function' ? a.path() : a.path;
  if (path != null && route().path !== path) return null;  // the screen has not changed yet
  for (const s of a.target || []) {
    const e = typeof s === 'function' ? s() : document.querySelector(s);
    if (e && e.getClientRects().length) return e;
  }
  return null;
}

function placeTour() {
  const root = document.getElementById('tour');
  if (!TOUR.open || !root) return;
  const spot = root.querySelector('.tour-spot'), card = root.querySelector('.tour-card');
  const W = window.innerWidth, H = window.innerHeight, pad = 12;
  const e = tourTarget();
  if (e && (e !== TOUR.target || !TOUR.scrolled)) {  // when the target is first found it is scrolled into view
    TOUR.target = e;
    TOUR.scrolled = true;
    const r0 = e.getBoundingClientRect();
    if (r0.top < 70 || r0.bottom > H - 20) e.scrollIntoView({ block: r0.height > H * 0.7 ? 'start' : 'center' });
  }
  let r = null;
  if (e) {
    const b = e.getBoundingClientRect();
    const x1 = Math.max(4, b.left - 6), y1 = Math.max(4, b.top - 6), x2 = Math.min(W - 4, b.right + 6), y2 = Math.min(H - 4, b.bottom + 6);
    if (x2 - x1 > 8 && y2 - y1 > 8) r = { x1, y1, x2, y2 };
  }
  const kw = card.offsetWidth, kh = card.offsetHeight;
  let kx, ky;
  if (!r) {  // nothing to highlight: card in the middle, the whole page dimmed
    spot.classList.add('none');
    Object.assign(spot.style, { left: '0px', top: '0px', width: W + 'px', height: H + 'px' });
    kx = (W - kw) / 2;
    ky = (H - kh) / 2;
  } else {
    spot.classList.remove('none');
    Object.assign(spot.style, { left: r.x1 + 'px', top: r.y1 + 'px', width: r.x2 - r.x1 + 'px', height: r.y2 - r.y1 + 'px' });
    const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
    if (r.y2 + pad + kh <= H - pad) [kx, ky] = [clamp(r.x1, pad, W - kw - pad), r.y2 + pad];                       // below
    else if (r.y1 - pad - kh >= pad) [kx, ky] = [clamp(r.x1, pad, W - kw - pad), r.y1 - pad - kh];                // above
    else if (r.x2 + pad + kw <= W - pad) [kx, ky] = [r.x2 + pad, clamp(r.y1, pad, H - kh - pad)];                 // right
    else if (r.x1 - pad - kw >= pad) [kx, ky] = [r.x1 - pad - kw, clamp(r.y1, pad, H - kh - pad)];                // left
    else [kx, ky] = [W - kw - pad - 8, H - kh - pad - 8];                                                         // does not fit: bottom right corner, over the element
  }
  Object.assign(card.style, { left: Math.round(kx) + 'px', top: Math.round(ky) + 'px' });
}

ACTIONS['tour'] = () => openTour(0);
ACTIONS['tour-next'] = () => showTourStep(TOUR.i + 1);
ACTIONS['tour-back'] = () => showTourStep(TOUR.i - 1);
ACTIONS['tour-close'] = () => closeTour();

document.addEventListener('keydown', ev => {
  if (!TOUR.open) return;
  if (ev.key === 'Escape') closeTour();
  else if (ev.key === 'ArrowRight') { if (TOUR.i < TOUR_STEPS.length - 1) showTourStep(TOUR.i + 1); }
  else if (ev.key === 'ArrowLeft') { if (TOUR.i > 0) showTourStep(TOUR.i - 1); }
  else if (ev.key === 'Tab') {  // keep focus inside the tour
    const d = [...document.querySelectorAll('#tour button')];
    const pos = d.indexOf(document.activeElement);
    ev.preventDefault();
    d[(pos + (ev.shiftKey ? d.length - 1 : 1)) % d.length]?.focus();
  }
});
window.addEventListener('resize', placeTour);
window.addEventListener('scroll', placeTour, true);

/* First launch: once the program is ready (status read), the tour starts once by itself. */
(function firstLaunch(attempt = 0) {
  if (tourSeen()) return;
  if (S.status && S.catalog) { markTourSeen(); return openTour(0); }  // marked as soon as it is shown: reloading the page does not restart it
  if (attempt < 100) setTimeout(() => firstLaunch(attempt + 1), 150);
})();
