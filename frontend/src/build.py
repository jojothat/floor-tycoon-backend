import base64,json,os
t=open('game_tpl.html').read().replace('__KO__',open('i18n/ko.json').read())
d=lambda p,m='image/webp':'data:%s;base64,%s'%(m,base64.b64encode(open(p,'rb').read()).decode())
TH={'kit':'10-lockers','demo':'33-demo','video':'30-videoroom','booth':'05-phonebooth','enclave':'24-enclave','screens':'57-screens','lab':'32-protolab','desk':'01-desk','office':'25-office',
    'ped':'09-pedestal','team':'11-teamwall','pod':'04-focuspod','lib':'21-library','baffle':'56-baffles','pantry':'13-pantry','terrace':'37-terrace',
    'cafe':'14-cafebar','nap':'15-nappod','green':'18-greenwall','massage':'16-massage','war':'28-warroom','stair':'51-stair','bench':'02-bench','nest':'53-neighbourhood'}
for k,f in TH.items(): t=t.replace('__t_%s__'%k,d('web/%s.webp'%f))
for o in 'abc': t=t.replace('__r%s__'%o,d('game/r%s.webp'%o))
M=json.load(open('zl/meta.json'))
L=",".join("'%s':'%s'"%(k,d('zl/%s.webp'%k)) for k in ['base']+sorted(x for x in M if x!='base'))
t=t.replace('{__LAYERS__}','{'+L+'}').replace('__LMETA__',json.dumps(M))
t=t.replace('__PROJF__',open('game/projF.json').read())
DK=json.load(open('deal.json'))
for s in DK:
    for k in ['learn','metric','mgmt','basis']: s.pop(k,None)
t=t.replace('__DECK__',json.dumps(DK,ensure_ascii=False)).replace('__LLMETA__',open('ll/meta.json').read())
IMGS=sorted({o['img'] for s in DK for o in s['opts'] if o.get('img')})
import os
ADD=sorted(f[:-5] for f in os.listdir('sp') if f[:-5] not in ('window','gates','culturewall','cutaway','partition'))
t=t.replace('__ADDABLE__',json.dumps(ADD)).replace('__SAMPLES__',open('wall_samples.json').read())
t_rel=t.replace('__LLSRC__','{}').replace('__THSRC__','{}').replace('__SPSRC__','{}')
LL=json.load(open('ll/meta.json'))
t_inl=t.replace('__SPSRC__',json.dumps({i:d('sp/%s.webp'%i) for i in ADD})).replace('__LLSRC__',json.dumps({k:d('ll/%s.webp'%k) for k in LL})).replace('__THSRC__',json.dumps({p:d(p) for p in IMGS}))
import shutil
t=t_rel
page=t.replace('<title>Floor Tycoon Playtest</title>','<title>Floor Tycoon</title>\n<script src="config.js"></script>',1)
out='../public'
os.makedirs(out,exist_ok=True)
open(out+'/index.html','w').write('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#F4F1EA"></head><body style="margin:0">'+page+'</body></html>')
for dname in ['ll','web','web2','sp']:
    shutil.rmtree(out+'/'+dname,ignore_errors=True); shutil.copytree(dname,out+'/'+dname)
if not os.path.exists(out+'/config.js'):
    open(out+'/config.js','w').write("window.FT_CONFIG = { apiBase: '/api', pilotToken: 'dev-pilot', gameVersion: 'dev', simMs: 20000 };\n")
# the single-file playtest (everything inlined, no backend) for sharing without a server
open('playtest-standalone.html','w').write('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body style="margin:0">'+t_inl+'</body></html>')
import re; print('built ../public/index.html', len(page)//1024, 'KB; unreplaced:', [x for x in re.findall(r'__[a-zA-Z_]+__',page) if x not in ('__THREE_DEVTOOLS__','__proto__')][:5])
