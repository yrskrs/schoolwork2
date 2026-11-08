import datetime,json,subprocess,time
from pathlib import Path
now=datetime.datetime.now(datetime.timezone.utc)
containers=[]
ids=subprocess.check_output(['docker','ps','-aq','--filter','name=schoolnet_'],text=True).splitlines()
if ids:
 rows=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
 for x in rows:containers.append({'name':x['Name'],'id':x['Id'],'image':x['Config']['Image'],'status':x['State']['Status'],'exit_code':x['State']['ExitCode'],'finished_at':x['State']['FinishedAt'],'started_at':x['State']['StartedAt'],'error':x['State']['Error'],'oom_killed':x['State']['OOMKilled'],'health':x['State'].get('Health',{}).get('Status')})
p=subprocess.run(['docker','events','--since','2026-11-08T19:34:00Z','--until',now.isoformat(),'--filter','type=container','--filter','label=com.docker.compose.project=schoolwork2','--format','{{json .}}'],text=True,capture_output=True,timeout=20)
events=[]
for line in p.stdout.splitlines():
 x=json.loads(line);a=x.get('Actor',{}).get('Attributes',{});action=x.get('Action')
 if action.startswith('exec_')or action=='exec_die':continue
 events.append({'time_utc':datetime.datetime.fromtimestamp(x['time'],datetime.timezone.utc).isoformat(),'action':action,'name':a.get('name'),'image':a.get('image'),'service':a.get('com.docker.compose.service'),'exit_code':a.get('exitCode')})
base=Path('/home/yrsadmin/school-deployments');deployments=[]
for d in sorted([p for p in base.iterdir()if p.is_dir()],key=lambda p:p.stat().st_mtime,reverse=True)[:5]:
 reports=[]
 for filename in ['results/deployment.json','results/report.json','report.json']:
  p=d/filename
  if p.is_file():
   try:
    row=json.loads(p.read_text());reports.append({'file':filename,'status':row.get('status'),'image':row.get('image')})
   except ValueError:reports.append({'file':filename,'status':'incomplete JSON'})
 deployments.append({'directory':d.name,'mtime_utc':datetime.datetime.fromtimestamp(d.stat().st_mtime,datetime.timezone.utc).isoformat(),'reports':reports})
print(json.dumps({'time_utc':now.isoformat(),'containers':containers,'events':events,'recent_deployment_metadata':deployments},ensure_ascii=False,indent=2))
