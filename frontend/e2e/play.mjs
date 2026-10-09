// End-to-end check: plays one full game in a phone-sized browser against a running stack,
// then checks every backend call landed. Exit code 0 = pass.
//   BASE_URL=http://localhost:8080 node e2e/play.mjs
// Needs: npm i playwright (or @playwright/test) and a Chromium (npx playwright install chromium).
// Env: BASE_URL (default http://localhost:8080), API_URL (default BASE_URL + '/api'), HEADLESS=0 to watch,
//      CHROMIUM_PATH to use an installed Chromium.
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://localhost:8080';
const API = process.env.API_URL || BASE.replace(/\/$/, '') + '/api';
const fail = m => { console.error('FAIL', m); process.exitCode = 1; };

const browser = await chromium.launch({ headless: process.env.HEADLESS !== '0', executablePath: process.env.CHROMIUM_PATH || undefined,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 844, height: 390 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const calls = [];
page.on('response', r => { const u = new URL(r.url()); if (u.pathname.includes('/v1/')) calls.push(`${r.request().method()} ${u.pathname.replace(/[0-9a-f-]{36}/g, ':id')} ${r.status()}`); });
const errors = []; page.on('pageerror', e => errors.push(e.message));

await page.goto(BASE + '/');
await page.waitForTimeout(1200);
const anon = await page.evaluate(() => window.__ftNet && window.__ftNet.anon);
if (!anon) fail('game is not configured for the backend (window.FT_CONFIG.apiBase missing in config.js)');

// start → rules → employee type → look → play
await page.click('#go-how'); await page.click('#go-role');
await page.click('#roles .opt:nth-child(1)'); await page.click('#go-char'); await page.click('#go-play');

for (let m = 0; m < 7; m++) {
  await page.waitForSelector('#bubble.on', { timeout: 30000 }); await page.waitForTimeout(300);
  await page.click('#bubble'); await page.waitForTimeout(800);
  if (m === 3) { await page.click('#cards .nonet'); continue; }            // one skip
  await page.click('#cards .card:nth-child(1)'); await page.waitForTimeout(500);
  await page.click('#chooseBtn');
  await page.waitForSelector('#decide.on', { timeout: 15000 });
  await page.waitForFunction(() => !document.querySelector('#decide').classList.contains('on'), null, { timeout: 15000 });
}
await page.waitForSelector('#end.on', { timeout: 30000 }); await page.waitForTimeout(2000);

// end of day: decline the add, render, share
if (await page.$('#eGap .gp.add')) { await page.click('#eGap .gp.add'); await page.waitForTimeout(300); await page.click('#eSwap .lk'); }
await page.click('#goRender'); await page.click('#palUse');
await page.waitForSelector('#ePanel.ready', { timeout: 120000 });
await page.click('#goRender'); await page.waitForTimeout(1500);
await page.click('#share'); await page.waitForSelector('#gallery.on', { timeout: 15000 }); await page.waitForTimeout(2000);
const mineOnWall = await page.$('.gc.mine');

// what arrived
const has = re => calls.some(c => re.test(c));
if (!has(/^POST \/(api\/)?v1\/sessions 20\d/)) fail('POST /v1/sessions did not succeed');
if (!has(/^POST .*\/v1\/sessions\/:id\/events 20\d/)) fail('no event batch accepted');
if (!has(/^POST .*\/finish 20\d/)) fail('finish not accepted');
if (!has(/^POST .*\/floor 20\d/)) fail('floor not accepted');
if (!has(/^POST .*\/v1\/wall 20\d/)) fail('wall post not accepted');
if (!has(/^GET .*\/v1\/wall 20\d/)) fail('wall not read');
if (!mineOnWall) fail('own office not shown as "You" on the wall');
const wallRes = await (await fetch(API + '/v1/wall', { headers: { 'X-FT-Player': anon } })).json().catch(() => null);
if (!wallRes || !wallRes.posts || !wallRes.posts.some(p => p.mine)) fail('GET /v1/wall does not return this player\'s post as mine');
if (wallRes && JSON.stringify(wallRes).includes(anon)) fail('wall response leaks the anon player id');
if (errors.length) fail('page errors: ' + errors.join(' | '));

console.log(calls.join('\n'));
console.log(process.exitCode ? 'E2E FAILED' : 'E2E PASSED');
await browser.close();
