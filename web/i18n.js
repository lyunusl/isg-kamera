'use strict';
/* Panel language (en | tr). English is the source language; T(text) returns the Turkish entry of the shared
   catalogue (engine/isg_core/locales/tr.json, served by the panel at /api/i18n) when the language is Turkish.
   Templates use named placeholders such as {n}, filled from T's second argument. N_(text) only marks a text for the catalogue.
   Loaded before app.js, synchronously, so that every screen is drawn in one language from the first paint. */
const I18N = { lang: 'tr', catalog: {}, undecided: false };
(function () {
  try {
    const x = new XMLHttpRequest();
    x.open('GET', '/api/i18n', false);
    x.send();
    if (x.status === 200) {
      const j = JSON.parse(x.responseText);
      I18N.catalog = j.catalog || {};
      if (j.lang === 'tr' || j.lang === 'en') I18N.lang = j.lang;
      else {
        // No setting yet (new installation started from source): follow the browser and save the choice.
        I18N.undecided = true;
        I18N.lang = /^tr\b/i.test(navigator.language || '') ? 'tr' : 'en';
        const p = new XMLHttpRequest();
        p.open('POST', '/api/i18n', false);
        p.setRequestHeader('Content-Type', 'application/json');
        p.send(JSON.stringify({ lang: I18N.lang }));
      }
    }
  } catch (e) { /* panel unreachable: stay with the defaults */ }
  document.documentElement.lang = I18N.lang;
})();
function T(text, values) {
  let s = I18N.lang === 'tr' && Object.prototype.hasOwnProperty.call(I18N.catalog, text) ? I18N.catalog[text] : text;
  if (values) s = s.replace(/\{(\w+)\}/g, (m, k) => (Object.prototype.hasOwnProperty.call(values, k) ? String(values[k]) : m));
  return s;
}
const N_ = text => text;
/* Static page text: elements with data-t hold plain (English) text without markup and get it translated;
   data-t-attr="title placeholder ..." names attributes whose (English) values are translated. */
function translateDom(root) {
  (root || document).querySelectorAll('[data-t]').forEach(e => { e.textContent = T(e.textContent.trim()); });
  (root || document).querySelectorAll('[data-t-attr]').forEach(e => {
    for (const a of e.getAttribute('data-t-attr').split(/\s+/)) if (a && e.hasAttribute(a)) e.setAttribute(a, T(e.getAttribute(a)));
  });
}
/* Changes the language, saves it in the panel settings and redraws the page. */
async function setLanguage(lang) {
  const r = await fetch('/api/i18n', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lang }) });
  if (r.ok) location.reload();
}
