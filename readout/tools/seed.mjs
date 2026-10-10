// Plays N synthetic games through the game's own API (the same calls the phone makes), so the read API
// and the dashboard have something to show before the pilot. Every session is tagged game_version
// "synthetic-seed" and the dashboard labels it; the read API leaves synthetic plays out unless asked.
//   API=http://localhost:8787 N=300 node tools/seed.mjs
// Never run this against the production database.
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const here = dirname(fileURLToPath(import.meta.url));
const API = (process.env.API || 'http://localhost:8787').replace(/\/$/, '');
const N = +process.env.N || 300;
const SCEN = JSON.parse(readFileSync(process.env.SCENARIOS || join(here, 'scenarios.json'), 'utf8'));
const ITEMS = JSON.parse(readFileSync(process.env.ITEMS || join(here, 'items.json'), 'utf8')).filter(i => !/gowning|airshower|ppevend/.test(i.id));
const PALETTES = ['Warm Minimal', 'Scandi Light', 'Japandi', 'Biophilic Calm', 'Soft Greige', 'Terracotta Studio', 'Midnight Brass', 'Industrial Loft', 'Mid-century Pop', 'Colour-block Tech'];
// most additions land on a short list, so the additions card shows a readable top 10 (players still pick anything)
const FAV = ['phonebooth', 'focuspod', 'pantry', 'nappod', 'greenwall', 'lockers', 'cafebar', 'huddleroom', 'videoroom', 'meditation', 'duobooth', 'massage', 'lights', 'baffles'];
const TYPE_MIX = [['CE', 30], ['PSE', 18], ['IE', 11], ['Sales', 13], ['Support', 16], ['Lead', 8], ['none', 4]];

// a small seeded random so every run makes the same pattern of preferences (players still vary)
let seed = +process.env.SEED || 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const pick = (arr, w) => { const t = w.reduce((a, b) => a + b, 0); let r = rnd() * t; for (let i = 0; i < arr.length; i++) { r -= w[i]; if (r <= 0) return arr[i]; } return arr[arr.length - 1]; };
const pref = {}; // (type|scenario) → weights per option, so roles disagree in a readable way
const weightsFor = (type, d) => { const k = type + '|' + d.id; if (!pref[k]) { const base = pref['*|' + d.id] || (pref['*|' + d.id] = d.options.map(() => .4 + rnd()));
  pref[k] = base.map(w => Math.max(.05, w * (.55 + rnd() * .9))); } return pref[k]; };
const skipRate = {}; const skipOf = d => skipRate[d.id] ?? (skipRate[d.id] = .03 + rnd() * .14);
const slow = {}; const slowOf = d => slow[d.id] ?? (slow[d.id] = 3 + rnd() * 9);

async function post(path, body, key, anon) {
  const r = await fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-FT-Player': anon, ...(key ? { Authorization: 'Bearer ' + key } : {}) }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(path + ' ' + r.status + ' ' + await r.text());
  return r.json().catch(() => ({}));
}

async function playOne(i) {
  const anon = randomUUID(), type = pick(TYPE_MIX.map(x => x[0]), TYPE_MIX.map(x => x[1]));
  const s = await post('/v1/sessions', { client_session_id: randomUUID(), anon_player_id: anon, invite_code: null, game_version: 'synthetic-seed', flow: 'quick', role_type: type, language: 'ko-KR', device_class: 'phone', viewport_h: 390, viewport_w: 844 }, null, anon);
  const deal = s.deal; const key = s.write_key; let seq = 0, t = 0; const ev = []; const E = e => ev.push({ ...e, seq: ++seq, t: Math.round(t) });
  E({ ev: 'deal', ids: deal, src: 'server' }); E({ ev: 'role_set', type });
  const scen = deal.map(id => SCEN.find(d => d.id === id)).sort((a, b) => a.time.localeCompare(b.time));
  const picks = {}, skips = {};
  const quitAt = rnd() < .12 ? Math.floor(rnd() * 7) : 99;          // some leave before 18:00
  for (let m = 0; m < scen.length; m++) {
    if (m >= quitAt) break;
    const d = scen[m]; t += 6000 + rnd() * 4000; E({ ev: 'answer', m: d.zone });
    if (rnd() < skipOf(d)) { t += 1500 + rnd() * 3000; E({ ev: 'skip', m: d.zone, why: 'none', rethinks: 0 }); picks[d.zone] = '0'; skips[d.zone] = 'none'; continue; }
    const w = weightsFor(type, d); const ch = pick(d.options, w);
    const nPrev = 1 + Math.floor(rnd() * Math.min(3, d.options.length));
    const previews = [...new Set([...Array(nPrev)].map(() => pick(d.options, w).k).concat(ch.k))];
    for (const k of previews) { t += 900 + rnd() * 1800; E({ ev: 'preview', m: d.zone, opt: k }); }
    t += slowOf(d) * 1000 * (.5 + rnd()); E({ ev: 'choose', m: d.zone, opt: ch.k }); t += 2600; E({ ev: 'keep', m: d.zone, opt: ch.k, rethinks: 0 });
    picks[d.zone] = ch.k;
  }
  if (quitAt < 99) { await post(`/v1/sessions/${s.session_id}/events`, { events: ev }, key, anon); return 'quit'; }
  // end of day: an edit now and then, then one addition or a decline
  if (rnd() < .24) { const d = rnd() < .35 ? (scen.find(x => x.zone === 'z7') || scen[0]) : scen[Math.floor(rnd() * scen.length)]; const w = weightsFor(type, d); const alt = d.options.map((o, i) => [o.k, w[i]]).filter(([k]) => k !== picks[d.zone]).sort((a, b) => b[1] - a[1])[0][0]; if (alt !== picks[d.zone]) { t += 4000; E({ ev: 'replay', m: d.zone }); t += 9000; E({ ev: 'edit', m: d.zone, from: picks[d.zone], to: alt }); picks[d.zone] = alt; delete skips[d.zone]; } }
  await post(`/v1/sessions/${s.session_id}/finish`, { picks, skips }, key, anon);
  let addition = { adds: {}, deskfit: null, swaps: {} };
  const r = rnd();
  if (r < .58) { const fav = ITEMS.filter(i => FAV.includes(i.id)); const it = rnd() < .85 ? fav[Math.floor(Math.pow(rnd(), 1.3) * fav.length)] : ITEMS[Math.floor(rnd() * ITEMS.length)]; const how = pick(['gap', 'end', 'desks_smaller', 'desks_share', 'swap'], [35, 35, 12, 8, 10]);
    const zone = how.startsWith('desks') ? 'z7' : scen[Math.floor(rnd() * scen.length)].zone; t += 3000; E({ ev: 'addOpen' });
    if (how.startsWith('desks') || how === 'swap') E({ ev: 'addNeedsRoom', item: it.id });
    E({ ev: 'add', m: zone, item: it.id, ...(how === 'gap' ? {} : { via: 'end' }), ...(how.startsWith('desks') ? { desks: how.slice(6) } : {}), ...(how === 'swap' ? { swap: picks[zone] || 'a' } : {}) });
    addition = { adds: { [zone]: it.id }, deskfit: how.startsWith('desks') ? { mode: how.slice(6), add: it.id, k: picks.z7 } : null, swaps: how === 'swap' ? { [zone]: it.id } : {} };
  } else if (r < .83) { t += 2000; E({ ev: 'addOpen' }); E({ ev: 'addSkip' }); }
  const pal = Math.floor(Math.pow(rnd(), 1.4) * PALETTES.length); t += 5000; E({ ev: 'palette_set', palette: pal, name: PALETTES[pal] });
  const removes = scen.filter(d => { const o = d.options.find(o => o.k === picks[d.zone]); return o && o.removes; }).length;
  const sim = { use: Math.round(9 + rnd() * 14), happy: Math.round(Math.max(10, Math.min(95, 78 - removes * 6 + (rnd() - .5) * 30))), desk_use: Math.round(25 + rnd() * 60), desks: picks.z7 === 'a' ? 12 : picks.z7 === 'b' ? 10 : 6, peak: 12 + Math.floor(rnd() * 3) };
  t += 20000; E({ ev: 'sim_done', use: sim.use, happy: sim.happy, deskU: sim.desk_use, peak: sim.peak });
  await post(`/v1/sessions/${s.session_id}/floor`, { palette: pal, palette_name: PALETTES[pal], picks, skips, addition, sim }, key, anon);
  if (rnd() < .7) { t += 4000; E({ ev: 'render_view' }); }
  let shared = false; if (rnd() < .4) { t += 3000; E({ ev: 'wall_post' }); shared = true; }
  await post(`/v1/sessions/${s.session_id}/events`, { events: ev }, key, anon);
  if (shared) await post('/v1/wall', { session_id: s.session_id }, key, anon).catch(() => {});
  return 'done';
}

const res = { done: 0, quit: 0, failed: 0 };
// after the plays: some players open other offices on the wall (views)
async function viewWall(k) {
  for (let j = 0; j < k; j++) { const anon = randomUUID();
    const w = await (await fetch(API + '/v1/wall', { headers: { 'X-FT-Player': anon } })).json().catch(() => ({ posts: [] }));
    for (const p of (w.posts || [])) if (rnd() < .08) await fetch(API + `/v1/wall/${p.id}/view`, { method: 'POST', headers: { 'X-FT-Player': anon } }).catch(() => {}); }
}
for (let i = 0; i < N; i++) { try { res[await playOne(i)]++; } catch (e) { res.failed++; if (res.failed < 3) console.error(e.message); } }
await viewWall(Math.round(N / 3));
console.log(`synthetic plays sent to ${API}:`, res);
