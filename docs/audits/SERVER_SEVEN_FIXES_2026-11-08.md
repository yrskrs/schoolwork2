# Виправлення семи підтверджених зауважень сервера — 08.11.2026

Стартова версія: 4.8.13f. Цільовий випуск: 4.8.13h.
Резервні копії: `/home/mlinux/schoolprjct/private-backups/seven-fixes-20261108/`
та `/home/yrsadmin/school-deployments/pre-seven-fixes-20261108/`.

## 1. Git-стан і штатні оновлення

**Було:** 86 записів `git status`, шість `.env.bak-*` untracked; `update.sh` і
`updater_service` відмовлялися працювати з брудним деревом.

**Зроблено:**
- `.env.bak-*`, `.env.before-*`, `.env.image-*` додано до `.gitignore`;
- приватні копії перенесено поза checkout у
  `~/school-private-backups/env-before-seven-fixes-20261108` з правами 0600;
- серверний код звірено з upstream, HEAD є предком `origin/main`;
- після розгортання виконано перевірене узгодження з upstream без сліпого
  масового додавання; фінальний HEAD/status фіксуються у звіті нижче.

**Перевірка:** `git check-ignore` для `.env.bak-*` — так; серверний
`git status --porcelain` — 0 записів; `HEAD == origin/main` =
`f9ef28cc44162635893b775d4958bb76ab53df81`; HEAD є предком upstream;
`git merge --ff-only origin/main` — `Already up to date`. Приватні копії
лежать у `~/school-private-backups/env-before-seven-fixes-20261108` (0600).
Повний `update.sh` на продакшені не запускався; перевірено передумови та
ізольовану логіку. Під час першої спроби розгортання g скрипт помилково
вважав зміну порту PostgreSQL «сторонньою» і відкотився на f; після
виправлення перевірки повторне розгортання g успішне.

## 2. Llama 4 Scout

**Було:** `supports_vision=false`, `max_images=0` через евристику за назвою.

**Зроблено:** явний запис каталогу `@cf/meta/llama-4-scout-17b-16e-instruct`:
Vision=true, context=131 000 (офіційна сторінка Cloudflare), `max_images=32`.
Qwen Cloudflare (vision/32) і GLM (text/0) не змінені.

**Додатково:** кількість PDF-сторінок тепер рахується після адаптації
PDF→зображення; сторінки не обрізаються заради ліміту.

**Перевірка:** `get_model_capabilities` для Scout/Qwen/GLM; тест
`media_image_units` на 2-сторінковому PDF.

## 3. HTTPS cookie/HSTS без поломки LAN

**Було:** session/CSRF cookies без `Secure`, HSTS=0.

**Зроблено:** `HTTPSHardeningMiddleware` додає HSTS (`max-age=86400`) і
`Secure` до session/CSRF cookies лише для HTTPS-запиту на налаштований
Funnel-хост із локального/loopback проксі або Tailscale Funnel marker.
LAN HTTP не отримує HSTS/Secure. Глобальний HTTPS-redirect не ввімкнено.

**Перевірка:** тести Funnel/LAN/невідомого джерела; перевірка живого
Funnel після розгортання: заголовок HSTS, `Secure` session/CSRF, вхід/CSRF/
здача; LAN: без HSTS, cookie без Secure.

## 4. PostgreSQL

**Було:** порт 5433 опублікований на всіх host-інтерфейсах.

**Зроблено:** `compose.yaml` публікує `127.0.0.1:${DB_PORT:-5433}:5432`.
Docker-мережа app/worker не змінена; зовнішні клієнти БД не знайдені,
легітимним залишається loopback/host-local доступ.

**Перевірка:** `docker compose config`, `ss`/TCP з LAN і loopback,
health app/worker, відсутність змін у даних.

## 5. Приватні `.env`-копії

**Було:** шість `.env.bak-*` untracked, права 0600; витік не встановлено.

**Зроблено:** переміщено в приватний каталог поза Git; додано ignore-патерни.
`.dockerignore` уже виключає `.env*`; у образі лише `.env.example`.

**Перевірка:** `git check-ignore`, `git status --short`, права 0600, відсутність
`.env.bak-*` у checkout/образі. Вміст конфігурацій не друкувався.

## 6. Квоти ШІ

**Статус: не завершено.** Усі п’ять підключень мають `quota_limits={}`;
фактичні ліміти акаунтів невідомі. Значення не вигадувалися.
Потрібні дані: RPM/RPD/TPM/TPD/ITPM/OTPM/concurrency, спільність квот і
двох Gemini-ключів. Деталі — `docs/plans/AI_QUOTA_VALUES_NEEDED_2026-11-08.md`.

## 7. Захист облікових записів

**Було:** `AUTH_PASSWORD_VALIDATORS=[]`, без throttling.

**Зроблено:**
- стандартні Django-валідатори (similarity, min 10, common, numeric);
  чинні паролі не змінюються;
- модель `LoginThrottle` + міграція 0075; ключ — SHA-256 від IP+username,
  пароль/логін у відкритому вигляді не зберігаються;
- те саме обмеження застосовано до `/admin/login/`; сигнали фіксують/очищають спроби.
- 5 невдач за 15 хвилин → тимчасове блокування 15 хвилин для цієї пари;
  успішний вхід очищає лічильник; ключ IP+username не блокує інших учнів
  зі спільним шкільним IP.

**Перевірка:** тести блокування, очищення, іншого користувача з тим самим IP,
валідаторів; перевірка відсутності пароля в ключі.

## Розгортання 4.8.13h

- Образ: `schoolwork2-app:4.8.13h-3735703470d8`.
- Ревізія: `6e53989e1294286aceb77ce89e52e985de0cbce4`.
- Відбиток коду: `3735703470d857aff929a828a89376e7661e174a38d24b4f989e5fcb0f328ca8`.
- `/healthz/` — 4.8.13h; app/ai_worker healthy; дані 451/95/17/296 без змін.
- PostgreSQL після recreate слухає лише `127.0.0.1:5433`; LAN-порт закритий.
- Funnel `/teacher/login/`: `Strict-Transport-Security: max-age=86400`, CSRF cookie `Secure`; LAN — без HSTS і без `Secure`.
- `LoginThrottle` (міграція 0075) застосована, таблиця порожня на старті.
- `schoolnet_updater` перезапущено після копіювання джерел.
- Повний кандидатний набір: **1013 тестів OK (2 skips)**.
- Живий Funnel: HSTS `max-age=86400`, session/CSRF `Secure`, вхід 302 → кабінет 200.
- Живий LAN: без HSTS, session/CSRF без `Secure`, вхід 302 → кабінет 200.
- Синтетичні здачі через Funnel і LAN (ID 605, 606) створені й видалені;
  підсумкові дані 451/95/17/296, тимчасових користувачів/повідомлень немає.
- PostgreSQL: `LISTEN 127.0.0.1:5433`, зовнішній LAN TCP закритий.
- `git status` на сервері — 0; HEAD = upstream.

[Стан розгортання](artifacts/server_seven_fixes_4.8.13h_deployment.json) ·
[Фактичні перевірки](artifacts/server_seven_fixes_4.8.13h_checks.json).

## Обмеження

- Квоти не налаштовані без фактичних даних власника.
- Повний `update.sh` не запускався на продакшені; перевірено передумови та
  ізольовану логіку.
- HSTS встановлено на 86 400 с; `includeSubDomains`/preload не вмикалися.
- CSP залишається окремим відкладеним пунктом.

[План](../plans/SERVER_SEVEN_FIXES_PLAN_2026-11-08.md) ·
[Квоти](../plans/AI_QUOTA_VALUES_NEEDED_2026-11-08.md) ·
[Попередній аудит](SERVER_READONLY_AUDIT_2026-11-08_2134.md) ·
[Повторна перевірка](SERVER_RECHECK_2026-11-08.md)
