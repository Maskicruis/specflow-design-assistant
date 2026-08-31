"""Double-click launcher: no console, safe local health check, no global install."""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from app.config import ROOT, DATA, INSTANCE

URL='http://127.0.0.1:8765'

def health():
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL+'/api/health',timeout=2) as response:
            return json.load(response)
    except Exception:
        return None

def run():
    result=health()
    if result and (result.get('app')!='SpecFlow' or result.get('instance')!=INSTANCE):
        raise RuntimeError('端口 8765 已被其他服务或旧版本占用。请先停止原服务，再启动此目录。')
    if not result or result.get('app')!='SpecFlow':
        python=ROOT/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not python.exists():
            raise RuntimeError('未找到项目运行环境。请先运行 安装依赖.cmd。')
        (ROOT/'work').mkdir(exist_ok=True)
        env=dict(os.environ,PYTHONUTF8='1')
        with open(ROOT/'work'/'server.log','ab') as out,open(ROOT/'work'/'server-error.log','ab') as err:
            process=subprocess.Popen([str(python),'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8765'],cwd=ROOT,env=env,stdout=out,stderr=err,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        for _ in range(60):
            result=health()
            if result and result.get('app')=='SpecFlow' and result.get('instance')==INSTANCE:
                break
            if process.poll() is not None:
                raise RuntimeError('服务启动失败，请查看 work/server-error.log。端口 8765 可能已被占用。')
            time.sleep(.25)
        else:
            raise RuntimeError('服务启动超时，请查看 work/server-error.log。')
    if result.get('pid'):
        DATA.mkdir(parents=True,exist_ok=True)
        (DATA/'server.pid').write_text(str(result['pid']),encoding='utf-8')
    if '--no-browser' not in sys.argv:
        webbrowser.open(URL)
    print(URL)

if __name__=='__main__':
    try:
        run()
    except Exception as exc:
        if '--no-browser' not in sys.argv and os.name=='nt':
            ctypes.windll.user32.MessageBoxW(0,str(exc),'规序 SpecFlow',0x10)
        else:
            print(str(exc),file=sys.stderr)
        sys.exit(1)
