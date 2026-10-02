// Captures every UI page with headless Chromium and reports console errors.
// Usage (from scripts/ui-screenshots.sh): node ui-screenshots.mjs <base-url> <out-dir>
import { chromium } from "playwright";

const [base = "https://127.0.0.1", out = "/out"] = process.argv.slice(2);
const exporter = encodeURIComponent(process.env.EXPORTER_ID ?? "");
const pages = [
  ["dashboard", "/?range=24h"],
  ["explorer", "/explorer?range=24h"],
  ["explorer-filtered", "/explorer?range=24h&vlan=101&protocol=udp"],
  ["devices", "/devices"],
  ["device", `/devices/${exporter}?range=24h`],
  ["interfaces", "/interfaces?range=24h"],
  ["interface", `/interfaces/${exporter}/8?range=24h`],
  ["health", "/health"],
  ["admin", "/admin"],
  ["hosts", "/hosts"],
  ["groups", "/groups"],
  ["layer2", "/layer2?range=24h"],
  ["trends", "/trends"],
  ["admin-users", "/admin?tab=users"],
  ["admin-integrations", "/admin?tab=integrations"],
  ["admin-notifications", "/admin?tab=notifications"],
  ["admin-certificate", "/admin?tab=certificate"],
  ["alerts", "/alerts"],
  ["explorer-groups", "/explorer?range=24h", "Groups"],
  ["explorer-flowmap", "/explorer?range=24h", "Flow map"],
];

const browser = await chromium.launch();
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 }, colorScheme: process.env.SCHEME ?? "light" });
// Sign in with the dedicated UI-test account (see ui-screenshots.sh).
if (process.env.UI_USER) {
  const r = await ctx.request.post(base + "/api/v1/auth/login", { data: { username: process.env.UI_USER, password: process.env.UI_PASSWORD } });
  if (!r.ok()) {
    console.error(`login failed: ${r.status()}`);
    process.exit(2);
  }
}
// The ui-test account is read-only: administration pages are skipped for it.
const me = await (await ctx.request.get(base + "/api/v1/auth/me")).json().catch(() => ({}));
const ADMIN_ONLY = { has: (n) => n === "admin" || n.startsWith("admin-") };
let failures = 0;
for (const [name, path, clickText] of pages) {
  if (me.role && me.role !== "admin" && ADMIN_ONLY.has(name)) {
    console.log(`${name}: skipped (read-only account)`);
    continue;
  }
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => r.status() >= 400 && errors.push(`${r.status()} ${r.url()}`));
  await page.goto(base + path, { waitUntil: "networkidle" });
  if (clickText) {
    await page.getByRole("button", { name: clickText, exact: true }).click();
    await page.waitForLoadState("networkidle");
  }
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
  console.log(`${name}: ${errors.length ? "ERRORS " + errors.join(" | ") : "ok"}`);
  failures += errors.length;
  await page.close();
}
await ctx.request.post(base + "/api/v1/auth/logout", { headers: { "X-Requested-With": "sflow" } }).catch(() => {});
await browser.close();
process.exit(failures ? 1 : 0);
