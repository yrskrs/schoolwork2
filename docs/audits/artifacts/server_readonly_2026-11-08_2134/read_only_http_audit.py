import datetime,gzip,hashlib,json,re,socket,ssl,subprocess,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
opener=urllib.request.build_opener(NoRedirect)
headers_to_keep=['Content-Type','Content-Encoding','Content-Length','Cache-Control','Vary','ETag','Strict-Transport-Security','X-Content-Type-Options','X-Frame-Options','Referrer-Policy','Content-Security-Policy']
def fetch(url,encoding='identity'):
 started=time.monotonic()
 request=urllib.request.Request(url,headers={'Accept-Encoding':encoding,'User-Agent':'SchoolNet-read-only-audit/1.0'})
 try:response=opener.open(request,timeout=15)
 except urllib.error.HTTPError as error:response=error
 except (urllib.error.URLError,TimeoutError)as error:return {'error_type':type(error).__name__,'seconds':round(time.monotonic()-started,3)},b''
 with response:
  body=response.read(2*1024*1024)
  result={'status':response.status,'seconds':round(time.monotonic()-started,3),'headers':{h:response.headers[h]for h in headers_to_keep if h in response.headers},'bytes':len(body),'cookies':[{'name':c.split('=',1)[0],'secure':bool(re.search(r';\s*secure(?:;|$)',c,re.I)),'httponly':bool(re.search(r';\s*httponly(?:;|$)',c,re.I))}for c in response.headers.get_all('Set-Cookie',[])]}
  if response.headers.get('Location'):
   parts=urllib.parse.urlsplit(response.headers['Location']);result['redirect_path']=parts.path
  return result,body
report={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'requests':{}}
base='http://127.0.0.1:8080'
for path in ['/healthz/','/','/teacher/login/']:
 result,body=fetch(base+path);report['requests']['LAN '+path]=result
 if path=='/teacher/login/':login=body
assets=[]
for item in re.findall(r'(?:src|href)=["\']([^"\']+)',login.decode(errors='replace')):
 path=urllib.parse.urlsplit(item).path
 if path.startswith('/static/')and path.endswith(('.css','.js'))and path not in assets:assets.append(path)
report['static_assets']={}
for path in assets[:6]:
 identity,raw=fetch(base+path);compressed,encoded=fetch(base+path,'gzip')
 try:decoded=gzip.decompress(encoded)if compressed.get('headers',{}).get('Content-Encoding')=='gzip'else encoded;same=hashlib.sha256(raw).digest()==hashlib.sha256(decoded).digest()
 except (OSError,EOFError):same=False
 report['static_assets'][path]={'identity':identity,'gzip':compressed,'decoded_matches':same}
# Known project Funnel hostname; public GET only, no authentication or submission.
for path in ['/healthz/','/teacher/login/','/']:
 result,_=fetch('https://localwork.tail9ec47d.ts.net'+path);report['requests']['Funnel '+path]=result
root=Path('/home/yrsadmin/schoolwork2')
report['private_backup_file_metadata']=[{'name':p.name,'mode':oct(p.stat().st_mode&0o777),'bytes':p.stat().st_size}for p in root.glob('.env.bak*')]
report['dockerignore_private_patterns']=[s for s in (root/'.dockerignore').read_text().splitlines()if 'env'in s.lower()]
report['gitignore_private_patterns']=[s for s in (root/'.gitignore').read_text().splitlines()if 'env'in s.lower()]
code=r'''
import json
from pathlib import Path
print(json.dumps({'environment_files_in_image':[{'name':p.name,'mode':oct(p.stat().st_mode&0o777)}for p in Path('/app').glob('.env*')if p.is_file()]}))
'''
r=subprocess.run(['docker','exec','-i','-e','PYTHONDONTWRITEBYTECODE=1','schoolnet_app','python','-'],input=code,text=True,capture_output=True)
if r.returncode==0:report['image_private_files']=json.loads(r.stdout)
code=r'''
import json,os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','schoolnet.settings')
import django;django.setup()
from django.db import connection
with connection.cursor()as cursor:
 cursor.execute('SHOW transaction_read_only')
 if cursor.fetchone()[0]!='on':raise RuntimeError('Read-only guard missing')
from feed.models import AISettings,AIRequestLog,AIJob
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count
row=AISettings.objects.filter(pk=1).values('provider_connections').first()or{}
print(json.dumps({'connection_limits':[{'provider':c.get('provider'),'quota_limits':c.get('quota_limits')or{},'explicit_quota_group':bool(c.get('quota_group'))}for c in row.get('provider_connections')or[]],'requests_since_current_app_start':list(AIRequestLog.objects.filter(created_at__gte='__CURRENT_START__').values('provider','status_code','is_success').annotate(count=Count('id'))),'jobs_since_current_app_start':list(AIJob.objects.filter(created_at__gte='__CURRENT_START__').values('kind','status').annotate(count=Count('id')))},ensure_ascii=False))
'''
app_started=subprocess.check_output(['docker','inspect','--format','{{.State.StartedAt}}','schoolnet_app'],text=True).strip()
code=code.replace('__CURRENT_START__',app_started)
r=subprocess.run(['docker','exec','-i','-e','PYTHONDONTWRITEBYTECODE=1','-e','PGOPTIONS=-c default_transaction_read_only=on','schoolnet_app','python','-'],input=code,text=True,capture_output=True)
if r.returncode==0:report['current_ai']=json.loads(r.stdout)
else:report['current_ai']={'error':'read-only query failed; raw output withheld'}
print(json.dumps(report,ensure_ascii=False,indent=2))
