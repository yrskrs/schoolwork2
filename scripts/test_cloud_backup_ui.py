"""Render both real backup pages and check responsive layout in headless Chrome.

Synthetic SQLite/users only. Needs sibling school_test checkout and Google Chrome.
Artifacts are written to an explicit /tmp directory, never production media.
"""
import asyncio
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT.parent/'school_test'
CHECKS = '''<script>
addEventListener('load',()=>setTimeout(async()=>{const result=document.createElement('pre');result.id='backup-ui-result';try{
const assert=(x,m)=>{if(!x)throw Error(m)};
assert(document.querySelector('.backup-page'),'Backup page exists');
assert(document.getElementById('backup-format').options.length===2,'Both archive formats');
assert(document.getElementById('backup-provider').options.length===2,'Both providers');
assert(document.querySelector('[name=recovery_only]'),'Read-only recovery setting');
assert(document.querySelector('[name=automatic]'),'Schedule setting');
if(window.openProblemsModal){window.currentServerNotifications=[{type:'backup',test_title:'Резервну копію створено',problem_type:'Успіх',message:'Копію перевірено',problem_detail:'Копію перевірено',link:'/teacher/backups'}];window.openProblemsModal();await new Promise(resolve=>setTimeout(resolve,300));const modal=document.getElementById('problems-modal');assert(modal.innerText.includes('Відкрити резервні копії'),'Backup notification opens backups');assert(!modal.innerText.includes('Перейти до питання'),'Backup success is not a question error');window.closeProblemsModal();await new Promise(resolve=>setTimeout(resolve,300))}
assert([...document.querySelectorAll('input[type=password]')].every(i=>!i.value),'Secrets never prefilled');
assert(document.documentElement.scrollWidth<=innerWidth+1,'No horizontal page overflow');
const provider=document.getElementById('backup-provider');for(const value of ['nextcloud','google']){provider.value=value;provider.dispatchEvent(new Event('change'));assert(document.querySelector('div[data-backup-provider='+value+']').hidden===false,'Selected provider visible');assert(document.documentElement.scrollWidth<=innerWidth+1,'Provider fits viewport: '+value)}provider.value='nextcloud';provider.dispatchEvent(new Event('change'));
for(const item of document.querySelectorAll('.backup-page section,.backup-page input:not([type=hidden]),.backup-page select')){const b=item.getBoundingClientRect();assert(b.left>=-1&&b.right<=innerWidth+1,'Control fits viewport: '+(item.id||item.tagName))}
result.textContent=JSON.stringify({ok:true,width:innerWidth,sections:document.querySelectorAll('.backup-page section').length});
}catch(e){result.textContent=JSON.stringify({ok:false,error:String(e),width:innerWidth})}document.body.append(result)},400));
</script>'''


def main():
    artifacts=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/cloud-backup-ui');artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cloud-backup-ui-') as work:
        os.environ.update(PYTHON_DOTENV_DISABLED='1', SECRET_KEY='ui-synthetic-secret', CLOUD_BACKUP_ROOT=work+'/control',
                          DATABASE_URL='sqlite:///'+work+'/test.sqlite3', DJANGO_SETTINGS_MODULE='schoolnet.test_settings')
        sys.path[:0]=[str(TEST_ROOT),str(ROOT)]
        from app.config import settings as test_settings
        from app.database import Base,engine,SessionLocal
        from app import models as tm
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            teacher=tm.Teacher(username='admin',full_name='Тестовий учитель',hashed_password='synthetic')
            db.add(teacher);db.commit();db.refresh(teacher);teacher.can_manage_updates=True
            from app.templating import templates
            from starlette.requests import Request
            from app.main import app
            from app.routes.backups import csrf
            from app.services.cloud_backups import state as test_state, Adapter as TestAdapter
            from cloud_backup.control import page_data
            request=Request({'type':'http','method':'GET','scheme':'http','path':'/teacher/backups','query_string':b'','headers':[], 'server':('testserver',80),'router':app.router})
            test_page=templates.TemplateResponse(request,'backups.html',{**page_data(test_state(),TestAdapter.owner(teacher),'personal'),
                'teacher':teacher,'backup_admin':True,'backup_csrf':csrf(teacher),'backup_base':'/teacher/backups'}).body.decode()
        import django
        django.setup()
        from django.core.management import call_command
        call_command('migrate',verbosity=0,interactive=False)
        from django.contrib.auth.models import User
        from feed.models import Teacher,School
        from django.test import RequestFactory
        from feed.views_backups import backups
        user=User.objects.create_superuser(username='admin',password='ui-only')
        Teacher.objects.create(user=user,full_name='Тестовий учитель')
        School.objects.create(name='Тестовий заклад',admin=user)
        request=RequestFactory().get('/teacher/backups');request.user=user;request.session={}
        work_page=backups(request).content.decode()
        pages={'/schooltest':test_page.replace('/static/','/test-static/'),'/schoolwork':work_page.replace('/static/','/work-static/')}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                path=self.path.split('?',1)[0]
                if path in pages:
                    body=pages[path].replace('</body>',CHECKS+'</body>').encode();content_type='text/html; charset=utf-8'
                elif path.startswith(('/test-static/','/work-static/')):
                    is_test=path.startswith('/test-static/')
                    base=TEST_ROOT/'app'/'static' if is_test else ROOT/'static'
                    prefix='/test-static/' if is_test else '/work-static/'
                    file=(base/path.removeprefix(prefix)).resolve()
                    if not file.is_relative_to(base.resolve()) or not file.is_file(): self.send_error(404);return
                    body=file.read_bytes();content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream'
                elif path.endswith('/status'):
                    body=json.dumps({'jobs':[],'backup_notices':[]}).encode();content_type='application/json'
                else:
                    self.send_error(404);return
                self.send_response(200);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            for site in ('schooltest','schoolwork'):
                for width,height in ((1440,1200),(430,1200)):
                    with tempfile.TemporaryDirectory(prefix='backup-chrome-') as profile:
                        import time,base64
                        from urllib.request import urlopen
                        from scripts.test_cloud_import_ui import CDP
                        command=[shutil.which('google-chrome') or 'chromium-browser','--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--disable-background-networking','--no-first-run','--remote-debugging-port=0',f'--user-data-dir={profile}',f'--window-size={width},{height}','--force-device-scale-factor=1','about:blank']
                        process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        try:
                            active=Path(profile)/'DevToolsActivePort'
                            deadline=time.monotonic()+15
                            while not active.exists():
                                if time.monotonic()>deadline: raise AssertionError('Chrome did not start')
                                time.sleep(.05)
                            port=active.read_text().splitlines()[0]
                            tab=next(t for t in json.load(urlopen('http://127.0.0.1:'+port+'/json/list')) if t['type']=='page')
                            cdp=CDP(tab['webSocketDebuggerUrl'])
                            try:
                                cdp.call('Page.enable')
                                cdp.call('Emulation.setDeviceMetricsOverride',{'width':width,'height':height,'deviceScaleFactor':1,'mobile':width<700})
                                cdp.call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/{site}'})
                                cdp.until("document.getElementById('backup-ui-result')&&document.getElementById('backup-ui-result').textContent.length>0")
                                check=cdp.evaluate("JSON.parse(document.getElementById('backup-ui-result').textContent)")
                                shot=cdp.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
                                (artifacts/(site+'-'+str(width)+'.png')).write_bytes(base64.b64decode(shot['data']))
                                if check['width']!=width: raise AssertionError('Incorrect viewport width')
                            finally: cdp.context.__exit__(None,None,None)
                        finally:
                            process.terminate();process.wait(timeout=10)
                        check['site']=site;print(json.dumps(check,ensure_ascii=False),flush=True)
                        (artifacts/(site+'-'+str(width)+'.json')).write_text(json.dumps(check,ensure_ascii=False,indent=2))
                        if not check['ok']: raise AssertionError(check)
        finally:
            server.shutdown();server.server_close()


if __name__=='__main__': main()
