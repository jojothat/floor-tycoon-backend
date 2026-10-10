// Records a demo copy of the local production stack: the game being played, the backend console and
// the readout, captured in step. Run by demo.sh, which starts that copy; compose.py stitches the videos.
// Env: OUT (frames and timings), ENV_FILE (the demo copy's settings), PROJ (its compose project),
//      MANAGER and MANAGER_PASSWORD (a readout login from managers.caddy).
import { chromium } from 'playwright';
import http from 'node:http';
import { spawn, execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';

import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';

const HERE = dirname(fileURLToPath(import.meta.url));
const DEPLOY = join(HERE, '..');
const OUT = process.env.OUT || join(HERE, 'out', 'frames');
const ENV_FILE = process.env.ENV_FILE || join(HERE, 'demo.env');
const CA = join(OUT, '..', 'demo-root.crt');
const PILOT_TOKEN = readFileSync(ENV_FILE, 'utf8').match(/^PILOT_TOKEN=(.*)$/m)[1].trim();
const { MANAGER, MANAGER_PASSWORD } = process.env;
if (!MANAGER || !MANAGER_PASSWORD) { console.error('set MANAGER and MANAGER_PASSWORD (a readout login)'); process.exit(2); }
const GAME = 'https://localhost', READOUT = 'https://localhost:8443';
const PROJ = process.env.PROJ || 'floor-tycoon-demo';
const COMPOSE = ['compose', '-p', PROJ, '--env-file', ENV_FILE];
rmSync(OUT, { recursive: true, force: true }); mkdirSync(OUT, { recursive: true });
execFileSync('docker', [...COMPOSE, 'cp', 'caddy:/data/caddy/pki/authorities/local/root.crt', CA], { cwd: DEPLOY, stdio: 'ignore' });  // this stack's own CA
const wait = ms => new Promise(r => setTimeout(r, ms));
const marks = []; const t0 = Date.now(); const mark = m => { marks.push([(Date.now() - t0) / 1000, m]); console.log(`${((Date.now() - t0) / 1000).toFixed(1)}s ${m}`); };

// ------------------------------------------------ the backend console, streamed to a page
const clients = new Set();
const send = line => { for (const c of clients) c.write(`data: ${JSON.stringify(line)}\n\n`); };
const logs = spawn('docker', [...COMPOSE, 'logs', '-f', '--tail', '0', '--no-log-prefix', 'api', 'jobs'], { cwd: DEPLOY });
let buf = '';
logs.stdout.on('data', d => { buf += d; const parts = buf.split('\n'); buf = parts.pop(); parts.forEach(l => l.trim() && send(l)); });
const PAGE = `<!doctype html><meta charset=utf-8><title>Backend console</title><style>
body{margin:0;background:#0d1117;color:#c9d1d9;font:15px/1.45 'DejaVu Sans Mono',monospace;height:100vh;display:flex;flex-direction:column}
header{background:#161b22;border-bottom:1px solid #30363d;padding:12px 16px;color:#e6edf3;font:600 15px 'Inter',sans-serif}
header small{display:block;color:#8b949e;font:13px 'DejaVu Sans Mono',monospace;margin-top:4px}
#log{flex:1;overflow:hidden;padding:10px 16px;white-space:pre-wrap;word-break:break-word}
.http{color:#6e7681;font-size:12.5px}.ev{color:#c9d1d9}.ev .tm{color:#6e7681}
.new{color:#7ee787;font-weight:bold}.fin{color:#f2cc60;font-weight:bold}.sim{color:#79c0ff}.wall{color:#d2a8ff}.ro{color:#ffa657;font-weight:bold}.job{color:#a5d6ff}
#stats{background:#161b22;border-top:1px solid #30363d;padding:8px 16px;color:#8b949e;font-size:13px}
#stats b{color:#e6edf3}</style>
<header>Backend console — Floor Tycoon, local production build<small>$ docker compose logs -f api jobs</small></header>
<div id=log></div><div id=stats>games started <b id=gs>0</b> · reached 18:00 <b id=gf>0</b> · events saved <b id=es>0</b> · readout loads <b id=rl>0</b></div>
<script>
const L=document.getElementById('log'),n={gs:0,gf:0,es:0,rl:0};
const bump=(k,v=1)=>{n[k]+=v;document.getElementById(k).textContent=n[k];};
function add(t){const d=document.createElement('div');let m;
 if(m=t.match(/"(GET|POST) (\\S+) HTTP\\/[\\d.]+" (\\d+)/)){ if(m[2]==='/healthz')return;
   d.className='http';d.textContent='    http  '+m[1].padEnd(4)+' '+m[2].replace(/sessions\\/([\\w-]{6})[\\w-]+/,'sessions/$1…').replace(/\\?.*/,'')+'  → '+m[3];}
 else if(/^\\d\\d:\\d\\d:\\d\\d  /.test(t)){d.className='ev';
   if(/New game started/.test(t)){d.className='new';bump('gs');}
   else if(/Reached 18:00/.test(t)){d.className='fin';bump('gf');}
   else if(/Simulated day results/.test(t))d.className='sim';
   else if(/to the wall/.test(t))d.className='wall';
   else if(/\\[readout\\]/.test(t)){d.className='ro';bump('rl');}
   const s=t.match(/Saved (\\d+) event/);if(s)bump('es',+s[1]);
   d.textContent=t;}
 else if(/rescored|deleted .* events/.test(t)){d.className='job';d.textContent='[jobs] '+t;}
 else return;
 L.appendChild(d);while(L.scrollHeight>L.clientHeight&&L.firstChild)L.removeChild(L.firstChild);}
new EventSource('/stream').onmessage=e=>add(JSON.parse(e.data));
</script>`;
const server = http.createServer((req, res) => {
  if (req.url === '/stream') { res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' }); clients.add(res); req.on('close', () => clients.delete(res)); return; }
  res.writeHead(200, { 'content-type': 'text/html' }); res.end(PAGE);
}).listen(9100);

// ------------------------------------------------ three recorded windows
const browser = await chromium.launch({ args: ['--ignore-certificate-errors', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
// Each window is captured with Chrome's screencast; every frame keeps its wall-clock time, so the three line up.
const casts = {}; const shooting = []; let stopShots = false;
const ctx = async (name, opts) => {
  const c = await browser.newContext({ ...opts, ignoreHTTPSErrors: true }); const p = await c.newPage();
  mkdirSync(`${OUT}/${name}`, { recursive: true }); const frames = casts[name] = [];
  if (opts.shots) {  // the game: Chrome's screencast crops its canvas, so take full screenshots back to back
    shooting.push((async () => { while (!stopShots) {
      const ts = Date.now() / 1000, file = `${OUT}/${name}/${String(frames.length).padStart(6, '0')}.jpg`;
      try { writeFileSync(file, await p.screenshot({ type: 'jpeg', quality: 80 })); frames.push([ts, file]); } catch { break; }
    } })());
    return [c, p];
  }
  const cdp = await c.newCDPSession(p);
  cdp.on('Page.screencastFrame', f => {
    const file = `${OUT}/${name}/${String(frames.length).padStart(6, '0')}.jpg`;
    writeFileSync(file, Buffer.from(f.data, 'base64')); frames.push([f.metadata.timestamp, file]);
    cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
  });
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 85, maxWidth: opts.viewport.width, maxHeight: opts.viewport.height });
  return [c, p];
};
const [cCon, con] = await ctx('console', { viewport: { width: 960, height: 1080 } });
const [cRo, ro] = await ctx('readout', { viewport: { width: 960, height: 636 }, httpCredentials: { username: MANAGER, password: MANAGER_PASSWORD } });
const [cGame, game] = await ctx('game', { viewport: { width: 844, height: 390 }, shots: true });
const errors = []; for (const p of [con, ro, game]) p.on('pageerror', e => errors.push(e.message));
await con.goto('http://localhost:9100/');
await game.goto(GAME + '/');
await ro.goto(READOUT + '/', { waitUntil: 'networkidle' });
mark('readout open: no games yet');
await ro.click('#ctl summary'); await ro.locator('#filters [name=synthetic]').check(); await wait(1500);
await ro.click('#ctl summary');
const refresh = async () => { await ro.evaluate(() => window.scrollTo({ top: 0 })); await ro.evaluate(() => document.getElementById('refresh').click()); await wait(900); };

async function playOne(label) {
  mark(`${label}: start`);
  await game.goto(GAME + '/'); await wait(900);
  await game.click('#go-how'); await wait(300); await game.click('#go-role'); await wait(300);
  await game.click('#roles .opt:nth-child(1)'); await wait(300); await game.click('#go-char'); await wait(300); await game.click('#go-play');
  for (let m = 0; m < 7; m++) {
    await game.waitForSelector('#bubble.on', { timeout: 30000 }); await wait(150);
    await game.click('#bubble'); await wait(500);
    if (m === 3) { await game.click('#cards .nonet'); continue; }
    await game.click(`#cards .card:nth-child(${1 + (m % 2)})`); await wait(300);
    await game.click('#chooseBtn');
    await game.waitForSelector('#decide.on', { timeout: 15000 });
    await game.waitForFunction(() => !document.querySelector('#decide').classList.contains('on'), null, { timeout: 15000 });
  }
  await game.waitForSelector('#end.on', { timeout: 30000 }); await wait(1200);
  mark(`${label}: 18:00`);
  if (await game.$('#eGap .gp.add')) { await game.click('#eGap .gp.add'); await wait(300); await game.click('#eSwap .lk'); }
  await game.click('#goRender'); await game.click('#palUse');
  await game.waitForSelector('#ePanel.ready', { timeout: 120000 });
  await game.click('#goRender'); await wait(1000);
  await game.click('#share'); await game.waitForSelector('#gallery.on', { timeout: 15000 }); await wait(1500);
  mark(`${label}: shared to the wall`);
}

await wait(1500);
await playOne('UI game 1');
await refresh(); mark('readout refreshed after game 1'); await wait(3000);

// a burst of games through the same game API, as phones would send them (labelled synthetic-seed)
mark('burst: 14 more games through the game API');
const seed = spawn('node', [join(DEPLOY, '..', '..', 'readout', 'tools', 'seed.mjs')], {
  env: { ...process.env, API: GAME + '/api', N: '14', PILOT_TOKEN, NODE_EXTRA_CA_CERTS: CA } });
let seedOut = ''; seed.stdout.on('data', d => seedOut += d); seed.stderr.on('data', d => seedOut += d);
const seedDone = new Promise(r => seed.on('close', r));
while (seed.exitCode === null) { await refresh(); await wait(1200); }
await seedDone; mark('burst done: ' + seedOut.trim().split('\n').pop());
if (!/failed: 0/.test(seedOut)) { console.error(seedOut); process.exit(1); }
await refresh(); await wait(2500);

// a second real game while a manager reads the slides
mark('UI game 2 while the readout is read');
const reading = (async () => { for (let i = 0; i < 26; i++) { await ro.mouse.wheel(0, 520); await wait(1100); } })();
await playOne('UI game 2');
await reading;
await refresh(); mark('readout refreshed: count includes game 2'); await wait(4000);

const facts = await ro.locator('.facts').innerText();
mark('readout header: ' + facts.replace(/\s+/g, ' '));
writeFileSync(OUT + '/marks.json', JSON.stringify(marks, null, 1));
const end = Date.now() / 1000;
stopShots = true; await Promise.all(shooting);
await cGame.close(); await cRo.close(); await cCon.close(); await browser.close();
// ffmpeg concat lists: each frame shown until the next one, all starting at the same moment
const start = Math.min(...Object.values(casts).map(f => f[0][0]));
for (const [n, frames] of Object.entries(casts)) {
  const rows = [];
  frames.forEach(([ts, file], i) => {
    const from = i === 0 ? start : ts, to = i + 1 < frames.length ? frames[i + 1][0] : end;
    rows.push(`file '${file}'`, `duration ${Math.max(0.001, to - from).toFixed(3)}`);
  });
  rows.push(`file '${frames.at(-1)[1]}'`);
  writeFileSync(`${OUT}/${n}.txt`, rows.join('\n') + '\n');
}
writeFileSync(OUT + '/timing.json', JSON.stringify({ start, end, t0: t0 / 1000 }));
logs.kill(); server.close();
console.log(errors.length ? 'PAGE ERRORS: ' + errors.join(' | ') : 'no page errors');
console.log('DEMO RECORDED');
