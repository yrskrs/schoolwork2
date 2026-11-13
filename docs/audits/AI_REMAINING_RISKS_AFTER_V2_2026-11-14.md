# Нові проблеми AI-підсистеми після v2: аудит 14.11.2026

**Статус:** аудит виконано, знайдені P0/P1 **виправлено** (FIX-1/2a/2b/3/5),
незалежно перевірено (FIX-VERIFY, WS4). Частина P2 закрита, решта — свідомі
залишкові обмеження, перелічені в §5.
**База:** [техпаспорти/guardrails v2](AI_ZERO_HALLUCINATION_TECHNICAL_PASSPORTS_2026-11-13.md),
[аудит v1→v2](AI_EVIDENCE_GAPS_REVIEW_2026-11-13.md).

**Методика FIX-VERIFY:** незалежні e2e через `evaluate_submission_with_gemini`
з підміненим провайдером (`patch('feed.gemini_service.call_ai_api')`), прямі
виклики чистих функцій, регресії на реальних файлах; окремо — повний прогін
`feed`. Код виконавців під час верифікації не виправлявся: знайдені дефекти
ескаловано Lead'у з точним відтворенням (expected/actual) і виправлено
власниками; тести після цього зелені.

Позначення: **P0** — прямий обхід гарантії/витік/поломка на існуючих БД;
**P1** — суттєвий неправильний бал/приватність/надійність; **P2** — гігієна,
UX, захист у глибину.

---

## 1. Статус P0 після FIX-1/2a/2b/3/5 та FIX-VERIFY

| № | Проблема аудиту | Статус | Що зроблено / чим доведено |
|---|---|---|---|
| P0-1 | Робота без файлів (лише коментар/посилання) повністю обходила аудит | **✅ виправлено** | Guard приймає `evidence_profile`; для технічної вимоги без verified-паспорта формується `implementation_unverified`/`no_evidence` (стеля 9, criteria `unverifiable`, `needs_teacher_review`). `expects_artifact` визначається з deliverable/task_type **і** технічної родини вимоги; есе/усні (`expects_artifact=False`) не караються. Тести: `NoEvidenceBypassTests` (comment-only IF 11→9, link-only Scratch 10→9, comment-only есе/усна — без змін), feed-регресія extensionless code → 9. **Під час верифікації знайдено й виправлено:** класифікатор віддає `task_type='programming'` (не було в artifact-маркерах) → link-only Scratch обходив захист; та regression: legacy `Submission.file` давав `has_files=True` без паспорта → 10 замість 9. Тепер `expects_artifact` враховує технічні families, а no-evidence сигнал формується і при `has_files=True`, якщо жодного verified-паспорта немає. |
| P0-2 | Хибний teacher waiver («достатньо», «не оцінюй») скасовував стелю | **✅ виправлено** | Waiver тепер **clause-level**: маркер і термін флагу мусять бути в одній клаузі; «обовʼязкові/вимагається» забороняє waiver; «достатньо» діє лише як «достатньо змісту/без …». Тести: `WaiverClauseTests` («Формули обовʼязкові. Достатньо однієї таблиці.» → бал 6, не waived; «Не оцінюй оформлення. Формули обовʼязкові.» → 6; справжній waiver → бал збережено, похвала прибрана). |
| P0-3 | Старі фабричні промпти раптово інжектились у system | **✅ виправлено** | `system_prompt_is_boilerplate` + `is_legacy_default_system_prompt` (сигнатури епохи 0043, відсутність поточних маркерів) → історичний дефолт вважається boilerplate; глобальний `AISettings.system_prompt` додається ПІСЛЯ предметного промпту пресета, а не замінює його; compact-варіант обрізає кастомний промт (`[скорочено]`). Data-міграція `0082_normalize_legacy_ai_defaults` оновлює legacy `AISettings`/системні пресети (ідемпотентно, редаговані промпти не чіпає). Тести: `SystemPromptHardeningTests` (legacy не інжектиться, короткий кастомний — після пресета, compact<4000, міграція). |
| P0-4 | Блок технічного аудиту витікав учню разом із raw-іменем файлу | **✅ виправлено** | Блок «🔎 Технічний аудит файлів» більше не дописується в `ai_feedback`; факти зберігаються лише в `Submission.ai_technical_evidence`. Студентські шляхи (`get_clean_ai_feedback_for_student`, `get_student_ai_sections`, weaknesses/strengths) беруть **лише** `student_ai_feedback` (fallback на `ai_feedback` прибрано); `compact_student_feedback` додатково вирізає audit-блок стабільним маркером. Учитель бачить факти окремою панеллю в `file_viewer.html` та `ai_job_payload.html`. Тести: `SystemPromptAndPersistenceTests.test_teacher_audit_block_and_student_feedback_are_separated` (немає блоку/назви файлу в публічних текстах; панель учителя рендериться), `StudentCheckWhitelistTests`, оновлені `tests_ai_student_feedback`. |

## 2. Статус P1

| № | Проблема аудиту | Статус | Що зроблено / чим доведено |
|---|---|---|---|
| P1-1 | PDF/скан: `assessment_blocked` замість стелі 9 | **✅ виправлено** | Guard ставить `criteria_results[*]['format_gap']=True` для `implementation_unverified`; `assessment_policy` виключає format-gap рядки з >50% блокування, але показує їх у `unverified_reasons` → числовий бал зі стелею 9, `needs_teacher_review=True`. Тести: `FormatGapAndCustomScaleTests` (2 з 3 і 2 з 4 формульних критеріїв на PDF-скані → бал 9, blocked=false). |
| P1-2 | Paragraph heuristic перезаписував completed у файлових роботах | **✅ виправлено** | `apply_multi_task_evaluation_guardrail` отримує `file_based`; для файлових проєктів бере `tasks_evaluated`/`tasks_completed_count`, а не кількість абзаців. **Під час верифікації знайдено й виправлено:** legacy `Submission.file` не враховувався в `is_file_project` → 3/3 completed ставало «1 із 3» (бал 5); тепер `get_files()`. Тести: `MultiTaskFileProjectTests` (SubmissionFile і legacy .xlsx з реальними формулами → не 5, tasks=3; текстовий multi-task лишається на абзацній евристиці). |
| P1-3 | Cross-family suppression: helper.py закривав формульну прогалину PDF-скану | **✅ виправлено** | Перевірка строго per-family (`_FAMILY_KINDS[family]`): formula підтверджує лише verified excel, code — python/code, тощо. Тест: `test_pdf_scan_plus_python_helper_is_still_implementation_unverified` (flags=`implementation_unverified`, бал 9, IF-похвала прибрана). |
| P1-4 | `custom_criteria` конфліктував з техстелею (4/4/4 → 6) | **✅ виправлено** | Якщо custom-шкала не вимагає родину, специфічна стеля (4/6) замінюється загальною 9; якщо custom сам вимагає родину — лишається строгіша. Тест: `test_custom_scale_is_not_cut_to_six` (бал 9, не 6 і не 10-12). |
| P1-5 | Факти аудиту не видно там, де обіцяно; stale findings; група; правила | **✅ виправлено** | `submission.ai_technical_evidence` персиститься в teacher- і self-check-потоці, копіюється співавторам групи, очищається перед новим прогоном і при fail; findings показуються в `ai_job_payload.html`/`file_viewer.html`; `rules_version` піднято до `2026-11-14.1`; job payload маніфест показує «Збирач / правила». Тести: `PersistenceAndObservabilityTests` (self-check persistence, group copy, fail cleanup, rules_version), `test_teacher_audit_block…` (UI). |
| P1-6 | DoS: unbounded розпакування/парсинг | **✅ виправлено** | Ліміти `.sb3` (compressed/uncompressed/targets/blocks), `.docx` (XML/абзаци/частини), `.pptx` (слайди/фігури/байти), `.xlsx` (формули/рядки/XML/час); при обрізанні — `limitation` + `facts['audit_complete']=False`, guard не робить висновку про відсутність. `.txt/.md/.rst/.log` → verified `text_document` (words/paragraphs/lines/instruction_like), есе не карається, code/formula на `.txt` → стеля 9. Тести: `PassportResourceLimitTests` (sb3-бомба→None; обрізаний docx/xlsx не дає хибного absence; text_document code→9; instruction-like warning). |
| P1-7 | Duplicate: multi-file ігнорувався, ПІБ у публічному warning, стеля лише в промпті | **✅ виправлено** | Детектор ітерує `SubmissionFile` + legacy `file`, повертає `matches`; PII-скоупінг (`access_control.scope_duplicate_info`) ховає імʼя/клас для публіки; `duplicate_ceiling(info)` → 4 для повного збігу застосовується детерміновано до `suggested_grade`/ГР/середнього. Тести: `tests_ai_remaining_fixes` (multi-file, PII, ceiling), `DuplicateCeilingEndToEndTests` (e2e cap 4). **Під час верифікації знайдено й виправлено:** `duplicate_ceiling.matches` містив `datetime` (`submitted_at`) → `json.dumps(raw_json)`/`AIJob.result` JSONField падали; тепер `isoformat()`. |
| P1-8 | Batch: втрата сторінок при помилці партії | **✅ виправлено (кодом FIX-2a)** | Невдалі партії додаються в `blocked_reasons`/`needs_teacher_review`, числовий бал не ставиться, якщо сторінки не переглянуто. Окремого e2e в integration-наборі немає (потребує багатосторінкових медіа); перевірено кодом і наявними сюїтами `tests_ai_assessment_v4`. |
| P1-9 | Provenance приймав model-only boolean/percent | **✅ виправлено** | Evidence-gate: raw model boolean/percent без verified/medium сигналу не дає `detected/suspected`; зберігається як «некалібрована оцінка моделі» (`model_estimate`). Тести: `tests_ai_remaining_fixes.ProvenanceEvidenceGateTests`, оновлені `tests_release_v41`, `tests_ai_detection_formats_v413`, `tests_ai_parallel_privacy`. |
| P1-10 | Приватні вказівки вчителя в `student_explanation` | **✅ виправлено** | `teacher_ai_instructions` не передаються в generation `student_explanation`; розуміння нормалізує старі дефолтні пресети. Тест: `test_understanding_does_not_send_private_teacher_instructions`. |
| P1-11 | Бекапи до v2 не відновлювались | **✅ виправлено** | `schema_compatibility`: нові поля поточної моделі ігноруються з попередженням; видалені/змінені — відмова. Тести: `tests_ai_remaining_fixes.BackupSchemaCompatibilityTests`. |
| P1-12 | Journal API віддавав AI-бал як офіційний | **✅ виправлено** | Рядок містить `is_ai_suggestion`/`ai_suggestion_confirmed_by_teacher`, офіційний `grade` окремо. Тест: `tests_ai_remaining_fixes.JournalAiSuggestionFlagTests`. |

## 3. Статус P2

| Проблема | Статус | Коментар |
|---|---|---|
| `job_response(student_check)` міг віддати teacher-only ключі | **✅ виправлено** | Явний whitelist `_STUDENT_CHECK_PUBLIC_FIELDS`; `ai_authorship_state/ai_generated_*` лишені спеціально для inline-банера самоперевірки. Тест: `StudentCheckWhitelistTests`. |
| Instruction-like лише косметичний | **🟡 частково** | `.txt/.md/.rst/.log` тепер verified `text_document` з `instruction_like_segments` і точним маркером; коментар студента в grading-промпті **досі не сканується** (детектор є лише в помічнику опису). Залишок §5. |
| Task-understanding роздувався старими пресетами / приватні нотатки | **✅ виправлено** | Нормалізація legacy-дефолтів і `strip_appended_zero_evidence_rule` застосовані і в understanding; приватні `teacher_ai_instructions` не надсилаються. |
| `AISettings.system_prompt` замінював пресет | **✅ виправлено** | Додається після предметного промпту; UI-попередження в `settings.html`; compact обрізає. |
| Self-check не персистив `ai_technical_evidence`, не копіював групі | **✅ виправлено** | Persist + копія співавторам + очищення при fail. |
| Stale findings не очищались | **✅ виправлено** | Очищення перед новим прогоном, при fail і при відсутності фактів. |
| Дублікат-стеля лише в промпті | **✅ виправлено** | Детермінована стеля 4 + `duplicate_ceiling` у результаті. |
| Compact для free-tier лише короткий маркер | 🔎 залишок | Повна секція паспорта й заборона — у user-prompt; повні стелі в `ASSESSMENT_RULES`. |

## 4. FIX-VERIFY: знайдені й виправлені під час незалежної перевірки

1. **P1-3 недоправлено:** exception «clean executable submission» глобально
   глушив `implementation_unverified`; PDF-скан формул + `helper.py` → flags=[],
   бал 10. Виправлено на строго per-family перевірку.
2. **P0-1, artifact-типи:** `task_type/deliverable='programming'` не входив у
   artifact-маркери → link-only Scratch обходив no-evidence стелю (10). Тепер
   `expects_artifact` враховує і технічну родину вимоги.
3. **P1-2, legacy-файли:** `is_file_project` дивився лише `submission.files`;
   legacy `Submission.file` давав «1 із 3», бал 5. Тепер `get_files()`.
4. **P1-7/P1-5, JSON:** `duplicate_ceiling.matches` містив `datetime` →
   серіалізація `raw_json`/`AIJob.result` падала. Тепер `isoformat()`.
5. **P0-1/P1-6, extensionless visible text:** regression після зміни
   `has_files` — файл без розширення без паспорта на технічну вимогу отримував
   10 замість 9. Виправлено умову no-evidence для `has_files=True` без
   verified-паспорта (есе/усні захищені `expects_artifact=False`).

Оновлено старі тести **лише там, де зміна поведінки законна за аудитом**:
P0-1 cap 9 для comment/link-only; P0-4 — без fallback на `ai_feedback` (фікстури
отримали `student_ai_feedback`); P1-9 evidence-gate (model-only → detected=false);
`.txt` → `text_document`; audit-блок більше не в `ai_feedback`. Де мета тесту —
fallback/вибір GR/apply — додано реальні файли (.xlsx з формулами, .py), щоб
перевірка лишалася змістовною.

## 5. Залишкові обмеження (свідомі, не дефекти цього релізу)

- **Task-understanding галюцинації:** захист перевіряє докази у файлах/вимогах,
  але не правильність інтерпретації завдання моделлю (understanding-шар має
  лише детерміновану нормалізацію промпту й заборону приватних нотаток).
- **Instruction-like у коментарі студента:** у grading-промпт коментар іде як
  дані, але детермінованого сканера інструкцій для нього немає (файли `.txt`
  покриті). Рекомендація: застосувати `_INSTRUCTION_LIKE_RE` до
  `comment_student` і додати warning/fact у контекст.
- **Runtime/правильність:** код не запускається, формули не перераховуються,
  тож «синтаксично коректно» ≠ «працює правильно».
- **Legacy-формати:** `.xls/.xlsm/.xlsb/.doc/.rtf` — лише unverified stub;
  Access на host без mdbtools — stub (у Docker з mdbtools читається).
- **Архіви:** без рекурсивного аудиту вмісту (stub + вибірковий текст).
- **PDF/скани:** формули/код не доводяться — лише `implementation_unverified`.
- **Скоуп стелі:** глобальна стеля, не per-task (для багатозадачних робіт може
  занижувати підсумок; пом'якшення — teacher waiver).
- **P1-8 batch:** окремого e2e в integration-наборі немає (перевірено кодом і
  дотичними сюїтами).
- **Евристичність текстового фільтра:** загальна похвала без згадки елемента
  може лишитися; коментарі ГР чистяться тим самим фільтром.
- **Compact-промпт:** повний перелік стель лише в `ASSESSMENT_RULES`.

## 6. Тести й фактичні результати (FIX-VERIFY, 14.11.2026)

| Набір | Тестів | Результат |
|---|---:|---|
| `feed.tests_ai_technical_integration` (WS4, FIX-VERIFY) | 100 | OK |
| `feed.tests_ai_remaining_fixes` (FIX-5) | 19 | OK |
| `feed.tests_ai_file_passport` | 81 | OK |
| `feed.tests_ai_evidence_guard` | 68 | OK |
| `feed.tests_ai_context_evidence` | 44 | OK |
| `feed.tests_ai_assessment_v4` | 25 | OK |
| `feed.tests_ai_grading_evidence` | 7 | OK |
| `feed.tests_ai_stability_policy` | 5 | OK |
| `feed.tests_ai_progress` | 30 | OK |
| `feed.tests_ai_detection_formats_v413` | 14 | OK |
| `feed.tests_ai_provenance_states` | 14 | OK |
| `feed.tests_ai_student_feedback` | 10 | OK |
| `feed.tests_ai_prompting` | 46 | OK |
| `feed.tests_cloud_backups` | 14 | OK |
| `feed.tests_journal_api` | 13 | OK |
| `feed.tests_journal_pagination` | 12 | OK |
| **Цільові сюїти разом** | **502** | **OK** |
| Повний `manage.py test feed` | **1554** | **OK (skipped=6)** |

Команди:

```bash
.venv/bin/python manage.py test feed.tests_ai_technical_integration \
    --settings=schoolnet.test_settings -v2
.venv/bin/python manage.py test feed.tests_ai_file_passport \
    feed.tests_ai_evidence_guard feed.tests_ai_technical_integration \
    feed.tests_ai_remaining_fixes feed.tests_ai_context_evidence \
    feed.tests_ai_assessment_v4 feed.tests_ai_grading_evidence \
    feed.tests_ai_stability_policy feed.tests_ai_progress \
    feed.tests_ai_detection_formats_v413 feed.tests_ai_provenance_states \
    feed.tests_ai_student_feedback feed.tests_ai_prompting \
    feed.tests_cloud_backups feed.tests_journal_api \
    feed.tests_journal_pagination --settings=schoolnet.test_settings
.venv/bin/python manage.py test feed --settings=schoolnet.test_settings
.venv/bin/python manage.py check --settings=schoolnet.test_settings
.venv/bin/python manage.py makemigrations --check --dry-run \
    --settings=schoolnet.test_settings
```

`manage.py check` — 0 issues; `makemigrations --check` — No changes detected.

## 7. Висновок

Усі P0 і P1 цього аудиту виправлено й підтверджено незалежними e2e/регресіями:
робота без файлів більше не обходить аудит; waiver не скасовується випадковою
фразою; старі фабричні промпти не інжектяться; audit-блок не витікає учню й
видимий учителю окремо; PDF/скани дають числову стелю 9, а не блокування;
файлові багатозадачні роботи не втрачають completed-завдання; cross-family
suppression прибрано; custom-шкала не ріжеться до 6; факти аудиту персистяться
й показуються; DoS-ліміти й `audit_complete` захищають сервер; `.txt` — verified
`text_document`; duplicate-детектор працює для multi-file, анонімізує дані та
застосовує стелю 4; provenance не звинувачує за model-only; старі бекапи
відновлюються; журнал API позначає AI-пропозицію; student-check whitelist не
віддає teacher-only полів. Залишок — свідомі обмеження §5: task-understanding,
instruction-like у коментарі, runtime, legacy/Access/архіви, глобальна стеля.
