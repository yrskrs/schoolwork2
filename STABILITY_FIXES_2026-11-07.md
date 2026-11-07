# Узгодження тесту масштабу з меню — 07.11.2026

Після коміту `61f59aa` меню масштабу має кнопки «−», «+» і скидання 100%, а окремі пресети вилучені. Тест `SchoolNetSubmissionsIntegrationTest.test_font_scale_controls_and_safe_limits` досі очікував видалені атрибути `data-scale="85"` і `data-scale="125"`.

Тепер перевіряються чинні кнопки з кроком −5/+5, скидання 100% та константи меж 85–125%. Продуктивний код масштабування не змінювався.

Перевірки:

```bash
PYTHON_DOTENV_DISABLED=1 python manage.py test feed.tests.SchoolNetSubmissionsIntegrationTest.test_font_scale_controls_and_safe_limits --settings=schoolnet.test_settings --noinput
PYTHON_DOTENV_DISABLED=1 python manage.py test --settings=schoolnet.test_settings --noinput
```

Цільовий тест пройшов. Повний набір: 942 тести, 939 пройшли, 3 пропущені, помилок немає. Використано Python 3.12, тимчасове середовище та ізольовану SQLite. Повний запуск мав доступ до системного LibreOffice для перевірки тестових документів; без нього sandbox спричиняє окремий збій рендерингу Excel.

Міграцій і залежностей немає. Це невипущені зміни перевірок; номер релізу не змінено, розгортання не виконувалося.
