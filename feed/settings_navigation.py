"""Keep the AI settings section in links and post-save redirects."""
from urllib.parse import urlencode

from django.urls import reverse

AI_SETTINGS_SECTIONS = (
    {'slug': 'connection', 'label': 'Підключення', 'icon': '🔌'},
    {'slug': 'criteria', 'label': 'Критерії', 'icon': '📋'},
    {'slug': 'models', 'label': 'Моделі', 'icon': '🧠'},
    {'slug': 'statistics', 'label': 'Статистика', 'icon': '📊'},
    {'slug': 'errors', 'label': 'Помилки', 'icon': '⚠️'},
)


def ai_settings_section(request):
    action = request.POST.get('action', '')
    default = 'connection'
    if 'criteria' in action or action == 'restore_default_presets':
        default = 'criteria'
    elif 'model' in action:
        default = 'models'
    elif action in ('clear_ai_error_logs', 'generate_ai_error_report', 'mark_all_errors_read'):
        default = 'errors'
    section = request.POST.get('ai_section') or request.GET.get('ai_section') or default
    return section if section in {item['slug'] for item in AI_SETTINGS_SECTIONS} else 'connection'


def ai_settings_url(request, extra_params=None):
    params = {'tab': 'ai', 'ai_section': ai_settings_section(request)}
    days = request.GET.get('stats_days') or request.POST.get('stats_days')
    if days in ('1', '3', '7', '14', '30', 'all'):
        params['stats_days'] = days
    errors_view = request.GET.get('errors_view') or request.POST.get('errors_view')
    if errors_view in ('list', 'analysis'):
        params['errors_view'] = errors_view
    if extra_params:
        params.update(extra_params)
    return f"{reverse('teacher_settings')}?{urlencode(params)}"
