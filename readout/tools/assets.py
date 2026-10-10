#!/usr/bin/env python3
"""Copies the game's floor pictures into public/assets/, at half size, for the readout.
   python3 tools/assets.py ../frontend/src
zl/  the base floor and each zone's layer for the seven native moments (keys z<zone><option>, z<zone>0 = left empty)
ll/  layers for library moments (keys L<scenario id><option>); a library option without one uses its zone's zl layer
sp/  one sprite per item (used for additions)
Each folder keeps the game's meta.json: where a layer sits on the floor, as fractions of the base picture."""
import sys, json, shutil, pathlib
from PIL import Image
src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '../frontend/src').resolve()
out = pathlib.Path(__file__).resolve().parent.parent / 'public/assets'
SCALE = 0.5
for d in ['zl', 'll', 'sp']:
    o = out / d; o.mkdir(parents=True, exist_ok=True)
    for f in sorted((src / d).glob('*.webp')):
        im = Image.open(f)
        if d != 'sp': im = im.resize((max(1, round(im.width * SCALE)), max(1, round(im.height * SCALE))), Image.LANCZOS)
        im.save(o / f.name, 'WEBP', quality=82, method=4)
    if (src / d / 'meta.json').exists(): shutil.copy(src / d / 'meta.json', o / 'meta.json')
print('assets written to', out, sum(f.stat().st_size for f in out.rglob('*')) // 1024, 'KB')
