// Runs the readout's reference read API (readout/mock-api/read.mjs) over a store given on stdin,
// for tests/test_read_parity.py. Input: {sessions, wall, views, queries: [[path, query], ...]}.
// Output: one JSON result (or CSV text for export.csv) per query, in order.
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const mockDir = join(here, '..', '..', 'readout', 'mock-api');
const { readApi, exportCsv } = await import(join(mockDir, 'read.mjs'));
const SCEN = JSON.parse(readFileSync(join(mockDir, 'scenarios.json'), 'utf8'));

const input = JSON.parse(readFileSync(0, 'utf8'));
const sessions = new Map();
for (const s of input.sessions) {
  sessions.set(s.id, { id: s.id, body: s.body, deal: s.deal, createdAt: s.createdAt, completedAt: s.completedAt,
    finish: s.finish, floor: s.floor, _posted: s._posted, events: new Map(s.events.map(e => [e.seq, e])) });
}
const wall = new Map(input.wall.map(p => [p.anon, p]));
const views = new Set(input.views);
const ctx = { sessions, SCEN, wall, views };
const out = input.queries.map(([path, query]) => {
  const url = new URL('http://x' + path + (query ? '?' + query : ''));
  return path === '/v1/read/export.csv' ? exportCsv(url, ctx) : readApi(path, url, ctx);
});
process.stdout.write(JSON.stringify(out));
