"""Observation only: no restarts, migrations, tests, model calls or server file writes."""
import collections,datetime,gzip,hashlib,json,os,re,socket,subprocess,time,urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
UTC=datetime.timezone.utc
started=datetime.datetime.now(UTC)
names=['schoolnet_app','schoolnet_ai_worker','schoolnet_postgres','schoolnet_updater','schoolwork2-code_runner-1']
def command(args,timeout=30):
 try:
  p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
  return {'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 except subprocess.TimeoutExpired:return {'error':'timeout','timeout_seconds':timeout}
def containers():
 r=subprocess.check_output(['docker','inspect',*names],text=True)
 return {x['Name'].lstrip('/'): {'id':x['Id'],'image':x['Config']['Image'],'image_id':x['Image'],'started_at':x['State']['StartedAt'],'restart_count':x['RestartCount'],'health':x['State'].get('Health',{}).get('Status'),'oom_killed':x['State'].get('OOMKilled'),'user':x['Config'].get('User') or 'root','memory_limit':x['HostConfig']['Memory'],'read_only_root':x['HostConfig']['ReadonlyRootfs'],'ports':x['HostConfig'].get('PortBindings'),'networks':sorted(x['NetworkSettings']['Networks']),'docker_socket_mounted':any(m['Destination']=='/var/run/docker.sock'for m in x['Mounts'])}for x in json.loads(r)}
report={'started_at_utc':started.isoformat(),'mode':'read-only; no external AI requests','containers_before':containers()}
parallel={
 'django_check':['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1','-e','PGOPTIONS=-c default_transaction_read_only=on','schoolnet_app','python','manage.py','check','--deploy'],
 'migration_check':['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1','-e','PGOPTIONS=-c default_transaction_read_only=on','schoolnet_app','python','manage.py','migrate','--check'],
 'dependencies':['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1','schoolnet_app','python','-m','pip','check'],
 'host_locale':['bash','--noprofile','--norc','-c','LC_ALL=en_US.UTF-8 locale charmap'],
 'swap_activity':['vmstat','1','3'],
 'disk_usage':['docker','system','df'],
 'server_git':['git','-C','/home/yrsadmin/schoolwork2','status','--short'],
 'runtime_fingerprint_app':['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1','schoolnet_app','python','scripts/source_fingerprint.py'],
 'runtime_fingerprint_worker':['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1','schoolnet_ai_worker','python','scripts/source_fingerprint.py'],
 'checkout_fingerprint':['python3','-B','/home/yrsadmin/schoolwork2/scripts/source_fingerprint.py'],
 'listen_ports':['ss','-lnt'],
 'funnel':['tailscale','funnel','status','--json'],
 'container_resources':['docker','stats','--no-stream','--format','{{json .}}',*names],
}
with ThreadPoolExecutor(max_workers=4)as pool:
 futures={name:pool.submit(command,args,90)for name,args in parallel.items()}
 for name,future in futures.items():report[name]=future.result()
# Do not export raw application logs: they may include URLs, names or tokens.
report['log_summaries']={}
for name in names:
 result=command(['docker','logs','--timestamps','--since','24h','--tail','4000',name],60)
 raw=result.get('stdout','')+result.get('stderr','')
 status=collections.Counter();exceptions=collections.Counter();markers=collections.Counter();last={}
 for line in raw.splitlines():
  match=re.search(r'"\s+(\d{3})(?:\s|$)',line)
  if match:status[match[1]]+=1
  for error in re.findall(r'\b(?:OperationalError|ProgrammingError|IntegrityError|PermissionError|FileNotFoundError|TimeoutError|ConnectionError|DisallowedHost|SuspiciousOperation|ReadOnlySqlTransaction|OutOfMemoryError|MemoryError|ModuleNotFoundError|ImportError)\b',line):
   exceptions[error]+=1;last[error]=line.split(' ',1)[0]
  for marker in ['Traceback (most recent call last)','WORKER TIMEOUT','Worker exiting','Booting worker','FATAL:','PANIC:','database is locked','No space left on device','Too many open files','Killed process','read-only transaction','CSRF verification failed']:
   if marker in line:markers[marker]+=1;last[marker]=line.split(' ',1)[0]
 report['log_summaries'][name]={'scope':'last up to 4000 lines in past 24h of current container','lines':len(raw.splitlines()),'http_status_counts':dict(status),'exception_counts':dict(exceptions),'marker_counts':dict(markers),'last_timestamps':last,'returncode':result.get('returncode')}
# DB connection defaults to read-only for this diagnostic process only.
code=r'''
import collections,json,os,statistics,time
from datetime import timedelta
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','schoolnet.settings')
import django;django.setup()
from django.conf import settings
from django.db import connection
from django.db.models import Count,Min,Max
from django.utils import timezone
from django.apps import apps
with connection.cursor()as cur:
 cur.execute('SHOW transaction_read_only');readonly=cur.fetchone()[0]
 if readonly!='on':raise RuntimeError('Read-only DB safeguard is not active')
 cur.execute("SELECT pg_database_size(current_database()), count(*) FILTER (WHERE state='active'), count(*) FILTER (WHERE state='idle in transaction'), max(EXTRACT(EPOCH FROM (now()-xact_start))) FILTER (WHERE pid<>pg_backend_pid()) FROM pg_stat_activity WHERE datname=current_database()")
 db=cur.fetchone()
from feed.models import AISettings,AIJob,AIErrorLog,AIRequestLog,Submission,Assignment
now=timezone.now();window=now-timedelta(hours=24);recent=now-timedelta(hours=2)
row=AISettings.objects.filter(pk=1).values().first()or{}
connections=row.get('provider_connections')or[]
try:models=json.loads(row.get('saved_models_list')or'[]')
except ValueError:models=[]
queued=list(AIJob.objects.filter(status__in=['queued','running']).values('id','kind','status','created_at','started_at','not_before'))
errors=[]
for since,label in [(window,'24h'),(recent,'2h')]:
 errors.append({'window':label,'groups':list(AIErrorLog.objects.filter(created_at__gte=since).values('provider','model_name','status_code','error_type').annotate(count=Count('id'),latest=Max('created_at')).order_by('-count'))})
requests=list(AIRequestLog.objects.filter(created_at__gte=recent).values('provider','status_code','is_success','latency_ms'))
latencies=sorted(x['latency_ms']for x in requests if x['latency_ms']is not None)
# Stat file references only; never export student filenames or file contents.
files=[]
for model in apps.get_app_config('feed').get_models():
 for field in model._meta.fields:
  if field.get_internal_type()!='FileField':continue
  checked=0;missing=[];inaccessible=0
  for identity,name in model.objects.exclude(**{field.name:''}).exclude(**{field.name:None}).values_list('pk',field.name).iterator(chunk_size=500):
   checked+=1
   try:
    if not Path(field.storage.path(name)).is_file():missing.append(str(identity))
   except (OSError,ValueError,NotImplementedError):inaccessible+=1
  if checked:files.append({'model':model.__name__,'field':field.name,'references_checked':checked,'missing_count':len(missing),'missing_ids':missing[:25],'uncheckable':inaccessible})
security={'debug':settings.DEBUG,'allowed_hosts':settings.ALLOWED_HOSTS,'secret_key_is_default':settings.SECRET_KEY=='schoolnet-local-secret-key-change-in-production-2026','password_validators_count':len(settings.AUTH_PASSWORD_VALIDATORS),'session_cookie_secure':settings.SESSION_COOKIE_SECURE,'csrf_cookie_secure':settings.CSRF_COOKIE_SECURE,'session_cookie_httponly':settings.SESSION_COOKIE_HTTPONLY,'csrf_cookie_httponly':settings.CSRF_COOKIE_HTTPONLY,'secure_ssl_redirect':settings.SECURE_SSL_REDIRECT,'hsts_seconds':settings.SECURE_HSTS_SECONDS,'trust_proxy_https':bool(getattr(settings,'SECURE_PROXY_SSL_HEADER',None)),'csrf_trusted_origins_count':len(settings.CSRF_TRUSTED_ORIGINS),'ai_worker_concurrency':settings.AI_WORKER_CONCURRENCY,'ai_student_reserved':settings.AI_STUDENT_RESERVED,'ai_job_timeout':settings.AI_JOB_TIMEOUT,'ai_request_deadline':settings.AI_REQUEST_DEADLINE}
print(json.dumps({'db_read_only':readonly,'db_bytes':db[0],'db_active_connections':db[1],'db_idle_in_transaction':db[2],'oldest_other_transaction_seconds':db[3],'security':security,'ai_settings':{'enabled':row.get('is_enabled'),'privacy_mode':row.get('privacy_mode'),'connections':[{'provider':c.get('provider'),'has_key':bool(c.get('api_key')),'has_custom_endpoint':bool(c.get('custom_url')),'quota_limits':{k:v for k,v in c.items()if any(t in k for t in ['rpm','rpd','tpm','tpd','max_parallel'])}}for c in connections],'enabled_model_count':sum(bool(m.get('enabled',True))for m in models if isinstance(m,dict))},'active_jobs':queued,'jobs_24h':list(AIJob.objects.filter(created_at__gte=window).values('kind','status').annotate(count=Count('id')).order_by('kind','status')),'errors':errors,'ai_requests_2h':{'count':len(requests),'successful_api_responses':sum(x['is_success']for x in requests),'status_counts':dict(collections.Counter(str(x['status_code'])for x in requests)),'median_latency_ms':statistics.median(latencies)if latencies else None,'p95_latency_ms':latencies[min(len(latencies)-1,int(.95*len(latencies)))]if latencies else None},'counts':{'submissions':Submission.objects.count(),'assignments':Assignment.objects.count()},'file_references':files},ensure_ascii=False,default=str))
'''
r=command(['docker','exec','-i','-e','PYTHONDONTWRITEBYTECODE=1','-e','PGOPTIONS=-c default_transaction_read_only=on','schoolnet_app','python','-'],90) if False else None
process=subprocess.run(['docker','exec','-i','-e','PYTHONDONTWRITEBYTECODE=1','-e','PGOPTIONS=-c default_transaction_read_only=on','schoolnet_app','python','-'],input=code,text=True,capture_output=True,timeout=90)
try:report['application']=json.loads(process.stdout)
except ValueError:report['application']={'returncode':process.returncode,'error':'diagnostic query failed; raw output withheld'}
# Backup metadata only. No restore, hashing of large archives, or new backups.
backups=[]
for base in [Path('/home/yrsadmin/schoolwork2/backups'),Path('/home/yrsadmin/school-deployments')]:
 if not base.exists():continue
 for p in base.rglob('database.dump'):
  try:
   media=p.parent/'media.tar.gz'
   backups.append({'directory':str(p.parent),'database_bytes':p.stat().st_size,'database_mtime':datetime.datetime.fromtimestamp(p.stat().st_mtime,UTC).isoformat(),'media_exists':media.is_file(),'media_bytes':media.stat().st_size if media.is_file()else None,'checksum_manifest_exists':any((p.parent/n).is_file()for n in ['SHA256SUMS','checksums.json'])})
  except OSError:pass
report['backups_metadata']=sorted(backups,key=lambda x:x['database_mtime'],reverse=True)[:12]
report['containers_after']=containers();report['containers_unchanged']=report['containers_before']==report['containers_after']
report['finished_at_utc']=datetime.datetime.now(UTC).isoformat()
print(json.dumps(report,ensure_ascii=False,indent=2,default=str))
