"""Deploy one verified image, with consistent backup and schema-aware rollback."""
import fcntl,gzip,hashlib,json,os,shutil,subprocess,tarfile,time,urllib.request
from pathlib import Path
os.umask(0o077)
root=Path('/home/yrsadmin/schoolwork2')
release=Path('/home/yrsadmin/school-deployments/ai-release-4.8.13-20261108')
source=release/'source'; results=release/'results'; backup=release/'backup';backup.mkdir(exist_ok=True)
manifest=json.loads((source/'RELEASE_SOURCE_MANIFEST.json').read_text())
image='schoolwork2-app:4.8.13-'+manifest['source_digest'][:12]
compose=['docker','compose','-p','schoolwork2','-f',str(root/'compose.yaml'),'-f',str(root/'.release-image.yaml'),'-f',str(root/'compose.override.yaml'),'-f',str(root/'compose.tailscale.yaml')]
def run(cmd,**kwargs):
 return subprocess.run(cmd,cwd=root,check=True,**kwargs)
def step(label,cmd):
 print(json.dumps({'stage':label,'status':'starting'}),flush=True)
 with (results/(label+'.log')).open('w') as log: run(cmd,stdout=log,stderr=subprocess.STDOUT)
 print(json.dumps({'stage':label,'status':'passed'}),flush=True)
def inspect(names):
 rows=json.loads(subprocess.check_output(['docker','inspect',*names]))
 return {r['Name'].lstrip('/'): {'image':r['Image'],'image_tag':r['Config']['Image'],'mounts':sorted((x['Source'],x['Destination'],x['RW'])for x in r['Mounts']),'networks':sorted(r['NetworkSettings']['Networks']),'ports':r['HostConfig']['PortBindings'],'command':r['Config']['Cmd'],'health':r['State'].get('Health',{}).get('Status'),'restart_count':r['RestartCount']}for r in rows}
for name,digest in manifest['files'].items():
 if hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:raise SystemExit('Source mismatch: '+name)
log=(results/'release-tests.log').read_text()
if 'Ran 998 tests' not in log or '\nOK (skipped=2)' not in log:raise SystemExit('Final image suite has not passed.')
lock=(root/'.maintenance.lock').open('r');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
original=inspect(['schoolnet_app','schoolnet_ai_worker','schoolnet_postgres','schooljournal-web-1','school_test-app-1'])
report={'image':image,'digest':manifest['source_digest'],'before':original,'status':'preparing'}
(results/'deployment.json').write_text(json.dumps(report,indent=2))
(backup/'release-image.yaml').write_bytes((root/'.release-image.yaml').read_bytes())
existing=[];missing=[]
with tarfile.open(backup/'source-before.tar.gz','w:gz') as tar:
 for name in manifest['files']:
  target=root/name
  if target.is_file():tar.add(target,arcname=name,recursive=False);existing.append(name)
  else:missing.append(name)
(backup/'new-source-files.json').write_text(json.dumps(missing))
stopped=False;installed=False
try:
 step('stop-writers',compose+['stop','app','ai_worker']);stopped=True
 with (backup/'database.dump').open('wb') as stream:
  run(['docker','exec','schoolnet_postgres','sh','-c','exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c'],stdout=stream)
 with (backup/'database.dump').open('rb') as stream:
  run(['docker','exec','-i','schoolnet_postgres','pg_restore','--list'],stdin=stream,stdout=subprocess.DEVNULL)
 with (backup/'media.tar.gz').open('wb') as stream:
  run(['docker','run','--rm','--network','none','--volumes-from','schoolnet_app:ro',original['schoolnet_app']['image'],'tar','--hard-dereference','-czf','-','-C','/app/media','.'],stdout=stream)
 with gzip.open(backup/'media.tar.gz','rb') as stream:
  while stream.read(1024*1024):pass
 hashes={n:hashlib.file_digest((backup/n).open('rb'),'sha256').hexdigest() for n in ['database.dump','media.tar.gz','source-before.tar.gz']}
 (backup/'checksums.json').write_text(json.dumps(hashes,indent=2));report['backup_verified']=True
 print(json.dumps({'stage':'backup','status':'verified','bytes':{n:(backup/n).stat().st_size for n in hashes}}),flush=True)
 for name in manifest['files']:
  target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,target)
 shutil.copy2(source/'RELEASE_SOURCE_MANIFEST.json',root/'RELEASE_SOURCE_MANIFEST.json')
 (root/'.release-image.yaml').write_text(json.dumps({'services':{s:{'image':image,'pull_policy':'never'}for s in ['app','ai_worker']}},indent=2)+'\n')
 installed=True
 step('start-release',compose+['up','-d','--no-deps','--wait','--wait-timeout','240','app','ai_worker'])
 step('django-check',compose+['exec','-T','app','python','manage.py','check'])
 step('migrations-check',compose+['exec','-T','app','python','manage.py','migrate','--check'])
 step('source-check',['python3',str(root/'scripts/verify_release.py')])
 current=inspect(list(original));report['after']=current
 for name in ['schoolnet_app','schoolnet_ai_worker']:
  for field in ['mounts','networks','ports','command']:
   if current[name][field]!=original[name][field]:raise RuntimeError('Runtime configuration changed: '+name+'/'+field)
  if current[name]['health']!='healthy':raise RuntimeError('Unhealthy service: '+name)
 if current['schoolnet_app']['image']!=current['schoolnet_ai_worker']['image']:raise RuntimeError('Mixed application images')
 for name in ['schoolnet_postgres','schooljournal-web-1','school_test-app-1']:
  if current[name]!=original[name]:raise RuntimeError('Unrelated service changed: '+name)
 with urllib.request.urlopen('http://127.0.0.1:8080/healthz/',timeout=10) as response:
  report['health']={'status':response.status,'body':json.loads(response.read())}
 if report['health']['body']['version']!='4.8.13':raise RuntimeError('Wrong live version')
 report['status']='deployed';print(json.dumps({'stage':'deployment','status':'deployed','version':'4.8.13'}),flush=True)
except Exception as error:
 report['status']='failed';report['error_type']=type(error).__name__
 if installed:
  # New non-null AIJob fields have no database defaults. Downgrade schema
  # with the new image before restoring the old application code.
  subprocess.run(compose+['stop','app','ai_worker'],cwd=root,capture_output=True)
  step('rollback-migration',compose+['run','--rm','--no-deps','-T','app','python','manage.py','migrate','feed','0072','--noinput'])
  with tarfile.open(backup/'source-before.tar.gz') as tar:tar.extractall(root,filter='data')
  for name in missing:
   path=root/name
   if path.is_file():path.unlink()
  shutil.copy2(backup/'release-image.yaml',root/'.release-image.yaml')
 if stopped:step('rollback-start',compose+['up','-d','--no-deps','--wait','--wait-timeout','240','app','ai_worker'])
 report['rollback']='completed';raise
finally:
 (results/'deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
