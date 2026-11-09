# Відтворення вимірювань payload (09.11.2026)

`probe_base.py` створює синтетичні об'єкти в lab-БД (вчитель `labprobe`, клас `8-П`,
завдання `LABPROBE assignment`), будує справжній промпт через `build_assessment_request()`
і друкує виміри: обсяг тексту роботи, медіа, мертві блоки, довідкові матеріали, ліміти.

Запуск у lab-контейнері (нічого не надсилає в API):

```bash
sg docker -c 'docker cp probe_base.py schoolwork2-ai-lab-app-1:/tmp/probe_base.py'
sg docker -c 'docker exec schoolwork2-ai-lab-app-1 python manage.py shell -c "exec(open(\"/tmp/probe_base.py\").read())"'
```

Очікувані ключові числа на 4.8.13n: текст роботи 40/40 абзаців; довідка 6 911 → 56 символів;
фото 2.9 МБ → ~2.9 млн символів «тексту» і ~833 тис. токенів; мертві блоки — 0 з 5 у payload.
