import datetime,json,subprocess,urllib.request
from pathlib import Path
root=Path('/home/yrsadmin/schoolwork2')
with urllib.request.urlopen('http://127.0.0.1:8080/healthz/',timeout=10)as response:health={'status':response.status,'body':json.loads(response.read())}
rows=json.loads(subprocess.check_output(['docker','inspect','schoolnet_app','schoolnet_ai_worker','schoolnet_postgres'],text=True))
print(json.dumps({'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'health':health,'server_git_head':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),'containers':{r['Name'].lstrip('/'): {'image':r['Config']['Image'],'id':r['Id'],'status':r['State']['Status'],'health':r['State'].get('Health',{}).get('Status'),'restart_count':r['RestartCount']}for r in rows}},ensure_ascii=False,indent=2))
