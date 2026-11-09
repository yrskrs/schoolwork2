# План виправлень: контекст перевірки ШІ (4.8.13n → 4.8.14)

Дата: 09.11.2026. Продовження [AI_ASSESSMENT_CONTEXT_VERDICT_2026-11-09.md](AI_ASSESSMENT_CONTEXT_VERDICT_2026-11-09.md).
Тут — конкретні зміни: що, де, як, навіщо, як перевірити і в якому порядку. Код — ескізи, не фінальні патчі.

## Три принципи, з яких виходять усі зміни

1. **Один збирач контексту.** Провайдерні адаптери змінюють лише транспорт (формат запиту), а не зміст.
   Якщо правило чи джерело не потрапило в `build_assessment_request()` — воно не існує.
2. **Повнота з маніфестом.** Усе, що вилучено або не прочитано, записується в журнал перевірки.
   «Тихо скоротили» — заборонено.
3. **Сервер не переписує рішення мовчки.** Будь-яка зміна балу після моделі зберігає «до → після → причина»
   і показується вчителю.

---

## P0 — зробити першим (1 робочий день)

### P0-1. Припинити декодувати бінарники як текст

**Де:** `feed/ai_context.py:53–124`, функція `_full_text()`; виклик у `extract_file_evidence()`.

**Проблема:** останній рядок `return service.get_normalized_file_content(path, path)` приймає будь-який файл,
включно з JPEG/PNG/MP4, і декодує байти. Фото 2.9 МБ → 2.9 млн символів і ~833 тис. токенів.

**Зміна:**

```python
# ai_context.py, поруч з IMAGES
MEDIA_ONLY = IMAGES | {'.heic', '.heif', '.mp3', '.wav', '.ogg', '.flac', '.aac',
                       '.mp4', '.webm', '.ogv', '.mov', '.m4v', '.mkv', '.avi', '.3gp'}


def _looks_like_text(value):
    sample = (value or '')[:20000]
    if not sample:
        return True
    printable = sum(1 for c in sample if c.isprintable() or c in '\n\r\t')
    return '\x00' not in sample and printable / len(sample) > 0.9


def _full_text(path, ext):
    ...
    if ext in MEDIA_ONLY:
        return ''                     # саме зображення піде через media, не текстом
    if service.is_text_file(path):
        return service.read_text_file(path, max_chars=None)
    text = service.get_normalized_file_content(path, path)
    return text if _looks_like_text(text) else ''
```

Плюс страховка в `extract_file_evidence()` перед збереженням у кеш:

```python
if data['text'] and not _looks_like_text(data['text']):
    data['text'] = ''
    data['limitations'].append('Текстовий витяг відкинуто як бінарний; використано візуальні сторінки.')
```

**Ефект:** фото 2.9 МБ → 0 символів тексту, ~1.7 МБ медіа (≈2 тис. токенів) замість 833 тис.
Три фото знову влазять у Gemini; Qwen отримує 3 зображення замість «6 000 токенів сміття».

**Тести:** `test_image_has_no_binary_text`, `test_unknown_binary_returns_empty`, `test_doc_still_extracts_text`.

---

### P0-2. Довідкові матеріали: не втрачати документ через розбиття

**Де:** `feed/ai_context.py:252–319` (`compact_reference_material`), `:766–775` (другий етап у
`build_assessment_request`).

**Проблема:** ділення на абзаци через `\n\s*\n`, а DOCX-екстрактор з'єднує абзаци одним `\n`.
Весь документ стає «одним абзацем» і, якщо довший за бюджет, відкидається повністю (6 911 → 56 символів).

**Зміна:**

```python
paragraphs = [p.strip() for p in re.split(r'\n+', cleaned) if p.strip()]
...
if not kept:                                  # ніколи не віддавати саму заглушку
    kept = [cleaned[:max(400, theory_budget)]]
    kept.append('[...решта довідки не вмістилася в бюджет...]')
```

Другий етап (compact) — не `theory_budget=0`, а явний мінімум:

```python
reference = [compact_reference_material(
    text, theory_budget=1200 if compact else 1500,
    task_numbers=assigned_numbers,
    task_source_resolved=any(selected for _, selected in primary_results)) for text in reference]
```

і після фільтрації критеріальних абзаців (769–775) додати fallback: якщо список порожній —
лишити перші 1 200 символів довідки, а не саму заглушку.

**Ефект:** учительська теорія/інструкція доходить до моделі; для 6 911 символів у промпті буде
~1 500 символів найрелевантнішого замість 56.

**Тести:** `test_docx_single_newline_reference_kept` (≥80% бюджету використано),
`test_reference_never_placeholder_only`, `test_compact_keeps_criteria_paragraphs`.

---

### P0-3. Прибрати мертвий блок інструкцій

**Де:** `feed/gemini_service.py:6324–6911` і `:6939–7025` (перезаписуються на `:7031`).

**Проблема:** 600 рядків правил, які ніколи не надсилаються; будь-яка правка там не має ефекту.
Виміряно: anti-empty, multi-task ceiling, «матеріали вчителя як джерело», `unassigned_material`,
`FINAL TASK UNDERSTANDING` — 0 з 5 у payload.

**Зміна (два кроки):**

1. Винести унікальні правила, яких немає в `ASSESSMENT_RULES`, в окремий модуль `feed/ai_rules.py`:
   - політика дубліката/плагіату (зараз відсутня — див. P1-3);
   - заборона вимагати те, чого не було в умові (частково є);
   - формат відповіді для презентацій/таблиць (є в `ASSESSMENT_RULES`, перевірити);
   - `RULES_VERSION = '2026-11-09.1'` — щоб версія правил була в журналі.
2. Видалити `prompt_lines` і всі `system_instruction += ...` до виклику `build_assessment_request()`.
   Джерело істини — тільки `build_assessment_request()` + `ai_rules.py`.

**Ефект:** неможливо знову «відредагувати правило, яке не працює»; менше дублювання тексту правил
(зараз 6.5 тис. символів системи + дублікати в промпті).

**Тести:** `test_payload_contains_rules(version)`, `test_no_duplicate_rule_blocks`,
`test_dead_markers_absent`.

---

### P0-4. Системний промпт шаблону не губити

**Де:** `feed/ai_context.py:780–791`.

**Проблема:** для шаблону береться `preset.system_prompt`, а не `preset.get_full_prompt()`; для системних
шаблонів він ще й замінюється короткою роллю. Текст рубрики (`extracted_criteria_text`) додається лише
якщо шаблон не системний або коротший за 1 500 символів.

**Зміна:** збирати систему явно й один раз:

```python
base = custom_prompt or (preset.get_full_prompt() if preset else DEFAULT_NUS_SYSTEM_PROMPT)
if not custom_prompt and preset and getattr(preset, 'is_system', False):
    base = preset.get_full_prompt()          # роль + ГР + критерії
system = base.strip() + '\n' + (COMPACT_RULES if compact else RULES)
```

**Ефект:** модель бачить ті самі ГР і критерії, що й учитель у шаблоні.

**Тести:** `test_system_prompt_contains_gr_definitions`, `test_custom_prompt_preserved_verbatim`.

---

## P1 — наступні 2–4 дні

### P1-1. Privacy: не роздувати JPEG у PNG

**Де:** `feed/ai_privacy.py:138–142`.

**Проблема:** кожне фото перекодовується в PNG без втрат: 1 745 КБ → 7 844 КБ (10.2 МБ base64),
межа OpenRouter — 7.5 МБ → 413.

**Зміна:**

```python
clean = Image.new('RGB', image.size, 'white')
clean.paste(image.convert('RGB'))
buffer = io.BytesIO()
if entry.get('mime_type') in ('image/jpeg', 'image/jpg'):
    clean.save(buffer, format='JPEG', quality=90, optimize=True, progressive=True)
else:
    clean.save(buffer, format='PNG', optimize=True)
```

Для скриншотів із текстом PNG лишається; для фото — JPEG. Метадані зникають в обох випадках.
Окремо: не перекодовувати, якщо метаданих не знайдено і формат уже JPEG — достатньо видалити EXIF.

**Ефект:** 7.8 МБ → ~1.6 МБ; запит влазить у 7.5 МБ OpenRouter і не лякає Gemini.

**Тести:** `test_privacy_jpeg_stays_jpeg`, `test_exif_removed`, `test_request_under_openrouter_limit`.

---

### P1-2. Ліміти медіа: партії замість відмови

**Де:** `feed/gemini_service.py:7145–7163` (перевірка `max_images`), `:7183–7196` (контекст).

**Проблема:** 8-сторінковий скан і Qwen (3 фото) → 413 і все. Учень не отримує нічого.

**Зміна (MVP, map-reduce):**

1. `split_media_batches(media, limit)` — ріже **сторінки**, не файли.
2. Новий режим job `batch_check`: той самий план завдання, але кожна партія оцінюється окремо з
   інструкцією «оцінюй лише ці сторінки; решту познач unverifiable».
3. Після всіх партій — один синтез-виклик **без зображень**: на вхід лише JSON партій, на вихід —
   узгоджений результат (union критеріїв, конфлікти → `needs_teacher_review`).
4. Якщо партія впала — результат частковий, а не «failed».

**Ефект:** 8 сторінок → 3+3+2, учень отримує оцінку; вартість контрольована.

**Тести:** `test_8_pages_split_into_batches`, `test_batch_merge_keeps_all_criteria`, `test_partial_batch_result`.

---

### P1-3. Дублікат: передати політику, а не лише прапорці

**Де:** `feed/gemini_service.py:7027` (тільки `duplicate_facts`), мертвий блок `:6573–6601`.

**Проблема:** модель знає `is_duplicate_student: true`, але не знає, що з цим робити. Пост-обробка
карає лише дублікат файлу вчителя.

**Зміна:**

```python
dup = scope.get('_duplicate_facts') or {}
if dup.get('is_duplicate_student') and not dup.get('plagiarism_ignored'):
    lines.append(
        'ВСТАНОВЛЕНО ПОВНИЙ ЗБІГ із роботою іншого учня. Політика: не став 10–12; '
        'познач збіг у weaknesses; бал не вище 4; рішення про порушення — за вчителем.')
```

Плюс детермінована серверна стеля (щоб текст і бал не розходилися):

```python
if dup.get('is_duplicate_student') and not dup.get('plagiarism_ignored') and suggested_grade != 'Доопрацювати':
    suggested_grade = str(min(4, int(suggested_grade)))
    result_json['server_adjustments'].append({'field': 'suggested_grade', 'reason': 'duplicate_student'})
```

Окремо виправити `Submission.is_coauthor_with()` (`feed/models.py:2422`): замінити підрядкове
порівняння прізвища на точний збіг компонента:

```python
def _name_matches(value, members):
    pattern = re.compile(rf'(?<!\w){re.escape(value)}(?!\w)', re.IGNORECASE)
    return any(pattern.search(m) for m in members)
```

**Тести:** `test_duplicate_policy_in_prompt`, `test_duplicate_grade_cap`, `test_coauthor_exact_surname_only`.

---

### P1-4. Один варіант контексту для всіх провайдерів

**Де:** `feed/gemini_service.py:7031` і `:7107–7113`.

**Проблема:** промпт будується двічі; для Gemini/Groq/OpenRouter — compact-варіант, який змінює **зміст**
(довідка, правила), а не лише обсяг. `base_prompt_length`-нарізка крихка.

**Зміна:** будувати один раз; `compact` впливає лише на бюджет викладу (скорочені формулювання правил,
без дублювання схеми), але не на набір джерел і не на факт передачі рубрики.

```python
prompt_content, system_instruction = build_assessment_request(..., compact=builder_compact)
# без повторного виклику в циклі; лише provider-специфічна серіалізація
```

**Тести:** `test_single_build_per_job`, `test_same_sources_full_and_compact`.

---

### P1-5. Журнал пакета (без нього далі не рухатися)

**Де:** `AIJob.result` / нове поле `AIJob.parameters['manifest']`.

Додати в результат:

```python
'payload_manifest': {
    'builder': 'assessment-v5', 'rules_version': RULES_VERSION,
    'prompt_chars': len(provider_prompt), 'system_chars': len(provider_system),
    'est_tokens': est_tokens, 'media_count': len(inline_media),
    'media_bytes': sum(len(m['data']) for m in inline_media),
    'sources': [{'file': name, 'role': role, 'chars': n} for ...],
    'dropped': [{'source': ..., 'reason': ...}],
    'sha256': hashlib.sha256(provider_prompt.encode()).hexdigest(),
}
```

Плюс сторінка «Що побачила модель» для вчителя (локально, без API): джерела, ролі, обсяг, медіа,
вилучення. Це і є інструмент діагностики наступних скарг.

**Тести:** `test_manifest_counts_match_payload`.

---

## P2 — 1–2 тижні

### P2-1. Знімок джерел на момент постановки в чергу
Зараз worker читає актуальні об'єкти: правка умови під час черги змінює перевірку. Зберігати в
`AIJob.parameters['snapshot']`: опис, критерії, хеші файлів, `preset_id`, ГР. Worker використовує знімок;
якщо файл зник — статус `manual_review`, а не «failed».

### P2-2. Прозорість серверних змін балу
`apply_multi_task_evaluation_guardrail`, `complete_result_groups` (`missing → 1`), середнє за ГР —
усі пишуть `server_adjustments[]` з причиною. Учитель бачить: «модель сказала 9, сервер знизив до 5,
бо виконано 1 із 3 завдань». Зараз це виглядає як «модель помилилася».

### P2-3. Вихідний бюджет і обірваний JSON
- при `finish_reason=MAX_TOKENS` — один повтор із бюджетом ×1.5 (до 16k), а не тим самим;
- `allow_partial=True` лише для діагностики, не для оцінки;
- схему JSON друкувати один раз (зараз вона в промпті й у правилах).

### P2-4. Аудіо/відео
Gemini приймає аудіо/відео. Або додати гілку в `extract_submission_content`, або явно писати
«формат не перевіряється» і не вважати це невиконанням.

### P2-5. Тести середовища
`AI_JOBS_EAGER` у compose ламає 2 тести; додати `override_settings` у setUp або окремий test-env файл.
Додати відсутній `feed/tests_ai_privacy.py`.

---

## Порядок і оцінка

| Крок | Що | Час | Ризик |
|---|---|---|---|
| 1 | P0-1 (бінарники) + тест | 1–2 год | низький |
| 2 | P0-2 (довідка) + тест | 2–3 год | низький |
| 3 | P0-3/P0-4 (мертвий код, системний промпт) | півдня | середній: змінює текст правил |
| 4 | P1-1 (privacy JPEG) | 1–2 год | низький |
| 5 | P1-3 (дублікат) | 2–3 год | середній: змінює бали дублікатів |
| 6 | P1-4 (один збирач) | півдня | середній |
| 7 | P1-5 (маніфест) | 1 день | низький |
| 8 | P1-2 (партії) | 2–3 дні | високий: новий режим job |
| 9 | P2 | 1–2 тижні | середній |

**Критерій готовності P0+P1:** робота з 3 фото важить у промпті < 30 тис. символів і < 30 тис. токенів;
усі 3 фото в медіа; довідка присутня; політика дубліката в тексті; у журналі видно джерела, ролі,
вилучення; жодне правило не існує поза активним збирачем.

## Як перевіряти (наскрізний сценарій)

1. **Фото 3 МБ + docx-умова + теорія.** До: 837 тис. токенів, теорія 56 символів. Після: < 20 тис. токенів,
   теорія ≥ 1 200 символів, усе в маніфесті.
2. **8-сторінковий скан + Qwen.** До: 413. Після: 3 партії + синтез, оцінка є, `needs_teacher_review`
   якщо частина сторінок не прочитана.
3. **Дублікат двох учнів.** До: прапорці без політики. Після: політика в промпті + стеля балу + причина
   в журналі.
4. **Регрес тексту:** 40-абзацний docx — 40/40 абзаців (уже працює, має лишитися).

## Ризики

- **Зміна поведінки оцінювання.** P0-3/P0-4 змінюють текст правил — спочатку прогнати на 3–5 еталонних
  роботах, порівняти бали «до/після», увімкнути прапорцем `AI_CONTEXT_V2`.
- **Партії (P1-2)** — найдорожче й найризикованіше; робити після маніфесту, щоб бачити, що саме пішло.
- **Приватність** не має постраждати від JPEG: метадані видаляються так само, EXIF не повертається.

---

## Статус виконання (09.11.2026, lab `schoolwork2-ai-lab`)

| Пункт | Стан | Виміряний ефект |
|---|---|---|
| P0-1 бінарники як текст | ✅ виконано (`ai_context.py`) | фото 2.9 МБ: 2 915 328 символів «тексту» → **0**; промпт фікстури 45 329 → **15 895** символів; оцінка токенів 833 тис. → ~2 тис. |
| P0-2 довідка | ✅ виконано (`ai_context.py`) | DOCX 6 911 символів: 56 символів заглушки → **1 248** символів реального тексту (ліміт 1 200 + підпис) |
| P0-3 мертвий блок | ✅ виконано (`gemini_service.py`, −615 рядків) | нормалізований хеш payload **однаковий** до/після видалення (`dd7ed3d3…`) — поведінка не змінилася, код більше не вводить в оману |
| P0-4 системний промпт шаблону | ⏸ відкладено | потребує рішення, чи дублювати текст рубрики (ризик подвійного бюджету); ГР уже в JSON-контексті |
| P1-1 privacy JPEG | ✅ виконано (`ai_privacy.py`) | 1 745.7 КБ JPEG → **1 613.8 КБ JPEG** (було 7 844.6 КБ PNG); base64 10.21 МБ → **2.1 МБ**; скриншоти з малою палітрою лишаються PNG |
| P1-3 дублікат | ✅ частково (`ai_context.py`, `gemini_service.py`, `models.py`) | у промпті з'явилася політика «ВСТАНОВЛЕНО ПОВНИЙ ЗБІГ…»; `needs_teacher_review=true`; автоматична стеля балу **не** застосовується (рішення про порушення — за вчителем); прізвища співавторів порівнюються точно (`Лис` ≠ `Лисенко`) |
| P1-2 партії сторінок | ⛔ не розпочато | наступний великий крок (map-reduce), потребує нового режиму job |
| P1-4 один збирач | ⏸ частково | мертвий збирач прибрано; друга збірка compact для Gemini/Groq/OpenRouter ще існує |
| P1-5 маніфест пакета | ⛔ не розпочато | заплановано разом із партіями |

**Файли:** `feed/ai_context.py`, `feed/ai_privacy.py`, `feed/gemini_service.py`, `feed/models.py`,
новий `feed/tests_ai_context_evidence.py` (7 тестів).

**Перевірка:** 173 тести AI-модулів — `OK` (з `AI_JOBS_EAGER=True`); сайт перезапущено,
`/healthz/` → `{"status": "ok", "version": "4.8.13n"}`.

### Друга ітерація (P1-4, P1-5)

| Пункт | Стан | Що зроблено |
|---|---|---|
| P1-4 один збирач | ✅ виконано (`gemini_service.py`) | обидва варіанти (full/compact) будуються **один раз до циклу провайдерів**; у циклі лише вибір готового варіанта. Прибрано `base_prompt_length`-нарізку та повторну збірку на кожну спробу. Нормалізований хеш промпту до/після — однаковий (`3009540f…` full, `8fbc9365…` compact) |
| P1-5 маніфест | ✅ для діагностики | `payload_manifest` у результаті job: builder, provider, model, `prompt_chars`, `system_chars`, `media_count`, `media_bytes`, `est_tokens`, `sha256`; заповнюється **після** privacy-обробки в `_raw_call_ai_api`. Команда `python manage.py ai_job_payload <job_id> [--events]`. Для самоперевірки учня маніфест вирізається з відповіді |
| P1-5 UI | ⛔ ще ні | сторінка «Що побачила модель» для вчителя (окремий шаблон) — наступна ітерація разом із партіями |

**Тести:** `feed/tests_ai_context_evidence.py` — 8 тестів, зокрема `test_raw_call_records_manifest_after_privacy`
та `test_manifest_describes_the_payload_without_evidence`.


### Третя ітерація (P1-5 UI, P1-2 партії)

| Пункт | Стан | Що зроблено |
|---|---|---|
| P1-5 UI | ✅ виконано | сторінка `teacher/ai/job/<uuid>/payload/` (`ai_job_payload_view` + `templates/feed/ai_job_payload.html`): пакет, джерела, події, хеш. Посилання «Що побачила модель» у панелі ШІ переглядача. Доступ лише власнику роботи; анонім/інший вчитель — 404 |
| P1-2 партії | ✅ механізм + тести | `evaluate_media_in_batches()`: ділить сторінки на партії за `max_images`, оцінює кожну тим самим планом, потім **синтез-виклик без зображень** збирає один JSON; `needs_teacher_review=true`, `batched_evaluation` у результаті; події `batch_start`/`batch_done`. 8 сторінок + ліміт 3 → 3+3+2 + синтез (перевірено мок-тестом) |

**Обмеження, яке виявилося під час впровадження:** для `qwen/qwen3.8-27b` каталог задає
`context_tokens = 6000` (це фактично ITPM-ліміт). Навіть 3 фото × 1500 токенів + текст ~5 тис. токенів
перевищують 6000, тому партії не рятують саме Qwen: перевірка контексту зупиняє його раніше.
Партії дають ефект для моделей із малим `max_images`, але достатнім контекстом. Для Qwen потрібно
або підняти ліміт у каталозі (якщо це ITPM, а не контекст), або не пропонувати його для багатосторінкових робіт.

**Тести:** `feed/tests_ai_context_evidence.py` — 13 тестів (сторінка маніфесту, партії 3+3+2, відмова без фальшивого результату).

### Четверта ітерація (P2-1, P2-2)

| Пункт | Стан | Що зроблено |
|---|---|---|
| P2-1 знімок джерел | ✅ виконано (`ai_jobs.py`) | `source_snapshot()` фіксує під час постановки в чергу: відбиток завдання, хеші опису/критеріїв/вказівок, `allow_ai_usage`, `preset_id`, ГР, файли роботи (ім'я, розмір, `mtime_ns`), коментар, посилання. Worker порівнює відбиток перед виконанням: при розбіжності — подія `sources_changed`, `snapshot_drift=true`, `needs_teacher_review=true` у результаті. Знімок не впливає на дедуплікацію черги (`_parameters_key`) |
| P2-2 серверні зміни балу | ✅ виконано (`gemini_service.py`) | `server_adjustments[]` у результаті: `field`, `before`, `after`, `reason` для стелі багатозадачності, середнього за ГР, округлення, відхилення роботи, «не зрозуміло завдання». Показуються на сторінці «Що побачила модель» окремим блоком |

**Тести:** 15 у `tests_ai_context_evidence.py`, зокрема `test_snapshot_is_stable_and_drift_is_detected`.

### П'ята ітерація (P2-3)

| Пункт | Стан | Що зроблено |
|---|---|---|
| P2-3 вихідний бюджет / MAX_TOKENS | ✅ виконано (`gemini_service.py`) | при `MAX_TOKENS` модель повторює **та сама** модель із більшим бюджетом: ×1.6 + 500, до 16 000, максимум 2 підвищення. Додаткові ітерації зарезервовані лише під це й не перетворюють мережеві збої на зайві повтори. Для `openrouter/free` поведінка лишається failover (динамічний роутер), щоб не палити квоту |
| Тест | ✅ | `test_max_tokens_grows_budget_and_retries_same_model`: два виклики тієї самої моделі, другий із більшим `max_output_tokens` |

### Шоста ітерація (P2-4, P2-5)

| Пункт | Стан | Що зроблено |
|---|---|---|
| P2-4 аудіо | ✅ частково (`gemini_service.py`, `ai_privacy.py`) | голосові відповіді (.mp3/.wav/.ogg/.m4a/.aac/.flac/.aiff) до 10 МБ передаються як медіа Gemini, а не як «файл формату .mp3»; у strict-режимі аудіо/відео блокуються (голос неможливо знеособити), у redact — дозволені. Відео поки не передається: розмір і приватність потребують File API та окремого рішення |
| P2-5 тести | ✅ виконано (`schoolnet/settings.py`) | `AI_JOBS_EAGER` у тестах тепер завжди `True`, незалежно від env compose; тести, яким потрібен черговий режим, уже використовують `override_settings`. Це прибирає два «падіння», спричинені середовищем |

**Тести:** 17 у `tests_ai_context_evidence.py`, зокрема `test_audio_is_attached_as_media_not_binary_text` і `test_strict_privacy_refuses_audio_but_redact_allows_it`.

### Сьома ітерація (повний знімок файлів)

| Пункт | Стан | Що зроблено |
|---|---|---|
| Копії файлів | ✅ виконано (`ai_jobs.py`, `gemini_service.py`, `ai_context.py`) | при постановці в чергу файли роботи й матеріали завдання копіюються в `MEDIA_ROOT/ai_job_snapshots/<job_id>/`; карта `snapshot_files` зберігається в `parameters`. Оцінювання, самоперевірка й аналіз умови читають саме копії через `path_overrides` (`extract_submission_content`, `teacher_materials`). Правка оригіналу після черги більше не змінює перевірку |
| Дрейф | ✅ | відбиток джерел і далі фіксується; розбіжність позначається `sources_changed`/`needs_teacher_review`, але оцінка йде за копіями |
| Тести | ✅ | `test_snapshot_files_keep_original_bytes_after_live_edit` (енд-ту-енд: зміна файлу після черги не потрапляє в промпт), `test_extraction_prefers_snapshot_copy` |

Залишок: прибирання старих копій (окрема команда за віком) і копіювання файлу рубрики `preset.document_file`.

### Восьма ітерація (рубрика, автоочистка, Qwen, відео)

| Пункт | Стан | Що зроблено |
|---|---|---|
| Рубрика в знімку | ✅ | `snapshot_submission_files` копіює `preset.document_file` для політики класу й дефолтного шаблону; оцінювання та аналіз умови читають копію через `source_overrides['preset']` |
| Автоочистка | ✅ | worker раз на 6 годин викликає `ai_job_cleanup_snapshots` з `AI_SNAPSHOT_RETENTION_DAYS` (типово 14); команда не торкається активних job |
| Qwen / малий контекст | ✅ | якщо контекст моделі не вміщає навіть одну сторінку разом із текстом, модель пропускається з поясненням «оберіть модель із більшим контекстом», а не загальною помилкою. Це прибирає марні партії для `qwen3.8-27b` (6 000 токенів) |
| Відео | ✅ частково | короткі `.mp4/.webm/.mov/.m4v/.3gp/.ogv` до 8 МБ передаються Gemini як медіа; більші — ручна перевірка. У strict-режимі відео блокується (як і аудіо) |

**Тести:** 20 у `tests_ai_context_evidence.py` (рубрика в знімку, відео), 232 у повному AI-наборі.

### Дев'ята ітерація (виправлення після перевірок)

| Пункт | Стан |
|---|---|
| Нормалізація `level` | ✅ `level_for_grade` / `normalized_level`; застосовано до підсумкового рівня й рівнів ГР; зміна фіксується в `server_adjustments` |
| Безпека очистки | ✅ `ai_job_cleanup_snapshots` без `--force` не приймає поріг < 1 дня |
| Qwen | ✅ `media_policy: text_only` у каталозі; пропуск із поясненням для медіа |

### Десята ітерація (Gemini File API для великих відео)

| Пункт | Стан | Що зроблено |
|---|---|---|
| File API | ✅ | новий `feed/ai_files.py`: resumable upload → очікування `ACTIVE` → `file_data` у запиті; помилки повертаються як 503 без втрати доказів. Відео 8–200 МБ більше не ріжеться і не кодується в base64: у медіа зберігається `upload_path`, а `_raw_call_ai_api` відділяє такі докази від inline-медіа |
| Політика | ✅ | не-Gemini провайдери отримують 415 «потрібен Gemini File API»; strict-режим, як і раніше, блокує відео |
| Маніфест | ✅ | `payload_manifest.uploads` показує кількість завантажених файлів |
| Тести | ✅ | `upload start/put/wait`, `file_data` у payload, 415 для не-Gemini, `test_large_video_uses_file_api_upload_path` |

Обмеження: файли на боці Google живуть до 48 годин; негайне видалення не робиться, щоб не зламати повтори Gemini 400.

### Одинадцята ітерація (паралельні партії + повтор перевірки)

| Пункт | Стан | Що зроблено |
|---|---|---|
| Паралельні партії | ✅ | `evaluate_media_in_batches` виконує партії через `ThreadPoolExecutor` (до `AI_BATCH_CONCURRENCY`, типово 3), зберігає порядок результатів, копіює `contextvars` у кожен потік (privacy-редактор і job-id не губляться), закриває DB-з'єднання у потоці |
| Повтор перевірки | ✅ | `teacher/ai/job/<uuid>/repeat/` (POST): копіює зафіксовані джерела в новий job-каталог, зберігає `repeat_of`, не створює дубль, якщо вже є активна перевірка. Кнопка на сторінці маніфесту |
| Тести | ✅ | `test_parallel_batches_keep_redaction_context`, `test_repeat_copies_snapshot_into_new_job`, `test_anonymous_cannot_repeat` |
