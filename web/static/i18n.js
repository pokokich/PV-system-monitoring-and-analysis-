/* Site language: Bulgarian (the source text) or English.
 *
 * Pages are written in Bulgarian. In English mode every Bulgarian text on the page (text nodes,
 * a few attributes, the title and the strings of chart options) is looked up in I18N_EN
 * (web/static/i18n-en.js). Numbers are taken out before the lookup, so one key such as
 * "{0} от {1} дни" covers every value: "{0} of {1} days". Text without a key stays Bulgarian.
 * The choice is kept per browser (localStorage and a cookie); ?lang=en or ?lang=bg sets it.
 */
"use strict";

const I18N = (() => {
  const root = document.documentElement;
  const KEY = "lang";
  let lang = "bg";
  try {
    const q = new URLSearchParams(location.search).get("lang");
    // the owner's choice in Settings, else the browser's language (Bulgarian only for Bulgarian browsers)
    const fallback = root.dataset.langDefault || ((navigator.language || "").toLowerCase().startsWith("bg") ? "bg" : "en");
    lang = q === "en" || q === "bg" ? q : (localStorage.getItem(KEY) || fallback);
    if (q) localStorage.setItem(KEY, lang);
  } catch (e) { /* private mode: Bulgarian */ }
  const en = lang === "en";
  root.lang = lang;
  if (en) root.classList.add("i18n-pending");

  const dict = window.I18N_EN || {};
  // Money: the keys are written with "€"; the installation's own symbol is swapped in and out around the lookup.
  const CCY = root.dataset.ccy || "€";
  const CCY_RE = CCY === "€" ? null : new RegExp(`(^|[^\\p{L}])${CCY.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\p{L}])`, "gu");
  const CYR = /[Ѐ-ӿ]/;
  const NUM = /\d+(?:[.,  ]\d+)*/g;
  const SEP = /( · |; | – | — |: |\. )/;

  // Numbers the server wrote Bulgarian-style ("1 234,5", "0,092") in English style ("1,234.5", "0.092").
  // Dates ("20.10.2026") and numbers already in English style ("6,000") stay as they are.
  function num(m) {
    if (m.includes(".") || /^[1-9]\d{0,2}(,\d{3})+$/.test(m) || !/[,\u00a0\u202f]/.test(m)) return m;
    return m.replace(",", ".").replace(/[\u00a0\u202f]/g, ",");
  }

  function lookup(s0) {
    if (CCY_RE) {
      const out = lookup1(s0.replace(CCY_RE, "$1€"));
      return out === null ? null : out.split("€").join(CCY);
    }
    return lookup1(s0);
  }

  function lookup1(s) {
    if (Object.prototype.hasOwnProperty.call(dict, s)) return dict[s];
    const nums = [];
    const key = s.replace(NUM, (m) => { nums.push(m); return `{${nums.length - 1}}`; });
    if (nums.length && Object.prototype.hasOwnProperty.call(dict, key)) {
      return dict[key].replace(/\{(\d+)\}/g, (m, i) => (i in nums ? num(nums[+i]) : m));
    }
    return null;
  }

  /** English for a Bulgarian string; the string itself when there is no key or the site is in Bulgarian. */
  function t(s, depth = 0) {
    if (!en || typeof s !== "string" || !CYR.test(s)) return s;
    const lead = s.match(/^\s*/)[0], tail = s.match(/\s*$/)[0];
    const core = s.trim();
    let out = lookup(core);
    if (out === null && depth < 2 && SEP.test(core)) {
      // a sentence built from parts ("a · b · c"): translate the parts one by one
      const parts = core.split(SEP);
      const done = parts.map((p, i) => (i % 2 ? p : t(p, depth + 1)));
      if (done.some((p, i) => p !== parts[i])) out = done.join("");
    }
    return out === null ? s : lead + out + tail;
  }

  // ------------------------------------------------------------ DOM
  const ATTRS = ["title", "aria-label", "placeholder", "alt", "data-label"];
  function node(n) {
    if (n.nodeType === 3) {
      const p = n.parentNode;
      if (!p || p.nodeName === "SCRIPT" || p.nodeName === "STYLE") return;
      const v = t(n.nodeValue);
      if (v !== n.nodeValue) n.nodeValue = v;
    } else if (n.nodeType === 1) {
      if (n.nodeName === "SCRIPT" || n.nodeName === "STYLE") return;
      for (const a of ATTRS) {
        const v0 = n.getAttribute(a);
        if (v0 && CYR.test(v0)) { const v = t(v0); if (v !== v0) n.setAttribute(a, v); }
      }
      if (n.nodeName === "INPUT" && (n.type === "button" || n.type === "submit") && CYR.test(n.value)) n.value = t(n.value);
      for (let c = n.firstChild; c; c = c.nextSibling) node(c);
    }
  }
  function translatePage() {
    if (!en) return;
    document.title = t(document.title);
    node(document.body);
  }

  /** Chart options: every Bulgarian string inside, and the strings that formatters return. */
  function deep(o, key) {
    if (!en || o === null || o === undefined) return o;
    if (typeof o === "string") return t(o);
    if (typeof o === "function") return key === "formatter" ? (...a) => { const r = o(...a); return typeof r === "string" ? t(r) : r; } : o;
    if (Array.isArray(o)) return o.map((x) => deep(x, key));
    if (typeof o === "object" && Object.getPrototypeOf(o) === Object.prototype) {
      const out = {};
      for (const k of Object.keys(o)) out[k] = deep(o[k], k);
      return out;
    }
    return o;
  }

  function set(next) {
    try { localStorage.setItem(KEY, next); } catch (e) { /* private mode */ }
    document.cookie = `${KEY}=${next}; path=/; max-age=31536000; SameSite=Lax`;
    const u = new URL(location.href);
    u.searchParams.delete("lang");
    location.replace(u.toString());
  }

  if (en) {
    document.addEventListener("DOMContentLoaded", () => {
      translatePage();
      root.classList.remove("i18n-pending");
      new MutationObserver((list) => {
        for (const m of list) {
          if (m.type === "characterData") node(m.target);
          else if (m.type === "attributes") node(m.target);
          else m.addedNodes.forEach(node);
        }
      }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    });
    setTimeout(() => root.classList.remove("i18n-pending"), 1500);   // never leave the page hidden
  }

  return { lang, en, locale: en ? "en-GB" : "bg-BG", t, deep, set, translatePage };
})();
