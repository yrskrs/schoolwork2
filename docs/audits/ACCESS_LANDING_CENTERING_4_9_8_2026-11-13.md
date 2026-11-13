# Центрування публічної сторінки доступу — 4.9.8

Дата: 13.11.2026. Версія: 4.9.8. Гілка `main`, локальна перевірка перед публікацією.

## Симптом

- Публічна сторінка входу за посиланням `/access/<token>/` (шаблон `feed/access_enter.html`, «Відкрити сайт») виглядала зламаною: картка з написом «Шкільні Завдання», назвою посилання й кнопкою «Відкрити сайт →» притискалася до лівого краю вікна, текст усередині картки не був центрований, кнопка стояла ліворуч. На широких екранах і через HTTPS-тунель дефект було добре видно.
- Сторінка `access_required.html` («Потрібне посилання від учителя» / «Зовнішній доступ вимкнено») мала той самий блок `.access-landing`/`.access-landing-card`, те саме притискання до лівого краю й додатково неузгоджені кольори, бо не підключала `theme.css`.

## Причина

- Обидва шаблони використовують класи `body.access-landing`, `.access-landing-card` і (на сторінці входу) `.access-open`, але у `static/css/access_links.css` для цих класів **не було жодних правил** — існував лише media-padding для інших елементів консолі посилань. Тому `body` і `main` лишалися звичайними block-елементами: картка розтягувалась на всю ширину або притискалася вліво, `text-align` не задавався, кнопка не мала власних стилів.
- `templates/feed/access_required.html` не підключав `theme.css` — лише `access_links.css`, тому CSS-змінні теми не працювали.
- Браузери й проксі тунелю могли віддавати закешований `access_links.css`, тож навіть після виправлення сторінка могла лишатися старою.

## Виправлення

- `static/css/access_links.css` — додано базові правила для публічного екрана:
  - `body.access-landing`: `margin: 0`, `min-height: 100vh` / `100svh`, `display: flex`, `align-items: center`, `justify-content: center`, `box-sizing: border-box`, `padding: 24px`, фон/колір/шрифт із змінних теми;
  - `.access-landing-card`: `box-sizing: border-box`, `width: 100%`, `max-width: 560px`, `padding: 40px 36px`, фон/рамка/радіус/тінь зі змінних, `text-align: center`;
  - `.access-eyebrow` (pill-мітка), `h1` з `clamp(1.6rem, 4vw, 2.1rem)` і `overflow-wrap: anywhere`, `p` з `max-width: 46ch` і центруванням, посилання в акцентному кольорі;
  - `.access-open`: `inline-flex`, `align-items/justify-content: center`, `gap: 8px`, `min-height: 44px`, `padding: 11px 24px`, радіус і кольори зі змінних, `cursor: pointer`, тінь, `transition`; `:hover` (зміна фону й `translateY(-1px)`) та `:focus-visible` (видимий outline 3px);
  - адаптив `@media (max-width: 640px)`: `body` padding 16px, картка — padding 28px 22px.
- `templates/feed/access_enter.html` — підключення `theme.css?v=20261113d` і `access_links.css?v=20261113d`.
- `templates/feed/access_required.html` — додано `theme.css?v=20261113d`; `access_links.css?v=20261113d` (раніше був лише `access_links.css` без версії).
- `templates/feed/access_links.html` (консоль «Посилання доступу») — `access_links.css?v=20261113d` замість `20261107c`.
- `scripts/test_access_links_ui.py` — browser-перевірка сторінки входу доповнена геометрією: `centerOffset ≤ 1px`, `textAlign === 'center'`, `body display === 'flex'`, і далі перевіряється, що кнопка «Відкрити сайт» веде на головну.

## Як перевірено

- **Browser-тест:** `scripts/test_access_links_ui.py` (Chrome через CDP) перевіряє центрування картки за обчисленими стилями й геометрією, а не лише наявність елементів. Результат: **OK** — Chrome/CDP, центрування підтверджено обчисленими стилями (`centerOffset ≤ 1`, `text-align: center`, `display: flex`); скріншот [access-enter-fixed.png](assets/2026-11-13-ai-helper/access-enter-fixed.png).
- **Django-тест:** `feed/test_access_links.py::test_access_landing_page_has_centered_layout_styles` перевіряє класи сторінки, `access_links.css?v=20261113d` і наявність правил центрування у CSS (`body.access-landing`, `display: flex`, `justify-content: center`, `.access-landing-card`, `text-align: center`, `.access-open`). Результат: **OK** — Chrome/CDP, центрування підтверджено обчисленими стилями (`centerOffset ≤ 1`, `text-align: center`, `display: flex`); скріншот [access-enter-fixed.png](assets/2026-11-13-ai-helper/access-enter-fixed.png).
- **Скріншот та обчислені стилі:** візуальний скріншот сторінки `/access/<token>/` (1440×1100) підтверджує, що картка по центру екрана, а текст і кнопка вирівняні по центру: [access-enter-fixed.png](assets/2026-11-13-ai-helper/access-enter-fixed.png). Повні значення `getBoundingClientRect()`/`getComputedStyle()` — після прогону browser-тесту.
- **CSS через тунель:** перевірка, що сторінка через HTTPS-тунель віддає оновлений `access_links.css` (cache-busting `?v=20261113d`) — **підтверджено**: live-сторінка посилається на `access_links.css?v=20261113d`, тунель віддає CSS 25 257 байт із правилами `body.access-landing`; headless Chrome-скріншот live-URL показує картку по центру екрана — [access-enter-live-fixed.png](assets/2026-11-13-ai-helper/access-enter-live-fixed.png).
- **Узгодженість метаданих:** `VERSION`, `Dockerfile` (`ARG APP_VERSION`), `compose.yaml` (теги образів `app`/`ai_worker` і build arg; runner лишається 4.5.7), `README.md`, `feed/changelog.py` (`SITE_VERSION`, `SITE_VERSION_SHORT`, перший запис `CHANGELOG_DATA`) і `RELEASES.json` вказують на 4.9.8; `is_latest` рівно один; `python -m py_compile feed/changelog.py` — OK; історія 4.9.7 у журналі та CHANGELOG збережена.

## Залишкові обмеження

- Центрування забезпечується CSS-класами `.access-landing`/`.access-landing-card`: якщо CSS не завантажиться, сторінка лишиться доступною, але без оформлення (це свідомий progressive enhancement, а не повна гарантія вигляду без стилів).
- Автоматизована перевірка геометрії виконується лише в Chrome через CDP; Safari/WebKit і мобільні браузери окремим прогоном не покриті (як і в попередніх релізах).
- Cache-busting ручний (`?v=20261113d`): під час наступних змін CSS версію треба оновлювати вручну, централізованого хешування статики не запроваджено.
- Виправлення стосується саме публічних сторінок доступу; інші standalone-шаблони (наприклад, сторінки помилок) не змінювалися.
- Перевірка через тунель залежить від його кешування та доступності Funnel; у межах цієї задачі тунельний прогін виконано після розгортання 4.9.8 на сервері; залежність від кешу браузера/проксі знято cache-busting-параметром.

## Розгортання на сервері

- Сервер `192.168.1.13` (Tailscale `localwork`), застосунок `http://192.168.1.13:8080`, публічний Funnel — `https://localwork.tail9ec47d.ts.net`.
- Оновлення виконано через `scripts/update.sh`: fast-forward до `eb82b947` (4.9.8), перевірена резервна копія, зібрано `schoolwork2-candidate:4.9.8-248fa7be1a3e`, `schoolnet_app`/`schoolnet_ai_worker` healthy, `manage.py check` — OK.
- Публічний `/access/<token>/` після оновлення віддає новий CSS; headless Chrome показує центровану картку (див. скріншот вище).
