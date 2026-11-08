import json,os,secrets,subprocess,time
from pathlib import Path
os.umask(0o077)
root=Path('/home/yrsadmin/school-deployments/ai-release-4.8.13-evidence-20261108')
state=json.loads((root/'results/deployment.json').read_text())
if state.get('status')!='deployed':raise SystemExit('Verified production deployment is required first.')
image=state['image'];identity='ai-real-trial-'+secrets.token_hex(4)
network=identity+'-net';pg=identity+'-pg';worker=identity+'-worker'
output=root/'real-work-results';output.mkdir(mode=0o700,exist_ok=True)
# Equivalent to local prewarming: reuse only previously prepared evidence,
# without copying old grades, jobs, quota state, or model responses.
(output/'cache').mkdir(mode=0o700,exist_ok=True)
subprocess.run(['docker','run','--rm','--network','none',
 '-v','/home/yrsadmin/school-deployments/ai-release-4.8.13-20261108/real-work-results/cache:/source-cache:ro',
 '-v',str(output/'cache')+':/destination-cache',image,'python','-c',
 'import shutil;shutil.copytree("/source-cache","/destination-cache",dirs_exist_ok=True)'],check=True)
password=secrets.token_hex(20);database='ai_real_trial_20261108'
def run(cmd,**kwargs):return subprocess.run(cmd,check=True,**kwargs)
owned_net=owned_pg=owned_worker=False
try:
 run(['docker','network','create','--label','schoolnet.ai-real-trial=true',network],capture_output=True);owned_net=True
 run(['docker','run','-d','--name',pg,'--network',network,'--label','schoolnet.ai-real-trial=true','--memory','512m','--cpus','1','-e','POSTGRES_DB='+database,'-e','POSTGRES_USER=trial','-e','POSTGRES_PASSWORD='+password,'postgres:16'],capture_output=True);owned_pg=True
 for attempt in range(60):
  if subprocess.run(['docker','exec',pg,'pg_isready','-U','trial','-d',database],capture_output=True).returncode==0:break
  time.sleep(1)
 else:raise RuntimeError('Trial database startup failed')
 with (root/'backup/database.dump').open('rb') as stream, (output/'restore.log').open('w') as log:
  run(['docker','exec','-i',pg,'pg_restore','--no-owner','--no-acl','-U','trial','-d',database],stdin=stream,stdout=log,stderr=subprocess.STDOUT)
 print(json.dumps({'stage':'database-copy','status':'restored'}),flush=True)
 command=['docker','run','--name',worker,'--network',network,'--label','schoolnet.ai-real-trial=true','--memory','2g','--cpus','2',
  '-e','PYTHON_DOTENV_DISABLED=1','-e','PYTHONPATH=/app','-e','PYTHONUNBUFFERED=1',
  '-e','DJANGO_SETTINGS_MODULE=schoolnet.settings','-e','SECRET_KEY=server-trial-only-key-not-used-for-production-authentication',
  '-e','DATABASE_URL=postgres://trial:'+password+'@'+pg+':5432/'+database,
  '-e','ROSTER_PEERS=[]','-e','ROSTER_SYNC_TOKEN=','-e','JOURNAL_API_BASE_URL=',
  '-e','AI_JOBS_EAGER=False','-e','AI_WORKER_CONCURRENCY=3','-e','AI_STUDENT_RESERVED=1',
  '-e','AI_CACHE_DIR=/trial-results/cache','-e','CLOUD_IMPORT_ROOT=/trial-results/cloud-imports',
  '--mount','type=volume,source=schoolwork2_app_media,target=/app/media,readonly',
  '-v',str(root/'real_work_trial.py')+':/trial-harness.py:ro','-v',str(output)+':/trial-results',
  image,'python','/trial-harness.py']
 with (output/'runtime.log').open('w') as log:
  process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT);owned_worker=True
  started=time.monotonic();samples=[]
  while process.poll() is None and time.monotonic()-started<1020:
   stats=subprocess.run(['docker','stats','--no-stream','--format','{{json .}}',worker],text=True,capture_output=True)
   if stats.returncode==0:
    try:
     row=json.loads(stats.stdout.strip());samples.append({'elapsed_seconds':round(time.monotonic()-started,1),'memory':row.get('MemUsage'),'cpu':row.get('CPUPerc')})
    except ValueError:pass
   time.sleep(2)
  if process.poll() is None:
   subprocess.run(['docker','stop','-t','5',worker],capture_output=True);process.wait(timeout=15)
  report={'container_returncode':process.returncode,'resource_samples':samples}
  info=subprocess.run(['docker','inspect',worker],text=True,capture_output=True)
  if info.returncode==0:
   state=json.loads(info.stdout)[0]['State'];report['oom_killed']=state.get('OOMKilled');report['exit_code']=state.get('ExitCode')
  (output/'runner.json').write_text(json.dumps(report,indent=2))
  print(json.dumps({k:v for k,v in report.items()if k!='resource_samples'}),flush=True)
  if process.returncode:raise RuntimeError('Work trial failed; private runtime log retained for diagnosis.')
finally:
 if owned_worker:subprocess.run(['docker','rm','-fv',worker],capture_output=True)
 if owned_pg:subprocess.run(['docker','rm','-fv',pg],capture_output=True)
 if owned_net:subprocess.run(['docker','network','rm',network],capture_output=True)
