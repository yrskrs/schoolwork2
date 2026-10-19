"""Real Chrome + Django + durable imports, entirely on synthetic temporary data.

Requires Chrome/Chromium and websockets (test tooling only). Never loads .env,
real users, provider accounts or existing uploaded files.
"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from wsgiref.simple_server import WSGIRequestHandler, make_server, WSGIServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CDP:
    def __init__(self, url):
        try:
            from websockets.sync.client import connect
        except ImportError:
            # Permit a host-installed QA dependency without changing the app venv.
            sys.path.append(str(Path.home() / '.local/lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'))
            from websockets.sync.client import connect
        self.context = connect(url, max_size=16*1024*1024)
        self.socket = self.context.__enter__()
        self.counter = 0
        self.errors = []

    def call(self, method, params=None):
        self.counter += 1
        self.socket.send(json.dumps({'id':self.counter, 'method':method, 'params':params or {}}))
        while True:
            message = json.loads(self.socket.recv(timeout=20))
            if message.get('method') == 'Runtime.exceptionThrown':
                self.errors.append(message['params']['exceptionDetails'].get('exception',{}).get('description',message['params']['exceptionDetails'].get('text','exception')))
            if message.get('id') == self.counter:
                if 'error' in message:
                    raise AssertionError(message['error'])
                return message.get('result', {})

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', {'expression':expression, 'returnByValue':True, 'awaitPromise':True})
        if 'exceptionDetails' in result:
            raise AssertionError(result['exceptionDetails'])
        return result.get('result', {}).get('value')

    def until(self, expression, timeout=20):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(.05)
        raise AssertionError('Browser condition timed out: '+expression+' state='+str(self.evaluate("JSON.stringify({url:location.href,title:document.title,state:document.readyState,scripts:[...document.scripts].map(s=>s.src),text:document.body.innerText.slice(0,800)})"))+' errors='+str(self.errors))


class Response(io.BytesIO):
    def __init__(self, body, content_type, name='metadata.json', slow=False):
        super().__init__(body)
        from urllib.parse import quote
        self.headers = {'Content-Type':content_type, 'Content-Length':str(len(body)),
                        'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name)}
        self.slow = slow

    def read(self, size=-1):
        assert size >= 0, 'Unbounded cloud read'
        if self.slow:
            time.sleep(.15)
            size = min(size, 32768)
        return super().read(size)


def main():
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolnet.test_settings'
    os.environ['JOURNAL_API_BASE_URL'] = ''
    os.environ['ROSTER_PEERS'] = '[]'
    chrome = os.environ.get('CHROME_BINARY') or shutil.which('google-chrome') or shutil.which('chromium')
    if not chrome:
        raise SystemExit('Chrome/Chromium is required for this QA script.')
    with tempfile.TemporaryDirectory(prefix='schoolnet-cloud-ui-') as temporary:
        from django.conf import settings
        # The threaded fixture shares SQLite with a writer. Reserve its single
        # write lock at transaction entry, avoiding deferred lock-upgrade races.
        # The release suite separately exercises the real PostgreSQL backend.
        settings.DATABASES = {'default':{'ENGINE':'django.db.backends.sqlite3','NAME':str(Path(temporary)/'test.sqlite3'),
            'OPTIONS':{'timeout':30,'transaction_mode':'IMMEDIATE'}}}
        import django
        django.setup()
        import logging
        logging.getLogger('django.request').addHandler(logging.StreamHandler(sys.stderr))
        from django.contrib.auth.models import User
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.management import call_command
        from django.core.wsgi import get_wsgi_application
        from django.test import Client
        from feed.middleware import set_has_admin
        import feed.middleware
        from feed.models import Assignment, AssignmentFile, ClassGroup, School, Subject, Teacher, CloudImport
        from feed.cloud_storage import GoogleDriveClient
        from feed import cloud_imports
        from PIL import Image
        call_command('migrate', verbosity=0, interactive=False)
        feed.middleware._safe_log = lambda *args, **kwargs: None
        user = User.objects.create_superuser('synthetic-cloud-browser', password='synthetic-only')
        teacher = Teacher.objects.create(user=user,full_name='Synthetic teacher')
        School.objects.create(name='Synthetic QA school',admin=user)
        set_has_admin(True)
        group = ClassGroup.objects.create(grade=9,letter='A')
        subject = Subject.objects.create(name='Synthetic subject')
        teacher.classes.add(group); teacher.subjects.add(subject)
        assignment = Assignment.objects.create(teacher=teacher,subject=subject,title='Synthetic cloud assignment',description='Synthetic',status='draft')
        assignment.classes.add(group)
        storage = teacher.get_cloud_storage()
        storage.google_drive_enabled=True;storage.google_access_token='synthetic-only'
        storage.nextcloud_enabled=True;storage.nextcloud_username='synthetic';storage.nextcloud_password='synthetic-only'
        image = Image.frombytes('RGB',(512,512),os.urandom(512*512*3))
        buffer = io.BytesIO();image.save(buffer,format='PNG'); original=buffer.getvalue()
        native = io.BytesIO()
        from docx import Document
        doc=Document();doc.add_paragraph('Synthetic original Google document');doc.save(native)
        native_bytes=native.getvalue()
        alpha=b'synthetic alpha';beta=b'synthetic beta'

        class DAV(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_PROPFIND(self):
                body=b'''<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response><d:href>/remote.php/dav/files/synthetic/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>'''
                for name,content in [('alpha.txt',alpha),('beta.txt',beta)]:
                    body+=f'<d:response><d:href>/remote.php/dav/files/synthetic/{name}</d:href><d:propstat><d:prop><d:displayname>{name}</d:displayname><d:getcontentlength>{len(content)}</d:getcontentlength></d:prop></d:propstat></d:response>'.encode()
                body+=b'</d:multistatus>'
                self.send_response(207);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_GET(self):
                body=alpha if self.path.endswith('alpha.txt') else beta
                self.send_response(200);self.send_header('Content-Type','text/plain');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        dav=ThreadingHTTPServer(('127.0.0.1',0),DAV)
        threading.Thread(target=dav.serve_forever,daemon=True).start()
        storage.nextcloud_url=f'http://127.0.0.1:{dav.server_port}';storage.save()

        def google_open(client,request):
            parsed=urlsplit(request.full_url);query=parse_qs(parsed.query)
            if parsed.path=='/uc':
                return Response(original,'image/png','Справжнє фото.png')
            if parsed.path.endswith('/about'):
                return Response(b'{"user":{}}','application/json')
            if parsed.path.endswith('/files'):
                return Response(json.dumps({'files':[
                    {'id':'SyntheticImage123456','name':'Справжнє фото.png','mimeType':'image/png','size':str(len(original))},
                    {'id':'SyntheticDoc12345678','name':'Google документ','mimeType':'application/vnd.google-apps.document'}]}).encode(),'application/json')
            if parsed.path.endswith('/export'):
                return Response(native_bytes,'application/vnd.openxmlformats-officedocument.wordprocessingml.document','Google документ.docx')
            if query.get('alt')==['media']:
                return Response(original,'image/png','Справжнє фото.png',slow=True)
            fid=parsed.path.split('/')[-1]
            metadata={'id':fid,'name':'Справжнє фото.png','mimeType':'image/png','size':str(len(original)),
                      'md5Checksum':hashlib.md5(original).hexdigest(),'sha256Checksum':hashlib.sha256(original).hexdigest()}
            if fid=='SyntheticDoc12345678':metadata={'id':fid,'name':'Google документ','mimeType':'application/vnd.google-apps.document'}
            return Response(json.dumps(metadata).encode(),'application/json')

        class Quiet(WSGIRequestHandler):
            def log_message(self,*args):pass
        server=make_server('127.0.0.1',0,StaticFilesHandler(get_wsgi_application()),handler_class=Quiet)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        client=Client();client.force_login(user)
        stop=threading.Event()
        artifacts=Path(os.environ.get('QA_ARTIFACTS','/tmp/schoolnet-cloud-ui-artifacts'));artifacts.mkdir(parents=True,exist_ok=True)
        profile=Path(temporary)/'chrome'
        with patch.object(GoogleDriveClient,'_open',google_open):
            worker=cloud_imports.start_worker(stop)
            browser=subprocess.Popen([chrome,'--headless','--no-sandbox','--disable-gpu','--no-first-run',
                '--disable-background-networking','--remote-debugging-port=0','--user-data-dir='+str(profile),'about:blank'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                port_file=profile/'DevToolsActivePort'
                for _ in range(200):
                    if port_file.exists():break
                    time.sleep(.05)
                port=port_file.read_text().splitlines()[0]
                with urlopen(f'http://127.0.0.1:{port}/json/list') as response:target=next(item for item in json.load(response) if item['type']=='page')
                cdp=CDP(target['webSocketDebuggerUrl'])
                cdp.call('Page.enable');cdp.call('Runtime.enable');cdp.call('Network.enable')
                cdp.call('Network.setCookie',{'name':settings.SESSION_COOKIE_NAME,'value':client.cookies[settings.SESSION_COOKIE_NAME].value,'url':f'http://127.0.0.1:{server.server_port}'})
                cdp.call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/teacher/assignment/{assignment.pk}/edit/'})
                cdp.until("typeof openCloudPickerModal==='function' && typeof addCloudImportsToStore==='function'")
                cdp.evaluate("void openGuideModal('teacher_settings')")
                cdp.until("document.querySelector('#guide-modal')?.style.display==='flex'")
                cdp.evaluate('closeGuideModal();void openChangelogModal()')
                cdp.until("document.querySelector('#changelog-modal')?.style.display==='flex'")
                cdp.evaluate('closeChangelogModal()')
                cdp.evaluate('openCloudPickerModal()')
                cdp.until("document.querySelectorAll('#cloud-files-tbody tr').length===2")
                assert cdp.evaluate("parseFloat(getComputedStyle(document.querySelector('#cloud-files-table')).fontSize)>=16"), 'Default text is too small'
                cdp.evaluate("changeCloudFont(20)")
                assert cdp.evaluate("parseFloat(getComputedStyle(document.querySelector('#cloud-files-table')).fontSize)>=19"), 'Text zoom did not apply'
                cdp.evaluate("closeCloudPickerModal();openCloudPickerModal()")
                assert cdp.evaluate("document.querySelector('#cloud-font-value').textContent==='120%'")
                cdp.until("document.querySelectorAll('#cloud-files-tbody tr').length===2")
                cdp.evaluate("window.syntheticFetch=window.fetch;window.fetch=(url,opts)=>String(url).includes('/cloud/browse/')?Promise.resolve(new Response(JSON.stringify({success:false,error:'<img src=x onerror=window.syntheticXss=1>'}),{status:502,headers:{'Content-Type':'application/json'}})):window.syntheticFetch(url,opts);reloadCurrentCloudFolder()")
                cdp.until("document.querySelector('#cloud-error-box').textContent.includes('<img')")
                assert cdp.evaluate("!window.syntheticXss && !document.querySelector('#cloud-error-box img')"),'Provider error executed HTML'
                cdp.evaluate('window.fetch=window.syntheticFetch;reloadCurrentCloudFolder()')
                cdp.until("document.querySelectorAll('#cloud-files-tbody tr').length===2")
                cdp.evaluate("document.querySelector('#cloud-search-input').value='alpha';filterCloudFilesList();toggleSelectAllCloudFiles(true)")
                assert cdp.evaluate("document.querySelector('#cloud-selection-counter').textContent.includes('1')"),'Filtered select-all includes hidden files'
                cdp.evaluate('void attachSelectedCloudFiles()')
                cdp.until("!window.cloudImportBusy && document.querySelectorAll('.cloud-imported-material').length===1")
                assert cdp.evaluate("document.querySelector('#cloud-picker-modal').style.display==='none' && !document.querySelector('#cloud-materials-status').hidden && document.querySelector('#cloud-transfer-panel').hidden"), 'Completion must close the picker and show a receipt'
                first_id=cdp.evaluate("JSON.parse(document.querySelector('#id_cloud_imports').value)[0]")
                cdp.call('Page.reload')
                cdp.until("typeof addCloudImportsToStore==='function' && !window.cloudImportBusy && document.querySelectorAll('.cloud-imported-material').length===1")
                assert cdp.evaluate("document.querySelector('#cloud-transfer-panel').hidden"), 'Completed progress was restored after reload'
                assert cdp.evaluate("document.querySelector('#cloud-font-value').textContent==='120%'"), 'Font preference was not retained'
                cdp.evaluate("document.querySelector('.cloud-imported-material button').click()")
                cdp.call('Page.reload')
                cdp.until("typeof addCloudImportsToStore==='function' && !window.cloudImportBusy")
                assert cdp.evaluate("JSON.parse(document.querySelector('#id_cloud_imports').value).length===0"), 'Removed file reappeared after reload'
                # Seed both an old global batch and a selected file in this draft.
                cdp.evaluate("sessionStorage.setItem('schoolnet-cloud-batch:'+document.querySelector('#cloud-picker-modal').dataset.owner+':/teacher/assignment/create/',JSON.stringify(["+json.dumps(first_id)+"]))")
                cdp.evaluate("fetch(document.querySelector('#cloud-picker-modal').dataset.importsUrl+'?ids='+"+json.dumps(first_id)+").then(r=>r.json()).then(r=>addCloudImportsToStore(r.imports))")
                for attempt in range(2):
                    cdp.call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/teacher/assignment/create/'})
                    cdp.until("typeof openCloudPickerModal==='function' && typeof addCloudImportsToStore==='function' && !window.cloudImportBusy")
                    cdp.evaluate('openCloudPickerModal()')
                    cdp.until("document.querySelectorAll('#cloud-files-tbody tr').length===2")
                    assert cdp.evaluate("JSON.parse(document.querySelector('#id_cloud_imports').value).length===0 && document.querySelector('#cloud-transfer-panel').hidden"), 'New draft inherited attachments or progress'
                    cdp.evaluate('closeCloudPickerModal()')
                    if attempt==0:
                        cdp.evaluate("fetch(document.querySelector('#cloud-picker-modal').dataset.importsUrl+'?ids='+"+json.dumps(first_id)+").then(r=>r.json()).then(r=>addCloudImportsToStore(r.imports))")
                cdp.call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/teacher/assignment/{assignment.pk}/edit/'})
                cdp.until("typeof addCloudImportsToStore==='function' && !window.cloudImportBusy")
                cdp.evaluate("fetch(document.querySelector('#cloud-picker-modal').dataset.importsUrl+'?ids='+"+json.dumps(first_id)+").then(r=>r.json()).then(r=>addCloudImportsToStore(r.imports))")
                cdp.until("document.querySelectorAll('.cloud-imported-material').length===1")
                cdp.evaluate("openCloudPickerModal();switchCloudDrive('google')")
                cdp.until("document.querySelector('#cloud-files-tbody').textContent.includes('Справжнє фото.png')")
                assert cdp.evaluate("[...document.querySelectorAll('#cloud-files-tbody tr')].some(r=>r.textContent.includes('Справжнє фото.png') && r.cells[2].textContent.includes('КБ'))"), 'Drive file size is missing'
                assert cdp.evaluate("document.querySelector('#cloud-files-tbody').textContent.includes('Після експорту')"), 'Native export size is misleading'
                cdp.evaluate("document.querySelector('#google-direct-link-input').value='https://drive.google.com/file/d/SyntheticImage123456/view';addGoogleDirectLink()")
                cdp.until("document.querySelector('#cloud-selection-counter').textContent.includes('1') && !document.querySelector('#google-direct-add').disabled")
                assert cdp.evaluate("document.querySelector('#cloud-files-tbody').textContent.includes('Справжнє фото.png')")
                cdp.evaluate('void attachSelectedCloudFiles()')
                cdp.until("window.cloudImportBusy && !document.querySelector('#cloud-transfer-panel').hidden")
                (artifacts/'cloud-progress-layout.png').write_bytes(base64.b64decode(cdp.call('Page.captureScreenshot',{'format':'png'})['data']))
                assert cdp.evaluate("document.querySelector('#cloud-transfer-panel').getBoundingClientRect().bottom<=innerHeight"),'Progress panel is clipped: '+str(cdp.evaluate("[innerHeight,...['.cloud-picker-dialog','#cloud-files-container','#cloud-transfer-panel'].map(s=>{const e=document.querySelector(s);return [s,e.getBoundingClientRect().toJSON(),e.scrollHeight,e.clientHeight,getComputedStyle(e).fontSize]})]"))
                # Provider cannot change while a batch is active.
                cdp.evaluate("switchCloudDrive('nextcloud')")
                assert cdp.evaluate("document.querySelector('.cloud-tab-btn.active').dataset.drive==='google'")
                cdp.until("Number(document.querySelector('#cloud-transfer-bar').value)>0 && window.cloudImportBusy")
                screenshot=cdp.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']
                (artifacts/'cloud-progress.png').write_bytes(base64.b64decode(screenshot))
                cdp.call('Page.reload')
                cdp.until("!window.cloudImportBusy && document.querySelectorAll('.cloud-imported-material').length===2")
                assert CloudImport.objects.count()==2, 'Reload enqueued duplicate imports'
                assert cdp.evaluate("document.querySelector('#cloud-transfer-panel').hidden"), 'Completed resumed progress is still shown'
                cdp.evaluate("openCloudPickerModal();switchCloudDrive('google')")
                cdp.until("document.querySelector('#cloud-files-tbody').textContent.includes('Справжнє фото.png')")
                cdp.evaluate("document.querySelector('#google-direct-link-input').value='https://docs.google.com/document/d/SyntheticDoc12345678/edit';addGoogleDirectLink()")
                cdp.until("document.querySelector('#cloud-selection-counter').textContent.includes('1') && !document.querySelector('#google-direct-add').disabled")
                cdp.evaluate('void attachSelectedCloudFiles()')
                cdp.until("!window.cloudImportBusy && document.querySelectorAll('.cloud-imported-material').length===3")
                cdp.evaluate('openCloudPickerModal();changeCloudFont(30)')
                cdp.until("document.querySelector('#cloud-files-tbody').textContent.includes('Справжнє фото.png')")
                for width,height in ((375,900),(375,700),(1366,900)):
                    cdp.call('Emulation.setDeviceMetricsOverride',{'width':width,'height':height,'deviceScaleFactor':1,'mobile':False})
                    assert cdp.evaluate('document.documentElement.scrollWidth<=innerWidth+2'), 'Viewport overflow'
                    (artifacts/f'cloud-ready-{width}-{height}.png').write_bytes(base64.b64decode(cdp.call('Page.captureScreenshot',{'format':'png'})['data']))
                    assert cdp.evaluate("document.querySelector('.cloud-picker-dialog').scrollWidth<=document.querySelector('.cloud-picker-dialog').clientWidth+2"), 'Dialog overflow with enlarged text: '+str(cdp.evaluate("[...document.querySelector('.cloud-picker-dialog').children].map(e=>[e.className,e.scrollWidth,e.clientWidth])"))
                    assert cdp.evaluate("document.querySelector('#cloud-files-container').clientHeight>=80"), 'File list is squeezed out by enlarged controls'
                    (artifacts/f'cloud-ready-{width}-{height}.png').write_bytes(base64.b64decode(cdp.call('Page.captureScreenshot',{'format':'png'})['data']))
                cdp.evaluate('closeCloudPickerModal()')
                cdp.evaluate("document.querySelector('[value=\"cloud:'+JSON.parse(document.querySelector('#id_cloud_imports').value)[1]+'\"]').click()")
                cdp.evaluate("document.querySelector('#assignment-form').requestSubmit()")
                cdp.until("!location.pathname.endsWith('/edit/')")
                materials=list(assignment.files.all())
                assert len(materials)==3,[(item.original_name,item.content_sha256) for item in materials]
                by_name={item.original_name:item for item in materials}
                assert 'Справжнє фото.png' in by_name and 'Google документ.docx' in by_name and 'alpha.txt' in by_name
                for name,body in [('Справжнє фото.png',original),('Google документ.docx',native_bytes),('alpha.txt',alpha)]:
                    material=by_name[name]
                    with material.file.open('rb') as handle:assert handle.read()==body,name
                    assert material.content_sha256==hashlib.sha256(body).hexdigest(),name
                assert sum(item.is_task_source_for_ai for item in materials)==1
                # Public HTML lacks sizes; headers fill them in without a full import.
                storage.google_access_token='';storage.save()
                public={'items':[{'id':'SyntheticImage123456','name':'Справжнє фото.png','is_dir':False,'mime_type':'application/octet-stream','file_url':'https://drive.google.com/file/d/SyntheticImage123456/view'}]}
                with patch('feed.cloud_storage.fetch_embedded_google_folder',return_value=public):
                    cdp.call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/teacher/assignment/create/'})
                    cdp.until("typeof openCloudPickerModal==='function'")
                    cdp.evaluate("openCloudPickerModal();switchCloudDrive('google')")
                    # Open a public subfolder through the synthetic browse response.
                    cdp.evaluate("window.syntheticFetch=window.fetch;window.fetch=(url,opts)=>String(url).includes('/cloud/browse/')?window.syntheticFetch(String(url).replace('folder_id=root','folder_id=SyntheticFolder123456'),opts):window.syntheticFetch(url,opts);reloadCurrentCloudFolder()")
                    cdp.until("[...document.querySelectorAll('#cloud-files-tbody tr')].some(r=>r.textContent.includes('Справжнє фото.png') && r.cells[2].textContent.includes('КБ'))")
                    assert cdp.evaluate("JSON.parse(document.querySelector('#id_cloud_imports').value).length===0"), 'Size lookup attached a file'
                    cdp.evaluate('window.fetch=window.syntheticFetch;closeCloudPickerModal()')
                cdp.call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/teacher/settings/'})
                cdp.until("document.readyState==='complete' && !!document.querySelector('#gd-input-apikey')")
                assert cdp.evaluate("!!document.querySelector('a[href=\"https://console.cloud.google.com/apis/credentials\"]') && !!document.querySelector('a[href=\"https://console.cloud.google.com/apis/library/drive.googleapis.com\"]')"), 'Google API setup links are missing'
                assert not cdp.errors,cdp.errors
                print(json.dumps({'browser':'Chrome','result':'PASS','checks':['readable default font and saved zoom','completion closes modal and shows receipt','reload retains only this draft','removed file stays removed after reload','new drafts ignore old/global imports','Google API and public header file sizes','Google API setup links','lazy help/history','provider errors as plain text','filtered select-all','real direct-link name/type','visible progress','provider locked during import','native document export','mobile/desktop layout at 150%','assignment save','exact original bytes and SHA256','AI source selection'],'files':3},ensure_ascii=False))
                cdp.context.__exit__(None,None,None)
            finally:
                stop.set();worker.join(timeout=20)
                browser.terminate();browser.wait(timeout=10)
                server.shutdown();server.server_close();dav.shutdown();dav.server_close()


if __name__=='__main__':
    main()
