"""Small, sourced catalog; quotas are examples, never enforced by SchoolNet."""
CATALOG_CHECKED_AT = '03.10.2026'
PROVIDER_CATALOG = [
    {'provider': 'gemini', 'title': 'Google Gemini', 'source': 'https://ai.google.dev/gemini-api/docs/models',
     'limits_url': 'https://ai.google.dev/gemini-api/docs/rate-limits',
     'quota_note': 'RPM / RPD / TPM залежать від моделі й рівня проєкту. Точні значення — у Google AI Studio.',
     'models': [{'name': name, 'quota': 'За рівнем проєкту', 'description': description} for name, description in [
         ('gemini-3.8-flash', 'Flash для складних і повсякденних завдань.'),
         ('gemini-3.6-flash', 'Flash попереднього покоління.'),
         ('gemini-3.1-flash-lite', 'Легка модель для великої кількості запитів.'),
         ('gemini-3.1-pro-preview', 'Попередня Pro-версія; доступність може змінюватися.'),
         ('gemini-2.5-flash', 'Попередня версія; доступ обмежено проєктами, які вже її використовували.')]]},
    {'provider': 'openai', 'title': 'OpenAI', 'source': 'https://developers.openai.com/api/docs/models/gpt-4.1-mini',
     'limits_url': 'https://developers.openai.com/api/docs/guides/rate-limits',
     'quota_note': 'Квоти залежать від usage tier організації. Наведений Tier 1 — орієнтир, а не квота вашого ключа.',
     'models': [
         {'name': 'gpt-4.1-mini', 'description': 'Текст і зображення; контекст 1 047 576 токенів.', 'quota': 'Tier 1: 500 RPM · 10 000 RPD · 200 000 TPM'},
         {'name': 'gpt-4o-mini', 'description': 'Компактна модель для тексту й зображень.', 'quota': 'За usage tier; див. кабінет'}]},
    {'provider': 'deepseek', 'title': 'DeepSeek', 'source': 'https://api-docs.deepseek.com/quick_start/pricing',
     'limits_url': 'https://api-docs.deepseek.com/quick_start/rate_limit/',
     'quota_note': 'Провайдер публікує ліміт одночасних запитів, а не універсальні RPM / RPD / TPM.',
     'models': [
         {'name': 'deepseek-flash', 'description': 'Швидка модель DeepSeek.', 'quota': 'До 2 500 одночасних запитів на акаунт'},
         {'name': 'deepseek-v4-pro', 'description': 'Модель Pro для складніших завдань.', 'quota': 'До 500 одночасних запитів на акаунт'}]},
    {'provider': 'groq', 'title': 'Groq', 'source': 'https://console.groq.com/docs/models',
     'limits_url': 'https://console.groq.com/docs/rate-limits',
     'quota_note': 'Орієнтири для Free Plan; фактичні квоти організації — у кабінеті Groq.',
     'models': [
         {'name': 'openai/gpt-oss-120b', 'description': 'Текстова модель GPT OSS; контекст 131 072 токени.', 'quota': 'Free: 30 RPM · 1 000 RPD · 8 000 TPM'},
         {'name': 'openai/gpt-oss-20b', 'description': 'Компактна текстова модель GPT OSS.', 'quota': 'Free: 30 RPM · 1 000 RPD · 8 000 TPM'}]},
    {'provider': 'openrouter', 'title': 'OpenRouter', 'source': 'https://openrouter.ai/models',
     'limits_url': 'https://openrouter.ai/docs/api_reference/limits',
     'quota_note': 'Квоти залежать від маршруту, постачальника й акаунта; безкоштовні варіанти мають окремі обмеження.',
     'models': [
         {'name': 'openai/gpt-4.1-mini', 'description': 'GPT-4.1 Mini через OpenRouter.', 'quota': 'За маршрутом і акаунтом'},
         {'name': 'google/gemini-2.5-flash', 'description': 'Gemini через OpenRouter; перевірте доступність маршруту.', 'quota': 'За маршрутом і акаунтом'}]},
    {'provider': 'custom', 'title': 'Власний OpenAI-сумісний API', 'source': '', 'limits_url': '',
     'quota_note': 'Назву моделі, Base URL і квоти надає ваш сервер. Вкажіть точний ідентифікатор вручну.', 'models': []},
]


def infer_model_provider(name, default='gemini'):
    """Compatibility for saved lists created before provider was recorded."""
    name = str(name).lower()
    if name.startswith('gemini-') or name.startswith('models/gemini-'): return 'gemini'
    if name.startswith('deepseek-'): return 'deepseek'
    if name.startswith(('gpt-', 'o1', 'o3', 'o4')): return 'openai'
    if name.startswith(('llama-', 'mixtral-')): return 'groq'
    if '/' in name: return 'openrouter'
    return default
