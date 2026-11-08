import datetime,json,subprocess
from pathlib import Path
code=r'''
import ast,json,os,re
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','schoolnet.settings')
import django;django.setup()
from django.db import connection
with connection.cursor()as cursor:
 cursor.execute('SHOW transaction_read_only')
 if cursor.fetchone()[0]!='on':raise RuntimeError('Read-only guard missing')
from unittest.mock import patch
from feed.models import AISettings,TailscaleConnection,AIRequestLog
from feed.ai_model_catalog import get_model_capabilities
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta
models=json.loads(AISettings.objects.filter(pk=1).values_list('saved_models_list',flat=True).first()or'[]')
wanted=['@cf/meta/llama-4-scout-17b-16e-instruct','@cf/qwen/qwen3.8-27b','@cf/zai-org/glm-4.7-flash']
capabilities=[]
# Avoid touching or expiring any shared file cache during capability inspection.
with patch('feed.ai_concurrency.get_model_context_limit',return_value=None):
 for name in wanted:
  matches=[x for x in models if x.get('name')==name]
  caps=get_model_capabilities(name,'cloudflare')
  capabilities.append({'model':name,'enabled_in_queue':any(x.get('enabled',True)for x in matches),'supports_vision':caps.get('supports_vision'),'max_images':caps.get('max_images')})
missing=[];refs=set();syntax_errors=[];python_count=0
for p in Path('/app/templates').rglob('*.html'):
 for name in re.findall(r'{%\s*static\s+["\']([^"\']+)["\']\s*%}',p.read_text()):
  refs.add(name)
  if not (Path('/app/staticfiles')/name).is_file():missing.append({'template':str(p.relative_to('/app')),'asset':name})
for folder in ['feed','schoolnet','scripts','school_sync','cloud_backup','runner']:
 for p in (Path('/app')/folder).rglob('*.py'):
  python_count+=1
  try:ast.parse(p.read_text(),filename=str(p))
  except (SyntaxError,UnicodeError)as error:syntax_errors.append({'file':str(p.relative_to('/app')),'error_type':type(error).__name__})
requests=list(AIRequestLog.objects.filter(created_at__gte=timezone.now()-timedelta(hours=2)).values('provider','action','status_code','is_success').annotate(count=Count('id')))
print(json.dumps({'cloudflare_capabilities':capabilities,'template_literal_static_assets_count':len(refs),'missing_literal_static_assets':missing,'python_files_parsed':python_count,'syntax_errors':syntax_errors,'ai_requests_last_2h_by_action':requests,'public_access':TailscaleConnection.objects.filter(pk=1).values('enabled','allow_without_link').first()},ensure_ascii=False))
'''
r=subprocess.run(['docker','exec','-i','-e','PYTHONDONTWRITEBYTECODE=1','-e','PGOPTIONS=-c default_transaction_read_only=on','schoolnet_app','python','-'],input=code,text=True,capture_output=True)
if r.returncode:raise SystemExit('Read-only source audit failed; raw output withheld.')
print(r.stdout)
