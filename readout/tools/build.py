#!/usr/bin/env python3
"""Wraps src/app.html into public/index.html (a full document that loads config.js and sample-data.js first).
Also writes space-readout-standalone.html: one file with the sample data and the pictures inlined, for sharing a preview.
Run tools/assets.py first so public/assets/ exists."""
import pathlib, re, json, base64
here = pathlib.Path(__file__).resolve().parent.parent
app = (here / 'src/app.html').read_text()
title = re.search(r'<title>.*?</title>', app).group(0)
body = app.replace(title, '')
head = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{title}
<meta name="robots" content="noindex">
<style>[hidden]{{display:none!important}}img{{max-width:100%}}</style>
'''
(here / 'public/index.html').write_text(head + '<script src="config.js"></script>\n<script src="sample-data.js"></script>\n</head>\n<body>\n' + body + '\n</body>\n</html>\n')
A = here / 'public/assets'; inl = {}
for f in sorted(A.rglob('*')):
    if f.is_file():
        rel = f.relative_to(A).as_posix()
        inl[rel] = json.loads(f.read_text()) if f.suffix == '.json' else 'data:image/webp;base64,' + base64.b64encode(f.read_bytes()).decode()
sample = (here / 'public/sample-data.js').read_text()
(here / 'space-readout-standalone.html').write_text(head + '<script>window.RO_CONFIG={apiBase:""};</script>\n<script>\n' + sample + '</script>\n<script>window.RO_ASSETS=' + json.dumps(inl) + ';</script>\n</head>\n<body>\n' + body + '\n</body>\n</html>\n')
print('wrote public/index.html and space-readout-standalone.html', (here / 'space-readout-standalone.html').stat().st_size // 1024, 'KB')
