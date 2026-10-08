"""Збір, аналіз та формування діагностичних звітів про збої ШІ для передачі ШІ-розробнику."""
import json
import re
from datetime import timedelta
from django.db.models import Count, Max, Min, Q
from django.utils import timezone


def get_error_summary_stats():
    """Повертає статистику журналу помилок ШІ: кількість, незчитані, розподіл за типами."""
    from .models import AIErrorLog, AIErrorReport

    total_count = AIErrorLog.objects.count()
    unread_count = AIErrorLog.objects.filter(is_read=False).count()
    read_count = total_count - unread_count

    recent_24h = AIErrorLog.objects.filter(created_at__gte=timezone.now() - timedelta(days=1)).count()

    by_status = list(
        AIErrorLog.objects.values('status_code')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )

    by_provider = list(
        AIErrorLog.objects.values('provider')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )

    last_report = AIErrorReport.objects.first()

    return {
        'total_count': total_count,
        'unread_count': unread_count,
        'read_count': read_count,
        'recent_24h': recent_24h,
        'by_status': by_status,
        'by_provider': by_provider,
        'last_report': last_report,
    }


def classify_errors_heuristically(error_records):
    """
    Евристичний аналіз переліку помилок: чи це зовнішня проблема постачальника (429, 502, 503,
    Cloudflare, перевантаження, вичерпаний баланс), чи внутрішня проблема коду/конфігурації (400, 401, парсинг).
    """
    if not error_records:
        return 'no_errors', 'Помилок ШІ не зафіксовано — система працює стабільно'

    code_keywords = [
        '400', 'bad request', 'invalid parameter', 'invalid argument',
        'json', 'parse', 'parsing', 'decode', 'unexpected assessment format',
        'validation error', 'typeerror', 'keyerror', 'syntaxerror',
        'unknown part type', 'missing required', 'payload too large',
        '413', 'not found', '404', 'internal error', 'exception', 'traceback'
    ]

    auth_keywords = [
        '401', '403', 'invalid api key', 'api key not found', 'unauthorized',
        'forbidden', 'permission denied'
    ]

    external_keywords = [
        '429', 'rate limit', 'high demand', '503', '502', '504', 'overload',
        'temporarily unavailable', 'cloudflare', 'timeout', 'timed out',
        'capacity', 'busy', 'service unavailable', 'insufficient balance',
        'credit', 'exhausted', 'model is overloaded', 'backend unavailable'
    ]

    code_issues = 0
    external_issues = 0
    auth_issues = 0

    for err in error_records:
        code = err.status_code
        err_type = (err.error_type or '').lower()
        msg = f"{err_type} {err.error_message or ''}".lower()

        if err_type in ('json_parse_error', 'validation_error', 'syntax_error') or any(k in msg for k in code_keywords):
            code_issues += 1
        elif code in (401, 403) or any(k in msg for k in auth_keywords):
            auth_issues += 1
        elif code in (429, 502, 503, 504) or any(k in msg for k in external_keywords):
            external_issues += 1
        else:
            external_issues += 1

    if code_issues > 0:
        return (
            'needs_fix',
            'Виявлено технічні помилки запитів (400 / парсинг / валідація) — рекомендовано перевірку коду'
        )
    elif auth_issues > 0:
        return (
            'needs_fix',
            'Помилки авторизації API (401 / 403) — перевірте API-ключі або права доступу в налаштуваннях'
        )
    elif external_issues > 0:
        return (
            'external_issue',
            'Проблема на боці провайдера (зовнішній сервер / квоти / 503 / 429) — змін у коді не потрібно'
        )
    else:
        return (
            'external_issue',
            'Зовнішні збої мережі або постачальника'
        )


def aggregate_error_data(error_records):
    """Групує однотипні помилки для компактного та інформативного звіту."""
    groups = {}
    for err in error_records:
        key = (
            err.provider or 'unknown',
            err.model_name or 'unknown',
            err.status_code or 0,
            err.error_type or 'General Error'
        )
        if key not in groups:
            groups[key] = {
                'provider': key[0],
                'model_name': key[1],
                'status_code': key[2],
                'error_type': key[3],
                'count': 0,
                'sample_message': err.error_message[:400],
                'sample_raw': err.raw_response[:400] if err.raw_response else '',
                'first_time': err.created_at,
                'last_time': err.created_at,
                'failover_count': 0,
            }
        item = groups[key]
        item['count'] += 1
        if err.failover_triggered:
            item['failover_count'] += 1
        if err.created_at > item['last_time']:
            item['last_time'] = err.created_at
        if err.created_at < item['first_time']:
            item['first_time'] = err.created_at

    return list(groups.values())


def build_local_diagnostic_report(error_records, aggregated_data, verdict, verdict_title):
    """Створює детальний звіт у форматі Markdown за локальними евристичними правилами."""
    now_str = timezone.localtime(timezone.now()).strftime('%d.%m.%Y %H:%M:%S')

    if not error_records:
        return (
            f"# 📋 Діагностичний звіт помилок ШІ SchoolNet\n\n"
            f"**Дата формування:** {now_str}\n\n"
            f"### 🟢 Вердикт: Помилок не виявлено\n\n"
            f"Журнал збоїв ШІ повністю чистий. Усі запити до мовних моделей виконуються успішно, "
            f"помилок у роботі системи немає.\n"
        )

    lines = [
        f"# 📋 Діагностичний звіт помилок та збоїв ШІ (SchoolNet LMS)",
        f"**Дата та час формування:** {now_str}",
        f"**Проаналізовано записів помилок:** {len(error_records)}",
        "",
        "---",
        "",
        f"## 🎯 ГОЛОВНИЙ ВЕРДИКТ",
        f"**{verdict_title}**",
        "",
    ]

    if verdict == 'external_issue':
        lines.extend([
            "> [!NOTE]",
            "> **Висновок для ШІ-асистента та розробника:**",
            "> Зафіксовані збої викликані **зовнішніми факторами постачальників** (Rate Limit 429, тимчасове перевантаження серверів 503, черги або вичерпання хвилинних квот).",
            "> **Змін у коді сайту вносити НЕ потрібно.** Вбудований механізм автоматичного переходу (Failover) "
            "та cooldown-паузи успішно перемикають запити на резервні моделі.",
            ""
        ])
    else:
        lines.extend([
            "> [!WARNING]",
            "> **Висновок для ШІ-асистента та розробника:**",
            "> Виявлено технічні помилки (неправильний ключ, помилка парсингу або непідтримуваний формат запиту), "
            "які **потребують втручання в налаштування або код**.",
            ""
        ])

    lines.extend([
        "## 📊 ЗВЕДЕНА ТАБЛИЦЯ ВИЯВЛЕНИХ ЗБОЇВ",
        "",
        "| Провайдер | Модель | HTTP Код | Тип помилки | Кількість | Резерв (Failover) | Приклад повідомлення |",
        "| :--- | :--- | :---: | :--- | :---: | :---: | :--- |"
    ])

    for g in aggregated_data:
        code_disp = f"HTTP {g['status_code']}" if g['status_code'] else "—"
        sample_msg = g['sample_message'].replace('\n', ' ').replace('|', '/')[:80]
        fo_disp = f"Так ({g['failover_count']})" if g['failover_count'] else "Ні"
        lines.append(
            f"| `{g['provider']}` | `{g['model_name']}` | {code_disp} | {g['error_type']} | **{g['count']}** | {fo_disp} | {sample_msg} |"
        )

    lines.extend([
        "",
        "## 🔬 ДЕТАЛЬНИЙ ТЕХНІЧНИЙ АНАЛІЗ ДЛЯ ШІ-АСИСТЕНТА",
        ""
    ])

    for idx, g in enumerate(aggregated_data, 1):
        lines.append(f"### {idx}. {g['provider'].upper()} — `{g['model_name']}` ({g['error_type']})")
        lines.append(f"- **Кількість випадків:** {g['count']}")
        lines.append(f"- **Часовий проміжок:** з {timezone.localtime(g['first_time']).strftime('%d.%m %H:%M')} до {timezone.localtime(g['last_time']).strftime('%d.%m %H:%M')}")
        lines.append(f"- **Повідомлення провайдера:** `{g['sample_message']}`")
        if g.get('sample_raw'):
            lines.append(f"- **Фрагмент сирої відповіді:**\n```json\n{g['sample_raw'][:250]}\n```")

        # Технічний коментар
        if g['status_code'] == 429:
            lines.append("- **Технічна природа:** Тимчасовий ліміт запитів (Rate Limit / RPM / RPD) у тарифі акаунта. Код сайту обробляє це коректно через cooldown і failover.")
        elif g['status_code'] in (502, 503, 504):
            lines.append("- **Технічна природа:** Перевантаження кластера моделі на боці постачальника (High demand / Worker queue). На стороні клієнта виправлення не потрібні.")
        elif g['status_code'] == 401 or g['status_code'] == 403:
            lines.append("- **Технічна природа:** Помилка автентифікації. Необхідно перевірити дійсність ключа в `Налаштування → Модуль ШІ → Підключення`.")
        elif g['status_code'] == 413:
            lines.append("- **Технічна природа:** Перевищено розмір запиту для даного провайдера. Матеріал або зображення завдання завеликі.")
        elif 'JSON' in g['error_type']:
            lines.append("- **Технічна природа:** Модель повернула текст замість очікуваного валідного JSON, або міркування вичерпали ліміт.")
        lines.append("")

    lines.extend([
        "## 💡 ПРАКТИЧНІ РЕКОМЕНДАЦІЇ ДЛЯ КОРИСТУВАЧА / ВЧИТЕЛЯ",
        ""
    ])

    if verdict == 'external_issue':
        lines.extend([
            "1. **Нічого не переписувати в коді:** Система відпрацювала штатно, відловивши помилку та передавши запит іншим моделям або зупинивши збій.",
            "2. **Якщо помилка 429 (Rate Limit):** Зачекайте 1-2 хвилини для відновлення ліміту, або додайте додатковий акаунт постачальника у чергу підключень.",
            "3. **Якщо помилка 503 (High Demand):** Постачальник перевантажений. Рекомендується переставити іншу модель вище у пріоритетах (⬆️) у розділі «Моделі».",
            "4. **Перевірте баланс:** Якщо використовується OpenRouter, переконайтеся, що на балансі є кошти."
        ])
    else:
        lines.extend([
            "1. **Перевірте API-ключі:** Якщо є помилки 401 або 403, оновіть ключ у розділі «Підключення».",
            "2. **Передайте цей звіт ШІ-асистенту:** Натисніть кнопку «Копіювати звіт» вище і надішліть його іншому ШІ, щоб швидко отримати точний патч або інструкцію з налаштування."
        ])

    return "\n".join(lines)


def generate_error_report_with_ai(teacher=None, mark_as_read=True, limit=50):
    """
    Головна функція:
    1. Зчитує помилки (пріоритет — незчитані).
    2. Позначає їх як зчитані (is_read=True, read_at=now).
    3. Відправляє дані ШІ для формування експертного звіту.
    4. Зберігає результат у моделі AIErrorReport.
    """
    from .models import AIErrorLog, AIErrorReport, AISettings
    from .gemini_service import call_ai_api

    # 1. Відбір помилок
    unread_qs = AIErrorLog.objects.filter(is_read=False).order_by('-created_at')
    unread_count_before = unread_qs.count()

    if unread_count_before > 0:
        errors = list(unread_qs[:limit])
    else:
        errors = list(AIErrorLog.objects.all().order_by('-created_at')[:limit])

    if not errors:
        # Немає жодної помилки
        report = AIErrorReport.objects.create(
            created_by=teacher,
            errors_count=0,
            unread_count_before=0,
            verdict='no_errors',
            verdict_title='Помилок ШІ не зафіксовано',
            report_text=build_local_diagnostic_report([], [], 'no_errors', 'Помилок ШІ не зафіксовано'),
            model_used='Локальна перевірка',
            raw_summary='{}'
        )
        return report

    # 2. Позначаємо відібрані помилки як зчитані
    if mark_as_read:
        pks = [e.pk for e in errors]
        AIErrorLog.objects.filter(pk__in=pks).update(is_read=True, read_at=timezone.now())

    # 3. Агрегуємо інформацію
    aggregated = aggregate_error_data(errors)
    heuristic_verdict, heuristic_title = classify_errors_heuristically(errors)

    summary_json = json.dumps({
        'total_analyzed': len(errors),
        'unread_before': unread_count_before,
        'groups': aggregated
    }, ensure_ascii=False, default=str)

    # 4. Пробуємо сформувати звіт через ШІ
    ai_report_text = None
    ai_model_name = ''
    try:
        settings = AISettings.get_solo()
        configs = settings.get_request_configs()
        if configs:
            active_cfg = configs[0]
            ai_model_name = f"{active_cfg['provider']}/{active_cfg['model']}"

            system_prompt = (
                "Ти — провідний системний архітектор та експерт-діагност бекенду шкільної платформи SchoolNet (Django LMS, Python). "
                "Твоє завдання — проаналізувати отриманий технічний лог помилок підключень ШІ (Google Gemini, Groq, OpenRouter тощо) "
                "та скласти вичерпний технічний звіт для передачі іншому ШІ-асистенту/програмісту для діагностики та виправлення.\n\n"
                "КРИТИЧНІ ВИМОГИ ДО ЗВІТУ:\n"
                "1. Чітко визнач головний вердикт: чи це ПРОБЛЕМА НА БОЦІ ПРОВАЙДЕРА (зовнішній сервер: 429 Rate Limit, 503 Overload, тимчасові таймаути), "
                "і тоді КОДУ ЗМІНЮВАТИ НЕ ПОТРІБНО; чи це ПОМИЛКА В КОДІ/НАЛАШТУВАННЯХ (400 Bad Request, 401 Auth, збій парсингу JSON), що потребує виправлення.\n"
                "2. Формат відповіді — чистий, структурований GitHub-style Markdown українською мовою з розділами: "
                "🎯 ГОЛОВНИЙ ВЕРДИКТ, 📊 ЗВЕДЕНА ТАБЛИЦЯ ЗБОЇВ, 🔬 ДЕТАЛЬНИЙ ТЕХНІЧНИЙ АНАЛІЗ ДЛЯ ШІ-АСИСТЕНТА, 💡 ПРАКТИЧНІ РЕКОМЕНДАЦІЇ ДЛЯ КОРИСТУВАЧА.\n"
                "3. Звіт має бути готовим до копіювання іншому ШІ, який зчитуватиме його для написання коду або пояснення ситуації."
            )

            prompt_content = (
                f"Ось зведені дані про {len(errors)} останніх помилок ШІ на нашому сервері:\n\n"
                f"```json\n{summary_json}\n```\n\n"
                "Сформуй професійний діагностичний звіт відповідно до вимог."
            )

            status_code, reply_text, err_msg, raw_data = call_ai_api(
                prompt_text=prompt_content,
                system_prompt=system_prompt,
                provider=active_cfg['provider'],
                api_key=active_cfg['api_key'],
                model_name=active_cfg['model'],
                custom_url=active_cfg.get('custom_url', ''),
                temperature=0.2,
                max_output_tokens=3000,
                timeout=45,
                action='error_diagnosis_report',
                connection_id=active_cfg.get('connection_id')
            )

            if status_code == 200 and reply_text and len(reply_text.strip()) > 100:
                ai_report_text = reply_text.strip()
    except Exception:
        ai_report_text = None

    # Якщо ШІ не відповів або виникла помилка, використовуємо наш детальний локальний генератор
    if not ai_report_text:
        ai_report_text = build_local_diagnostic_report(errors, aggregated, heuristic_verdict, heuristic_title)
        ai_model_name = 'Локальний аналізатор SchoolNet'

    # Визначаємо фінальний вердикт
    verdict = heuristic_verdict
    verdict_title = heuristic_title
    if 'КОДУ ЗМІНЮВАТИ НЕ ПОТРІБНО' in ai_report_text.upper() or 'ПРОБЛЕМА НА БОЦІ' in ai_report_text.upper():
        verdict = 'external_issue'
        verdict_title = 'Проблема на боці провайдера (зовнішній сервер) — виправлень у коді не потрібно'
    elif 'ПОТРІБНЕ ВИПРАВЛЕННЯ' in ai_report_text.upper() or 'ПОТРЕБУЮТЬ ВТРУЧАННЯ' in ai_report_text.upper():
        verdict = 'needs_fix'
        verdict_title = 'Виявлено технічні помилки — рекомендовано перевірку або виправлення'

    report = AIErrorReport.objects.create(
        created_by=teacher,
        errors_count=len(errors),
        unread_count_before=unread_count_before,
        verdict=verdict,
        verdict_title=verdict_title,
        report_text=ai_report_text,
        model_used=ai_model_name,
        raw_summary=summary_json
    )

    return report
