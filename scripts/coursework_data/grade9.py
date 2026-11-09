"""Informatics assignments and submissions for Grade 9 (NUS)."""
from coursework_data.rich_content import (
    G9_L2_TEACHER_ATTACHMENT,
    G9_L2_SUBMISSIONS,
    G9_L3_TEACHER_ATTACHMENT,
    G9_L3_SUBMISSIONS,
)

LESSONS_9 = [
    {
        'grade': 9,
        'lesson': 1,
        'title': 'Програмування на Python: списки та алгоритми їх опрацювання',
        'kind': 'py',
        'description': (
            "Тема уроку: Списки (масиви) у Python. Алгоритми пошуку екстремумів, фільтрації та статистичної обробки.\n"
            "Завдання:\n"
            "Написати програму для класного керівника, яка аналізує підсумкові оцінки учнів класу з інформатики за семестр.\n"
            "Вимоги до програми:\n"
            "1. Задати тестовий список оцінок 25 учнів (числа від 1 до 12), або забезпечити введення.\n"
            "2. Власноруч (за допомогою одного циклу `for` без використання стандартних функцій `min()`, `max()`, `sum()`):\n"
            "   - знайти найвищий (максимальний) та найнижчий (мінімальний) бал у класі;\n"
            "   - обчислити суму та середній бал успішності класу;\n"
            "   - підрахувати кількість оцінок на кожному з 4-х рівнів навчальних досягнень:\n"
            "     * Початковий рівень (1-3 бали);\n"
            "     * Середній рівень (4-6 балів);\n"
            "     * Достатній рівень (7-9 балів);\n"
            "     * Високий рівень (10-12 балів).\n"
            "3. Сформувати новий відфільтрований список учнів-відмінників (оцінки >= 10).\n"
            "4. Вивести красивий зведений аналітичний звіт у консоль."
        ),
        'custom_criteria': (
            "1. Самостійна реалізація алгоритмів пошуку екстремумів та накопичення суми без вбудованих функцій.\n"
            "2. Точність класифікації оцінок за 4-ма рівнями навчальних досягнень.\n"
            "3. Правильність формування списку відфільтрованих оцінок високого рівня.\n"
            "4. Акуратність, структурованість та наочність звіту."
        ),
        'submissions': {
            12: {
                'content': (
                    "# ====================================================================\n"
                    "# Практична робота: Статистичний аналіз списку оцінок (Python)\n"
                    "# Автор: Мельник Софія, учениця 9-А класу\n"
                    "# ====================================================================\n\n"
                    "def analyze_grades(scores):\n"
                    "    print(\"=\" * 60)\n"
                    "    print(\"       АНАЛІТИЧНИЙ ЗВІТ УСПІШНОСТІ УЧНІВ 9-А КЛАСУ        \")\n"
                    "    print(\"=\" * 60)\n\n"
                    "    if not scores:\n"
                    "        print(\"Помилка: список оцінок порожній!\")\n"
                    "        return\n\n"
                    "    n = len(scores)\n"
                    "    print(f\"Вихідні оцінки ({n} учнів):\\n{scores}\\n\")\n\n"
                    "    # 1. Ручна ініціалізація алгоритмів обробки списку\n"
                    "    min_score = scores[0]\n"
                    "    max_score = scores[0]\n"
                    "    total_sum = 0\n"
                    "    \n"
                    "    # Лічильники за чотирма рівнями НУШ\n"
                    "    levels = {\n"
                    "        'Початковий (1-3)': 0,\n"
                    "        'Середній (4-6)': 0,\n"
                    "        'Достатній (7-9)': 0,\n"
                    "        'Високий (10-12)': 0\n"
                    "    }\n"
                    "    high_achievers = []\n\n"
                    "    # 2. Один ефективний прохід списку\n"
                    "    for mark in scores:\n"
                    "        total_sum += mark\n"
                    "        \n"
                    "        if mark < min_score:\n"
                    "            min_score = mark\n"
                    "        if mark > max_score:\n"
                    "            max_score = mark\n"
                    "            \n"
                    "        if 1 <= mark <= 3:\n"
                    "            levels['Початковий (1-3)'] += 1\n"
                    "        elif 4 <= mark <= 6:\n"
                    "            levels['Середній (4-6)'] += 1\n"
                    "        elif 7 <= mark <= 9:\n"
                    "            levels['Достатній (7-9)'] += 1\n"
                    "        elif 10 <= mark <= 12:\n"
                    "            levels['Високий (10-12)'] += 1\n"
                    "            high_achievers.append(mark)\n\n"
                    "    avg_score = total_sum / n\n\n"
                    "    # 3. Виведення результатів\n"
                    "    print(\"ЗВЕДЕНІ ПОКАЗНИКИ:\")\n"
                    "    print(f\"• Загальна кількість робіт:     {n}\")\n"
                    "    print(f\"• Найвищий бал:                 {max_score}\")\n"
                    "    print(f\"• Найнижчий бал:                {min_score}\")\n"
                    "    print(f\"• Середній бал класу:           {avg_score:.2f}\")\n"
                    "    print(\"-\" * 60)\n"
                    "    print(\"РОЗПОДІЛ ЗА РІВНЯМИ НАВЧАЛЬНИХ ДОСЯГНЕНЬ:\")\n"
                    "    for level_name, count in levels.items():\n"
                    "        percentage = (count / n) * 100\n"
                    "        bar = '█' * int(percentage // 5)\n"
                    "        print(f\"  {level_name:<18} : {count:>2} учн. ({percentage:5.1f}%) {bar}\")\n"
                    "    print(\"-\" * 60)\n"
                    "    print(f\"Відфільтровані бали високого рівня: {high_achievers}\")\n"
                    "    print(f\"Якісна успішність (достатній + високий): \"\n"
                    "          f\"{((levels['Достатній (7-9)'] + levels['Високий (10-12)']) / n) * 100:.1f}%\")\n"
                    "    print(\"=\" * 60)\n\n"
                    "# Тестовий набір 25 оцінок класу\n"
                    "sample_grades = [10, 8, 11, 7, 5, 12, 9, 6, 11, 4, 10, 8, 7, 9, 12, 10, 6, 8, 11, 5, 7, 9, 10, 4, 12]\n"
                    "\n"
                    "if __name__ == '__main__':\n"
                    "    analyze_grades(sample_grades)\n"
                ),
                'comment_student': "Доброго дня! Реалізувала повний статистичний аналіз списку за один прохід циклу, додала текстову гістограму та розрахунок якісної успішності.",
                'rationale': "Високий рівень (12 балів). Бездоганний алгоритм: власна реалізація екстремумів та сум без заборонених вбудованих функцій, словникова структура рівнів, наочна консольна псевдографіка та валідація.",
                'gaps': [],
            },
            10: {
                'content': (
                    "# Аналіз оцінок класу\n"
                    "# Шевченко Данило, 9-А\n\n"
                    "grades = [11, 7, 9, 10, 6, 12, 8, 5, 10, 11, 7, 9, 12, 4, 8, 10, 9, 7, 6, 11, 10, 8, 12, 5, 9]\n"
                    "\n"
                    "min_val = grades[0]\n"
                    "max_val = grades[0]\n"
                    "total = 0\n"
                    "l1 = 0; l2 = 0; l3 = 0; l4 = 0\n"
                    "high_scores = []\n\n"
                    "for g in grades:\n"
                    "    total += g\n"
                    "    if g < min_val: min_val = g\n"
                    "    if g > max_val: max_val = g\n"
                    "    if g <= 3: l1 += 1\n"
                    "    elif g <= 6: l2 += 1\n"
                    "    elif g <= 9: l3 += 1\n"
                    "    else:\n"
                    "        l4 += 1\n"
                    "        high_scores.append(g)\n\n"
                    "print(\"Кількість учнів:\", len(grades))\n"
                    "print(\"Мінімальна оцінка:\", min_val)\n"
                    "print(\"Максимальна оцінка:\", max_val)\n"
                    "print(\"Середній бал:\", round(total / len(grades), 2))\n"
                    "print(f\"Початковий рівень: {l1}\")\n"
                    "print(f\"Середній рівень: {l2}\")\n"
                    "print(f\"Достатній рівень: {l3}\")\n"
                    "print(f\"Високий рівень: {l4}\")\n"
                    "print(\"Бали високого рівня:\", high_scores)\n"
                ),
                'comment_student': "Здав програму обробки списку оцінок.",
                'rationale': "Високий рівень (10 балів). Усі вимоги виконано власноруч у циклі, класифікація точна, список високого рівня відфільтровано. Оформлення консольного виводу простіше, без відсотків.",
                'gaps': ["Відсутній розрахунок процентного співвідношення рівнів."],
            },
            7: {
                'content': (
                    "# Оцінки класу\n"
                    "# Бондаренко Анна, 9-А\n\n"
                    "grades = [10, 8, 7, 5, 12, 9, 6, 11, 4, 10]\n"
                    "\n"
                    "# Використала стандартні функції\n"
                    "print(\"Мінімум:\", min(grades))\n"
                    "print(\"Максимум:\", max(grades))\n"
                    "print(\"Середнє:\", sum(grades) / len(grades))\n"
                    "\n"
                    "high = []\n"
                    "for x in grades:\n"
                    "    if x >= 10:\n"
                    "        high.append(x)\n"
                    "print(\"Високий рівень:\", len(high), high)\n"
                ),
                'comment_student': "Завдання виконала.",
                'rationale': "Достатній рівень (7 балів). Програма працює, але порушено ключову вимогу завдання: використано стандартні функції `min()`, `max()`, `sum()` замість власного циклічного алгоритму; рівні 1-3, 4-6, 7-9 не підраховано.",
                'gaps': ["Використано готові функції min/max/sum замість власного алгоритму.", "Пропущено підрахунок 1, 2 і 3 рівнів."],
            },
            5: {
                'content': (
                    "# списки\n"
                    "# Коваль Максим\n\n"
                    "grades = [10, 8, 5, 12, 4]\n"
                    "s = 0\n"
                    "for i in range(len(grades)):\n"
                    "    s = s + grades[i]\n"
                    "print(\"Сума:\", s)\n"
                    "print(\"Максимум: 12\")\n"
                ),
                'comment_student': "здав роботу",
                'rationale': "Середній рівень (5 балів). Реалізовано лише підрахунок суми, максимум жорстко зашитий текстом у print замість алгоритму пошуку, мінімум, рівні та фільтрація відсутні.",
                'gaps': ["Максимум захардкоджений числом 12.", "Мінімум, рівні та фільтрація відсутні."],
            },
            4: {
                'content': (
                    "grades = [10, 11, 12]\n"
                    "print(grades[5])\n"
                ),
                'comment_student': "здано",
                'rationale': "Початковий рівень (4 бали). Код падає з помилкою `IndexError: list index out of range`. Жоден алгоритм обробки масивів не написаний.",
                'gaps': ["IndexError під час запуску коду.", "Алгоритм відсутній."],
            },
        },
    },
    {
        'grade': 9,
        'lesson': 2,
        'title': 'Аналіз даних: фільтри, зведені таблиці, розрахунок відвідуваності',
        'kind': 'xlsx',
        'assigned_task': 'Завдання 2 (Аналітична статистика гуртків)',
        'teacher_attachment': G9_L2_TEACHER_ATTACHMENT,
        'description': (
            "Ознайомтеся з даними у прикріпленому файлі «dani_analiz_vidviduvannya.xlsx».\n"
            "Обов'язково виконайте завдання 2 на другому аркуші (розрахувати людино-години за формулою множення, загальну суму через =SUM та частку кожного гуртка у відсотках з абсолютною адресацією).\n"
            "Перший аркуш книги є довідковим і його розраховувати не потрібно.\n"
            "Надішліть файл електронної таблиці (.xlsx)."
        ),
        'custom_criteria': (
            "1. Правильність формул множення та підсумку =SUM().\n"
            "2. Використання абсолютної адресації при розрахунку відсоткових часток.\n"
            "3. Повнота та безпомилковість заповнення аналітичної таблиці.\n"
            "4. Охайність форматування числових комірок."
        ),
        'submissions': G9_L2_SUBMISSIONS,
    },
    {
        'grade': 9,
        'lesson': 3,
        'title': "Комп'ютерні мережі: топології та адресація IPv4",
        'kind': 'docx',
        'assigned_task': 'Завдання 2 (Розрахунок параметрів підмережі /26)',
        'teacher_attachment': G9_L3_TEACHER_ATTACHMENT,
        'description': (
            "Опрацюйте практичну роботу у прикріпленому файлі «praktychna_robota_ipv4_merezhi.docx».\n"
            "До виконання обов'язково: виконати завдання 2 (розрахунок адресного простору та параметрів підмережі /26 для комп'ютерного класу).\n"
            "Інші пункти методички є оглядовими і їх письмово виконувати не потрібно.\n"
            "Звіт із розрахунками та таблицею адрес оформіть у файлі Word (.docx)."
        ),
        'custom_criteria': (
            "1. Точність визначення двійкового та десяткового запису маски /26.\n"
            "2. Правильний розрахунок кількості корисних хостів за формулою 2^k - 2.\n"
            "3. Визначення меж діапазону адрес хостів та широкомовної адреси Broadcast.\n"
            "4. Охайність та структурованість оформлення звіту у Word (.docx)."
        ),
        'submissions': G9_L3_SUBMISSIONS,
    },
    {
        'grade': 9,
        'lesson': 4,
        'title': 'Основи мови SQL: запити вибірки, фільтрації та сортування',
        'kind': 'txt',
        'description': (
            "Тема уроку: Мова структурованих запитів SQL. Оператори SELECT, FROM, WHERE, ORDER BY, функції агрегації та GROUP BY.\n"
            "Завдання:\n"
            "Для реляційної таблиці інтернет-магазину техніки:\n"
            "`Products(id INT, title VARCHAR, category VARCHAR, price DECIMAL, quantity INT, in_stock BOOLEAN, rating DECIMAL)`\n"
            "Скласти 5 конкретних SQL-запитів:\n"
            "1. Вибрати всі поля для товарів категорії 'Смартфони', ціна яких менша або дорівнює 15 000 грн.\n"
            "2. Вивести назву (`title`), ціну (`price`) та рейтинг товарів, які є в наявності (`in_stock = TRUE`), "
            "відсортувавши їх за спаданням рейтингу (`ORDER BY ... DESC`), а при однаковому рейтингу — за зростанням ціни.\n"
            "3. Знайти всі товари, назва яких містить слово 'Pro' (незалежно від регістру або за шаблоном `%Pro%`), з ціною від 10 000 до 30 000 грн (`BETWEEN`).\n"
            "4. Обчислити кількість товарів та середню ціну одиниці товару для кожної категорії окремо (`GROUP BY category`), "
            "присвоївши підсумковим колонкам зрозумілі псевдоніми (`AS total_items`, `AS avg_price`).\n"
            "5. Пояснити різницю між фільтрацією рядків за допомогою `WHERE` та сортуванням результатів через `ORDER BY`."
        ),
        'custom_criteria': (
            "1. Безпомилковість синтаксису мови SQL (лапки для рядків, ключові слова, умови).\n"
            "2. Коректність використання операторів `BETWEEN`, `LIKE`, складного `ORDER BY` за двома полями.\n"
            "3. Правильність застосування агрегатних функцій `COUNT()`, `AVG()` та пропозиції `GROUP BY`.\n"
            "4. Грамотне теоретичне пояснення відмінності між фільтрацією та сортуванням даних."
        ),
        'submissions': {
            12: {
                'content': (
                    "Звіт з практичної роботи: Складання SQL-запитів до бази даних товарів\n"
                    "Автор: Бондаренко Анна, учениця 9-А класу\n\n"
                    "Вихідна структура: Products(id, title, category, price, quantity, in_stock, rating)\n\n"
                    "-- Запит 1: Смартфони з ціною до 15 000 грн\n"
                    "SELECT id, title, category, price, quantity, in_stock, rating\n"
                    "FROM Products\n"
                    "WHERE category = 'Смартфони' AND price <= 15000;\n\n"
                    "-- Запит 2: Наявні товари з подвійним сортуванням (рейтинг спадно, ціна зростаюче)\n"
                    "SELECT title, price, rating\n"
                    "FROM Products\n"
                    "WHERE in_stock = TRUE\n"
                    "ORDER BY rating DESC, price ASC;\n\n"
                    "-- Запит 3: Товари зі словом 'Pro' у діапазоні цін від 10 000 до 30 000 грн\n"
                    "SELECT title, category, price\n"
                    "FROM Products\n"
                    "WHERE title LIKE '%Pro%'\n"
                    "  AND price BETWEEN 10000 AND 30000;\n\n"
                    "-- Запит 4: Кількість та середня ціна товарів за кожною категорією\n"
                    "SELECT \n"
                    "    category,\n"
                    "    COUNT(*) AS total_items,\n"
                    "    ROUND(AVG(price), 2) AS avg_price\n"
                    "FROM Products\n"
                    "GROUP BY category\n"
                    "ORDER BY total_items DESC;\n\n"
                    "5. Теоретичне порівняння WHERE та ORDER BY:\n"
                    "• WHERE виконує фільтрацію (відбір) первинних даних ДО формування підсумкової вибірки. "
                    "Він перевіряє логічну умову для кожного окремого рядка таблиці: якщо умова повертає FALSE, рядок повністю відсікається "
                    "і не передається далі на обробку. WHERE впливає на КІЛЬКІСТЬ рядків у результаті.\n"
                    "• ORDER BY виконує виключно сортування вже відібраних рядків за вказаними критеріями (за зростанням ASC або спаданням DESC). "
                    "Він не додає і не приховує жодного рядка, а лише змінює ПОРЯДОК їх візуального розташування у вихідному наборі даних."
                ),
                'comment_student': "Доброго дня! Написала всі 5 SQL-запитів з коментарями, застосувала подвійне сортування та ROUND для AVG, додала детальне пояснення WHERE і ORDER BY.",
                'rationale': "Високий рівень (12 балів). Бездоганний синтаксис SQL: точне подвійне сортування `rating DESC, price ASC`, оператор `BETWEEN`, `LIKE '%Pro%'`, коректний `GROUP BY` з округленням та глибоке теоретичне розрізнення фільтрації та сортування.",
                'gaps': [],
            },
            10: {
                'content': (
                    "Практична робота: SQL запити\n"
                    "Виконав: Коваль Максим, 9-А клас\n\n"
                    "-- 1. Смартфони до 15000 грн\n"
                    "SELECT * FROM Products\n"
                    "WHERE category = 'Смартфони' AND price <= 15000;\n\n"
                    "-- 2. Товари в наявності з сортуванням за рейтингом\n"
                    "SELECT title, price, rating FROM Products\n"
                    "WHERE in_stock = TRUE\n"
                    "ORDER BY rating DESC, price ASC;\n\n"
                    "-- 3. Пошук Pro у ціні 10000..30000\n"
                    "SELECT title, price FROM Products\n"
                    "WHERE title LIKE '%Pro%' AND price >= 10000 AND price <= 30000;\n\n"
                    "-- 4. Статистика по категоріях\n"
                    "SELECT category, COUNT(*) AS total_items, AVG(price) AS avg_price\n"
                    "FROM Products\n"
                    "GROUP BY category;\n\n"
                    "5. Різниця: WHERE вибирає потрібні рядки за умовою, а ORDER BY просто розставляє їх за зростанням або спаданням."
                ),
                'comment_student': "Здав роботу з SQL.",
                'rationale': "Високий рівень (10 балів). Усі запити синтаксично правильні, розрізнення операторів вірне. Замість оператора `BETWEEN` використано `>= AND <=`, пояснення лаконічне.",
                'gaps': ["Використано конструкцію >= AND <= замість BETWEEN."],
            },
            7: {
                'content': (
                    "SQL запити\n"
                    "Мельник Софія, 9-А\n\n"
                    "1) SELECT * FROM Products WHERE category = 'Смартфони' AND price < 15000;\n"
                    "2) SELECT title, price, rating FROM Products WHERE in_stock = true ORDER BY rating DESC;\n"
                    "3) SELECT * FROM Products WHERE title LIKE '%Pro%';\n"
                    "4) SELECT category, COUNT(id) FROM Products GROUP BY category;\n\n"
                    "WHERE фільтрує записи, а ORDER BY сортує."
                ),
                'comment_student': "Завдання виконала.",
                'rationale': "Достатній рівень (7 балів). Базовий синтаксис засвоєно. Але: у запиті 2 пропущено друге поле сортування за ціною; у запиті 3 пропущено фільтрацію діапазону цін (10 000 - 30 000); у запиті 4 пропущено середню ціну та псевдоніми AS.",
                'gaps': ["Пропущено діапазон цін у запиті 3.", "Пропущено середню ціну AVG у запиті 4."],
            },
            5: {
                'content': (
                    "SQL робота\n"
                    "Ткаченко Марія\n\n"
                    "1) SELECT * FROM Products WHERE category = Смартфони\n"
                    "2) SELECT title FROM Products SORT BY rating\n"
                    "3) SELECT * FROM Products WHERE price BETWEEN 10000 AND 30000\n"
                ),
                'comment_student': "здаю",
                'rationale': "Середній рівень (5 балів). Грубі синтаксичні помилки: відсутні лапки навколо текстового значення `Смартфони`, використано неіснуючий оператор `SORT BY` замість `ORDER BY`, запит з групуванням `GROUP BY` пропущено.",
                'gaps': ["Синтаксична помилка: рядок без лапок.", "Використано помилковий оператор SORT BY.", "Пропущено запит 4."],
            },
            4: {
                'content': (
                    "SELECT Products FROM database\n"
                ),
                'comment_student': "здала",
                'rationale': "Початковий рівень (4 бали). Синтаксично некоректний фрагмент без розуміння структури мови SQL (таблиця вказана замість полів у SELECT).",
                'gaps': ["Некоректний синтаксис SQL."],
            },
        },
    },
    {
        'grade': 9,
        'lesson': 5,
        'title': 'Вебдизайн: створення адаптивного макета портфоліо на HTML5/CSS',
        'kind': 'html',
        'description': (
            "Тема уроку: Адаптивний вебдизайн. Метатег viewport, медіа-запити @media, сучасна типографіка та форма зв'язку.\n"
            "Завдання:\n"
            "Створити завершену сторінку персонального навчального портфоліо випускника 9 класу.\n"
            "Вимоги до проєкту:\n"
            "1. Метатег адаптивності `<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">`.\n"
            "2. Секція «Про мене»: коротка біографія, інтереси в IT (програмування, робототехніка, дизайн).\n"
            "3. Секція «Мої проєкти»: адаптивна сітка мінімум з трьох карток проєктів з описом використаних технологій (Python, HTML/CSS, Scratch тощо).\n"
            "4. Секція «Контакти»: валідна HTML-форма надсилання повідомлення (поля: Ім'я, Email, Тема повідомлення, Текст повідомлення, кнопка «Надіслати»).\n"
            "5. Адаптивність через CSS медіа-запит: на екранах шириною менше 768px картки проєктів мають перебудовуватися в один вертикальний стовпчик."
        ),
        'custom_criteria': (
            "1. Наявність метатегу viewport та реалізація адаптивності через `@media (max-width: 768px)`.\n"
            "2. Семантична повнота розмітки (header, main, section, form, input, textarea, button).\n"
            "3. Валідність структури форми зв'язку з атрибутами `required`, `type=\"email\"`.\n"
            "4. Естетичність інтерфейсу та чистота CSS стилів."
        ),
        'submissions': {
            12: {
                'content': (
                    "<!DOCTYPE html>\n<html lang=\"uk\">\n<head>\n"
                    "    <meta charset=\"UTF-8\">\n"
                    "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
                    "    <title>Портфоліо випускника 9-А класу</title>\n"
                    "    <style>\n"
                    "        :root {\n"
                    "            --primary: #4f46e5;\n"
                    "            --primary-hover: #4338ca;\n"
                    "            --bg-dark: #0f172a;\n"
                    "            --card-bg: #1e293b;\n"
                    "            --text-light: #f8fafc;\n"
                    "            --text-muted: #94a3b8;\n"
                    "        }\n"
                    "        * { box-sizing: border-box; margin: 0; padding: 0; }\n"
                    "        body {\n"
                    "            font-family: 'Inter', system-ui, -apple-system, sans-serif;\n"
                    "            background-color: var(--bg-dark);\n"
                    "            color: var(--text-light);\n"
                    "            line-height: 1.6;\n"
                    "        }\n"
                    "        .container { max-width: 1000px; margin: 0 auto; padding: 0 20px; }\n"
                    "        header {\n"
                    "            padding: 50px 0 30px;\n"
                    "            text-align: center;\n"
                    "            border-bottom: 1px solid #334155;\n"
                    "        }\n"
                    "        header h1 { font-size: 32px; color: #818cf8; margin-bottom: 8px; }\n"
                    "        header p { color: var(--text-muted); font-size: 18px; }\n"
                    "        section { padding: 40px 0; border-bottom: 1px solid #334155; }\n"
                    "        h2 { font-size: 24px; color: #c7d2fe; margin-bottom: 20px; text-align: center; }\n"
                    "        .projects-grid {\n"
                    "            display: grid;\n"
                    "            grid-template-columns: repeat(3, 1fr);\n"
                    "            gap: 20px;\n"
                    "        }\n"
                    "        .project-card {\n"
                    "            background: var(--card-bg);\n"
                    "            border: 1px solid #334155;\n"
                    "            border-radius: 12px;\n"
                    "            padding: 24px;\n"
                    "            display: flex;\n"
                    "            flex-direction: column;\n"
                    "            transition: transform 0.2s, border-color 0.2s;\n"
                    "        }\n"
                    "        .project-card:hover {\n"
                    "            transform: translateY(-5px);\n"
                    "            border-color: #818cf8;\n"
                    "        }\n"
                    "        .project-card h3 { color: #38bdf8; margin-bottom: 10px; font-size: 18px; }\n"
                    "        .project-card p { color: var(--text-muted); font-size: 14px; flex-grow: 1; margin-bottom: 15px; }\n"
                    "        .tech-stack { font-size: 12px; color: #a5b4fc; font-weight: bold; }\n"
                    "        /* Форма контактів */\n"
                    "        .contact-form {\n"
                    "            max-width: 550px;\n"
                    "            margin: 0 auto;\n"
                    "            background: var(--card-bg);\n"
                    "            padding: 30px;\n"
                    "            border-radius: 12px;\n"
                    "        }\n"
                    "        .form-group { margin-bottom: 16px; }\n"
                    "        label { display: block; margin-bottom: 6px; font-size: 14px; color: #cbd5e1; }\n"
                    "        input, textarea {\n"
                    "            width: 100%;\n"
                    "            padding: 10px 14px;\n"
                    "            border-radius: 6px;\n"
                    "            border: 1px solid #475569;\n"
                    "            background: #0f172a;\n"
                    "            color: white;\n"
                    "            font-size: 14px;\n"
                    "        }\n"
                    "        input:focus, textarea:focus { outline: none; border-color: var(--primary); }\n"
                    "        button.btn-submit {\n"
                    "            width: 100%;\n"
                    "            padding: 12px;\n"
                    "            background: var(--primary);\n"
                    "            color: white;\n"
                    "            border: none;\n"
                    "            border-radius: 6px;\n"
                    "            font-size: 15px;\n"
                    "            font-weight: bold;\n"
                    "            cursor: pointer;\n"
                    "            transition: background 0.2s;\n"
                    "        }\n"
                    "        button.btn-submit:hover { background: var(--primary-hover); }\n"
                    "        /* Адаптивність для мобільних екранів */\n"
                    "        @media (max-width: 768px) {\n"
                    "            .projects-grid {\n"
                    "                grid-template-columns: 1fr;\n"
                    "            }\n"
                    "            header h1 { font-size: 26px; }\n"
                    "            .contact-form { padding: 20px; }\n"
                    "        }\n"
                    "    </style>\n</head>\n<body>\n"
                    "    <header>\n"
                    "        <div class=\"container\">\n"
                    "            <h1>Коваль Максим — Junior Developer</h1>\n"
                    "            <p>Учень 9-А класу Ліцею № 12. Захоплююся алгоритмами на Python та сучасною вебверсткою.</p>\n"
                    "        </div>\n"
                    "    </header>\n"
                    "    <main class=\"container\">\n"
                    "        <section>\n"
                    "            <h2>Мої завершені навчальні проєкти</h2>\n"
                    "            <div class=\"projects-grid\">\n"
                    "                <article class=\"project-card\">\n"
                    "                    <h3>Аналізатор успішності</h3>\n"
                    "                    <p>Консольний додаток на Python для статистичної обробки списків оцінок, пошуку екстремумів та якісної успішності.</p>\n"
                    "                    <span class=\"tech-stack\">Стек: Python 3, Списки, Алгоритми</span>\n"
                    "                </article>\n"
                    "                <article class=\"project-card\">\n"
                    "                    <h3>База даних спортклубу</h3>\n"
                    "                    <p>Проєкт реляційної моделі даних з 4 нормалізованими таблицями, зв'язком M:N та аналітичними SQL-запитами.</p>\n"
                    "                    <span class=\"tech-stack\">Стек: SQL, Relational Model, СУБД</span>\n"
                    "                </article>\n"
                    "                <article class=\"project-card\">\n"
                    "                    <h3>Сайт клубу RoboLab</h3>\n"
                    "                    <p>Адаптивна посадкова сторінка гуртка робототехніки на CSS Grid з темною темою та інтерактивними картками.</p>\n"
                    "                    <span class=\"tech-stack\">Стек: HTML5, CSS Grid/Flexbox</span>\n"
                    "                </article>\n"
                    "            </div>\n"
                    "        </section>\n"
                    "        <section>\n"
                    "            <h2>Зворотний зв'язок</h2>\n"
                    "            <form class=\"contact-form\" action=\"#\" method=\"post\">\n"
                    "                <div class=\"form-group\">\n"
                    "                    <label for=\"name\">Ваше ім'я:</label>\n"
                    "                    <input type=\"text\" id=\"name\" required placeholder=\"Введіть ваше ім'я\">\n"
                    "                </div>\n"
                    "                <div class=\"form-group\">\n"
                    "                    <label for=\"email\">Електронна пошта:</label>\n"
                    "                    <input type=\"email\" id=\"email\" required placeholder=\"example@school.ua\">\n"
                    "                </div>\n"
                    "                <div class=\"form-group\">\n"
                    "                    <label for=\"msg\">Повідомлення:</label>\n"
                    "                    <textarea id=\"msg\" rows=\"4\" required placeholder=\"Ваш коментар чи пропозиція...\"></textarea>\n"
                    "                </div>\n"
                    "                <button type=\"submit\" class=\"btn-submit\">Надіслати повідомлення</button>\n"
                    "            </form>\n"
                    "        </section>\n"
                    "    </main>\n</body>\n</html>"
                ),
                'comment_student': "Доброго дня! Створив адаптивне портфоліо з медіа-запитом @media (max-width: 768px), формою зворотного зв'язку та CSS-змінними.",
                'rationale': "Високий рівень (12 балів). Сучасний адаптивний вебмакет: CSS Custom Properties, правильний медіа-запит мобільної адаптації, валідна форма зв'язку з атрибутами `required` та `type=\"email\"`.",
                'gaps': [],
            },
            10: {
                'content': (
                    "<!DOCTYPE html>\n<html lang=\"uk\">\n<head>\n"
                    "    <meta charset=\"UTF-8\">\n"
                    "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
                    "    <title>Портфоліо Мельник Софії</title>\n"
                    "    <style>\n"
                    "        body { font-family: Arial, sans-serif; background: #f8fafc; color: #334155; margin: 0; padding: 20px; }\n"
                    "        .container { max-width: 900px; margin: auto; }\n"
                    "        header { text-align: center; margin-bottom: 30px; }\n"
                    "        .projects { display: flex; gap: 20px; justify-content: space-between; margin-bottom: 40px; }\n"
                    "        .card { background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; flex: 1; }\n"
                    "        form { max-width: 500px; margin: auto; background: white; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px; }\n"
                    "        input, textarea { width: 100%; margin-bottom: 12px; padding: 8px; box-sizing: border-box; }\n"
                    "        button { background: #3b82f6; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; }\n"
                    "        @media (max-width: 768px) {\n"
                    "            .projects { flex-direction: column; }\n"
                    "        }\n"
                    "    </style>\n</head>\n<body>\n"
                    "    <div class=\"container\">\n"
                    "        <header>\n"
                    "            <h1>Портфоліо: Мельник Софія, 9-А клас</h1>\n"
                    "            <p>Мої проєкти з програмування та баз даних</p>\n"
                    "        </header>\n"
                    "        <h2>Проєкти</h2>\n"
                    "        <div class=\"projects\">\n"
                    "            <div class=\"card\"><h3>Калькулятор ремонту</h3><p>Програма Python для кошторису.</p></div>\n"
                    "            <div class=\"card\"><h3>База даних</h3><p>Таблиці для бібліотеки.</p></div>\n"
                    "            <div class=\"card\"><h3>Вебсайт</h3><p>HTML сторінка про науку.</p></div>\n"
                    "        </div>\n"
                    "        <h2>Написати мені</h2>\n"
                    "        <form>\n"
                    "            <input type=\"text\" placeholder=\"Ім'я\" required>\n"
                    "            <input type=\"email\" placeholder=\"Email\" required>\n"
                    "            <textarea placeholder=\"Повідомлення\" rows=\"3\" required></textarea>\n"
                    "            <button type=\"submit\">Надіслати</button>\n"
                    "        </form>\n"
                    "    </div>\n</body>\n</html>"
                ),
                'comment_student': "Здала макет портфоліо з адаптивністю.",
                'rationale': "Високий рівень (10 балів). Метатег `viewport` на місці, медіа-запит `@media` адаптує картки у вертикальний стовпчик, форма зв'язку працює. Дизайн базовий, без CSS-змінних та розширеного опису проєктів.",
                'gaps': ["Опис навчальних проєктів у картках мінімалістичний."],
            },
            7: {
                'content': (
                    "<!DOCTYPE html>\n<html>\n<head>\n"
                    "    <meta charset=\"UTF-8\">\n"
                    "    <title>Моє портфоліо</title>\n"
                    "    <style>\n"
                    "        .card { border: 1px solid gray; padding: 10px; margin: 10px; display: inline-block; width: 200px; }\n"
                    "    </style>\n</head>\n<body>\n"
                    "    <h1>Портфоліо учня 9 класу</h1>\n"
                    "    <div class=\"card\">Проєкт 1: Python</div>\n"
                    "    <div class=\"card\">Проєкт 2: База даних</div>\n"
                    "    <div class=\"card\">Проєкт 3: Верстка</div>\n"
                    "    <h3>Контакти</h3>\n"
                    "    <form>\n"
                    "        Ім'я: <input type=\"text\"><br>\n"
                    "        Пошта: <input type=\"text\"><br>\n"
                    "        <input type=\"submit\" value=\"Відправити\">\n"
                    "    </form>\n"
                    "</body>\n</html>"
                ),
                'comment_student': "Здав роботу.",
                'rationale': "Достатній рівень (7 балів). Картки та форма присутні. Але відсутній обов'язковий метатег `<meta name=\"viewport\">`, відсутній медіа-запит `@media`, поле пошти має тип `text` замість `email` без валідації `required`.",
                'gaps': ["Відсутній метатег viewport.", "Немає адаптивності через @media.", "Форма без валідації."],
            },
            5: {
                'content': (
                    "<html><body>\n"
                    "<h1>Портфоліо</h1>\n"
                    "<p>Я робив проєкти в школі на інформатиці.</p>\n"
                    "<input type=\"text\" placeholder=\"Ваш емейл\">\n"
                    "<button>Ок</button>\n"
                    "</body></html>"
                ),
                'comment_student': "моє портфоліо",
                'rationale': "Середній рівень (5 балів). Картки проєктів відсутні, форма неповноцінна (одне поле поза тегом form), адаптивності та стилів CSS немає.",
                'gaps': ["Картки проєктів відсутні.", "Немає тегу form та CSS стилів."],
            },
            4: {
                'content': (
                    "портфоліо учня\n"
                ),
                'comment_student': "здав",
                'rationale': "Початковий рівень (4 бали). Звичайний текст без верстки, завдання проігноровано.",
                'gaps': ["HTML-код відсутній."],
            },
        },
    },
]
