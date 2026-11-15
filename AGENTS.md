# Інструкції для систем ШІ (AI Agent Instructions)

Цей файл містить обов'язкові правила для будь-яких автономних AI-асистентів, LLM чи агентів (Cursor, Claude, Copilot, Antigravity тощо), які працюють над кодовою базою **SchoolNet** (`schoolwork2`).

---

## 1. Політика релізів та нумерації версій (Release & Versioning Policy)

Будь-які релізи та підвищення номерів версій у цьому проекті підпорядковуються стандарту **Scope-based SemVer**:
$$\text{Версія} = \mathbf{MAJOR} \,.\, \mathbf{EXTERNAL} \,.\, \mathbf{INTERNAL}$$
Детальний стандарт та дерево рішень описано в [docs/VERSIONING_POLICY.md](docs/VERSIONING_POLICY.md).

### Короткі правила вибору номерів:
1. **Зовнішні зміни (User / UI / Features)**:
   - Якщо змінено зовнішній вигляд, форми, шаблони `templates/`, стилі `static/css/`, публічні сторінки, кабінети чи додано новий функціонал, помітний для вчителів або учнів:
   - **Підвищуйте другу цифру** (`EXTERNAL`): `4.10.1` $\to$ `4.11.0`.
   - Заголовок повинен починатися з тегу `[UI]` або назви фічі.
2. **Внутрішні зміни (Core / Logic / Fixes / System / Infra)**:
   - Якщо виправлено дефект, оновлено алгоритми ШІ (`feed/gemini_service.py`, `feed/ai_context.py`), оптимізовано базу даних, оновлено Docker, скрипти бекапів або інфраструктуру, а користувацький інтерфейс залишився незмінним:
   - **Підвищуйте третю цифру** (`INTERNAL`): `4.10.0` $\to$ `4.10.1`.
   - Заголовок повинен починатися з відповідного тегу: `[CORE]`, `[SYS]` або `[SEC]`.
3. **Заборонені формати**:
   - Ніколи не додавайте літерні суфікси на кшталт `4.8.13a` ... `4.8.13n`.
   - Номер версії має бути суворо числовим: `X.Y.Z`.

### Обов'язкова синхронізація 4 файлів при релізі:
Коли створюється реліз, обов'язково оновлюються:
1. `VERSION`
2. `RELEASES.json` (новий запис нагорі з `title`, `date`, `changes`)
3. `CHANGELOG.md` (новий заголовок `## [X.Y.Z] - YYYY-MM-DD - [TAG] ...`)
4. `feed/changelog.py` (`SITE_VERSION`, `SITE_VERSION_SHORT`, перший елемент `CHANGELOG_DATA`)

Перед створенням коміту завжди запускайте перевірку:
```bash
python3 scripts/check_version_policy.py
```

---

## 2. Верифікація та тестування перед комітом

Перед комітом релізу або важливих змін обов'язково виконайте перевірки:
1. **Тести механізму оновлень**:
   ```bash
   .venv/bin/python3 manage.py test feed.tests_system_updates
   PYTHONPATH=. .venv/bin/python3 scripts/test_updater_service.py
   PYTHONPATH=. .venv/bin/python3 scripts/test_updater_jobs.py
   PYTHONPATH=. .venv/bin/python3 scripts/test_update_helpers.py
   node scripts/test_updates_ui.cjs
   ```
2. **Перевірка політики версій**:
   ```bash
   python3 scripts/check_version_policy.py
   ```
3. **Відповідність робочих контейнерів (якщо оновлюється сервер)**:
   ```bash
   PYTHONPATH=scripts python3 scripts/verify_release.py
   ```

---

## 3. Безпека та цілісність даних
- Не змінюйте чутливі файли конфігурації (`.env`, приватні ключі).
- Будь-які зміни в моделях Django повинні супроводжуватися відповідними міграціями (`makemigrations`).
- Зберігайте детермінованість перевірок файлів та нульову толерантність до галюцинацій ШІ при оцінюванні робіт.
