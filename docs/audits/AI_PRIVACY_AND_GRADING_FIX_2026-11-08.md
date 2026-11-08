# Звіт: придатна приватність і стабільне оцінювання ШІ

Дата: 08.11.2026
Версія: **4.8.13a** (база — 4.8.13)
План: [`docs/plans/AI_PRIVACY_AND_GRADING_FIX_PLAN.md`](../plans/AI_PRIVACY_AND_GRADING_FIX_PLAN.md)
Керівництво: [`docs/guides/AI_CHECKING.md`](../guides/AI_CHECKING.md)

Цей звіт **чітко розділяє** три речі: що виправлено в коді, що реально перевірено тестами, і що залишилось зробити. Успішний API-виклик не вважається доказом правильності оцінювання.

---

## 1. Що виправлено (зміни в коді)

### 1.1 Приватність: блокування за реальні дані, а не за формат

Файл: [`feed/ai_privacy.py`](../../feed/ai_privacy.py:22)

- `PrivacyBlocked` тепер несе `reason` (`detected_data` проти `mode_restriction`) і перелік `detected`, щоб UI розрізняв «виявлено реальні персональні дані» від «обмеження режиму».
- Додано `_detect_embedded_data()` ([`ai_privacy.py`](../../feed/ai_privacy.py:77)) — перевіряє EXIF (Artist, Copyright, XPAuthor, ImageDescription, GPSInfo) і PNG-текстові чанки (Author, Comment, Description, Title, Software, XMP) на реальні email/телефони.
- `Redactor.media()` ([`ai_privacy.py`](../../feed/ai_privacy.py:116)) переписано:
  - у `strict` блокує **лише** коли детектор знайшов реальні дані (`reason='detected_data'`); чисте зображення проходить;
  - у `redact` вилучає метадані й надсилає зображення.

### 1.2 Типовий режим `redact` + міграція

- [`feed/models.py`](../../feed/models.py:3500) — `privacy_mode` за замовчуванням `redact`, опції перевпорядковано.
- Нова міграція [`feed/migrations/0074_privacy_default_redact.py`](../../feed/migrations/0074_privacy_default_redact.py:1) — `AlterField` (default `redact`) + `RunPython`, що переводить **усі** наявні `strict` у `redact`. `strict` лишається лише як новий явний вибір учителя.
- [`templates/feed/settings.html`](../../templates/feed/settings.html:677) — оновлено тексти опцій: пояснено, що EXIF-очищення ≠ анонімізація пікселів, а `strict` блокує лише за виявлені дані.

### 1.3 Unverifiable блокує числовий бал

- [`feed/ai_context.py`](../../feed/ai_context.py:498) — додано `unverifiable_labels()`, що повертає людські назви кожного непідтвердженого критерію/ГР.
- [`feed/gemini_service.py`](../../feed/gemini_service.py:7829) — обчислюються `blocked_labels` та `assessment_blocked`; у результат пишуться `assessment_blocked` і `blocked_reasons`; `needs_teacher_review` враховує блокування; у відгук додається ⛔-пояснення з переліком непідтвердженого.
- [`feed/gemini_service.py`](../../feed/gemini_service.py:7904) — якщо `assessment_blocked`, `ai_suggested_grade` і `ai_score_level` зберігаються порожніми.
- [`feed/gemini_service.py`](../../feed/gemini_service.py:7944) — у результаті `suggested_grade=None`, `level=''`, додано `assessment_blocked` і `blocked_reasons`.

### 1.4 Стабільність балу — єдине джерело істини

- Прибрано перезапис `suggested_grade` середнім за ГР ([`gemini_service.py`](../../feed/gemini_service.py:7418)) — `avg_gr_grade` лишається проміжним значенням.
- Додано **єдиний** блок фінального визначення балу ([`gemini_service.py`](../../feed/gemini_service.py:7882)): бал нормалізується рівно один раз (середнє за ГР або один `ceil`), після всіх перевірок (відхилення, unverifiable, ГР).

### 1.5 UI

- [`templates/feed/components/viewer_ai_panel.html`](../../templates/feed/components/viewer_ai_panel.html:85) — показ позначки «⛔ Оцінку заблоковано — потрібна перевірка вчителя», коли `ai_suggested_grade` порожній.
- [`feed/views.py`](../../feed/views.py:3111) — поважає `assessment_blocked`: не зберігає числовий бал/рівень у студентській самоперевірці.

### 1.6 Кеш конвертації медіа — змін не потрібно

- [`feed/ai_context.py`](../../feed/ai_context.py:392) — `media_for_provider()` **уже** кешує PDF→сторінки через `evidence_cache()` + `singleflight`, ключ за SHA-256, TTL 7 днів. Резервні моделі не конвертують повторно. Окремих змін не вносилось.

---

## 2. Що реально перевірено

### 2.1 Автоматичні тести (синтетика, HTTP перехоплено)

Запуск у контейнері `schoolnet_app` (Django 6.1.2), набір `feed.tests_ai_parallel_privacy`:

```
Ran 31 tests in 1.011s
OK
```

Міграція `0074_privacy_default_redact` застосувалася чисто в тестовій БД.

Нові/оновлені тести:

| Тест | Що доводить |
| --- | --- |
| `test_strict_mode_allows_clean_image_without_embedded_data` | `strict` пропускає чисте зображення (HTTP викликано) |
| `test_strict_mode_blocks_image_with_embedded_personal_data` | `strict` блокує зображення з EXIF Artist (HTTP не викликано) |
| `test_redact_mode_allows_image_with_embedded_data_after_stripping` | `redact` вилучає метадані й надсилає |
| `test_unverifiable_gr_blocks_numeric_grade` | непідтверджена ГР → `assessment_blocked=True`, `suggested_grade=None`, у БД порожній бал |
| `test_origin_claim_does_not_lower_content_grade` | підозра ШІ не знижує змістовий бал |

### 2.2 Що ці тести **не** доводять

- Що модель дає **правильну** оцінку на реальних роботах. Синтетика перевіряє контракти поведінки (блокування/збереження/стабільність), а не педагогічну якість.
- Що `strict` розпізнає **всі** персональні дані. Детектор перевіряє метадані (EXIF/PNG), а не пікселі; імена на самому зображенні він не бачить.
- Що `redact` анонімізує зображення. Він лише вилучає EXIF; видимі обличчя/підписи/імена передаються.

---

## 3. Що залишилось

1. **Реальний узгоджений набір** (роботи 602, 450, 436, 435, 433, 427, 418, 410, 372, 347) — прогнати на **копії** БД із тими самими матеріалами, окремою чергою, локальною підготовкою та зовнішнім API. Порівняти стабільність балу між повторами. Не виконано в цьому сеансі.
2. **Калібровка якості** оцінювання на реальних роботах — потребує ручної звірки вчителем; гарантована точність не заявляється.
3. **Розширення детектора даних** — за потреби додати розпізнавання більшої кількості форматів метаданих (PDF XMP, Office core properties) та, за окремим рішенням, локальне маскування пікселів (не видається за наявне).
4. **Розгортання** змін на робочий сервер із застосуванням міграції `0074` — не виконувалось у цьому сеансі; зміни лишаються в робочій копії.

---

## 4. Безпека та обмеження

- Приватні ключі, `.env`, API-ключі не виводились і не змінювались.
- Чужі незакомічені зміни не скидались і не перезаписувались.
- Тестові файли тимчасово копіювались у робочий контейнер для прогону тестів і **повернуті до оригіналу** після перевірки; міграція `0074` з контейнера видалена. Робочий контейнер лишено в початковому стані.
- Вилучення EXIF і регулярні вирази **не** подаються як повна анонімізація зображень.
- Прискорення не досягалось прихованим видаленням сторінок, малюнків, формул чи критеріїв.
