// UX check: screenshots every dashboard page at desktop (light, dark) and phone width and reports
// horizontal overflow and JS errors. Needs Playwright with Chromium and a running dashboard.
//   node scripts/ux-check.js [base-url] [out-dir]     (defaults: http://localhost:8088, ./ux-shots)
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://localhost:8088";
const OUT = process.argv[3] || "ux-shots";
const PAGES = ["/", "/production", "/battery", "/grid", "/economics", "/quality", "/health", "/goals", "/analysis", "/settings"];
const VIEWS = [[1440, 900, "desktop", "light"], [1440, 900, "dark", "dark"], [390, 844, "phone", "light"]];

(async () => {
  require("fs").mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  let problems = 0;
  for (const [width, height, tag, scheme] of VIEWS) {
    const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, locale: "bg-BG" });
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    for (const path of PAGES) {
      await page.goto(BASE + path, { waitUntil: "networkidle" });
      await page.waitForTimeout(1200);
      const name = path === "/" ? "overview" : path.slice(1);
      await page.screenshot({ path: `${OUT}/${name}-${tag}.png`, fullPage: true });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      const errs = errors.splice(0);
      if (overflow > 0 || errs.length) problems++;
      console.log(`${tag.padEnd(8)}${path.padEnd(14)}overflow ${overflow}px${errs.length ? "  errors: " + errs.join(" | ") : ""}`);
    }
    await ctx.close();
  }
  await browser.close();
  process.exit(problems ? 1 : 0);
})();
