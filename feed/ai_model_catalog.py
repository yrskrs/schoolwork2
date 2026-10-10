"""Small, sourced catalog with capabilities (vision, context size, quotas); quotas are examples, never enforced by SchoolNet."""
CATALOG_CHECKED_AT = '03.10.2026'

PROVIDER_CATALOG = [
    {
        'provider': 'gemini', 'title': 'Google Gemini', 'source': 'https://ai.google.dev/gemini-api/docs/models',
        'limits_url': 'https://ai.google.dev/gemini-api/docs/rate-limits',
        'quota_note': 'RPM / RPD / TPM залежать від моделі й рівня проєкту. Точні значення — у Google AI Studio.',
        'models': [
            {'name': 'gemini-3.8-flash', 'description': 'Flash для складних і повсякденних завдань.', 'quota': 'За рівнем проєкту', 'supports_vision': True, 'context_tokens': 1048576, 'context_display': '1 048 576 токенів (~1M)'},
            {'name': 'gemini-3.6-flash', 'description': 'Flash попереднього покоління.', 'quota': 'За рівнем проєкту', 'supports_vision': True, 'context_tokens': 1048576, 'context_display': '1 048 576 токенів (~1M)'},
            {'name': 'gemini-3.1-flash-lite', 'description': 'Легка модель для великої кількості запитів.', 'quota': 'За рівнем проєкту', 'supports_vision': True, 'context_tokens': 1048576, 'context_display': '1 048 576 токенів (~1M)'},
            {'name': 'gemini-3.1-pro-preview', 'description': 'Попередня Pro-версія; доступність може змінюватися.', 'quota': 'За рівнем проєкту', 'supports_vision': True, 'context_tokens': 1048576, 'context_display': '1 048 576 токенів (~1M)'},
            {'name': 'gemini-2.5-flash', 'description': 'Попередня версія; доступ обмежено проєктами, які вже її використовували.', 'quota': 'За рівнем проєкту', 'supports_vision': True, 'context_tokens': 1048576, 'context_display': '1 048 576 токенів (~1M)'},
        ]
    },
    {
        'provider': 'openai', 'title': 'OpenAI', 'source': 'https://developers.openai.com/api/docs/models/gpt-4.1-mini',
        'limits_url': 'https://developers.openai.com/api/docs/guides/rate-limits',
        'quota_note': 'Квоти залежать від usage tier організації. Наведений Tier 1 — орієнтир, а не квота вашого ключа.',
        'models': [
            {'name': 'gpt-4.1-mini', 'description': 'Текст і зображення; контекст 1 047 576 токенів.', 'quota': 'Tier 1: 500 RPM · 10 000 RPD · 200 000 TPM', 'supports_vision': True, 'context_tokens': 1047576, 'context_display': '1 047 576 токенів (~1M)'},
            {'name': 'gpt-4o', 'description': 'Флагманська модель OpenAI для тексту й зображень.', 'quota': 'За usage tier; див. кабінет', 'supports_vision': True, 'context_tokens': 128000, 'context_display': '128 000 токенів (~128K)'},
            {'name': 'gpt-4o-mini', 'description': 'Компактна модель для тексту й зображень.', 'quota': 'За usage tier; див. кабінет', 'supports_vision': True, 'context_tokens': 128000, 'context_display': '128 000 токенів (~128K)'},
            {'name': 'o3-mini', 'description': 'Модель логічних міркувань; працює виключно з текстом.', 'quota': 'За usage tier', 'supports_vision': False, 'context_tokens': 200000, 'context_display': '200 000 токенів (тільки текст)'},
        ]
    },
    {
        'provider': 'deepseek', 'title': 'DeepSeek', 'source': 'https://api-docs.deepseek.com/quick_start/pricing',
        'limits_url': 'https://api-docs.deepseek.com/quick_start/rate_limit/',
        'quota_note': 'Провайдер публікує ліміт одночасних запитів, а не універсальні RPM / RPD / TPM.',
        'models': [
            {'name': 'deepseek-flash', 'description': 'Швидка текстова модель DeepSeek.', 'quota': 'До 2 500 одночасних запитів на акаунт', 'supports_vision': False, 'context_tokens': 64000, 'context_display': '64 000 токенів (~64K, тільки текст)'},
            {'name': 'deepseek-v4-pro', 'description': 'Модель Pro для складніших текстових завдань.', 'quota': 'До 500 одночасних запитів на акаунт', 'supports_vision': False, 'context_tokens': 128000, 'context_display': '128 000 токенів (~128K, тільки текст)'},
        ]
    },
    {
        'provider': 'groq', 'title': 'Groq', 'source': 'https://console.groq.com/docs/models',
        'limits_url': 'https://console.groq.com/docs/rate-limits',
        'quota_note': 'Орієнтири для Free Plan; фактичні квоти організації — у кабінеті Groq.',
        'models': [
            {'name': 'openai/gpt-oss-120b', 'description': 'Текстова модель GPT OSS (Free TPM ліміт ~8 000).', 'quota': 'Free: 30 RPM · 1 000 RPD · 8 000 TPM', 'supports_vision': False, 'context_tokens': 6500, 'context_display': '6 500 токенів (Free TPM ліміт, тільки текст)', 'max_images': 0},
            {'name': 'openai/gpt-oss-20b', 'description': 'Компактна текстова модель GPT OSS.', 'quota': 'Free: 30 RPM · 1 000 RPD · 8 000 TPM', 'supports_vision': False, 'context_tokens': 6500, 'context_display': '6 500 токенів (Free TPM ліміт, тільки текст)', 'max_images': 0},
            {'name': 'llama-3.3-70b-versatile', 'description': 'Швидка мовна модель LLaMA 3.3 від Meta.', 'quota': 'Free: 30 RPM · 6 000 TPM', 'supports_vision': False, 'context_tokens': 6000, 'context_display': '6 000 токенів (Free TPM ліміт, тільки текст)', 'max_images': 0},
            {'name': 'qwen/qwen3.8-27b', 'description': 'Мультимодальна модель Qwen із підтримкою зображень (до 3 фото).', 'quota': 'Free: 30 RPM · 7 000 ITPM', 'supports_vision': True, 'context_tokens': 6000, 'context_display': '6 000 токенів (Free ITPM ліміт, до 3 фото)', 'max_images': 3},
        ]
    },
    {
        'provider': 'openrouter', 'title': 'OpenRouter', 'source': 'https://openrouter.ai/models',
        'limits_url': 'https://openrouter.ai/docs/api_reference/limits',
        'quota_note': 'Квоти залежать від маршруту, постачальника й акаунта; безкоштовні варіанти мають окремі обмеження.',
        'models': [
            {'name': 'openai/gpt-4.1-mini', 'description': 'GPT-4.1 Mini через OpenRouter.', 'quota': 'За маршрутом і акаунтом', 'supports_vision': True, 'context_tokens': 1047576, 'context_display': '1 047 576 токенів (~1M)'},
            {'name': 'google/gemini-2.5-flash', 'description': 'Gemini через OpenRouter; перевірте доступність маршруту.', 'quota': 'За маршрутом і акаунтом', 'supports_vision': True, 'context_tokens': 1000000, 'context_display': '~1M токенів'},
            {'name': 'deepseek/deepseek-chat', 'description': 'DeepSeek Chat через OpenRouter.', 'quota': 'За маршрутом і акаунтом', 'supports_vision': False, 'context_tokens': 64000, 'context_display': '64 000 токенів (тільки текст)'},
        ]
    },
    {
        'provider': 'cloudflare', 'title': 'Cloudflare Workers AI', 'source': 'https://developers.cloudflare.com/workers-ai/models/',
        'limits_url': 'https://developers.cloudflare.com/workers-ai/platform/limits/',
        'quota_note': 'ШІ-моделі на платформі Cloudflare Workers AI. Вкажіть API Token та Account ID.',
        'models': [
            {'name': '@cf/meta/llama-3.3-70b-instruct', 'description': 'Потужна відкрита модель Meta LLaMA 3.3 на Cloudflare.', 'quota': 'Згідно з тарифом Cloudflare', 'supports_vision': False, 'context_tokens': 128000, 'context_display': '128 000 токенів (~128K, тільки текст)'},
            {'name': '@cf/meta/llama-3.1-8b-instruct', 'description': 'Швидка та легка модель LLaMA 3.1 8B.', 'quota': 'Згідно з тарифом Cloudflare', 'supports_vision': False, 'context_tokens': 128000, 'context_display': '128 000 токенів (~128K, тільки текст)'},
            {'name': '@cf/meta/llama-3.2-11b-vision-instruct', 'description': 'Мультимодальна модель Meta з підтримкою зображень.', 'quota': 'Згідно з тарифом Cloudflare', 'supports_vision': True, 'context_tokens': 128000, 'context_display': '128 000 токенів (~128K, текст і фото)'},
            {'name': '@cf/deepseek-ai/deepseek-r1-distill-qwen-32b', 'description': 'Модель міркувань DeepSeek R1 на базі Qwen 32B.', 'quota': 'Згідно з тарифом Cloudflare', 'supports_vision': False, 'context_tokens': 32768, 'context_display': '32 768 токенів (~32K, тільки текст)'},
        ]
    },
    {
        'provider': 'custom', 'title': 'Власний OpenAI-сумісний API', 'source': '', 'limits_url': '',
        'quota_note': 'Назву моделі, Base URL і квоти надає ваш сервер. Вкажіть точний ідентифікатор вручну.',
        'models': []
    },
]


def infer_model_provider(name, default='gemini'):
    """Compatibility for saved lists created before provider was recorded."""
    name = str(name).lower()
    if name.startswith('@cf/'): return 'cloudflare'
    if name.startswith('gemini-') or name.startswith('models/gemini-'): return 'gemini'
    if name.startswith('deepseek-'): return 'deepseek'
    if name.startswith(('gpt-', 'o1', 'o3', 'o4')): return 'openai'
    if name.startswith(('llama-', 'mixtral-')): return 'groq'
    if '/' in name: return 'openrouter'
    return default


def get_model_capabilities(model_name, provider=None):
    """
    Повертає характеристики моделі:
    - supports_vision: bool (чи підтримує зображення)
    - context_tokens: int (розмір контексту в токенах)
    - context_display: str (людинозрозумілий опис розміру)
    - is_context_flagged: bool (чи зафіксовано помилку перевищення контексту)
    - context_flag_error: str (текст помилки обсягу)
    """
    name = str(model_name or '').strip().lower()
    prov = (provider or infer_model_provider(name)).lower().strip()

    base_caps = None

    for group in PROVIDER_CATALOG:
        if group['provider'] == prov:
            for m in group['models']:
                if m['name'].lower() == name:
                    base_caps = {
                        'supports_vision': m.get('supports_vision', True),
                        'context_tokens': m.get('context_tokens', 128000),
                        'context_display': m.get('context_display', '128 000 токенів'),
                        'max_images': m.get('max_images'),
                    }
                    break
            if base_caps:
                break

    if not base_caps:
        # Евристичні правила для кастомних/нових моделей
        if prov == 'cloudflare' or name.startswith('@cf/'):
            has_vision = any(v in name for v in ('vision', 'vl', 'multimodal', '11b-vision'))
            ctx = 32768 if '32b' in name else 128000
            base_caps = {
                'supports_vision': has_vision,
                'context_tokens': ctx,
                'context_display': f'{ctx} токенів ({"текст і фото" if has_vision else "тільки текст"})'
            }
        elif prov == 'gemini' or 'gemini' in name:
            base_caps = {'supports_vision': True, 'context_tokens': 1048576, 'context_display': '~1 000 000 токенів (~1M)'}
        elif 'gpt-4.1' in name:
            base_caps = {'supports_vision': True, 'context_tokens': 1047576, 'context_display': '~1 000 000 токенів (~1M)'}
        elif 'gpt-4o' in name or 'gpt-4-turbo' in name:
            base_caps = {'supports_vision': True, 'context_tokens': 128000, 'context_display': '128 000 токенів (~128K)'}
        elif any(m in name for m in ('o1', 'o3', 'gpt-3.5', 'text-')):
            base_caps = {'supports_vision': False, 'context_tokens': 128000, 'context_display': '128 000 токенів (тільки текст)'}
        elif prov == 'deepseek' or 'deepseek' in name:
            has_vision = any(v in name for v in ('vl', 'vision', 'multimodal'))
            base_caps = {'supports_vision': has_vision, 'context_tokens': 64000 if 'flash' in name else 128000, 'context_display': '64K–128K токенів (тільки текст)'}
        elif prov == 'groq':
            if any(v in name for v in ('vision', 'qwen3.8', 'qwen-vl')):
                base_caps = {'supports_vision': True, 'context_tokens': 6000, 'context_display': '6 000 токенів (Free ITPM ліміт, до 3 фото)', 'max_images': 3}
            else:
                base_caps = {'supports_vision': False, 'context_tokens': 6500 if 'gpt-oss' in name else 6000, 'context_display': '6 000–6 500 токенів (Free TPM ліміт, тільки текст)', 'max_images': 0}
        elif any(v in name for v in ('vision', '4o', 'gemini', 'claude-3', 'vl', 'pixtral')):
            base_caps = {'supports_vision': True, 'context_tokens': 128000, 'context_display': '128 000 токенів (~128K)'}
        else:
            base_caps = {'supports_vision': False if prov in ('groq', 'deepseek') else True, 'context_tokens': 128000, 'context_display': '128 000 токенів'}

    # Перевіряємо, чи зафіксовано для цієї моделі обмеження через помилку контексту
    from .ai_concurrency import get_model_context_limit
    recorded_limit = get_model_context_limit(prov, name)
    if recorded_limit:
        base_caps['is_context_flagged'] = True
        base_caps['context_flag_error'] = recorded_limit.get('error', '')
        if recorded_limit.get('est_tokens'):
            base_caps['context_tokens'] = min(base_caps['context_tokens'], int(recorded_limit['est_tokens']))
            base_caps['context_display'] = f"{base_caps['context_tokens']} токенів (⚠️ зафіксовано ліміт)"
        else:
            base_caps['context_display'] = f"{base_caps['context_display']} (⚠️ зафіксовано ліміт)"
    else:
        base_caps['is_context_flagged'] = False
        base_caps['context_flag_error'] = ''

    return base_caps

