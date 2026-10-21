// Lists Bulgarian text still shown in English mode: visits every page (desktop and phone), opens table
// views, dialogs, period presets and chart tooltips, and prints the untranslated strings as lookup keys
// (numbers as {0}, {1}…), the format of web/static/i18n-en.js.
//   node scripts/i18n-collect.js [base-url] > missing.json
const { chromium } = require("playwright");
const BASE = process.argv[2] || "http://localhost:8088";
const PAGES = ["/", "/production", "/battery", "/grid", "/economics", "/quality", "/health", "/goals", "/analysis", "/settings"];
const PRESETS = ["today", "7d", "all"];

const harvest = () => {
  const CYR = /[Ѐ-ӿ]/, out = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) {
    const p = n.parentNode;
    if (p && !["SCRIPT", "STYLE"].includes(p.nodeName) && CYR.test(n.nodeValue)) out.push(n.nodeValue.trim());
  }
  document.querySelectorAll("[title],[aria-label],[placeholder],[alt],[data-label]").forEach((e) => {
    for (const a of ["title", "aria-label", "placeholder", "alt", "data-label"]) {
      const v = e.getAttribute(a); if (v && CYR.test(v)) out.push(v.trim());
    }
  });
  if (CYR.test(document.title)) out.push(document.title);
  const walk = (o) => {
    if (typeof o === "string") { if (CYR.test(o)) out.push(o.trim()); }
    else if (Array.isArray(o)) o.forEach(walk);
    else if (o && typeof o === "object") Object.values(o).forEach(walk);
  };
  ((window.PV && PV.registry) || []).forEach((r) => walk(r.inst.getOption()));
  return out;
};

const tooltips = async () => {
  const out = [];
  for (const r of (window.PV && PV.registry) || []) {
    for (const i of [0, 3, 10]) {
      r.inst.dispatchAction({ type: "showTip", seriesIndex: 0, dataIndex: i });
      await new Promise((f) => setTimeout(f, 60));
      out.push(...r.el.innerText.split("\n"));
    }
    r.inst.dispatchAction({ type: "hideTip" });
  }
  return out.filter((s) => /[Ѐ-ӿ]/.test(s)).map((s) => s.trim());
};

(async () => {
  const browser = await chromium.launch();
  const found = new Set();
  const add = (list) => list.forEach((s) => {
    let i = 0;
    if (s) found.add(s.replace(/\d+(?:[.,  ]\d+)*/g, () => `{${i++}}`));
  });
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    await ctx.addInitScript(() => { try { localStorage.setItem("lang", "en"); } catch (e) { /* none */ } });
    const page = await ctx.newPage();
    for (const path of PAGES) {
      await page.goto(BASE + path, { waitUntil: "networkidle" });
      await page.waitForTimeout(1500);
      add(await page.evaluate(harvest));
      if (w < 1000) continue;
      add(await page.evaluate(tooltips));
      for (const p of PRESETS) {
        const b = await page.$(`[data-preset="${p}"]`);
        if (b) { await b.click(); await page.waitForTimeout(1500); add(await page.evaluate(harvest)); add(await page.evaluate(tooltips)); }
      }
      for (const b of await page.$$("[data-table]")) await b.click().catch(() => {});
      await page.waitForTimeout(300);
      add(await page.evaluate(harvest));
      for (const b of await page.$$("main button.btn, main .card-head button:not([data-table])")) {
        await b.click({ timeout: 1000 }).catch(() => {});
        await page.waitForTimeout(300);
        add(await page.evaluate(harvest));
        await page.keyboard.press("Escape");
      }
    }
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify([...found].sort(), null, 1));
})();
