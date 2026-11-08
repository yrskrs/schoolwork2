"""Offline audit probes: real function bodies, synthetic objects, mocked HTTP.

Run with Python 3 from any directory. Does not import Django, read private data,
send requests, or measure live model performance. Dependency boundaries are
stubbed; this is not a substitute for the Django integration test suite.
"""
import ast
import copy
import json
import logging
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[4]


def module(name, **values):
    obj = types.ModuleType(name)
    obj.__dict__.update(values)
    sys.modules[name] = obj
    return obj


def functions(path, names, target):
    tree = ast.parse(path.read_text())
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign))
                and (getattr(n, 'name', '') in names or
                     isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), target)


module('feed', __path__=[])
module('django')
module('django.core')
module('django.core.cache', caches={})
module('feed.models', DEFAULT_NUS_SYSTEM_PROMPT='NUS', DEFAULT_NUS_GR_SYSTEM_PROMPT='GR',
       DEFAULT_TRADITIONAL_SYSTEM_PROMPT='TRAD', AISettings=NS(objects=NS(first=lambda: NS())))
module('feed.ai_provenance', submission_provenance=lambda submission: [])
service = module('feed.gemini_service', re=re, json=json, logger=logging.getLogger('probe'))
functions(ROOT / 'feed/gemini_service.py', {
    'parse_teacher_specific_task_numbers', 'clean_model_name', 'get_provider_endpoint',
    '_raw_call_ai_api', '_normalize_chat_error', 'api_error_message',
    'GEMINI_API_BASE_URL', 'OPENAI_API_BASE_URL', 'DEEPSEEK_API_BASE_URL',
    'GROQ_API_BASE_URL', 'OPENROUTER_API_BASE_URL', 'CLOUDFLARE_API_BASE_URL',
}, service.__dict__)
context = module('feed.ai_context', __package__='feed')
exec(compile((ROOT / 'feed/ai_context.py').read_text(), str(ROOT / 'feed/ai_context.py'), 'exec'), context.__dict__)

assignment = NS(title='Синтетичне завдання', description='Створити програму.', subject=NS(name='Інформатика'),
                custom_criteria='', allow_ai_usage=True, teacher_ai_instructions='PRIVATE_TEACHER_NOTE',
                get_ai_task_understanding_data=lambda: {})
submission = NS(assignment=assignment, class_group=NS(name='7-А', grade=7),
                is_collective_work=lambda: True, is_group_work=True, ignore_plagiarism=False,
                get_student_full_name=lambda: 'PROFILE_NAME_SENTINEL', group_authors='COAUTHOR_SENTINEL',
                is_resubmission=True, previous_submission=NS(teacher_comment='PREVIOUS_FEEDBACK_SENTINEL'))

results = {}
for compact in (False, True):
    prompt, system = context.build_assessment_request(
        submission, None, [], {}, ['FILE_NAME_SENTINEL.docx: BODY_NAME_SENTINEL; COMMENT_EMAIL_SENTINEL'],
        [], [], [], ai_settings=NS(ai_detector_tolerance_percent=25), compact=compact)
    label = 'compact' if compact else 'standard'
    results[label] = {
        'profile_name_sent': 'PROFILE_NAME_SENTINEL' in prompt,
        'coauthor_field_sent': 'COAUTHOR_SENTINEL' in prompt,
        'file_name_sent': 'FILE_NAME_SENTINEL' in prompt,
        'body_name_sent': 'BODY_NAME_SENTINEL' in prompt,
        'comment_email_sent': 'COMMENT_EMAIL_SENTINEL' in prompt,
        'private_teacher_note_sent': 'PRIVATE_TEACHER_NOTE' in prompt,
        'previous_feedback_sent': 'PREVIOUS_FEEDBACK_SENTINEL' in prompt,
        'prompt_chars': len(prompt), 'system_chars': len(system),
    }
    assert not results[label]['profile_name_sent']
    assert not results[label]['coauthor_field_sent']
    assert all(results[label][k] for k in ('file_name_sent', 'body_name_sent', 'comment_email_sent', 'private_teacher_note_sent'))
    assert not results[label]['previous_feedback_sent']

# Media processing is a separate boundary, intentionally identity-stubbed here.
# These probes verify the actual adapter's field inclusion and three-image cap.
context.media_for_provider = lambda media, provider: media
module('feed.ai_payload', optimize_media=lambda media, provider: media,
       pack_document_pages=lambda media: media,
       groq_output_budget=lambda model, prompt, system, media, requested: requested,
       encode_payload=lambda payload: json.dumps(payload).encode(), OPENROUTER_MAX_REQUEST_BYTES=7500000)
captured = []


def http(url, payload, **kwargs):
    captured.append(copy.deepcopy(payload))
    return 200, {'candidates': [{'content': {'parts': [{'text': '{}'}]}}],
                 'choices': [{'message': {'content': '{}'}}]}, '{}'


service._http_post_json = http
service.__package__ = 'feed'
for provider, model in [('gemini', 'synthetic-flash'), ('openrouter', 'synthetic/model'),
                        ('groq', 'qwen/qwen3.8-27b')]:
    media = [{'mime_type': 'image/png', 'data': 'SYNTHETIC_IMAGE_BYTES', 'source': f'FILE_NAME_SENTINEL-{i}'}
             for i in range(5)]
    response = service._raw_call_ai_api(prompt, system, media, provider=provider,
                                      api_key='synthetic-key', model_name=model, json_mode=True)
    assert response[0] == 200 and response[1] == '{}', response
    payload = captured[-1]
    encoded = json.dumps(payload, ensure_ascii=False)
    count = (sum('inlineData' in p for p in payload['contents'][0]['parts']) if provider == 'gemini'
             else sum(p.get('type') == 'image_url' for p in payload['messages'][-1]['content']))
    results[provider] = {'images_in': 5, 'images_sent': count,
                         'source_filename_sent': 'FILE_NAME_SENTINEL' in encoded,
                         'body_name_sent': 'BODY_NAME_SENTINEL' in encoded}
    assert count == (3 if provider == 'groq' else 5)
    assert results[provider]['source_filename_sent'] and results[provider]['body_name_sent']

template = (ROOT / 'templates/feed/ai_batch_check.html').read_text()
results['batch_template'] = {
    'reads_delay_element': "document.getElementById('batch-delay').value" in template,
    'declares_delay_element': bool(re.search(r'id=[\"\']batch-delay[\"\']', template)),
}
assert results['batch_template']['reads_delay_element']
assert not results['batch_template']['declares_delay_element']

def incompatible_http(url, payload, **kwargs):
    captured.append(copy.deepcopy(payload))
    return 400, {'error': {'message': 'Synthetic invalid argument'}}, 'Synthetic invalid argument'


service._http_post_json = incompatible_http
before = len(captured)
service._raw_call_ai_api('synthetic', 'synthetic rules', [], provider='gemini',
                         api_key='synthetic-key', model_name='synthetic-flash',
                         json_mode=True, thinking_budget=0)
results['gemini_compatibility'] = {'http_calls_for_one_adapter_call': len(captured) - before}
assert results['gemini_compatibility']['http_calls_for_one_adapter_call'] == 4

authorship = {}
functions(ROOT / 'feed/ai_provenance.py', {'normalize_authorship'}, authorship)
weak_result = authorship['normalize_authorship']({
    'ai_generated_percent': 90,
    'ai_generated_details': 'Синтетичне припущення про стиль; без незалежних доказів',
}, allowed=False, provenance=[])
results['authorship'] = {'claim_without_provenance_flagged': weak_result['ai_generated_detected'],
                         'confidence': weak_result['ai_generated_confidence']}
assert results['authorship']['claim_without_provenance_flagged']
print(json.dumps({'scope': 'offline synthetic probes; no network; not Django integration tests',
                  'results': results}, ensure_ascii=False, indent=2))
