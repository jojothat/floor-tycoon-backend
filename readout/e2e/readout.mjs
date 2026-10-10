// End-to-end check of the Space Readout against a running read API (mock or real backend).
// Seed first (tools/seed.mjs) so there are at least 10 completed games; optionally play real games too (frontend/e2e/play.mjs).
// Env: READOUT_URL (http://localhost:8081), API_URL (backend base, for the direct API checks; default READOUT_URL + '/api'),
//      READ_TOKEN (dev-viewer), REQUIRE_REAL=1 to also demand at least one non-synthetic game, CHROMIUM_PATH, HEADLESS=0 to watch.
import { chromium } from 'playwright';

const URL_ = (process.env.READOUT_URL || 'http://localhost:8081').replace(/\/$/, '');
const API = (process.env.API_URL || URL_ + '/api').replace(/\/$/, '');
const TOKEN = process.env.READ_TOKEN || 'dev-viewer';
const fails = [];
const ok = (cond, msg) => { console.log((cond ? 'ok   ' : 'FAIL ') + msg); if (!cond) fails.push(msg); };
const ENDPOINTS = ['meta', 'summary', 'funnel', 'picks', 'skips', 'edits', 'additions', 'sharing', 'pain-hours', 'bundles', 'space-brief', 'sim', 'leads-gap', 'styles', 'wall'];

// ---- 1. the read API itself ----
const noAuth = await fetch(`${API}/v1/read/summary`);
ok(noAuth.status === 401 || noAuth.status === 403, `read API refuses a request without a viewer token (${noAuth.status})`);
const H = { Authorization: 'Bearer ' + TOKEN };
const shapes = {};
for (const n of ENDPOINTS) {
  const r = await fetch(`${API}/v1/read/${n}?synthetic=include`, { headers: H });
  ok(r.ok, `GET /v1/read/${n} → ${r.status}`);
  if (r.ok) { const j = await r.json(); shapes[n] = j; ok(typeof j.n === 'number' && typeof j.updated_at === 'string', `${n} has n and updated_at`); }
}
const sumAll = shapes.summary || {};
ok((sumAll.completed || 0) >= 10, `at least 10 completed games in the store (${sumAll.completed}); seed first if this fails`);
const real = await (await fetch(`${API}/v1/read/summary`, { headers: H })).json().catch(() => ({}));
console.log(`     real (non-synthetic) games: started ${real.started}, completed ${real.completed}`);
if (process.env.REQUIRE_REAL === '1') ok('started' in real && real.started !== 0, 'at least one real game reached the backend');
// suppression: every per-scenario row under 10 must carry lt10 and no shares
const leak = (shapes.picks?.scenarios || []).filter(s => s.n < 10 && (s.options || []).some(o => o.share != null));
ok(leak.length === 0, `no scenario with fewer than 10 games shows shares (${leak.length} leaks)`);
const csv = await fetch(`${API}/v1/read/export.csv?synthetic=include`, { headers: H });
ok(csv.ok && (await csv.text()).startsWith('scenario_id,'), 'export.csv returns the picks table');

// ---- 2. the dashboard in a browser ----
const browser = await chromium.launch({ headless: process.env.HEADLESS !== '0', executablePath: process.env.CHROMIUM_PATH || undefined });
for (const [label, vp] of [['desktop', { width: 1280, height: 900 }], ['phone', { width: 400, height: 800 }]]) {
  const page = await browser.newPage({ viewport: vp });
  const errors = [], reads = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) errors.push(m.text()); });
  page.on('response', r => { if (r.status() >= 400 && !/favicon|fonts\.(googleapis|gstatic)/.test(r.url())) errors.push(r.status() + ' ' + r.url()); });
  page.on('request', r => { if (r.url().includes('/v1/read/')) reads.push(r.url()); });
  await page.goto(URL_ + '/', { waitUntil: 'networkidle' });
  ok(await page.locator('.facts .live').count() === 1, `${label}: shows "Live data", not sample`);
  ok(await page.locator('.note.err').count() === 0, `${label}: no connection or sign-in error`);
  // synthetic plays are left out by default; include them so the seeded store fills every slide (filters sit in a drawer)
  await page.click('#ctl summary');
  const syn = page.locator('#filters [name=synthetic]');
  if (!(await syn.isChecked())) { await syn.check(); await page.waitForLoadState('networkidle'); await page.waitForTimeout(500); }
  ok(reads.some(u => u.includes('synthetic=include')), `${label}: the synthetic toggle reaches the API`);
  const slides = await page.locator('.slide').count(), empty = await page.locator('.slide .lt10').count();
  ok(slides === 13, `${label}: ${slides} slides (13 expected)`);
  ok(empty === 0, `${label}: every slide has data (${empty} show "fewer than 10")`);
  ok(await page.locator('.slide h2').count() === 13 && await page.locator('.slide .so').count() >= 11, `${label}: slides have a headline and a "So what"`);
  ok((await page.locator('.facts').innerText()).includes(String(sumAll.completed)), `${label}: header count matches the API (${sumAll.completed})`);
  ok(await page.locator('.floor img').count() >= 8, `${label}: the assembled floor drew its base and zone layers`);
  const broken = await page.evaluate(() => [...document.images].filter(i => i.complete && !i.naturalWidth).map(i => i.getAttribute('src')));
  ok(broken.length === 0, `${label}: all option pictures load${broken.length ? ': ' + broken.slice(0, 3).join(', ') : ''}`);
  const kinds = await page.evaluate(() => ({ dots: !!document.querySelector('.slide circle'), ring: document.querySelectorAll('.ring svg').length, waffle: !!document.querySelector('.waf svg'), heat: !!document.querySelector('.slide svg rect[rx="6"]'), seg: document.querySelectorAll('.seg').length, fills: document.querySelectorAll('.fill').length, pairs: document.querySelectorAll('.pc').length }));
  ok(kinds.dots && kinds.ring >= 1 && kinds.waffle && kinds.heat && kinds.seg >= 2 && kinds.fills >= 3 && kinds.pairs >= 1, `${label}: varied charts present ${JSON.stringify(kinds)}`);
  await page.locator('details.nums summary').first().click();
  ok(await page.locator('details.nums[open] table').count() >= 1, `${label}: "See the numbers" opens a table`);
  const small = await page.evaluate(() => [...document.querySelectorAll('.slide svg text')].filter(t => t.getBoundingClientRect().height > 0 && t.getBoundingClientRect().height < 9).length);
  ok(small === 0, `${label}: no chart text smaller than 9 px (${small})`);
  const sw = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  ok(sw <= 1, `${label}: no sideways scroll (${sw}px)`);
  if (label === 'desktop') {
    const before = reads.length;
    await page.selectOption('#filters [name=type]', 'CE'); await page.waitForLoadState('networkidle'); await page.waitForTimeout(400);
    ok(reads.slice(before).some(u => u.includes('type=CE')), 'desktop: employee-type filter reaches the API');
    ok(await page.locator('.note.err').count() === 0, 'desktop: filtered view loads without error');
    await page.screenshot({ path: 'readout-e2e.png', fullPage: true });
  }
  ok(errors.length === 0, `${label}: no page errors${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`);
  await page.close();
}
await browser.close();

console.log(fails.length ? `\nREADOUT E2E FAILED (${fails.length})` : '\nREADOUT E2E PASSED');
process.exit(fails.length ? 1 : 0);
