"""Record real skill subprocess output beside the live DGX dashboard.

No prerecorded verdicts: each displayed command launches the shipped bridge.
The operator provisions a pinned restore-only scope before invoking the skill.
Keep the complete browser capture and a timestamped command/output journal.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SKILL = ROOT / 'skills/aegis-self-repair'
ORIGIN = 'http://127.0.0.1:8822'
CHROME = Path.home() / 'AppData/Local/ms-playwright/chromium-1124/chrome-win/chrome.exe'
EVENTS = []
RESULT = {}
STARTED = time.monotonic()
LOCK = threading.Lock()
TERMINAL = {'succeeded', 'failed', 'partial', 'cancelled', 'interrupted'}

PAGE = '''<!doctype html><meta charset="utf-8"><title>Aegis · 实际技能执行</title>
<style>*{box-sizing:border-box}body{margin:0;background:#080e18;color:#e4ecf7;font-family:"Microsoft YaHei",sans-serif}
header{height:100px;padding:20px 35px;border-bottom:1px solid #293548}h1{margin:0;font-size:30px}small{color:#8ab7b0;font-size:16px}
main{display:grid;grid-template-columns:790px 1130px;height:930px}#terminal{padding:25px 28px;overflow:auto;border-right:1px solid #293548;font:20px/1.55 Consolas,"Microsoft YaHei",monospace;white-space:pre-wrap;overflow-wrap:anywhere}
.command{color:#6fd7ff;margin-top:18px}.out{color:#b7d9b8}.stage{color:#ffd166;margin-top:15px}.error{color:#ff8c8c}
#wrap{width:1130px;height:930px;overflow:hidden}iframe{border:0;width:1920px;height:1580px;transform:scale(.5885);transform-origin:0 0}
footer{height:50px;padding:12px 30px;border-top:1px solid #293548;color:#a0afc5;font-size:18px}#run{color:#78dec7}</style>
<header><h1>Aegis Skills：从命令到真实修补收据</h1><small>正在执行仓库内的 aegis-self-repair CLI · DGX Spark 授权靶场 · restore 模式</small></header>
<main><div id="terminal"></div><div id="wrap"><iframe id="demo" src="http://127.0.0.1:8822/demo"></iframe></div></main>
<footer>同一运行编号：<span id="run">等待 start 返回</span>　｜　左：实际 stdout　右：真实后台　｜　候选入队 ≠ 已训练</footer>
<script>let n=0;async function update(){let d=await (await fetch('/events')).json();for(let e of d.events.slice(n)){let p=document.createElement('div');p.className=e.kind;p.textContent=e.text;document.querySelector('#terminal').append(p)}n=d.events.length;document.querySelector('#terminal').scrollTop=999999;if(d.result.run_id)document.querySelector('#run').textContent=d.result.run_id;window.finished=!!d.result.finished;window.captureResult=d.result;}setInterval(update,350);update();</script>'''

def get(path):
    with urllib.request.urlopen(ORIGIN + path, timeout=12) as response:
        return json.load(response)

def emit(kind, text):
    with LOCK:
        EVENTS.append({'t': round(time.monotonic()-STARTED, 3), 'kind': kind, 'text': text})

def run(args, display=None):
    emit('command', '$ python scripts/aegis_skill.py ' + (display or ' '.join(args)))
    p = subprocess.run([os.sys.executable, str(SKILL/'scripts/aegis_skill.py'), *args],
                       cwd=SKILL, env=ENV, capture_output=True, text=True, encoding='utf-8', timeout=75)
    raw = p.stdout.strip()
    d = json.loads(raw) if raw else {'error':'no_stdout'}
    # Print actual JSON (no injected success fields); scope host path is hidden.
    emit('out' if p.returncode == 0 else 'error', json.dumps(d, ensure_ascii=False, indent=2))
    JOURNAL.append({'args':args, 'exit_code':p.returncode, 'stdout':d, 'stderr':p.stderr[:500], 't':time.monotonic()-STARTED})
    if p.returncode:
        raise RuntimeError('skill command rejected: ' + str(d))
    return d

def worker():
    try:
        emit('stage', '读取 SKILL.md：selftest → start → status → verdict')
        emit('out', '操作员已在调用前配置：登记靶场 / 哈希固定的授权范围 / 仅 repair_restore')
        time.sleep(2)
        run(['selftest','--scope', str(SCOPE)], 'selftest --scope <operator-scope.json>')
        time.sleep(2)
        # Close preflight/start race as far as possible; Console owns its lock.
        if any(r.get('state') not in TERMINAL for r in get('/api/demo/runs')['runs']):
            raise RuntimeError('backend busy; no POST issued')
        d = run(['start','--scope',str(SCOPE),'--request-id',REQUEST_ID,'--cleanup','restore'],
                'start --scope <operator-scope.json> --request-id '+REQUEST_ID+' --cleanup restore')
        RESULT['run_id'] = rid = d['run_id']
        RESULT['accepted_t'] = round(time.monotonic()-STARTED,3)
        previous = None
        deadline = time.monotonic()+360
        while time.monotonic() < deadline:
            snap = get('/api/demo/runs/'+rid)
            r = snap.get('run') or snap
            signature = (r.get('state'),r.get('stage'))
            if signature != previous:
                emit('stage', '后台实时阶段：'+str(signature[1])+' | '+str(signature[0]))
                previous = signature
            if r.get('state') in TERMINAL:
                break
            time.sleep(1)
        else:
            raise RuntimeError('recording deadline reached; backend run is not cancelled')
        RESULT['terminal_t'] = round(time.monotonic()-STARTED,3)
        run(['status','--run-id',rid])
        time.sleep(3)
        v = run(['verdict','--run-id',rid])
        RESULT['verdict'] = v
        if v.get('claim') != 'verified_restored':
            raise RuntimeError('real verdict is not verified_restored')
        emit('stage','真实收据核验通过；默认恢复原始代码；新样本仅进入候选队列。')
        time.sleep(7)
    except Exception as exc:
        RESULT['error'] = str(exc)
        emit('error',str(exc))
        time.sleep(5)
    finally:
        RESULT['finished'] = True

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with LOCK:
            body = (json.dumps({'events':EVENTS,'result':RESULT}, ensure_ascii=False).encode()
                    if self.path == '/events' else PAGE.encode())
        self.send_response(200);self.send_header('Content-Type','application/json' if self.path=='/events' else 'text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

if __name__ == '__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    listing = get('/api/demo/runs')['runs']
    if any(r.get('state') not in TERMINAL for r in listing):
        raise SystemExit('Busy: recording will not start a new run')
    # Operator preparation, separate from agent CLI. No retain permission.
    SCOPE = OUT/'operator-scope.local.json'
    SCOPE.write_text(json.dumps({'schema_version':1,'target_id':'edu-lite-a','case_id':'sqli',
        'target_base':'http://127.0.0.1:8081','source_root':'target/edu-lite',
        'console_origin':ORIGIN,'allowed_operations':['repair_restore']}), encoding='utf-8')
    ENV = dict(os.environ,AEGIS_CONSOLE_ORIGIN=ORIGIN,AEGIS_SCOPE_SHA256=hashlib.sha256(SCOPE.read_bytes()).hexdigest(),PYTHONIOENCODING='utf-8')
    JOURNAL=[]
    REQUEST_ID='film-skill-'+time.strftime('%Y%m%d-%H%M%S')
    server=ThreadingHTTPServer(('127.0.0.1',18825),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=str(CHROME))
        context=browser.new_context(viewport={'width':1920,'height':1080},record_video_dir=str(OUT/'raw'),record_video_size={'width':1920,'height':1080})
        page=context.new_page(); video=page.video
        page.goto('http://127.0.0.1:18825',wait_until='domcontentloaded')
        page.wait_for_timeout(1500)
        STARTED=time.monotonic()
        threading.Thread(target=worker,daemon=True).start()
        # Dashboard auto follows the newly created run; terminal is actual stdout.
        page.wait_for_function('window.finished === true',timeout=460000)
        page.screenshot(path=str(OUT/'terminal.png'))
        context.close()
        rid=RESULT.get('run_id','attempt')
        video.save_as(str(OUT/f'aegis-skill-live-{rid}-raw.webm'))
        browser.close()
    server.shutdown()
    report={'mode':'real subprocess execution with live dashboard','result':RESULT,'events':EVENTS,'commands':JOURNAL,
        'skill_sha256':hashlib.sha256((SKILL/'scripts/aegis_skill.py').read_bytes()).hexdigest(),
        'scope_sha256':ENV['AEGIS_SCOPE_SHA256'],'source_preexisting_runs':len(listing)}
    (OUT/'capture.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(RESULT,ensure_ascii=False))
    raise SystemExit(1 if RESULT.get('error') else 0)
