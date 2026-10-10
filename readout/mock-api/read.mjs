// Management read endpoints for the mock API: the reference for what the Space Readout dashboard expects.
// Computed live from the sessions the game sent, so a real play shows up straight away.
// Rules: only completed games count for preferences; any group under MIN_GROUP comes back as null with lt10:true.
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const MIN = 10;
const itemsPath = [process.env.ITEMS, join(here, 'items.json')].find(p => p && existsSync(p));
const ITEMS = itemsPath ? Object.fromEntries(JSON.parse(readFileSync(itemsPath, 'utf8')).map(i => [i.id, i])) : {};
const TYPES = ['CE', 'PSE', 'IE', 'Sales', 'Support', 'Lead'];
const palPath = [process.env.PALETTES, join(here, 'palettes.json')].find(p => p && existsSync(p));
const PALETTES = palPath ? JSON.parse(readFileSync(palPath, 'utf8')) : [];
const ZONE_HOUR = t => +String(t || '0').split(':')[0];
const pct = (a, b) => (b >= MIN ? Math.round(a / b * 1000) / 10 : null);
const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const sup = (n, obj) => (n >= MIN ? obj : { n, lt10: true });

// one tidy record per session, derived from its raw events (what the real backend's derived tables hold)
function derive(s, SCEN) {
  const evs = [...s.events.values()].sort((a, b) => a.seq - b.seq);
  const deal = s.deal || (evs.find(e => e.ev === 'deal') || {}).ids || [];
  const sc = deal.map(id => SCEN.find(d => d.id === id)).filter(Boolean);
  const byZone = Object.fromEntries(sc.map(d => [d.zone, d]));
  const moments = {};
  for (const d of sc) moments[d.zone] = { id: d.id, zone: d.zone, time: d.time, previews: [], answerT: null, chooseT: null, first: null };
  let editsDone = 0;
  const edits = [], adds = [], removed = new Set(); let declined = false, howLast = null;
  for (const e of evs) {
    const m = e.m && moments[e.m];
    if (e.ev === 'answer' && m && m.answerT == null) m.answerT = e.t;
    if (e.ev === 'preview' && m && m.first == null) m.previews.push(e.opt);
    if (e.ev === 'choose' && m && m.first == null) m.chooseT = e.t;
    if ((e.ev === 'keep' || e.ev === 'skip') && m && m.first == null) m.first = e.ev === 'skip' ? 'skip' : e.opt;
    if (e.ev === 'edit') { edits.push({ zone: e.m, from: e.from, to: e.to }); editsDone++; }
    if (e.ev === 'add') { adds.push({ zone: e.m, item: e.item, how: e.desks ? 'desks_' + e.desks : e.swap ? 'swap' : e.via === 'end' ? 'end' : 'gap' }); }
    if (e.ev === 'addRemoved') removed.add(e.m);
    if (e.ev === 'addSkip' || e.ev === 'gapLeave') declined = true;
  }
  const picks = (s.floor && s.floor.picks) || (s.finish && s.finish.picks) || {};
  const finalOf = z => (picks[z] && picks[z] !== '0' ? picks[z] : 'skip');
  const add = adds.filter(a => !removed.has(a.zone)).pop() || null;
  const sim = (s.floor && s.floor.sim) || null;
  const played = Object.values(moments).filter(m => m.first != null).length;
  const evTypes = new Set(evs.map(e => e.ev));
  return {
    id: s.id, type: TYPES.includes(s.body.role_type) ? s.body.role_type : 'none', version: s.body.game_version || 'dev', flow: s.body.flow || 'quick',
    created: s.createdAt, completed: !!s.completedAt, synthetic: /^synthetic/.test(s.body.game_version || ''),
    playSec: evs.length ? Math.round((evs[evs.length - 1].t || 0) / 1000) : 0,
    moments: Object.values(moments), byZone, finalOf, edits, add, declined, sim, palette: s.floor && s.floor.palette_name, played,
    reached: { started: true, role: evTypes.has('role_set') || !!s.body.role_type, end: !!s.completedAt, palette: evTypes.has('palette_set'), render: evTypes.has('render_view'), wall: !!s._posted || evTypes.has('wall_post') }
  };
}

function filterOf(url) {
  const q = url.searchParams;
  return r => (!q.get('type') || r.type === q.get('type')) && (!q.get('flow') || r.flow === q.get('flow')) && (!q.get('version') || r.version === q.get('version'))
    && (!q.get('from') || r.created >= Date.parse(q.get('from'))) && (!q.get('to') || r.created <= Date.parse(q.get('to')) + 864e5)
    && (q.get('synthetic') === 'include' || q.get('synthetic') === 'only' ? (q.get('synthetic') !== 'only' || r.synthetic) : !r.synthetic);
}

export function readApi(path, url, ctx) {
  const { sessions, SCEN, wall, views } = ctx;
  const all = [...sessions.values()].map(s => derive(s, SCEN));
  const R = all.filter(filterOf(url));
  const done = R.filter(r => r.completed);
  const N = done.length;
  const base = { n: N, updated_at: new Date().toISOString() };
  // started minus completed would show the unfinished games, so a gap under 10 hides it
  const started = R.length - N > 0 && R.length - N < MIN ? null : R.length;
  const name = (id, k) => { const d = SCEN.find(x => x.id === id); const o = d && d.options.find(o => o.k === k); return k === 'skip' ? 'Skipped' : (o && o.name) || k; };
  const route = path.replace('/v1/read/', '');

  if (route === 'meta') return { ...base, n_all: all.length, versions: [...new Set(all.map(r => r.version))], synthetic_in_store: all.filter(r => r.synthetic).length,
    types: TYPES, palettes: PALETTES,
    scenarios: SCEN.map(d => ({ id: d.id, zone: d.zone, time: d.time, question: d.question, theme: d.theme, native: !!d.native, options: d.options.map(o => ({ k: o.k, name: o.name, item: o.item || null })) })) };

  if (route === 'summary') {
    const byType = {}; for (const t of [...TYPES, 'none']) { const n = done.filter(r => r.type === t).length; byType[t] = n >= MIN ? n : null; }
    return { ...base, started, completed: N, completion: R.length >= MIN && started != null ? Math.round(N / R.length * 1000) / 10 : null,
      median_play_s: N >= MIN ? median(done.map(r => r.playSec)) : null, by_type: byType, synthetic: done.filter(r => r.synthetic).length };
  }

  if (route === 'funnel') {
    const steps = [['started', 'Started a game'], ['role', 'Chose a job type']];
    for (let k = 1; k <= 7; k++) steps.push(['m' + k, `Played moment ${k}`]);
    steps.push(['end', 'Reached 18:00'], ['palette', 'Picked a palette'], ['render', 'Opened the render'], ['wall', 'Shared to the wall']);
    const cnt = key => R.filter(r => key[0] === 'm' ? r.played >= +key.slice(1) : r.reached[key]).length;
    // a step under 10, or within 10 of the starts or of the finished count (n on every response), hides its count
    const near = (a, b) => a !== b && Math.abs(a - b) < MIN;
    const step = (key, label) => { const n = cnt(key);
      return n < MIN ? { key, label, n: null, lt10: true } : near(R.length, n) ? { key, label, n: null, dropped_lt10: true }
        : near(n, N) ? { key, label, n: null, near_lt10: true } : { key, label, n }; };
    return { n: started, updated_at: base.updated_at, steps: R.length >= MIN ? steps.map(([key, label]) => step(key, label)) : null, lt10: R.length < MIN || undefined };
  }

  // per scenario: what people chose (final pick), how split they were, how long they took
  const perScenario = () => {
    const out = [];
    for (const d of SCEN) {
      const rows = done.filter(r => r.byZone[d.zone] && r.byZone[d.zone].id === d.id);
      const n = rows.length; if (!n) continue;
      const counts = {}; for (const r of rows) { const f = r.finalOf(d.zone); counts[f] = (counts[f] || 0) + 1; }
      const opts = d.options.map(o => ({ k: o.k, name: o.name, share: pct(counts[o.k] || 0, n) }));
      const top = Math.max(0, ...d.options.map(o => counts[o.k] || 0));
      const times = rows.map(r => { const m = r.moments.find(m => m.zone === d.zone); return m && m.answerT != null && m.chooseT != null ? (m.chooseT - m.answerT) / 1000 : null; }).filter(x => x != null);
      const edits = rows.filter(r => r.edits.some(e => e.zone === d.zone));
      out.push({ id: d.id, zone: d.zone, time: d.time, question: d.question, theme: d.theme, n, lt10: n < MIN || undefined,
        options: opts, skip_share: pct(counts.skip || 0, n), top_share: pct(top, n), median_decide_s: n >= MIN ? median(times) : null,
        edit_share: pct(edits.length, n), _rows: rows });
    }
    return out;
  };

  if (route === 'picks') return { ...base, scenarios: perScenario().map(({ _rows, ...s }) => s) };
  if (route === 'skips') return { ...base, rows: perScenario().filter(s => !s.lt10).map(s => ({ id: s.id, question: s.question, time: s.time, n: s.n, skip_share: s.skip_share })).sort((a, b) => b.skip_share - a.skip_share) };
  if (route === 'edits') return { ...base, rows: perScenario().filter(s => !s.lt10).map(s => {
      const sw = {}; for (const r of s._rows) for (const e of r.edits.filter(e => e.zone === s.zone)) { const k = e.from + '>' + e.to; sw[k] = (sw[k] || 0) + 1; }
      const top = Object.entries(sw).sort((a, b) => b[1] - a[1])[0];
      return { id: s.id, question: s.question, time: s.time, n: s.n, edit_share: s.edit_share, top_switch: top && top[1] >= MIN ? { from: name(s.id, top[0].split('>')[0]), to: name(s.id, top[0].split('>')[1]), from_k: top[0].split('>')[0], to_k: top[0].split('>')[1], n: top[1] } : null };
    }).sort((a, b) => b.edit_share - a.edit_share), any_share: pct(done.filter(r => r.edits.length).length, N),
    switches: (() => { const c = {}; for (const r of done) for (const e of r.edits) { const d = r.byZone[e.zone]; if (d) { const k = d.id + ':' + e.from + '>' + e.to; c[k] = (c[k] || 0) + 1; } }
      return Object.entries(c).filter(([, n]) => n >= MIN).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, n]) => { const [id, sw] = k.split(':'), [f, t] = sw.split('>'), d = SCEN.find(x => x.id === +id);
        return { id: +id, question: d && d.question, from: { k: f, name: name(+id, f) }, to: { k: t, name: name(+id, t) }, n, share: pct(n, N) }; }); })() };

  if (route === 'additions') {
    const withAdd = done.filter(r => r.add), cnt = {}, how = {};
    for (const r of withAdd) { cnt[r.add.item] = (cnt[r.add.item] || 0) + 1; how[r.add.how] = (how[r.add.how] || 0) + 1; }
    return { ...base, added_share: pct(withAdd.length, N), declined_share: pct(done.filter(r => r.declined && !r.add).length, N),
      items: Object.entries(cnt).filter(([, n]) => n >= MIN).sort((a, b) => b[1] - a[1]).slice(0, 15).map(([id, n]) => ({ id, name: (ITEMS[id] || {}).name || id, cat: (ITEMS[id] || {}).cat || null, n, share: pct(n, N) })),
      other_items_lt10: Object.values(cnt).filter(n => n < MIN).reduce((a, b) => a + b, 0),
      how: Object.fromEntries(['gap', 'end', 'desks_smaller', 'desks_share', 'swap'].map(k => [k, pct(how[k] || 0, withAdd.length)])) };
  }

  if (route === 'sharing') {
    const d1 = SCEN.find(d => d.id === 1), by = {};
    for (const t of [...TYPES, 'all']) { const rows = done.filter(r => t === 'all' || r.type === t); const n = rows.length;
      by[t] = sup(n, { n, ...Object.fromEntries([...d1.options.map(o => o.k), 'skip'].map(k => [k, pct(rows.filter(r => r.finalOf('z7') === k).length, n)])) }); }
    const fab = SCEN.find(d => d.id === 30), fabBy = {};
    if (fab) for (const t of ['CE', 'IE', 'all']) { const rows = done.filter(r => (t === 'all' || r.type === t) && r.byZone.z6 && r.byZone.z6.id === 30); const n = rows.length;
      fabBy[t] = sup(n, { n, ...Object.fromEntries([...fab.options.map(o => o.k), 'skip'].map(k => [k, pct(rows.filter(r => r.finalOf('z6') === k).length, n)])) }); }
    const room = done.filter(r => r.add && /^desks_|swap/.test(r.add.how)), rn = room.length;
    return { ...base, team_desks: { options: d1.options.map(o => ({ k: o.k, name: o.name })), by_type: by },
      fab_days: fab ? { options: fab.options.map(o => ({ k: o.k, name: o.name })), by_type: fabBy } : null,
      making_room: sup(rn, { n: rn, desks_smaller: pct(room.filter(r => r.add.how === 'desks_smaller').length, rn), desks_share: pct(room.filter(r => r.add.how === 'desks_share').length, rn), swap: pct(room.filter(r => r.add.how === 'swap').length, rn) }) };
  }

  if (route === 'pain-hours') {
    const ps = perScenario().filter(s => !s.lt10), hours = {};
    for (const s of ps) { const h = ZONE_HOUR(s.time); (hours[h] = hours[h] || []).push(s); }
    const rows = Object.entries(hours).map(([h, ss]) => { const dec = median(ss.map(s => s.median_decide_s).filter(x => x != null)) || 0;
      const sk = ss.reduce((a, s) => a + (s.skip_share || 0), 0) / ss.length, ed = ss.reduce((a, s) => a + (s.edit_share || 0), 0) / ss.length;
      return { h: +h, scenarios: ss.length, decide_s: dec, skip_share: Math.round(sk * 10) / 10, edit_share: Math.round(ed * 10) / 10, top: ss.map(s => s.question).slice(0, 2) }; }).sort((a, b) => a.h - b.h);
    const mx = k => Math.max(1, ...rows.map(r => r[k]));
    rows.forEach(r => r.index = Math.round(100 * (r.decide_s / mx('decide_s') * .4 + r.skip_share / mx('skip_share') * .35 + r.edit_share / mx('edit_share') * .25)));
    return { ...base, hours: rows };
  }

  if (route === 'bundles') {
    const keyOf = (r, z) => { const m = r.byZone[z]; const f = r.finalOf(z); return m && f !== 'skip' ? m.id + ':' + f : null; };
    const single = {}, pair = {};
    for (const r of done) { const ks = Object.keys(r.byZone).map(z => keyOf(r, z)).filter(Boolean).sort();
      ks.forEach(k => single[k] = (single[k] || 0) + 1); for (let i = 0; i < ks.length; i++) for (let j = i + 1; j < ks.length; j++) { const p = ks[i] + '|' + ks[j]; pair[p] = (pair[p] || 0) + 1; } }
    const pairs = Object.entries(pair).filter(([, n]) => n >= MIN).map(([p, n]) => { const [a, b] = p.split('|'); const lift = (n / N) / ((single[a] / N) * (single[b] / N));
      const lab = k => { const [id, opt] = k.split(':'); return { id: +id, k: opt, name: name(+id, opt) }; }; return { a: lab(a), b: lab(b), n_both: n, lift: Math.round(lift * 100) / 100 }; })
      .sort((x, y) => y.lift - x.lift).slice(0, 12);
    return { ...base, pairs };
  }

  if (route === 'space-brief') {
    const cats = {};
    for (const r of done) { const have = new Set();
      for (const z of Object.keys(r.byZone)) { const d = r.byZone[z], f = r.finalOf(z); const o = d.options.find(o => o.k === f); (o && o.items || []).forEach(i => ITEMS[i] && have.add(ITEMS[i].cat)); }
      if (r.add && ITEMS[r.add.item]) have.add(ITEMS[r.add.item].cat);
      have.forEach(c => cats[c] = (cats[c] || 0) + 1); }
    return { ...base, categories: Object.entries(cats).map(([cat, n]) => ({ cat, share_of_floors: pct(n, N) })).sort((a, b) => (b.share_of_floors || 0) - (a.share_of_floors || 0)) };
  }

  if (route === 'sim') {
    const agg = rows => { const s = rows.filter(r => r.sim); const n = s.length; const av = k => n >= MIN ? Math.round(s.reduce((a, r) => a + (+r.sim[k] || 0), 0) / n) : null; return sup(n, { n, use: av('use'), happy: av('happy'), desk_use: av('desk_use') }); };
    const by = {}; for (const t of TYPES) by[t] = agg(done.filter(r => r.type === t));
    const bins = Array(10).fill(0); done.filter(r => r.sim).forEach(r => bins[Math.min(9, Math.floor((+r.sim.happy || 0) / 10))]++);
    return { ...base, all: agg(done), by_type: by, happy_hist: N >= MIN ? bins : null };
  }

  if (route === 'leads-gap') {
    const L = done.filter(r => r.type === 'Lead'), O = done.filter(r => r.type !== 'Lead' && r.type !== 'none'), rows = [];
    for (const d of SCEN) { const l = L.filter(r => r.byZone[d.zone] && r.byZone[d.zone].id === d.id), o = O.filter(r => r.byZone[d.zone] && r.byZone[d.zone].id === d.id);
      if (l.length < MIN || o.length < MIN) continue;
      for (const op of d.options) { const a = pct(l.filter(r => r.finalOf(d.zone) === op.k).length, l.length), b = pct(o.filter(r => r.finalOf(d.zone) === op.k).length, o.length);
        rows.push({ id: d.id, question: d.question, k: op.k, name: op.name, lead: a, staff: b, gap: Math.round((b - a) * 10) / 10, n_lead: l.length, n_staff: o.length }); } }
    return { ...base, n_lead: L.length, n_staff: O.length, rows: rows.sort((x, y) => Math.abs(y.gap) - Math.abs(x.gap)).slice(0, 10) };
  }

  if (route === 'styles') { const c = {}; done.forEach(r => r.palette && (c[r.palette] = (c[r.palette] || 0) + 1));
    return { ...base, palettes: Object.entries(c).map(([name, n]) => ({ name, share: pct(n, N) })).sort((a, b) => (b.share || 0) - (a.share || 0)) }; }

  if (route === 'wall') { const ids = new Set(done.map(r => r.id)), posts = [...wall.values()].filter(p => ids.has(p.session_id)), pids = new Set(posts.map(p => p.id));
    return { ...base, posts: posts.length, share_posted: pct(posts.length, N), views: [...views].filter(v => pids.has(v.split('|')[1])).length,
    median_views: posts.length >= MIN ? median(posts.map(p => [...views].filter(v => v.endsWith('|' + p.id)).length)) : null }; }

  return null;
}

export function exportCsv(url, ctx) {
  const r = readApi('/v1/read/picks', url, ctx); const lines = ['scenario_id,question,option,name,share_pct,n'];
  for (const s of r.scenarios) { if (s.lt10) continue; for (const o of s.options) lines.push([s.id, JSON.stringify(s.question), o.k, JSON.stringify(o.name), o.share, s.n].join(',')); lines.push([s.id, JSON.stringify(s.question), 'skip', 'Skipped', s.skip_share, s.n].join(',')); }
  return lines.join('\n') + '\n';
}
