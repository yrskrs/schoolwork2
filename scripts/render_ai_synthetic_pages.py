"""Render the real batch template with synthetic context; no database needed."""
import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=Path('/tmp/ai-batch-browser.html'))
parser.add_argument('--viewer-output', type=Path)
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolnet.test_settings'
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
import django
django.setup()
from django.template.loader import get_template
from django.test import RequestFactory

request = RequestFactory().get('/teacher/ai/batch-check/')
request.user = SimpleNamespace(pk=1, is_authenticated=True, is_superuser=True,
    username='synthetic', get_full_name=lambda: 'Synthetic Teacher')
html = get_template('feed/ai_batch_check.html').render({'request': request, 'user': request.user,
    'csrf_token': 'synthetic-token', 'ai_settings': SimpleNamespace(has_usable_models=True, is_enabled=True),
    'criteria_presets': [], 'all_classes': [], 'all_assignments': [],
    'total_submissions': 2, 'ungraded_count': 2, 'ai_evaluated_count': 0})
args.output.write_text(html)
if args.viewer_output:
    from django.core.management import call_command
    from django.urls import reverse
    from feed.tests_ai_connections import AIConnectionsTests
    from django.test import Client
    call_command('migrate', verbosity=0)
    fixture = AIConnectionsTests('test_any_number_of_providers_and_same_provider_keys_have_exact_queue_order')
    fixture.client = Client()
    fixture.setUp()
    response = fixture.client.get(reverse('view_file', args=[fixture.sub.pk]))
    if response.status_code != 200:
        raise RuntimeError('Synthetic teacher viewer failed to render')
    args.viewer_output.write_bytes(response.content)
