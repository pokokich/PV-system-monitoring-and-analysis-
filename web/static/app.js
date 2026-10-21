/* Shared dashboard code: period filter, API calls, ECharts styling, tiles and table views. */
"use strict";

const PV = (() => {
  const root = document.documentElement;
  const css = (name) => getComputedStyle(root).getPropertyValue(name).trim();
  const tokens = () => ({
    surface: css("--surface"), text1: css("--text-primary"), text2: css("--text-secondary"),
    muted: css("--text-muted"), grid: css("--grid"), axis: css("--axis"), border: css("--border"),
    pv: css("--c-pv"), load: css("--c-load"), gridImp: css("--c-grid"), battery: css("--c-battery"),
    export: css("--c-export"), roof: css("--c-roof"), carport: css("--c-carport"), day: css("--c-day"),
    night: css("--c-night"), money: css("--c-money"), temp: css("--c-temp"),
    seq: [0, 1, 2, 3, 4].map((i) => css(`--seq-${i}`)),
    heat: [0, 1, 2, 3, 4, 5].map((i) => css(`--heat-${i}`)), socRamp: [0, 1, 2, 3].map((i) => css(`--soc-${i}`)),
  });
  const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  const LOCALE = typeof I18N !== "undefined" ? I18N.locale : "bg-BG";

  // ------------------------------------------------------------ formatting
  const num = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? "–"
    : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: d, maximumFractionDigits: d });
  const compact = (v) => {
    if (v === null || v === undefined) return "–";
    const a = Math.abs(v);
    if (a >= 1e6) return num(v / 1e6, 1) + (LOCALE === "en-GB" ? "M" : " млн");
    if (a >= 1e4) return num(v / 1e3, 1) + (LOCALE === "en-GB" ? "k" : " хил");
    return num(v, a >= 100 ? 0 : 1);
  };
  const fmtDate = (iso) => { if (!iso) return "–"; const [y, m, d] = iso.split("-"); return d ? `${d}.${m}.${y}` : `${m}.${y}`; };
  const hhmm = (h) => { if (h === null || h === undefined) return "–"; const m = Math.round(h * 60); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`; };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MONTHS = LOCALE === "en-GB" ? ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    : ["яну", "фев", "мар", "апр", "май", "юни", "юли", "авг", "сеп", "окт", "ное", "дек"];
  const label = (x) => {
    if (/^\d{4}-\d{2}$/.test(x)) return `${MONTHS[+x.slice(5) - 1]} ${x.slice(2, 4)}`;
    if (/^\d{4}-\d{2}-\d{2}$/.test(x)) return `${x.slice(8)}.${x.slice(5, 7)}`;
    return x;
  };

  // ------------------------------------------------------------ period state
  const body = document.body;
  const first = body.dataset.first, last = body.dataset.last;
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const addDays = (s, n) => { const d = new Date(s + "T12:00:00"); d.setDate(d.getDate() + n); return iso(d); };
  const clamp = (s) => (s < first ? first : s > last ? last : s);
  function presetRange(p) {
    switch (p) {
      case "today": return [last, last];
      case "7d": return [clamp(addDays(last, -6)), last];
      case "30d": return [clamp(addDays(last, -29)), last];
      case "month": return [clamp(last.slice(0, 8) + "01"), last];
      case "year": return [clamp(last.slice(0, 5) + "01-01"), last];
      default: return [first, last];
    }
  }
  const state = { from: null, to: null, day: null, preset: null };
  let onChange = () => {};

  function readUrl() {
    const q = new URLSearchParams(location.search);
    state.preset = q.get("p") || (q.get("from") ? null : body.dataset.defaultPeriod);
    if (state.preset) [state.from, state.to] = presetRange(state.preset);
    else { state.from = clamp(q.get("from") || first); state.to = clamp(q.get("to") || last); }
    state.day = clamp(q.get("day") || last);
  }
  function writeUrl() {
    const q = new URLSearchParams();
    if (state.preset) q.set("p", state.preset); else { q.set("from", state.from); q.set("to", state.to); }
    if (state.day !== last) q.set("day", state.day);
    history.replaceState(null, "", `${location.pathname}?${q}`);
    // keep the period when moving between pages
    document.querySelectorAll("nav.pages a").forEach((a) => { a.search = q.toString(); });
  }
  function syncControls() {
    document.querySelectorAll("[data-preset]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.preset === state.preset)));
    const f = document.getElementById("from"), t = document.getElementById("to"), d = document.getElementById("day");
    if (f) f.value = state.from;
    if (t) t.value = state.to;
    if (d) d.value = state.day;
    const note = document.getElementById("range-note");
    if (note) {
      const n = Math.round((new Date(state.to) - new Date(state.from)) / 864e5) + 1;
      note.textContent = `${fmtDate(state.from)} – ${fmtDate(state.to)} · ${n} ${n === 1 ? "ден" : "дни"}`;
    }
  }
  function changed() { writeUrl(); syncControls(); onChange(state); }

  function initFilters(handler) {
    onChange = handler;
    readUrl();
    document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
      state.preset = b.dataset.preset; [state.from, state.to] = presetRange(state.preset); changed();
    }));
    const f = document.getElementById("from"), t = document.getElementById("to"), d = document.getElementById("day");
    if (f) f.addEventListener("change", () => { if (!f.value) return; state.preset = null; state.from = clamp(f.value); if (state.from > state.to) state.to = state.from; changed(); });
    if (t) t.addEventListener("change", () => { if (!t.value) return; state.preset = null; state.to = clamp(t.value); if (state.to < state.from) state.from = state.to; changed(); });
    if (d) d.addEventListener("change", () => { if (!d.value) return; state.day = clamp(d.value); changed(); });
    changed();
  }

  // ------------------------------------------------------------ API
  async function api(path, params = {}) {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined));
    const res = await fetch(`/api/${path}${q.toString() ? "?" + q : ""}`);
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return res.json();
  }
  const range = () => ({ from: state.from, to: state.to });

  // ------------------------------------------------------------ motion
  // Everything animated respects "reduce motion" in the OS; numbers then appear at once.
  const motion = () => !matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Count a formatted number up from zero ("1 143,46", "82", "−3,5"); other text (dates, "a / b", "–") is set as is.
  function countUp(el, text, ms = 900) {
    text = String(text ?? "");
    const m = (LOCALE === "en-GB" ? /^(-|−)?([\d,]*\d)(?:\.(\d+))?(\s*\D{0,3})$/ : /^(-|−)?([\d\u00a0\u202f ]*\d)(?:,(\d+))?(\s*\D{0,3})$/).exec(text);
    const year = m && !m[3] && !m[4] && /^(19|20)\d\d$/.test(m[2]);
    if (!el || !m || year || !motion()) { if (el) el.textContent = text; return; }
    const sign = m[1] ? -1 : 1, digits = m[3] ? m[3].length : 0, suffix = m[4] || "";
    const target = sign * parseFloat(m[2].replace(/[\u00a0\u202f ,]/g, "") + (m[3] ? "." + m[3] : ""));
    const token = (el.dataset.countToken = String(Math.random()));
    const t0 = performance.now();
    const step = (t) => {
      if (el.dataset.countToken !== token) return;   // a newer value took over
      const x = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - x, 3);
      el.textContent = x < 1 ? num(target * e, digits) + suffix : text;
      if (x < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  // Heartbeat line for a health status (good / watch / bad / no_data): the beat runs in the status color.
  function ecg(level) {
    const color = { good: "var(--s-good)", watch: "var(--s-warn)", bad: "var(--s-bad)" }[level];
    const d = "M0 20 H70 L80 20 L88 4 L98 34 L106 20 H200";
    return `<svg class="ecg${color ? "" : " idle"}" viewBox="0 0 200 38" preserveAspectRatio="none" aria-hidden="true">` +
      `<path class="base" d="${d}"/><path class="beat" d="${d}" style="stroke:${color || "var(--axis)"};animation-duration:${level === "bad" ? 1.2 : 2.2}s"/></svg>`;
  }
  // Ring gauge 0-100 %; the colored arc grows to the value.
  function ring(el, pct, color, label) {
    const C = 2 * Math.PI * 62, v = Math.max(0, Math.min(100, pct ?? 0));
    el.innerHTML = `<svg viewBox="0 0 150 150" aria-hidden="true"><circle class="ring-bg" cx="75" cy="75" r="62"/>` +
      `<circle class="ring-fg" cx="75" cy="75" r="62" stroke="${color}" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${C.toFixed(1)}" transform="rotate(-90 75 75)"/></svg>` +
      `<div class="ring-txt"><b></b><span>${esc(label)}</span></div>`;
    el.setAttribute("role", "img"); el.setAttribute("aria-label", `${label}: ${num(pct, 0)}%`);
    const fg = el.querySelector(".ring-fg");
    requestAnimationFrame(() => requestAnimationFrame(() => { fg.style.strokeDashoffset = (C * (1 - v / 100)).toFixed(1); }));
    countUp(el.querySelector("b"), `${num(pct, 0)}%`, 1200);
  }
  // Cards, tiles and heroes slide in one after another on the first load of a page.
  function enter() {
    if (!motion()) return;
    document.querySelectorAll("main .card, main .hero, main .note.box").forEach((el, i) => {
      el.style.setProperty("--i", Math.min(i, 12)); el.classList.add("enter");
    });
  }

  // ------------------------------------------------------------ tiles
  // entity dot on a tile, from the first KPI code of the catalog groups
  function entityOf(code) {
    const k = String(code || "").split(/\s+/)[0];
    if (!k) return null;
    const exact = { E01: "pv", E02: "load", E03: "grid", E04: "grid", E05: "export", E06: "battery", E09: "pv", I04: "temp" };
    if (exact[k]) return exact[k];
    return { P: "pv", U: "pv", B: "battery", F: "money", G: "money", I: "muted", D: "muted", Q: "muted" }[k[0]] || null;
  }
  function tiles(container, items) {
    const el = typeof container === "string" ? document.getElementById(container) : container;
    el.innerHTML = "";
    for (const it of items) {
      const div = document.createElement("div");
      div.className = "tile" + (it.conditional ? " cond" : "");
      const c = it.color || entityOf(it.code);
      if (c) div.dataset.c = c;
      if (it.title) div.title = it.title;
      div.innerHTML = `<span class="code"></span><div class="label"></div><div class="value"><span class="v"></span><span class="unit"></span></div><div class="sub"></div>`;
      div.querySelector(".code").textContent = it.code || "";
      div.querySelector(".label").textContent = it.label;
      const info = it.info || infoFor(it.label);
      if (info) div.querySelector(".label").appendChild(infoButton(it.label, info));
      countUp(div.querySelector(".v"), it.value);
      div.querySelector(".unit").textContent = it.unit || "";
      div.querySelector(".sub").textContent = it.sub || "";
      el.appendChild(div);
    }
  }

  // ------------------------------------------------------------ indicator info
  // The small "i" next to a tile label opens a box with what the indicator shows, how it is counted and
  // what a good value is (web/static/kpi-info.js). Hover or focus shows it, a click or tap keeps it open.
  function infoFor(label) {
    const I = window.KPI_INFO || {};
    const k = String(label || "");
    const e = I[k] || I[k.replace(/,\s*\d{4}$/, "")];
    const ccy = document.documentElement.dataset.ccy || "€";   // the texts are written with "€"
    return e ? (typeof I18N !== "undefined" && I18N.en ? e.en : e.bg).map((s) => s.split("€").join(ccy)) : null;
  }
  let pop = null, popFor = null, pinned = false, hideTimer = 0;
  function infoButton(label, info) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "info-btn";
    const tr = typeof I18N !== "undefined" ? I18N.t : (x) => x;
    b.setAttribute("aria-label", `${tr("Какво показва")}: ${tr(label)}`);
    b.setAttribute("aria-expanded", "false");
    b.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7"/><path d="M8 7.2v4M8 4.6v.1"/></svg>';
    b.addEventListener("mouseenter", () => { clearTimeout(hideTimer); if (!pinned) showInfo(b, label, info); });
    b.addEventListener("mouseleave", () => { if (!pinned) hideTimer = setTimeout(hideInfo, 120); });
    b.addEventListener("focus", () => { if (!pinned) showInfo(b, label, info); });
    b.addEventListener("blur", () => { if (!pinned) hideInfo(); });
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (pinned && popFor === b) { pinned = false; hideInfo(); return; }
      showInfo(b, label, info); pinned = true;
    });
    return b;
  }
  function showInfo(btn, label, info) {
    if (!pop) {
      pop = document.createElement("div");
      pop.className = "info-pop"; pop.id = "info-pop"; pop.setAttribute("role", "dialog");
      pop.addEventListener("mouseenter", () => clearTimeout(hideTimer));
      pop.addEventListener("mouseleave", () => { if (!pinned) hideTimer = setTimeout(hideInfo, 120); });
      document.body.appendChild(pop);
      document.addEventListener("click", (ev) => { if (pinned && !pop.contains(ev.target)) { pinned = false; hideInfo(); } });
      document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && popFor) { const b = popFor; pinned = false; hideInfo(); b.focus(); } });
      window.addEventListener("scroll", () => { if (popFor) place(popFor); }, { passive: true });
      window.addEventListener("resize", () => { if (popFor) place(popFor); });
    }
    if (popFor && popFor !== btn) popFor.setAttribute("aria-expanded", "false");
    const en = typeof I18N !== "undefined" && I18N.en;
    const [what, how, good] = info;
    const title = typeof I18N !== "undefined" ? I18N.t(label) : label;
    pop.setAttribute("aria-label", title);
    pop.innerHTML = `<b class="info-title">${esc(title)}</b><p>${esc(what)}</p>` +
      (how ? `<p><span>${en ? "How it is counted" : "Как се смята"}</span>${esc(how)}</p>` : "") +
      (good ? `<p><span>${en ? "How to read it" : "Как да го четеш"}</span>${esc(good)}</p>` : "");
    popFor = btn; btn.setAttribute("aria-expanded", "true"); btn.setAttribute("aria-controls", "info-pop");
    pop.hidden = false; place(btn);
  }
  function hideInfo() {
    if (!pop) return;
    pop.hidden = true;
    if (popFor) popFor.setAttribute("aria-expanded", "false");
    popFor = null;
  }
  function place(btn) {
    const r = btn.getBoundingClientRect(), w = Math.min(320, window.innerWidth - 24);
    pop.style.width = `${w}px`;
    const h = pop.offsetHeight;
    const left = Math.max(12, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 12));
    const below = r.bottom + 8 + h <= window.innerHeight || r.top - 8 - h < 0;
    pop.style.left = `${left}px`;
    pop.style.top = `${below ? r.bottom + 8 : r.top - 8 - h}px`;
  }

  // ------------------------------------------------------------ charts
  const registry = [];

  function axisCommon(t) {
    return {
      axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false },
      axisLabel: { color: t.muted, fontFamily: FONT, hideOverlap: true },
      splitLine: { show: false }, nameTextStyle: { color: t.muted, fontFamily: FONT },
    };
  }
  function valueAxis(t, name, extra = {}) {
    return Object.assign({
      type: "value", name, nameGap: 8, nameLocation: "end",
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: t.muted, fontFamily: FONT, formatter: (v) => compact(v) },
      splitLine: { lineStyle: { color: t.grid, width: 1 } }, nameTextStyle: { color: t.muted, fontFamily: FONT, align: "left" },
    }, extra);
  }
  function catAxis(t, data, extra = {}) {
    return Object.assign({ type: "category", data, ...axisCommon(t) }, extra, {
      axisLabel: Object.assign({}, axisCommon(t).axisLabel, { formatter: (v) => label(v) }, extra.axisLabel || {}),
    });
  }
  function timeAxis(t, extra = {}) {
    return Object.assign({ type: "time", ...axisCommon(t) }, extra);
  }

  // tooltip: value first, series name after, a short line key in the series color
  function tooltip(t, unitOf = () => "", digits = 1, headFmt = (x) => x) {
    return {
      trigger: "axis", confine: true, backgroundColor: t.surface, borderColor: t.border,
      textStyle: { color: t.text1, fontFamily: FONT, fontSize: 13 },
      axisPointer: { type: "line", lineStyle: { color: t.axis, width: 1 } },
      formatter: (ps) => {
        if (!ps.length) return "";
        const head = ps[0].axisType === "xAxis.time"
          ? new Date(ps[0].value[0]).toLocaleString(LOCALE, { timeZone: "UTC", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
          : headFmt(ps[0].axisValue);
        const rows = ps.filter((p) => !p.seriesName.startsWith("_")).map((p) => {
          const v = Array.isArray(p.value) ? p.value[1] : p.value;
          return `<div style="display:flex;align-items:center;gap:8px"><span style="display:inline-block;width:12px;height:2px;background:${p.color}"></span>` +
            `<b>${esc(num(v, digits))}</b>&nbsp;${esc(unitOf(p))}<span style="color:${t.text2}">${esc(p.seriesName)}</span></div>`;
        }).join("");
        return `<div style="color:${t.text2};margin-bottom:4px">${esc(head)}</div>${rows}`;
      },
    };
  }

  function legend(t, extra = {}) {
    // line key by default; bars pass { icon: "roundRect", itemHeight: 10 }. Series named "_…" stay out of it.
    return Object.assign({ top: 0, left: 0, icon: "rect", itemWidth: 14, itemHeight: 3, itemGap: 16,
      textStyle: { color: t.text2, fontFamily: FONT } }, extra);
  }

  function base(t, o) {
    return Object.assign({
      backgroundColor: "transparent", useUTC: true,
      animationDuration: 900, animationEasing: "cubicOut", animationDurationUpdate: 500,
      textStyle: { fontFamily: FONT, color: t.text2 },
      grid: { left: 8, right: 20, top: 40, bottom: 8, containLabel: true },
    }, o);
  }

  const line = (name, data, color, extra = {}) => Object.assign({
    name, type: "line", data, showSymbol: false, symbolSize: 8, connectNulls: false,
    lineStyle: { width: 2, color, cap: "round", join: "round" }, itemStyle: { color },
    emphasis: { focus: "none" },
  }, extra);
  const barLegend = (t, extra = {}) => legend(t, Object.assign({ icon: "roundRect", itemWidth: 12, itemHeight: 10 }, extra));
  const bar = (name, data, color, extra = {}) => Object.assign({
    animationDelay: (i) => i * 14,
    name, type: "bar", data, barMaxWidth: 24, itemStyle: { color, borderRadius: [4, 4, 0, 0] },
    emphasis: { itemStyle: { opacity: 0.85 } },
  }, extra);
  // stacked segments: a surface-colored border makes the 2px gap
  const stackBar = (t, name, data, color, stack = "s") => bar(name, data, color, {
    stack, itemStyle: { color, borderColor: t.surface, borderWidth: 1, borderRadius: 0 },
  });

  /** Draw a chart into a card; `build(tokens)` returns the option. Redrawn on theme change. */
  function chart(id, build) {
    const el = document.getElementById(id);
    if (!el) return null;
    let entry = registry.find((r) => r.id === id);
    if (!entry) {
      entry = { id, el, inst: echarts.init(el, null, { renderer: "canvas" }), build };
      describe(el);
      registry.push(entry);
      new ResizeObserver(() => entry.inst.resize()).observe(el);
      setupTable(entry);
    }
    entry.build = build;
    draw(entry);
    return entry.inst;
  }
  function draw(entry) {
    const option = typeof I18N !== "undefined" ? I18N.deep(entry.build(tokens())) : entry.build(tokens());
    if (option.legend && !option.legend.data && Array.isArray(option.series)) {
      option.legend.data = option.series.map((x) => x.name).filter((n) => n && !String(n).startsWith("_"));
    }
    entry.option = option;
    entry.inst.setOption(option, true);
    if (entry.tableShown) renderTable(entry);
  }

  // screen readers get the card title and subtitle; the table view carries the numbers
  function describe(el) {
    const card = el.closest(".card");
    const h = card && card.querySelector("h2"), sub = card && card.querySelector(".subtitle");
    el.setAttribute("role", "img");
    const tr = typeof I18N !== "undefined" ? I18N.t : (x) => x;
    el.setAttribute("aria-label", [h && h.textContent, sub && sub.textContent].filter(Boolean).map(tr).join(". ") +
      (card && card.querySelector("[data-table]") ? tr(". Стойностите са в бутона Таблица.") : ""));
  }

  // table view for every chart: x values in rows, one column per series
  function setupTable(entry) {
    const card = entry.el.closest(".card");
    const btn = card && card.querySelector("[data-table]");
    if (!btn) return;
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.hidden = true;
    btn.setAttribute("aria-pressed", "false");
    entry.el.after(wrap);
    entry.tableEl = wrap;
    btn.addEventListener("click", () => {
      entry.tableShown = !entry.tableShown;
      wrap.hidden = !entry.tableShown;
      entry.el.style.display = entry.tableShown ? "none" : "";
      btn.textContent = entry.tableShown ? "Графика" : "Таблица";
      btn.setAttribute("aria-pressed", String(entry.tableShown));
      if (entry.tableShown) renderTable(entry); else entry.inst.resize();
    });
  }
  function renderTable(entry) {
    const o = entry.option;
    const series = (o.series || []).filter((s) => !String(s.name || "").startsWith("_") && s.type !== "heatmap");
    const x = Array.isArray(o.xAxis) ? o.xAxis[0] : o.xAxis;
    const rows = new Map();
    const timeX = x && x.type === "time";
    series.forEach((s, i) => (s.data || []).forEach((p, j) => {
      const key = timeX ? p[0] : (x && x.data ? x.data[j] : j);
      const val = Array.isArray(p) ? p[1] : (p && typeof p === "object" ? p.value : p);
      if (!rows.has(key)) rows.set(key, new Array(series.length).fill(null));
      rows.get(key)[i] = val;
    }));
    const keys = [...rows.keys()].sort((a, b) => (a > b ? 1 : a < b ? -1 : 0));
    const fmtKey = (k) => timeX ? new Date(k).toLocaleString(LOCALE, { timeZone: "UTC", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : label(String(k));
    const table = document.createElement("table");
    table.className = "data";
    const thead = table.createTHead().insertRow();
    [""].concat(series.map((s) => s.name)).forEach((h) => { const th = document.createElement("th"); th.textContent = h; thead.appendChild(th); });
    const tb = table.createTBody();
    for (const k of keys) {
      const tr = tb.insertRow();
      tr.insertCell().textContent = fmtKey(k);
      rows.get(k).forEach((v) => { tr.insertCell().textContent = num(v, entry.digits ?? 2); });
    }
    entry.tableEl.innerHTML = "";
    entry.tableEl.appendChild(table);
  }

  function setLoading(on) { document.body.classList.toggle("loading", on); }

  function empty(id, text) {
    const el = document.getElementById(id);
    const entry = registry.find((r) => r.id === id);
    if (entry) entry.inst.clear();
    let msg = el.parentElement.querySelector(".empty");
    if (text) {
      if (!msg) { msg = document.createElement("div"); msg.className = "empty"; el.before(msg); }
      msg.textContent = text;
      el.style.visibility = "hidden";
      el.style.height = "0";
    } else {
      if (msg) msg.remove();
      el.style.visibility = ""; el.style.height = "";
    }
  }

  // ------------------------------------------------------------ theme
  function initTheme() {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;
    const isDark = () => root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    const show = () => {
      btn.querySelector("span").textContent = isDark() ? "Светла тема" : "Тъмна тема";
      btn.setAttribute("aria-label", `Включи ${isDark() ? "светла" : "тъмна"} тема`);
    };
    btn.addEventListener("click", () => {
      root.dataset.theme = isDark() ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (e) { /* private mode */ }
      show();
      registry.forEach(draw);
    });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { show(); registry.forEach(draw); });
    show();
  }

  // language switch: Bulgarian is the source, English comes from web/static/i18n-en.js
  function initLang() {
    const btn = document.getElementById("lang-toggle");
    if (!btn || typeof I18N === "undefined") return;
    btn.querySelector("span").textContent = I18N.en ? "Български" : "English";
    btn.setAttribute("lang", I18N.en ? "bg" : "en");
    btn.addEventListener("click", () => I18N.set(I18N.en ? "bg" : "en"));
  }

  // ------------------------------------------------------------ shell: menu drawer, data freshness, page sections
  function initMenu() {
    const side = document.getElementById("sidebar"), btn = document.getElementById("menu-btn"), scrim = document.getElementById("scrim");
    if (!side || !btn) return;
    const set = (open) => {
      side.classList.toggle("open", open); scrim.hidden = !open; btn.setAttribute("aria-expanded", String(open));
      if (open) (side.querySelector("a.active") || side.querySelector("a")).focus();
    };
    btn.addEventListener("click", () => set(!side.classList.contains("open")));
    scrim.addEventListener("click", () => set(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && side.classList.contains("open")) { set(false); btn.focus(); } });
  }

  // last station frame on every page: green up to 15 min, amber up to 60, red above (the collector polls every 5-7 min)
  async function live() {
    const els = document.querySelectorAll("[data-live]");
    if (!els.length) return;
    let cls = "down", text = "няма данни", long = "Няма данни от колектора", sys = null;
    try {
      const n = await api("now");
      if (n.ts) {
        const age = Math.max(0, n.age_min);
        cls = age <= 15 ? "ok" : age <= 60 ? "stale" : "down";
        const ago = age < 1 ? "сега" : age < 90 ? `преди ${num(age, 0)} мин` : `преди ${num(age / 60, 1)} ч`;
        text = `${num(n.pv_kw, 1)} kW PV · ${ago}`;
        long = `Последни данни ${ago}: PV ${num(n.pv_kw, 2)} kW, SOC ${num(n.soc, 0)}%` + (cls === "down" ? ". Провери колектора." : "");
      }
      sys = n.system;
    } catch (e) { /* keep "няма данни" */ }
    els.forEach((el) => {
      el.classList.remove("ok", "stale", "down"); el.classList.add(cls);
      el.querySelector(".live-text").textContent = text; el.title = long;
    });
    // guard checks (backup, alerts, disk, errors from Deye): one line under the live status while something is wrong
    document.querySelectorAll("[data-sys]").forEach((el) => {
      const probs = (sys && sys.problems) || [];
      el.hidden = !probs.length;
      if (!probs.length) return;
      el.className = `sys-alert ${sys.level}`;
      el.querySelector(".sys-text").textContent = probs.length === 1 ? probs[0].text : `${probs[0].text} (+${probs.length - 1})`;
      el.title = probs.map((p) => p.text).join("\n");
    });
  }

  // "На тази страница": jump links to the section headings of long pages
  function initToc() {
    const heads = [...document.querySelectorAll("main h2.section")];
    if (heads.length < 2) return;
    const nav = document.createElement("nav");
    nav.className = "toc"; nav.setAttribute("aria-label", "На тази страница");
    nav.innerHTML = "<span>На тази страница:</span>";
    heads.forEach((h, i) => {
      if (!h.id) h.id = `s-${i + 1}`;
      const a = document.createElement("a");
      a.href = `#${h.id}`; a.textContent = h.textContent;
      nav.appendChild(a);
    });
    const lead = document.querySelector("main .lead") || document.querySelector("main h1");
    if (lead) lead.after(nav);
  }

  // WRITE_PASSWORD on the server: the first change from this browser asks for it once, then a cookie keeps it
  const rawFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const res = await rawFetch(input, init);
    const method = (init.method || "GET").toUpperCase();
    if (res.status !== 401 || method === "GET" || String(input).startsWith("/api/login")) return res;
    const body = await res.clone().json().catch(() => ({}));
    if (body.code !== "password_required") return res;
    for (let tries = 0; tries < 3; tries++) {
      const pw = window.prompt(tries ? "Грешна парола. Опитай пак:" : "Парола за промени в дашборда:");
      if (pw === null) return res;
      const ok = await rawFetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw }) });
      if (ok.ok) return rawFetch(input, init);
    }
    return res;
  };

  document.addEventListener("DOMContentLoaded", () => {
    enter();
    initTheme(); initLang(); initMenu(); initToc(); live();
    setInterval(live, 60000);
  });

  return { locale: LOCALE, tz: document.documentElement.dataset.tz || undefined, t: (s) => (typeof I18N !== "undefined" ? I18N.t(s) : s), live, countUp, motion, ecg, ring, css, tokens, num, compact, fmtDate, hhmm, esc, label, state, initFilters, api, range, tiles, chart,
    base, valueAxis, catAxis, timeAxis, tooltip, legend, barLegend, line, bar, stackBar, setLoading, empty, registry,
    addDays, first: () => first, last: () => last };
})();
