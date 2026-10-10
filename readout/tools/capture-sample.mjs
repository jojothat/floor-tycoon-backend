// Rebuilds public/sample-data.js from a running read API that holds ONLY synthetic plays (a fresh mock + tools/seed.mjs).
//   API=http://localhost:8787 READ_TOKEN=dev-viewer node tools/capture-sample.mjs && python3 tools/build.py
// Never point this at a backend with real employee data: the file is shipped with the page.
import { writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const API = (process.env.API || 'http://localhost:8787').replace(/\/$/, ''), H = { Authorization: 'Bearer ' + (process.env.READ_TOKEN || 'dev-viewer') };
const names = ['meta', 'summary', 'funnel', 'picks', 'skips', 'edits', 'additions', 'sharing', 'pain-hours', 'bundles', 'space-brief', 'sim', 'leads-gap', 'styles', 'wall'];
const real = await (await fetch(`${API}/v1/read/meta`, { headers: H })).json();
if (real.n_all !== real.synthetic_in_store) { console.error(`refusing: the store holds ${real.n_all - real.synthetic_in_store} non-synthetic games`); process.exit(1); }
const o = {}; for (const n of names) o[n] = await (await fetch(`${API}/v1/read/${n}?synthetic=include`, { headers: H })).json();
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), '../public/sample-data.js'),
  '// Sample data: synthetic test plays from tools/seed.mjs, captured with tools/capture-sample.mjs.\n// Shown only when config.js has no apiBase. Not real employee responses.\nwindow.RO_SAMPLE=' + JSON.stringify(o) + ';\n');
console.log('wrote public/sample-data.js from', o.summary.completed, 'synthetic games');
