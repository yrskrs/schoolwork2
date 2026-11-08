"""Run production code against a private server-side DB copy and read-only media."""
import hashlib,json,os,statistics,subprocess,sys,time
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','schoolnet.settings')
import django
django.setup()
from django.db import connections
from django.utils import timezone
from feed.models import AIJob,Submission,AISettings
from feed.ai_jobs import enqueue_submission_job
from feed.ai_privacy import for_submission
folder=Path('/trial-results');folder.mkdir(exist_ok=True)
ids=[602,450,436,435,433,427,418,410,372,347]
# All writes below target the explicitly restored test database.
from django.conf import settings
if not settings.DATABASES['default']['NAME'].startswith('ai_real_trial_'):
 raise SystemExit('Refusing to run outside an explicitly named trial database.')
from django.core.management import call_command
call_command('migrate',verbosity=0)
AIJob.objects.all().delete()
# Save the copied settings exactly; never change model priorities or quotas.
config=AISettings.get_solo()
secrets=[c.get('api_key','')for c in config.provider_connections]+[config.api_key,config.backup_api_key]
initial={};jobs={}
for identity in ids:
 sub=Submission.objects.select_related('assignment__teacher__user','class_group').get(pk=identity)
 initial[identity]={'grade':sub.grade,'student_ai_checked':sub.student_ai_checked}
 job=enqueue_submission_job(sub,'teacher_check',user=sub.assignment.teacher.user,
  parameters={'ignore_strict_privacy':True})
 jobs[identity]=str(job.pk)
start=time.monotonic();children=[];peak_running=0;peak_worker_rss_kb=0
try:
 while time.monotonic()-start<900:
  children=[p for p in children if p.poll() is None]
  pending=AIJob.objects.filter(pk__in=jobs.values(),status__in=['queued','running']).count()
  if not pending and not children:break
  peak_running=max(peak_running,AIJob.objects.filter(pk__in=jobs.values(),status='running').count())
  (folder/'progress.json').write_text(json.dumps({'elapsed_seconds':round(time.monotonic()-start,1),'jobs':[{'submission_id':i,'status':AIJob.objects.get(pk=j).status}for i,j in jobs.items()]},indent=2))
  while pending and len(children)<2:
   children.append(subprocess.Popen([sys.executable,'manage.py','process_ai_jobs','--once'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
  time.sleep(.5)
finally:
 for child in children:
  if child.poll() is None:child.terminate()
 for child in children:
  try:child.wait(timeout=10)
  except subprocess.TimeoutExpired:child.kill();child.wait()
rows=[]
for identity in ids:
 sub=Submission.objects.select_related('assignment__teacher','class_group').get(pk=identity)
 job=AIJob.objects.get(pk=jobs[identity]);result=job.result or {};redactor=for_submission(sub,'redact')
 def clean(value):
  value=redactor.text(str(value or ''))
  for secret in secrets:
   if secret:value=value.replace(secret,'[KEY]')
  return value
 usage=result.get('request_metadata') or sub.get_ai_request_metadata()
 rows.append({'submission_id':identity,'assignment_id':sub.assignment_id,'class':str(sub.class_group),
  'topic':clean(sub.assignment.title),'submitted_at':sub.submitted_at.isoformat(),
  'original_teacher_grade':initial[identity]['grade'],'ai_grade':result.get('suggested_grade'),
  'status':job.status,'error':clean(result.get('error') or sub.ai_error_reason)[:1000],
  'provider':usage.get('provider'),'model':usage.get('model'),
  'usage':usage,'metrics':job.metrics,
  'queue_seconds':round((job.started_at-job.created_at).total_seconds(),3) if job.started_at else None,
  'run_seconds':round((job.finished_at-job.started_at).total_seconds(),3)if job.finished_at and job.started_at else None,
  'total_seconds':round((job.finished_at-job.created_at).total_seconds(),3)if job.finished_at else None,
  'file_types':[Path(str(getattr(f,'file',''))).suffix.lower()for f in sub.get_files()],
  'feedback':clean(result.get('feedback') or sub.ai_feedback),
  'grade_explanation':clean(result.get('grade_explanation')),
  'events':[{'kind':e.get('kind'),'message':clean(e.get('message'))[:400]}for e in job.events],
  'teacher_grade_preserved_in_copy':sub.grade==initial[identity]['grade'],
  'student_attempt_preserved_in_copy':sub.student_ai_checked==initial[identity]['student_ai_checked']})
report={'scope':'server-only restored database; original production rows untouched; read-only production media',
 'privacy':'one-request teacher visual override; text redaction enabled; configured model list unchanged',
 'count':len(rows),'class_count':len({r['class']for r in rows}),'assignment_count':len({r['assignment_id']for r in rows}),
 'distinct_topic_titles':len({r['topic']for r in rows}),'elapsed_seconds':round(time.monotonic()-start,3),
 'peak_running_jobs':peak_running,'success':sum(r['status']=='succeeded'for r in rows),'rows':rows}
(folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items()if k!='rows'},ensure_ascii=False),flush=True)
