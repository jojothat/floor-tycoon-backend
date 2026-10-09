// Floor Tycoon mock API: a reference for the contract the game calls.
// Zero dependencies (Node 18+). In-memory only, never for real data.
//   node mock-api/server.mjs                     → API on :8787 (routes /v1/... and /api/v1/...)
//   STATIC_DIR=public node mock-api/server.mjs   → also serves the game, so one port runs everything
// Env: PORT (8787), STATIC_DIR, SCENARIOS (../sample/scenarios.json or ./scenarios.json)
import http from 'node:http';
import { readFileSync, existsSync, statSync, createReadStream } from 'node:fs';
import { join, extname, normalize, dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const PORT = +process.env.PORT || 8787;
const STATIC = process.env.STATIC_DIR ? normalize(process.env.STATIC_DIR) : null;
const scenPath = [process.env.SCENARIOS, join(here, 'scenarios.json'), join(here, '../sample/scenarios.json')].find(p => p && existsSync(p));
const SCEN = scenPath ? JSON.parse(readFileSync(scenPath, 'utf8')) : [];
const MIN_GROUP = 10;
const TYPES = ['CE', 'PSE', 'IE', 'Sales', 'Support', 'Lead', 'none'];
const ROLECODE = { CE: 'CE', PSE: 'PSE', IE: 'INS', Sales: 'SAL', Support: 'SUP', Lead: 'LEAD' };

// ---- store ----
const sessions = new Map();   // id → {id, anon, key, body, events: Map(seq→event), finish, floor, completedAt}
const wall = new Map();       // anon → post
const views = new Set();      // viewerAnon|postId

// ---- the default deal (spec §2) ----
function deal(type) {
  const code = ROLECODE[type] || null, R = Math.random;
  const w = d => { let x = 1; if (code) { const roles = String(d.roles || 'ALL'); if (roles === 'ALL') x = 1.5; else if (roles.split(';').includes(code)) x = 3; else x = .35; } if (d.tier === 'Core') x *= 2; if (d.native) x *= 1.3; return x; };
  const byId = id => SCEN.find(d => d.id === id);
  if (!byId(1)) return null;
  const pick = { z7: 1 }, used = new Set([byId(1).theme]);
  if ((code === 'CE' || code === 'INS') && byId(30)) { pick.z6 = 30; used.add(byId(30).theme); }
  if (code === 'LEAD' && byId(56)) { pick.z6 = 56; used.add(byId(56).theme); }
  for (const z of ['z1', 'z2', 'z3', 'z4', 'z5', 'z6'].sort(() => R() - .5)) {
    if (pick[z]) continue;
    let pool = SCEN.filter(d => d.zone === z && !used.has(d.theme)); if (!pool.length) pool = SCEN.filter(d => d.zone === z);
    const tot = pool.reduce((s, d) => s + w(d), 0); let r = R() * tot, ch = pool[0];
    for (const d of pool) { r -= w(d); if (r <= 0) { ch = d; break; } }
    pick[z] = ch.id; used.add(ch.theme);
  }
  return Object.values(pick);
}

// ---- helpers ----
const send = (res, code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(obj === undefined ? '' : JSON.stringify(obj)); };
const body = req => new Promise((ok, bad) => { let b = ''; req.on('data', c => { b += c; if (b.length > 65536) { bad(new Error('too big')); req.destroy(); } }); req.on('end', () => { try { ok(b ? JSON.parse(b) : {}); } catch (e) { bad(e); } }); });
const auth = (req, s) => s && req.headers.authorization === 'Bearer ' + s.key;
const anonOf = req => String(req.headers['x-ft-player'] || '');

function games() {
  const done = [...sessions.values()].filter(s => s.completedAt);
  const cnt = {}; for (const s of done) { const t = TYPES.includes(s.body.role_type) ? s.body.role_type : 'none'; cnt[t] = (cnt[t] || 0) + 1; }
  const out = { total: done.length }; let other = 0;
  for (const [t, n] of Object.entries(cnt)) { if (n >= MIN_GROUP && t !== 'none') out[t] = n; else other += n; }
  if (other) out.other = other;   // the group-of-10 rule: small types are merged
  return out;
}

async function api(req, res, path) {
  const m = req.method;
  let x;
  if (m === 'POST' && path === '/v1/sessions') {
    const b = await body(req);
    const id = b.client_session_id || randomUUID();
    let s = sessions.get(id);
    if (!s) { s = { id, anon: b.anon_player_id || anonOf(req), key: randomUUID(), body: b, events: new Map(), deal: deal(b.role_type), createdAt: Date.now() }; sessions.set(id, s); }
    return send(res, 201, { session_id: s.id, deal: s.deal, write_key: s.key });
  }
  if ((x = path.match(/^\/v1\/sessions\/([^/]+)\/(events|finish|floor)$/)) && m === 'POST') {
    const s = sessions.get(decodeURIComponent(x[1]));
    if (!s) return send(res, 404, { error: 'no such session' });
    if (!auth(req, s)) return send(res, 401, { error: 'bad write key' });
    const b = await body(req);
    if (x[2] === 'events') {
      const evs = Array.isArray(b.events) ? b.events : []; let added = 0;
      for (const e of evs) { if (typeof e.seq !== 'number') continue; if (!s.events.has(e.seq)) { s.events.set(e.seq, { ...e, server_t: Date.now() }); added++; } }
      const seqs = evs.map(e => e.seq).filter(n => typeof n === 'number');
      return send(res, 200, { accepted: seqs.length ? [Math.min(...seqs), Math.max(...seqs)] : null, new: added });
    }
    if (x[2] === 'finish') { s.finish = b; s.completedAt = s.completedAt || Date.now(); return send(res, 200, { ok: true }); }
    if (x[2] === 'floor') { s.floor = b; return send(res, 200, { ok: true, quality: null }); }
  }
  if (m === 'POST' && path === '/v1/wall') {
    const b = await body(req); const s = sessions.get(b.session_id);
    if (!s) return send(res, 404, { error: 'no such session' });
    if (!auth(req, s)) return send(res, 401, { error: 'bad write key' });
    if (!s.floor) return send(res, 409, { error: 'floor not sent yet' });
    const f = s.floor, add = f.addition || {};
    const post = { id: randomUUID(), anon: s.anon, session_id: s.id, deal: s.deal || (s.events.get(1) || {}).ids, picks: f.picks, skips: f.skips || {}, adds: add.adds || {}, deskfit: add.deskfit || null, swaps: add.swaps || {}, pal: f.palette, use: f.sim && f.sim.use, happy: f.sim && f.sim.happy, ts: Date.now() };
    const prev = wall.get(s.anon); if (prev) for (const v of [...views]) if (v.endsWith('|' + prev.id)) views.delete(v);
    wall.set(s.anon, post);
    return send(res, 200, { ok: true, id: post.id });
  }
  if (m === 'GET' && path === '/v1/wall') {
    const me = anonOf(req);
    const posts = [...wall.values()].sort((a, b) => b.ts - a.ts).slice(0, 60).map(p => ({
      id: p.id, mine: p.anon === me, deal: p.deal, picks: p.picks, skips: p.skips, adds: p.adds, deskfit: p.deskfit, swaps: p.swaps,
      pal: p.pal, use: p.use, happy: p.happy, views: [...views].filter(v => v.endsWith('|' + p.id)).length, ts: p.ts }));   // no anon ids leave the server
    return send(res, 200, { posts, games: games() });
  }
  if ((x = path.match(/^\/v1\/wall\/([^/]+)\/view$/)) && m === 'POST') {
    const id = decodeURIComponent(x[1]), me = anonOf(req), p = [...wall.values()].find(q => q.id === id);
    if (!p) return send(res, 404, { error: 'no such post' });
    if (me && p.anon !== me) views.add(me + '|' + id);
    return send(res, 200, { ok: true });
  }
  if (m === 'GET' && path === '/debug/sessions') {   // mock only: lets tests check what arrived
    return send(res, 200, [...sessions.values()].map(s => ({ id: s.id, role_type: s.body.role_type, flow: s.body.flow, deal: s.deal, events: s.events.size,
      types: [...new Set([...s.events.values()].map(e => e.ev))], finished: !!s.completedAt, floor: !!s.floor })));
  }
  if (m === 'GET' && path === '/health') return send(res, 200, { ok: true, scenarios: SCEN.length });
  return send(res, 404, { error: 'not found' });
}

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.json': 'application/json', '.webp': 'image/webp', '.png': 'image/png', '.css': 'text/css' };
function serveStatic(req, res, path) {
  const p = normalize(join(STATIC, path === '/' ? 'index.html' : decodeURIComponent(path)));
  if (!p.startsWith(STATIC) || !existsSync(p) || !statSync(p).isFile()) return send(res, 404, { error: 'not found' });
  res.writeHead(200, { 'Content-Type': MIME[extname(p)] || 'application/octet-stream' }); createReadStream(p).pipe(res);
}

http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x'); let path = url.pathname;
  res.setHeader('Access-Control-Allow-Origin', '*'); res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-FT-Player'); res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  if (req.method === 'OPTIONS') return send(res, 204);
  try {
    if (path.startsWith('/api/')) path = path.slice(4);
    if (path.startsWith('/v1/') || path.startsWith('/debug/') || path === '/health') return await api(req, res, path);
    if (STATIC) return serveStatic(req, res, path);
    return send(res, 404, { error: 'not found' });
  } catch (e) { return send(res, 400, { error: String(e.message || e) }); }
}).listen(PORT, () => console.log(`mock api on :${PORT}${STATIC ? ' + game from ' + STATIC : ''} · ${SCEN.length} scenarios`));
