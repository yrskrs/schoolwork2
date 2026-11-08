# План: HTTPS-посилення cookie/HSTS для SchoolNet (відкладено)

Дата: 08.11.2026. Статус: **рекомендація, не реалізовано**. Повернутися після
рішення про публічний доступ і перед постійним публічним використанням Funnel.

## Навіщо

SchoolNet працює у двох режимах:

- LAN: `http://192.168.1.13:8080` — потрібні звичайні cookie без `Secure`;
- Funnel: `https://localwork.tail9ec47d.ts.net` — публічний HTTPS-доступ.

Поточна перевірка 4.8.13d:

- cookie access-посилання **вже** створюється з `Secure=True` і `HttpOnly`
  (`feed/access_views.py`);
- session-cookie (`schoolwork2_sessionid`) і CSRF-cookie
  (`schoolwork2_csrftoken`) не мають `Secure`;
- HSTS-заголовка немає;
- CSP-заголовка немає;
- HTTP-запит до Funnel перенаправляється на HTTPS (302), але без HSTS браузер
  не запам’ятовує це правило.

Це не функціональна поломка, а відсутність додаткових запобіжників для
публічного HTTPS.

## Головне правило

**Не прив’язувати безпеку до створення access-посилання або до
`allow_without_link`.** HSTS і `Secure` — транспортні властивості: вони мають
залежати від схеми/хоста запиту, а не від того, чи вчитель щось створив.

- HTTPS/Funnel → HSTS + `Secure` cookies.
- LAN HTTP → без HSTS і без `Secure`, інакше зламається локальний вхід.

## Рекомендований порядок

### 1. HSTS для Funnel-домену

Додавати `Strict-Transport-Security` лише для HTTPS-запитів Funnel-домену.
Почати з малого `max-age` (наприклад, 300 або 86400), перевірити вхід, здачу,
посилання й HTTPS-редирект, потім поступово збільшити до 31536000.

Не додавати HSTS для LAN-IP: HSTS не призначений для IP і може зламати
локальний HTTP-доступ. `includeSubDomains` і `preload` — тільки після окремої
перевірки, що весь домен і піддомени працюють лише по HTTPS.

Варіанти:

- **A. На рівні reverse proxy перед Funnel** — найчистіше: nginx/Caddy додає
  HSTS і `proxy_cookie_flags ... secure`. Але з’являється додатковий компонент.
- **B. Django middleware/response hook** — додає HSTS, якщо
  `request.is_secure()` або запит визначено як Funnel. Менше інфраструктури,
  але треба уважно перевірити `X-Forwarded-Proto`, `SCHOOLNET_TRUST_PROXY_HTTPS`
  і `CSRF_TRUSTED_ORIGINS`.

### 2. `Secure` для session/CSRF cookies

Не вмикати `SESSION_COOKIE_SECURE=True` / `CSRF_COOKIE_SECURE=True` глобально:
LAN HTTP перестане працювати.

Варіанти:

- окремий HTTPS-профіль налаштувань для Funnel-інстансу;
- response-middleware, який для HTTPS/Funnel перевстановлює session/CSRF
  cookies з `Secure`, не змінюючи LAN-шлях;
- reverse proxy, який додає `Secure` до `Set-Cookie`.

Потрібно перевірити сценарій «вчитель увійшов через LAN, потім відкрив
Funnel»: стара non-Secure cookie не має ставати дійсною для публічного HTTPS
без повторного входу. Краще, щоб Funnel-сесія створювалася окремо.

### 3. CSP — окремим етапом

CSP складніший, бо на сторінках багато вбудованих скриптів/стилів. Не додавати
суворий CSP «навмання»: спочатку інвентаризація inline-скриптів, потім nonce/hash
і тести всіх сторінок (стрічка, завдання, здача, кабінет учителя, діалоги,
переглядач файлів). CSP не є блокером для запуску, але бажаний для публічного
Funnel.

## Тест-матриця перед впровадженням

| Сценарій | LAN HTTP | Funnel HTTPS |
|---|---|---|
| Вхід учителя | працює, cookie без `Secure` | працює, session/CSRF `Secure` |
| CSRF-форма здачі | працює | працює |
| Здача роботи + сповіщення | працює | працює |
| Access link | не потрібен | cookie `Secure`, HSTS-заголовок |
| HTTP → HTTPS | — | 302/308, HSTS після першого HTTPS |
| Відкритий режим | не залежить | не залежить |
| Вимкнений відкритий режим | LAN працює | без link — 403, з link — 200 |

Додатково:

- перевірити, що HSTS не з’являється на LAN-IP;
- перевірити, що старі non-Secure cookies не приймаються як Funnel-сесія;
- перевірити `Set-Cookie` через `curl -D` для LAN і Funnel;
- прогнати `feed.test_access_links`, `feed.tests_submission_*`,
  `feed.tests_ai_*` і повний `feed`;
- перевірити в реальному браузері, не лише curl.

## Що не робити

- Не ставити `SESSION_COOKIE_SECURE=True` глобально без окремого LAN-профілю.
- Не додавати HSTS для `192.168.1.13`.
- Не прив’язувати HSTS/`Secure` до `allow_without_link` або факту створення
  посилання.
- Не вважати access-link cookie проблемою: вона вже `Secure=True`.

## Орієнтовні файли

- `schoolnet/settings.py` — можливі окремі HTTPS-прапорці;
- новий middleware або `feed/access_middleware.py` — HSTS/перевстановлення
  cookies для HTTPS;
- `compose.tailscale.yaml` / reverse-proxy конфігурація — якщо обрано варіант A;
- `feed/test_access_links.py`, `feed/tests_submission_*` — тест-матриця.

## Пов’язані записи

- [Готовність 4.8.13d](../audits/READINESS_4_8_13D_2026-11-08.md)
- [Поточний стан доступу](../TAILSCALE_ACCESS.md)
- [Funnel-аудит 06.11.2026](../FUNNEL_AUDIT_2026-11-06.md)
