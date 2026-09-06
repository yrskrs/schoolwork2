from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
import os
import json
from unittest.mock import patch, MagicMock

from .models import (
    Teacher, ClassGroup, Student, Subject, Assignment, AssignmentFile,
    Submission, SubmissionComment, SubmissionActivityLog, School,
    AISettings, DEFAULT_NUS_SYSTEM_PROMPT, AICriteriaPreset, DEFAULT_TRADITIONAL_SYSTEM_PROMPT
)


from .fuzzy_search import fuzzy_search_submissions, translate_en_to_ua
from .gemini_service import evaluate_submission_with_gemini


class SchoolNetSubmissionsIntegrationTest(TestCase):
    def setUp(self):
        # Створюємо вчителя
        self.user = User.objects.create_user(username='teacher1', password='password123', is_staff=True)
        self.teacher = Teacher.objects.create(
            user=self.user,
            full_name='Коваленко Петро Іванович'
        )

        self.class_group = ClassGroup.objects.create(grade=9, letter='А', name='9-А')
        self.teacher.classes.add(self.class_group)

        self.subject = Subject.objects.create(name='Інформатика', icon='💻', color='#3b82f6')
        self.teacher.subjects.add(self.subject)

        # Створюємо завдання
        self.assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Практична робота №1: Таблиці Excel',
            description='Створіть таблицю обліку товарів з формулами SUM та AVERAGE.',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        self.assignment.classes.add(self.class_group)

        self.client = Client()

    def test_student_submission_creation(self):
        """Тест здачі роботи учнем з файлом та посиланням."""
        file = SimpleUploadedFile("report.py", b"print('Hello SchoolNet')", content_type="text/x-python")

        submission = Submission.objects.create(
            assignment=self.assignment,
            first_name='Олександр',
            last_name='Шевченко',
            class_group=self.class_group,
            teacher=self.teacher,
            file=file,
            comment_student='Ось мій файл з розрахунками'
        )

        self.assertEqual(submission.get_student_full_name(), 'Шевченко Олександр')
        self.assertEqual(submission.get_file_extension(), '.py')
        self.assertEqual(submission.get_file_icon(), '💻')

    def test_fuzzy_search_and_keyboard_layout(self):
        """Тест розумного пошуку з виправленням розкладки En/Ua."""
        Submission.objects.create(
            assignment=self.assignment,
            first_name='Максим',
            last_name='Бондаренко',
            class_group=self.class_group,
            teacher=self.teacher,
        )

        # Пошук на англійській розкладці замість української "Бондаренко" -> " <jylfh"
        translated = translate_en_to_ua("Gtnhj")
        self.assertEqual(translated, "Петро")

        # Пошук за прізвищем
        qs = Submission.objects.all()
        results = fuzzy_search_submissions(qs, 'Бондар')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].last_name, 'Бондаренко')

    def test_file_viewer_and_ajax_grading(self):
        """Тест File Viewer, виставлення оцінки та коментарів через AJAX."""
        self.client.login(username='teacher1', password='password123')

        submission = Submission.objects.create(
            assignment=self.assignment,
            first_name='Іван',
            last_name='Мельник',
            class_group=self.class_group,
            teacher=self.teacher,
            link='https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        )

        # 1. Відкриття сторінки переглядача
        response = self.client.get(reverse('view_file', args=[submission.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Мельник Іван')
        self.assertContains(response, 'YouTube player')

        # 2. Виставлення оцінки через AJAX
        grade_resp = self.client.post(
            reverse('grade_submission', args=[submission.id]),
            {'grade': '11'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(grade_resp.status_code, 200)
        data = grade_resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['grade'], '11')

        submission.refresh_from_db()
        self.assertEqual(submission.grade, '11')

        # 3. Додавання коментаря вчителя
        comment_resp = self.client.post(
            reverse('add_submission_comment', args=[submission.id]),
            {'text': 'Чудова робота! Всі формули правильні.'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(comment_resp.status_code, 200)
        cdata = comment_resp.json()
        self.assertEqual(cdata['status'], 'success')
        self.assertEqual(submission.comments.count(), 1)

    def test_gradebook_and_export(self):
        """Тест журналу оцінок та CSV експорту."""
        self.client.login(username='teacher1', password='password123')

        Submission.objects.create(
            assignment=self.assignment,
            first_name='Анна',
            last_name='Коваль',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='12'
        )

        # Перевірка сторінки журналу (Режим 1: Класична матриця)
        response = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&view_mode=journal')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Коваль')
        self.assertContains(response, '12')
        self.assertContains(response, 'journal-table')
        self.assertContains(response, 'journal-grade-badge')

        # Перевірка сторінки журналу (Режим 2: Табличний список)
        resp_table = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&view_mode=table')
        self.assertEqual(resp_table.status_code, 200)
        self.assertContains(resp_table, 'submissions-table')
        self.assertContains(resp_table, 'Коваль')

        # Перевірка експорту в CSV
        export_resp = self.client.get(reverse('export_grades') + f'?class_group={self.class_group.id}')
        self.assertEqual(export_resp.status_code, 200)
        self.assertEqual(export_resp['Content-Type'], 'text/csv; charset=utf-8-sig')
        content = export_resp.content.decode('utf-8-sig')
        self.assertIn('Коваль', content)
        self.assertIn('12', content)


    def test_activity_log_tracking(self):
        """Тест логування дій."""
        self.client.login(username='teacher1', password='password123')

        logs_count_before = SubmissionActivityLog.objects.count()

        # Робимо дію (наприклад створюємо здачу)
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Тест',
            last_name='Учень',
            class_group=self.class_group,
            teacher=self.teacher,
        )

        # Оцінюємо
        self.client.post(
            reverse('grade_submission', args=[sub.id]),
            {'grade': '10'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        self.assertGreater(SubmissionActivityLog.objects.count(), logs_count_before)

    def test_student_detail_view(self):
        """Тест сторінки історії учня."""
        self.client.login(username='teacher1', password='password123')

        Submission.objects.create(
            assignment=self.assignment,
            first_name='Денис',
            last_name='Кравченко',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='11'
        )

        response = self.client.get(reverse('student_detail', kwargs={'student_name': 'Кравченко_Денис'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Кравченко Денис')
        self.assertContains(response, '11')

    def test_school_settings_and_profiles(self):
        """Тест сторінки керування закладом та вчителями."""
        self.user.is_superuser = True
        self.user.save()
        self.client.login(username='teacher1', password='password123')

        # Перехід у режим супер-адміна
        resp = self.client.get(reverse('enter_superadmin_mode'), follow=True)
        self.assertEqual(resp.status_code, 200)

        # Зміна назви школи
        resp_school = self.client.post(
            reverse('school_settings'),
            {'school_name': 'Ліцей «Інтелект»'},
            follow=True
        )
        self.assertEqual(resp_school.status_code, 200)
        self.assertEqual(School.objects.first().name, 'Ліцей «Інтелект»')

    def test_assignment_detail_new_layout(self):
        """Тест макету деталей завдання з вбудованим переглядом файлів."""
        test_file = SimpleUploadedFile("urok_material.txt", b"Test text content for lesson", content_type="text/plain")
        AssignmentFile.objects.create(
            assignment=self.assignment,
            file=test_file,
            original_name="urok_material.txt"
        )

        response = self.client.get(reverse('assignment_detail', args=[self.assignment.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Здати роботу')
        self.assertContains(response, self.assignment.title)
        self.assertContains(response, 'urok_material.txt')
        self.assertContains(response, 'Переглянути в браузері')
        self.assertContains(response, 'Завантажити файл')


    def test_student_submissions_portal_view(self):
        """Тест публічного розділу для учнів 'Здані роботи'."""
        # Створюємо здачу з коментарем
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Софія',
            last_name='Мельник',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='12'
        )
        SubmissionComment.objects.create(
            submission=sub,
            author=self.user,
            text='Відмінно виконане практичне завдання!'
        )

        # Перегляд без фільтра
        resp = self.client.get(reverse('student_submissions_portal'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Софія')
        self.assertContains(resp, 'Оцінено вчителем')
        self.assertNotContains(resp, 'grade-badge')
        self.assertContains(resp, 'Відмінно виконане практичне завдання!')


        # Пошук за прізвищем
        resp_search = self.client.get(reverse('student_submissions_portal') + '?search=Мельник')
        self.assertEqual(resp_search.status_code, 200)
        self.assertContains(resp_search, 'Мельник')

    def test_submit_assignment_and_success_layout(self):
        """Тест макету форми здачі роботи та автоматичного вибору класу."""
        # Форма здачі
        resp = self.client.get(reverse('submit_assignment', args=[self.assignment.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Надіслати виконане завдання')
        self.assertContains(resp, 'Як здавати роботу')
        # Перевірка автоматичного вибору класу завдання у формі
        self.assertEqual(resp.context['form'].initial.get('class_group'), self.class_group)
        self.assertContains(resp, f'value="{self.class_group.id}" selected')

        # Сторінка успіху
        resp_succ = self.client.get(reverse('submit_success', args=[self.assignment.id]))
        self.assertEqual(resp_succ.status_code, 200)
        self.assertContains(resp_succ, 'Роботу успішно здано!')
        self.assertContains(resp_succ, 'Прийняті роботи учнів')


    def test_all_teacher_panel_views_with_sidebar(self):
        """Тест усіх сторінок вчительської панелі з сайдбаром."""
        self.client.login(username='teacher1', password='password123')

        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Максим',
            last_name='Бондар',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='10'
        )

        urls_to_test = [
            reverse('teacher_dashboard'),
            reverse('assignment_create'),
            reverse('teacher_profile'),
            reverse('teacher_profiles'),
            reverse('all_submissions_dashboard'),
            reverse('activity_log'),
            reverse('gradebook') + '?view=all_grades',
            reverse('assignment_submissions', args=[self.assignment.id]),
            reverse('submission_detail', args=[sub.id]),
        ]

        for url in urls_to_test:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f"Failed on URL {url}")
            self.assertContains(resp, 'feed-layout')
            self.assertContains(resp, 'feed-main-content')
            self.assertContains(resp, 'sidebar')

    def test_student_name_normalization_and_diminutives(self):
        """Тест алгоритмів зіставлення імен, зменшувальних форм та одруків."""
        from .student_matcher import (
            are_first_names_equivalent, are_last_names_equivalent,
            is_same_student_identity, resolve_canonical_student_name
        )

        # 1. Еквівалентність імен та пестливих/скорочених форм
        self.assertTrue(are_first_names_equivalent('Олександр', 'Саша'))
        self.assertTrue(are_first_names_equivalent('Олександр', 'Сашко'))
        self.assertTrue(are_first_names_equivalent('Дмитро', 'Діма'))
        self.assertTrue(are_first_names_equivalent('Владислав', 'Влад'))
        self.assertTrue(are_first_names_equivalent('Софія', 'Соня'))
        self.assertTrue(are_first_names_equivalent('Катерина', 'Катя'))
        self.assertTrue(are_first_names_equivalent('Тарас', 'Тарасик'))
        self.assertTrue(are_first_names_equivalent('Т.', 'Тарас'))

        # 2. Еквівалентність прізвищ з одруками / відмінками
        self.assertTrue(are_last_names_equivalent('Шевченко', 'Шевченка'))
        self.assertTrue(are_last_names_equivalent('Мельник', 'Мельнік'))
        self.assertTrue(are_last_names_equivalent('Бондарчук', 'Бондарчука'))

        # 3. Зіставлення одного й того ж учня в прямому та зворотному порядку
        self.assertTrue(is_same_student_identity('Шевченко', 'Тарас', 'Шевченко', 'Тарас'))
        self.assertTrue(is_same_student_identity('Шевченко', 'Тарас', 'Тарас', 'Шевченко'))
        self.assertTrue(is_same_student_identity('Шевченко', 'Тарас', 'Шевченка', 'Тарасик'))
        self.assertTrue(is_same_student_identity('Ковальчук', 'Олександр', 'Саша', 'Ковальчук'))

        # 4. Канонічна резолюція при здачі роботи
        Submission.objects.create(
            assignment=self.assignment,
            last_name='Шевченко',
            first_name='Тарас',
            class_group=self.class_group,
            teacher=self.teacher,
        )

        # Спроба здати як "Тарас Шевченко" у той самий клас
        canon_ln, canon_fn = resolve_canonical_student_name('Тарас Шевченко', class_group=self.class_group)
        self.assertEqual(canon_ln, 'Шевченко')
        self.assertEqual(canon_fn, 'Тарас')

        # Спроба здати як "Шевченко Тарасик"
        canon_ln2, canon_fn2 = resolve_canonical_student_name('Шевченко Тарасик', class_group=self.class_group)
        self.assertEqual(canon_ln2, 'Шевченко')
        self.assertEqual(canon_fn2, 'Тарас')

    def test_gradebook_clusters_student_variations(self):
        """Тест групування варіацій імен учнів в один рядок класного журналу."""
        self.client.login(username='teacher1', password='password123')

        # Створюємо здачі одного учня з різним написанням імені
        Submission.objects.create(
            assignment=self.assignment,
            last_name='Шевченко',
            first_name='Тарас',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='12'
        )
        Submission.objects.create(
            assignment=self.assignment,
            last_name='Тарас',
            first_name='Шевченко',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='10'
        )
        Submission.objects.create(
            assignment=self.assignment,
            last_name='Шевченка',
            first_name='Тарасик',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='11'
        )

        resp = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}')
        self.assertEqual(resp.status_code, 200)

        # Перевіряємо, що в журналі створено рівно 1 об'єднаний рядок для цього учня
        students = resp.context['students']
        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]['total_submissions'], 3)
        self.assertEqual(students[0]['avg_score'], 11.0)

    def test_student_autocomplete_api(self):
        """Тест API автодоповнення імен учнів для швидкої здачі робіт."""
        Submission.objects.create(
            assignment=self.assignment,
            last_name='Коваленко',
            first_name='Оксана',
            class_group=self.class_group,
            teacher=self.teacher,
        )

        resp = self.client.get(reverse('api_students_autocomplete') + f'?class_group_id={self.class_group.id}&query=Ковал')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('students', data)
        self.assertTrue(any(s['last_name'] == 'Коваленко' for s in data['students']))

    def test_zip_export_and_backup_endpoints(self):
        """Тест масового експорту робіт у ZIP та бекапу бази даних."""
        self.client.login(username='teacher1', password='password123')

        # Створюємо роботу з посиланням
        Submission.objects.create(
            assignment=self.assignment,
            last_name='Петренко',
            first_name='Сергій',
            class_group=self.class_group,
            teacher=self.teacher,
            link='https://github.com/school/project'
        )

        # ZIP-експорт
        zip_resp = self.client.get(reverse('download_assignment_submissions_zip', args=[self.assignment.pk]))
        self.assertEqual(zip_resp.status_code, 200)
        self.assertEqual(zip_resp['Content-Type'], 'application/zip')

        # Резервна копія БД
        self.user.is_superuser = True
        self.user.save()
        backup_resp = self.client.get(reverse('download_database_backup'))
        self.assertEqual(backup_resp.status_code, 200)
        self.assertIn(backup_resp['Content-Type'], ['application/x-sqlite3', 'application/json'])

    def test_ai_settings_and_connection(self):
        """Тест налаштувань Google Gemini AI та тесту підключення."""
        from .models import AISettings, DEFAULT_NUS_SYSTEM_PROMPT
        self.client.login(username='teacher1', password='password123')

        # Перевірка сторінки налаштувань ШІ
        resp = self.client.get(reverse('ai_settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Налаштування модуля Google Gemini AI')

        # Збереження налаштувань
        post_resp = self.client.post(reverse('ai_settings'), {
            'api_key': 'AIzaTestFakeKey123',
            'model_name': 'gemini-2.5-flash',
            'system_prompt': DEFAULT_NUS_SYSTEM_PROMPT,
            'temperature': '0.3',
            'is_enabled': '1'
        }, follow=True)
        self.assertEqual(post_resp.status_code, 200)

        settings = AISettings.get_solo()
        self.assertEqual(settings.api_key, 'AIzaTestFakeKey123')
        self.assertEqual(settings.model_name, 'gemini-2.5-flash')
        self.assertTrue(settings.is_enabled)


    @patch('feed.gemini_service._http_post_json')
    def test_gemini_service_evaluation_and_apply(self, mock_http_post):
        """Тест сервісу ШІ-перевірки та застосування оцінки вчителем."""
        from .models import AISettings
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.api_key = 'fake-api-key'
        settings.is_enabled = True
        settings.save()

        # Мокаємо успішну відповідь від Google Gemini
        mock_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"suggested_grade": "11", "level": "Високий (10-12)", "summary": "Відмінна робота", "strengths": ["Гарна структура"], "weaknesses": [], "feedback_comment": "Чудово впоралась з усіма завданнями!"}'
                            }
                        ]
                    }
                }
            ]
        }
        mock_http_post.return_value = (200, mock_data, json.dumps(mock_data))

        # Створюємо роботу учня
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Анна',
            last_name='Коваленко',
            class_group=self.class_group,
            comment_student='Ось мій розв\'язок задачі #4'
        )


        res = evaluate_submission_with_gemini(sub)
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['suggested_grade'], '11')
        self.assertEqual(res['level'], 'Високий (10-12)')

        sub.refresh_from_db()
        self.assertEqual(sub.ai_suggested_grade, '11')
        self.assertEqual(sub.ai_status, 'success')
        self.assertIn('Чудово', sub.ai_feedback)

        # Тест застосування оцінки в 1 клік
        self.client.login(username='teacher1', password='password123')
        apply_resp = self.client.post(reverse('ai_apply_suggested_grade', args=[sub.id]), follow=True)
        self.assertEqual(apply_resp.status_code, 200)

        sub.refresh_from_db()
        self.assertEqual(sub.grade, '11')
        self.assertEqual(sub.comments.count(), 1)
        self.assertIn('відгук ШІ', sub.comments.first().text)

    @patch('feed.gemini_service._http_post_json')
    def test_ai_scope_of_work_instruction(self, mock_http_post):
        """Тест правила Scope of Work: пріоритет умови вчителя над вмістом прикріпленого файлу."""
        from .models import AISettings, AssignmentFile
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.api_key = 'fake-api-key'
        settings.is_enabled = True
        settings.save()

        # Завдання з вимогою виконати тільки одне завдання з кількох
        self.assignment.description = "Виконати ТІЛЬКИ завдання 2 з практичної роботи. Інші завдання робити не потрібно!"
        self.assignment.save()

        # Додаємо прикріплений файл вчителя з 5 завданнями
        teacher_file = SimpleUploadedFile(
            "Практична_робота_1.txt",
            "Завдання 1. ...\nЗавдання 2. ...\nЗавдання 3. ...\nЗавдання 4. ...\nЗавдання 5. ...".encode('utf-8'),
            content_type="text/plain"
        )
        AssignmentFile.objects.create(
            assignment=self.assignment,
            file=teacher_file,
            original_name="Практична_робота_1.txt"
        )

        mock_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"suggested_grade": "11", "level": "Високий (10-12)", "summary": "Завдання 2 виконано бездоганно", "strengths": ["Точний розв\'язок завдання 2"], "weaknesses": [], "feedback_comment": "Відмінна робота!"}'
                            }
                        ]
                    }
                }
            ]
        }
        mock_http_post.return_value = (200, mock_data, json.dumps(mock_data))

        student_file = SimpleUploadedFile("task2_solution.txt", "Розв'язок завдання 2: ...".encode('utf-8'), content_type="text/plain")
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Максим',
            last_name='Бондар',
            class_group=self.class_group,
            file=student_file,
            comment_student='Зробив тільки завдання 2, як ви й просили.'
        )

        res = evaluate_submission_with_gemini(sub)
        self.assertEqual(res['status'], 'success')

        # Перевіряємо сформований payload, відправлений в Gemini API
        self.assertTrue(mock_http_post.called)
        sent_payload = mock_http_post.call_args[0][1]

        # 1. Системна інструкція містить обов'язкове правило SCOPE OF WORK
        sys_text = sent_payload['systemInstruction']['parts'][0]['text']
        self.assertIn('SCOPE OF WORK', sys_text)
        self.assertIn('ПРІОРИТЕТ ВИМОГ ВЧИТЕЛЯ', sys_text)

        # 2. Промпт містить умову вчителя, блок правил SCOPE OF WORK та позначення файлів як довідкових
        user_prompt_text = sent_payload['contents'][0]['parts'][0]['text']
        self.assertIn('УМОВА ТА ВИМОГИ ВЧИТЕЛЯ (ЗАВДАННЯ ДО ВИКОНАННЯ):', user_prompt_text)
        self.assertIn('Виконати ТІЛЬКИ завдання 2', user_prompt_text)
        self.assertIn('SCOPE OF WORK', user_prompt_text)
        self.assertIn('МАТЕРІАЛИ ДО УРОКУ / ДОВІДКОВІ ФАЙЛИ ВЧИТЕЛЯ', user_prompt_text)
        self.assertIn('решта завдань з файлу вважаються незаданими', user_prompt_text.lower())

    def test_ai_batch_check_queue_api(self):
        """Тест API черги пакетної перевірки робіт."""
        self.client.login(username='teacher1', password='password123')

        # Створюємо дві роботи
        Submission.objects.create(
            assignment=self.assignment,
            first_name='Олег',
            last_name='Бондар',
            class_group=self.class_group
        )
        Submission.objects.create(
            assignment=self.assignment,
            first_name='Ірина',
            last_name='Шевченко',
            class_group=self.class_group
        )

        resp = self.client.post(reverse('api_ai_get_batch_queue'), {
            'class_id': str(self.class_group.id),
            'only_ungraded': 'true',
            'only_unreviewed_ai': 'true'
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('queue', data)
        self.assertGreaterEqual(data['total'], 2)

    def test_ai_file_without_extension_reading(self):
        """Тест читання ШІ файлу без розширення (детекція та відкриття як текст)."""
        from .gemini_service import extract_submission_content, is_text_file

        file_data = b"Task 1: x = 10\nTask 2: y = 20\nResult: 30"
        # Створюємо файл без розширення (наприклад 'solution')
        uploaded_file = SimpleUploadedFile("solution_no_ext", file_data, content_type="application/octet-stream")

        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Роман',
            last_name='Лисенко',
            class_group=self.class_group,
            file=uploaded_file
        )

        self.assertTrue(is_text_file(sub.file.path))
        text_parts, inline_media, err = extract_submission_content(sub)
        self.assertIsNone(err)
        self.assertTrue(any('Task 1: x = 10' in part for part in text_parts))
        self.assertTrue(any('solution_no_ext' in part for part in text_parts))

        # Перевірка у File Viewer
        self.client.login(username='teacher1', password='password123')
        fv_resp = self.client.get(reverse('view_file', args=[sub.id]))
        self.assertEqual(fv_resp.status_code, 200)
        self.assertEqual(fv_resp.context['file_type'], 'text')
        self.assertIn('Task 1: x = 10', fv_resp.context['content'])

    def test_ai_pdf_extraction(self):
        """Тест обробки PDF файлу (мультимодальна передача + локальний текст)."""
        from .gemini_service import extract_submission_content

        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 300 300]/Parent 2 0 R>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n170\n%%EOF"
        )

        uploaded_file = SimpleUploadedFile("report.pdf", pdf_bytes, content_type="application/pdf")
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Софія',
            last_name='Мороз',
            class_group=self.class_group,
            file=uploaded_file
        )

        text_parts, inline_media, err = extract_submission_content(sub)
        self.assertIsNone(err)
        self.assertEqual(len(inline_media), 1)
        self.assertEqual(inline_media[0]['mime_type'], 'application/pdf')
        self.assertTrue(len(inline_media[0]['data']) > 0)

    def test_ai_docx_reading(self):
        """Тест видобування вмісту з документа Word (.docx)."""
        import io, docx
        from .gemini_service import extract_submission_content

        doc = docx.Document()
        doc.add_heading('Лабораторна робота з фізики', level=1)
        doc.add_paragraph('Мета: виміряти прискорення вільного падіння.')
        t = doc.add_table(rows=2, cols=2)
        t.cell(0, 0).text = 'Дослід'
        t.cell(0, 1).text = 'Значення'
        t.cell(1, 0).text = '№1'
        t.cell(1, 1).text = '9.81 м/с2'

        buf = io.BytesIO()
        doc.save(buf)
        docx_bytes = buf.getvalue()

        uploaded_file = SimpleUploadedFile("physics.docx", docx_bytes, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Олена',
            last_name='Гриценко',
            class_group=self.class_group,
            file=uploaded_file
        )

        text_parts, inline_media, err = extract_submission_content(sub)
        self.assertIsNone(err)
        full_text = "\n".join(text_parts)
        self.assertIn('Лабораторна робота з фізики', full_text)
        self.assertIn('9.81 м/с2', full_text)

    def test_ai_image_multimodal(self):
        """Тест передачі зображень у мультимодальний формат ШІ."""
        import io
        from PIL import Image
        from .gemini_service import extract_submission_content

        img = Image.new('RGB', (100, 100), color='blue')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        png_bytes = buf.getvalue()

        uploaded_file = SimpleUploadedFile("notebook.png", png_bytes, content_type="image/png")
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Артем',
            last_name='Сидоренко',
            class_group=self.class_group,
            file=uploaded_file
        )

        text_parts, inline_media, err = extract_submission_content(sub)
        self.assertIsNone(err)
        self.assertEqual(len(inline_media), 1)
        self.assertEqual(inline_media[0]['mime_type'], 'image/png')
        self.assertTrue(len(inline_media[0]['data']) > 0)

    @patch('feed.gemini_service.urllib.request.urlopen')
    def test_ai_web_url_and_youtube_fetching(self, mock_urlopen):
        """Тест видобування вмісту веб-посилань та YouTube."""
        from .gemini_service import extract_submission_content

        # Мокаємо відповідь YouTube oEmbed
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            'title': 'Урок 5: Основи алгоритмів',
            'author_name': 'Шкільний канал'
        }).encode('utf-8')
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Надія',
            last_name='Клименко',
            class_group=self.class_group,
            link='https://www.youtube.com/watch?v=12345678901'
        )

        text_parts, inline_media, err = extract_submission_content(sub)
        self.assertIsNone(err)
        full_text = "\n".join(text_parts)
        self.assertIn('Основи алгоритмів', full_text)

    def test_ai_archive_inspection(self):
        """Тест інспекції архіва та читання файлів всередині нього."""
        import io, zipfile
        from .gemini_service import extract_submission_content

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('main.py', 'def calculate_sum(a, b):\n    return a + b\n')
            zf.writestr('readme.txt', 'Проєкт калькулятора')
        zip_bytes = buf.getvalue()

        uploaded_file = SimpleUploadedFile("project.zip", zip_bytes, content_type="application/zip")
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Євген',
            last_name='Волошин',
            class_group=self.class_group,
            file=uploaded_file
        )

        text_parts, inline_media, err = extract_submission_content(sub)
        self.assertIsNone(err)
        full_text = "\n".join(text_parts)
        self.assertIn('main.py', full_text)
        self.assertIn('calculate_sum', full_text)

    @patch('feed.gemini_service._http_post_json')
    def test_ai_file_format_criteria_and_warning(self, mock_http_post):
        """Тест врахування формату файлу (наприклад, файл без розширення для коду) та відображення зауваження."""
        from .models import AISettings
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.api_key = 'fake-api-key'
        settings.is_enabled = True
        settings.save()

        # Мокаємо відповідь від Google Gemini з попередженням про невідповідний формат файлу
        mock_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "suggested_grade": "10",
                                    "level": "Високий (10-12)",
                                    "format_warning": "Файл здано без розширення (для коду Python очікується файл .py). Знижено 1 бал.",
                                    "summary": "Код працює вірно, але розширення файлу відсутнє.",
                                    "strengths": ["Коректний алгоритм підрахунку"],
                                    "weaknesses": ["Файл прикріплено без розширення .py"],
                                    "feedback_comment": "Гарна робота! Зверни увагу: обов'язково зберігай файли з розширенням .py."
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_http_post.return_value = (200, mock_data, json.dumps(mock_data))

        # Створюємо роботу з файлом без розширення
        code_data = b"def sum_numbers(a, b):\n    return a + b\n"
        uploaded_file = SimpleUploadedFile("my_script", code_data, content_type="application/octet-stream")
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Максим',
            last_name='Ткачук',
            class_group=self.class_group,
            file=uploaded_file
        )

        res = evaluate_submission_with_gemini(sub)
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['suggested_grade'], '10')
        self.assertIn('format_warning', res)
        self.assertTrue(len(res['format_warning']) > 0)

        sub.refresh_from_db()
        self.assertIn('Зауваження до формату файлу', sub.ai_feedback)
        self.assertIn('без розширення', sub.ai_feedback)

        # Перевірка відображення у переглядачі вчителя
        self.client.login(username='teacher1', password='password123')
        view_resp = self.client.get(reverse('view_file', args=[sub.id]))
        self.assertEqual(view_resp.status_code, 200)
        self.assertContains(view_resp, 'Невідповідний формат файлу')

        # Перевірка відображення у черзі пакетної перевірки
        queue_resp = self.client.post(reverse('api_ai_get_batch_queue'), {
            'class_id': str(self.class_group.id)
        })
        self.assertEqual(queue_resp.status_code, 200)
        queue_data = queue_resp.json()
        item = next((i for i in queue_data['queue'] if i['id'] == sub.id), None)
        self.assertTrue(item['has_format_warning'])

    @patch('feed.gemini_service._http_post_json')
    def test_ai_format_warning_content_error_separation(self, mock_http_post):
        """Тест: якщо ШІ повертає зауваження до змісту (наприклад, зображення людини замість кота), воно переноситься у weaknesses і не вважається дефектом формату файлу."""
        from .models import AISettings
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.api_key = 'fake-api-key'
        settings.is_enabled = True
        settings.save()

        # ШІ повернув змістовне зауваження у полі format_warning
        mock_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "suggested_grade": "Доопрацювати",
                                    "level": "Початковий (1-3)",
                                    "format_warning": "Завдання виконано некоректно: замість фотографії кота завантажено зображення людини.",
                                    "summary": "Невідповідність темі завдання.",
                                    "strengths": [],
                                    "weaknesses": ["Зображення не відповідає завданню"],
                                    "feedback_comment": "Будь ласка, завантажте фото кота, як вимагалося в завданні."
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_http_post.return_value = (200, mock_data, json.dumps(mock_data))

        # Завантажено валідний файл із розширенням .jpg
        img_data = b"fake_jpg_content"
        uploaded_file = SimpleUploadedFile("cat_photo.jpg", img_data, content_type="image/jpeg")
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Олексій',
            last_name='Коваленко',
            class_group=self.class_group,
            file=uploaded_file
        )

        res = evaluate_submission_with_gemini(sub)
        self.assertEqual(res['status'], 'success')
        # format_warning має бути порожнім, бо це змістовна помилка, а не технічний дефект файлу
        self.assertEqual(res['format_warning'], '')

        sub.refresh_from_db()
        self.assertNotIn('Зауваження до формату файлу', sub.ai_feedback)
        self.assertIn('замість фотографії кота', sub.ai_feedback)

    def test_teacher_settings_view_tabs(self):
        """Тест доступу до Єдиного Центру Налаштувань та всіх його вкладок."""
        self.client.login(username='teacher1', password='password123')

        # Головна сторінка налаштувань
        resp = self.client.get(reverse('teacher_settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Центр налаштувань')
        self.assertContains(resp, 'Профіль вчителя')
        self.assertContains(resp, 'Середовище та заклад')
        self.assertContains(resp, 'Модуль ШІ (Gemini)')

        # Вкладка Профіль
        resp_prof = self.client.get(reverse('teacher_settings') + '?tab=profile')
        self.assertEqual(resp_prof.status_code, 200)
        self.assertContains(resp_prof, 'Особисті дані та аватар')

        # Вкладка Середовище
        resp_env = self.client.get(reverse('teacher_settings') + '?tab=environment')
        self.assertEqual(resp_env.status_code, 200)
        self.assertContains(resp_env, 'Параметри закладу освіти')

        # Вкладка ШІ
        resp_ai = self.client.get(reverse('teacher_settings') + '?tab=ai')
        self.assertEqual(resp_ai.status_code, 200)
        self.assertContains(resp_ai, 'Пріоритети моделей та автоматичний перехід')

    def test_ai_model_priorities_management_and_deletion(self):
        """Тест додавання моделей з пріоритетами, зміни пріоритетів та видалення моделі."""
        from .models import AISettings
        self.client.login(username='teacher1', password='password123')
        settings = AISettings.get_solo()

        # 1. Додавання нової власної моделі з пріоритетом 1 (зробити основною)
        post_resp = self.client.post(reverse('teacher_settings'), {
            'action': 'add_custom_model',
            'new_model_name': 'gemini-custom-model-v1',
            'priority': '1',
            'make_active': '1'
        }, follow=True)
        self.assertEqual(post_resp.status_code, 200)

        settings.refresh_from_db()
        self.assertEqual(settings.model_name, 'gemini-custom-model-v1')
        models_list = settings.get_models_with_priority()
        self.assertEqual(models_list[0]['name'], 'gemini-custom-model-v1')
        self.assertEqual(models_list[0]['priority'], 1)

        # 2. Додавання другої моделі
        self.client.post(reverse('teacher_settings'), {
            'action': 'add_custom_model',
            'new_model_name': 'gemini-backup-model-v2',
            'priority': '2'
        })
        settings.refresh_from_db()
        models_list = settings.get_models_with_priority()
        model_names = [m['name'] for m in models_list]
        self.assertIn('gemini-backup-model-v2', model_names)

        # 3. Переміщення пріоритету (вгору / вниз)
        self.client.post(reverse('teacher_settings'), {
            'action': 'move_model_priority',
            'model_name': 'gemini-backup-model-v2',
            'direction': 'up'
        })
        settings.refresh_from_db()
        models_list = settings.get_models_with_priority()
        self.assertEqual(models_list[0]['name'], 'gemini-backup-model-v2')

        # 4. Видалення моделі зі списку
        del_resp = self.client.post(reverse('teacher_settings'), {
            'action': 'delete_model',
            'model_to_delete': 'gemini-custom-model-v1'
        }, follow=True)
        self.assertEqual(del_resp.status_code, 200)

        settings.refresh_from_db()
        saved_names = settings.get_saved_models()
        self.assertNotIn('gemini-custom-model-v1', saved_names)

    @patch('feed.gemini_service._http_post_json')
    def test_ai_fallback_execution_when_primary_model_fails(self, mock_http_post):
        """Тест автоматичного fallback на наступну пріоритетну модель при 429 Rate Limit першої."""
        from .models import AISettings
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.api_key = 'fake-api-key'
        settings.is_enabled = True
        # Налаштовуємо чергу: primary = model-fail-429, fallback = model-success-fallback
        settings.saved_models_list = json.dumps([
            {"name": "gemini-fail-model", "priority": 1, "enabled": True},
            {"name": "gemini-fallback-success", "priority": 2, "enabled": True}
        ])
        settings.model_name = "gemini-fail-model"
        settings.save()

        # Мокаємо запити: якщо url містить gemini-fail-model -> повертаємо 429, якщо gemini-fallback-success -> 200 OK
        def side_effect(url, payload_dict, timeout=30):
            if 'gemini-fail-model' in url:
                err_body = {'error': {'message': 'Resource has been exhausted (rate limit)', 'code': 429}}
                return (429, err_body, json.dumps(err_body))
            else:
                success_data = {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": '{"suggested_grade": "12", "level": "Високий (10-12)", "summary": "Відмінно", "strengths": ["Все супер"], "weaknesses": [], "feedback_comment": "Чудово!"}'
                                    }
                                ]
                            }
                        }
                    ]
                }
                return (200, success_data, json.dumps(success_data))

        mock_http_post.side_effect = side_effect

        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Олег',
            last_name='Петренко',
            class_group=self.class_group,
            comment_student="Розв'язання завдання"
        )

        res = evaluate_submission_with_gemini(sub)
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['suggested_grade'], '12')
        self.assertEqual(res['model_used'], 'gemini-fallback-success')
        self.assertTrue(res.get('fallback_activated'))

        sub.refresh_from_db()
        self.assertEqual(sub.ai_status, 'success')
        self.assertEqual(sub.ai_model_used, 'gemini-fallback-success')
        self.assertEqual(sub.ai_suggested_grade, '12')

    def test_superadmin_mode_toggle_and_navbar_color(self):
        """Тест перемикання режиму супер-адміністратора та зміни оформлення навбару."""
        self.user.is_superuser = True
        self.user.save()
        self.client.login(username='teacher1', password='password123')

        # 1. За замовчуванням користувач у безпечному режимі
        resp = self.client.get(reverse('teacher_settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'navbar-superadmin')
        self.assertContains(resp, 'Увійти як Адмін')

        # 2. Активація режиму супер-адміністратора
        enter_resp = self.client.get(reverse('enter_superadmin_mode'), follow=True)
        self.assertEqual(enter_resp.status_code, 200)
        self.assertContains(enter_resp, 'navbar-superadmin')
        self.assertContains(enter_resp, 'РЕЖИМ СУПЕР-АДМІНІСТРАТОРА АКТИВОВАНО')
        self.assertContains(enter_resp, 'Вийти з Адміна')

        # 3. Вихід з режиму супер-адміністратора
        exit_resp = self.client.get(reverse('exit_superadmin_mode'), follow=True)
        self.assertEqual(exit_resp.status_code, 200)
        self.assertNotContains(exit_resp, 'navbar-superadmin')

    def test_ai_criteria_presets_crud_and_defaults(self):
        """Тест створення, вибору дефолтного, редагування та видалення шаблонів критеріїв."""
        self.client.login(username='teacher1', password='password123')

        # 1. Перевірка базових системних шаблонів (НУШ та Традиційна)
        AICriteriaPreset.ensure_default_presets()
        self.assertTrue(AICriteriaPreset.objects.filter(evaluation_type='nus', is_system=True).exists())
        self.assertTrue(AICriteriaPreset.objects.filter(evaluation_type='traditional', is_system=True).exists())

        nus_p = AICriteriaPreset.objects.filter(evaluation_type='nus').first()
        trad_p = AICriteriaPreset.objects.filter(evaluation_type='traditional').first()
        self.assertTrue(nus_p.is_default)
        self.assertIn("НУШ", nus_p.get_full_prompt())
        self.assertIn("1-12", trad_p.get_full_prompt())

        # 2. Створення власного шаблону через teacher_settings_view з TXT файлом критеріїв
        txt_doc = SimpleUploadedFile("mon_criteria_2026.txt", "Офіційні критерії МОН: за правильний алгоритм +5 балів, за оформлення +2 бали.".encode('utf-8'))
        resp = self.client.post(reverse('teacher_settings') + '?tab=ai', {
            'action': 'create_criteria_preset',
            'preset_name': 'Критерії МОН з інформатики 2026',
            'evaluation_type': 'custom',
            'description': 'Офіційні методичні рекомендації МОН',
            'system_prompt': 'Оцінюй за рекомендаціями МОН.',
            'document_file': txt_doc,
            'is_default': '1',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        custom_p = AICriteriaPreset.objects.filter(name='Критерії МОН з інформатики 2026').first()
        self.assertIsNotNone(custom_p)
        self.assertTrue(custom_p.is_default)
        self.assertIn("Офіційні критерії МОН", custom_p.extracted_criteria_text)
        self.assertIn("Офіційні критерії МОН", custom_p.get_full_prompt())

        # 3. Встановлення класичного шаблону дефолтним
        resp2 = self.client.post(reverse('teacher_settings') + '?tab=ai', {
            'action': 'set_default_criteria_preset',
            'preset_id': trad_p.id,
        }, follow=True)
        self.assertEqual(resp2.status_code, 200)
        trad_p.refresh_from_db()
        custom_p.refresh_from_db()
        self.assertTrue(trad_p.is_default)
        self.assertFalse(custom_p.is_default)

        # 4. Видалення кастомного шаблону
        resp3 = self.client.post(reverse('teacher_settings') + '?tab=ai', {
            'action': 'delete_criteria_preset',
            'preset_id': custom_p.id,
        }, follow=True)
        self.assertEqual(resp3.status_code, 200)
        self.assertFalse(AICriteriaPreset.objects.filter(id=custom_p.id).exists())

    @patch('feed.gemini_service._http_post_json')
    def test_ai_evaluation_with_specific_criteria_preset(self, mock_http):
        """Тест оцінювання роботи з передачею обраного шаблону критеріїв."""
        from .gemini_service import evaluate_submission_with_gemini

        ai_set = AISettings.get_solo()
        ai_set.api_key = "test-gemini-key"
        ai_set.is_enabled = True
        ai_set.save()

        # Створюємо завдання та здачу
        subj, _ = Subject.objects.get_or_create(name='Інформатика', defaults={'icon': '💻', 'color': '#3b82f6'})
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=subj,
            title='Практична робота з Python',
            description='Створіть калькулятор'
        )
        sub = Submission.objects.create(
            assignment=assignment,
            class_group=self.class_group,
            first_name='Анна',
            last_name='Коваль',
            comment_student='print(2 + 2)'
        )

        trad_preset = AICriteriaPreset.objects.filter(evaluation_type='traditional').first()
        if not trad_preset:
            AICriteriaPreset.ensure_default_presets()
            trad_preset = AICriteriaPreset.objects.filter(evaluation_type='traditional').first()

        mock_http.return_value = (200, {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "suggested_grade": "11",
                            "level": "Високий (10-12)",
                            "format_warning": None,
                            "summary": "Відмінна робота за класичною шкалою МОН.",
                            "strengths": ["Точний синтаксис", "Правильний вивід"],
                            "weaknesses": [],
                            "feedback_comment": "Роботу виконано на 11 балів за традиційною системою."
                        })
                    }]
                }
            }]
        }, "ok")

        res = evaluate_submission_with_gemini(sub, preset_id=trad_preset.id)
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['suggested_grade'], '11')
        sub.refresh_from_db()
        self.assertEqual(sub.ai_suggested_grade, '11')

    def test_mon_informatics_criteria_and_deletion_restoration(self):
        """Тест критеріїв НУШ Інформатика (ГР 1-4), видалення та відновлення шаблонів."""
        self.client.login(username='teacher1', password='password123')

        AICriteriaPreset.ensure_default_presets(force_recreate=True)
        info_p = AICriteriaPreset.objects.filter(name__icontains="Інформатична освітня галузь").first()
        self.assertIsNotNone(info_p)
        self.assertEqual(len(info_p.get_gr_list()), 4)
        self.assertIn("Працює з інформацією", info_p.get_gr_list()[0]['name'])
        self.assertIn("Створює інформаційні продукти", info_p.get_gr_list()[1]['name'])
        self.assertIn("Працює в цифровому середовищі", info_p.get_gr_list()[2]['name'])
        self.assertIn("Безпечно та відповідально", info_p.get_gr_list()[3]['name'])
        self.assertIn("МЕТОДИЧНИЙ ПОСІБНИК", info_p.get_full_prompt())

        # Тест видалення шаблону
        info_id = info_p.id
        resp_del = self.client.post(reverse('teacher_settings') + '?tab=ai', {
            'action': 'delete_criteria_preset',
            'preset_id': info_id
        }, follow=True)
        self.assertEqual(resp_del.status_code, 200)
        self.assertFalse(AICriteriaPreset.objects.filter(id=info_id).exists())

        # Тест відновлення стандартних шаблонів
        resp_restore = self.client.post(reverse('teacher_settings') + '?tab=ai', {
            'action': 'restore_default_presets'
        }, follow=True)
        self.assertEqual(resp_restore.status_code, 200)
        self.assertTrue(AICriteriaPreset.objects.filter(name__icontains="Інформатична освітня галузь").exists())

    @patch('feed.gemini_service._http_post_json')
    def test_selected_gr_evaluation_and_average_calculation(self, mock_post):
        """Тест вибору конкретних ГР прапорцями, розрахунку середнього балу та очищення коментарів учневі."""
        ai_set = AISettings.get_solo()
        ai_set.api_key = 'test-gemini-key'
        ai_set.save()

        self.client.login(username='teacher1', password='password123')
        sub = Submission.objects.create(
            assignment=self.assignment,
            class_group=self.class_group,
            first_name='Михайло',
            last_name='Коцюбинський',
            link='https://github.com/student/informatics-project'
        )

        mock_post.return_value = (200, {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "suggested_grade": "10",
                            "level": "Високий",
                            "format_warning": None,
                            "summary": "Робота виконана якісно та структуровано.",
                            "strengths": ["Чіткий алгоритм", "Враховано принципи безпеки"],
                            "weaknesses": ["Дрібні зауваження до коментарів"],
                            "feedback_comment": "Чудова робота, продовжуй у тому ж дусі!",
                            "gr_results": [
                                {"code": "ГР 1", "name": "Працює з інформацією", "grade": "10", "level": "Високий", "comment": "Відмінний аналіз"},
                                {"code": "ГР 2", "name": "Створює інформаційні продукти", "grade": "6", "level": "Середній", "comment": "Не обрано"},
                                {"code": "ГР 3", "name": "Працює в цифровому середовищі", "grade": "8", "level": "Достатній", "comment": "Добре володіння інструментами"}
                            ]
                        })
                    }]
                }
            }]
        }, "ok")

        # Вчитель обрав лише ГР 1 та ГР 3
        res = evaluate_submission_with_gemini(sub, selected_gr_codes=['ГР 1', 'ГР 3'])
        self.assertEqual(res['status'], 'success')
        self.assertEqual(len(res['gr_results']), 2)
        self.assertEqual(res['gr_results'][0]['code'], 'ГР 1')
        self.assertEqual(res['gr_results'][1]['code'], 'ГР 3')

        # Середній бал (10 + 8) / 2 = 9
        self.assertEqual(res['gr_avg'], 9)
        self.assertEqual(res['suggested_grade'], '9')

        sub.refresh_from_db()
        self.assertEqual(sub.get_ai_gr_average(), 9)
        self.assertEqual(sub.get_ai_gr_average_display(), '9')

        # Перевірка конфіденційності: get_clean_ai_feedback_for_student() не повинно містити детальних оцінок ГР
        clean_feedback = sub.get_clean_ai_feedback_for_student()
        self.assertNotIn("Оцінювання за групами результатів", clean_feedback)
        self.assertNotIn("→ 10 б.", clean_feedback)
        self.assertNotIn("→ 8 б.", clean_feedback)
        self.assertIn("Чудова робота, продовжуй у тому ж дусі!", clean_feedback)
        self.assertIn("Чіткий алгоритм", clean_feedback)

        # Перевірка застосування оцінки через ai_apply_suggested_grade
        resp_apply = self.client.post(reverse('ai_apply_suggested_grade', kwargs={'submission_id': sub.id}), {
            'format': 'json'
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp_apply.status_code, 200)

        sub.refresh_from_db()
        self.assertEqual(sub.grade, '9')
        # Коментар учневі містить лише очищені педагогічні рекомендації
        self.assertEqual(sub.comments.count(), 1)
        comment_text = sub.comments.first().text
        self.assertNotIn("Оцінювання за групами результатів", comment_text)
        self.assertNotIn("→ 10 б.", comment_text)
        self.assertIn("Чіткий алгоритм", comment_text)

        # Перевірка заокруглення дробового середнього балу на користь учня (наприклад 8.5 -> 9, 7.2 -> 8)
        sub.ai_gr_results = json.dumps([
            {"code": "ГР 1", "name": "Тест 1", "grade": "8"},
            {"code": "ГР 2", "name": "Тест 2", "grade": "9"}
        ])
        sub.save()
        # Середнє арифметичне 8.5 -> заокруглюється до 9
        self.assertEqual(sub.get_ai_gr_average(), 9)
        self.assertEqual(sub.get_ai_gr_average_display(), '9')
        self.assertIsInstance(sub.get_ai_gr_average(), int)

    @patch('feed.gemini_service._http_post_json')
    def test_api_ai_process_item_with_selected_gr(self, mock_post):
        """Тест пакетного ендпоінту api_ai_process_item із вибором конкретних ГР."""
        ai_set = AISettings.get_solo()
        ai_set.api_key = 'test-gemini-key'
        ai_set.save()

        self.client.login(username='teacher1', password='password123')
        sub = Submission.objects.create(
            assignment=self.assignment,
            class_group=self.class_group,
            first_name='Леся',
            last_name='Українка',
            link='https://github.com/student/batch-project'
        )

        mock_post.return_value = (200, {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "suggested_grade": "11",
                            "level": "Високий",
                            "format_warning": None,
                            "summary": "Відмінно",
                            "strengths": ["Гарна робота"],
                            "weaknesses": [],
                            "feedback_comment": "Супер!",
                            "gr_results": [
                                {"code": "ГР 1", "name": "Інформація", "grade": "11", "level": "Високий", "comment": "Добре"},
                                {"code": "ГР 2", "name": "Продукти", "grade": "12", "level": "Високий", "comment": "Відмінно"}
                            ]
                        })
                    }]
                }
            }]
        }, "ok")

        resp = self.client.post(reverse('api_ai_process_item', kwargs={'submission_id': sub.id}), {
            'selected_gr_codes': json.dumps(['ГР 2'])
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(len(data['gr_results']), 1)
        self.assertEqual(data['gr_results'][0]['code'], 'ГР 2')
        self.assertEqual(data['suggested_grade'], '12')

    def test_gradebook_multiple_evaluation_dates(self):
        """Тест відображення оцінок за різними датами оцінювання в журналі."""
        from datetime import datetime
        from django.utils import timezone
        self.client.login(username='teacher1', password='password123')

        dt1 = timezone.make_aware(datetime(2026, 9, 1, 10, 0, 0))
        dt2 = timezone.make_aware(datetime(2026, 9, 5, 12, 0, 0))

        # Дві роботи, здані одного дня, але оцінені в різні дати
        sub1 = Submission.objects.create(
            assignment=self.assignment,
            first_name='Петро',
            last_name='Коваленко',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='10',
            graded_at=dt1
        )
        sub2 = Submission.objects.create(
            assignment=self.assignment,
            first_name='Петро',
            last_name='Коваленко',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='11',
            graded_at=dt2
        )

        resp = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&view_mode=journal')
        self.assertEqual(resp.status_code, 200)
        # Журнал повинен мати дві різні дати оцінювання (01.09 та 05.09)
        self.assertContains(resp, '01.09')
        self.assertContains(resp, '05.09')
        self.assertContains(resp, '10')
        self.assertContains(resp, '11')

        # Перевірка сторінки всіх оцінок
        resp_all = self.client.get(reverse('gradebook') + '?view=all_grades')
        self.assertEqual(resp_all.status_code, 200)
        self.assertContains(resp_all, '01.09.2026')
        self.assertContains(resp_all, '05.09.2026')

    def test_gradebook_filters_and_rework_badge(self):
        """Тест фільтрації за датою, діапазоном оцінок та відображення значка 'Д' (Доопрацювати)."""
        from datetime import datetime
        from django.utils import timezone
        self.client.login(username='teacher1', password='password123')

        dt1 = timezone.make_aware(datetime(2026, 9, 10, 10, 0, 0))
        dt2 = timezone.make_aware(datetime(2026, 9, 20, 12, 0, 0))

        sub_high = Submission.objects.create(
            assignment=self.assignment,
            first_name='Оксана',
            last_name='Шевченко',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='11',
            graded_at=dt1
        )
        sub_rework = Submission.objects.create(
            assignment=self.assignment,
            first_name='Іван',
            last_name='Бондар',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='Доопрацювати',
            graded_at=dt2
        )

        # 1. Перевірка відображення значка 'Д' з описом "Доопрацювати"
        resp_j = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&view_mode=journal')
        self.assertEqual(resp_j.status_code, 200)
        self.assertContains(resp_j, 'rework')
        self.assertContains(resp_j, 'Д')
        self.assertContains(resp_j, 'Доопрацювати')

        # 2. Фільтр за конкретним днем (single_date=2026-09-10)
        resp_day = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&single_date=2026-09-10')
        self.assertEqual(resp_day.status_code, 200)
        self.assertContains(resp_day, '10.09')
        self.assertNotContains(resp_day, '20.09')

        # 3. Фільтр за діапазоном оцінок (grade_filter=rework)
        resp_rework = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&grade_filter=rework')
        self.assertEqual(resp_rework.status_code, 200)
        self.assertContains(resp_rework, 'Бондар')
        self.assertNotContains(resp_rework, 'Шевченко')

        # 4. Фільтр за високим балом (grade_filter=10-12)
        resp_high = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&grade_filter=10-12')
        self.assertEqual(resp_high.status_code, 200)
        self.assertContains(resp_high, 'Шевченко')
        self.assertNotContains(resp_high, 'Бондар')

    def test_teacher_dashboard_pagination_and_navbar_buttons(self):
        """Тест кнопки 'Нове завдання' у верхній панелі, кнопки 'Як бачить учень' та пагінації 20 завдань."""
        self.client.login(username='teacher1', password='password123')

        # 1. Перевірка наявності кнопки 'Нове завдання' у шапці
        resp_base = self.client.get(reverse('teacher_dashboard'))
        self.assertEqual(resp_base.status_code, 200)
        self.assertContains(resp_base, 'Нове завдання')
        self.assertContains(resp_base, 'Як бачить учень')

        # 2. Створюємо 25 завдань для перевірки пагінації по 20 штук
        for i in range(25):
            Assignment.objects.create(
                teacher=self.teacher,
                title=f"Тестове завдання №{i+1}",
                status=Assignment.STATUS_PUBLISHED
            )

        resp_page1 = self.client.get(reverse('teacher_dashboard') + '?tab=published&page=1')
        self.assertEqual(resp_page1.status_code, 200)
        # На першій сторінці має бути 20 завдань
        self.assertEqual(len(resp_page1.context['assignments']), 20)
        self.assertTrue(resp_page1.context['page_obj'].has_next())

        resp_page2 = self.client.get(reverse('teacher_dashboard') + '?tab=published&page=2')
        self.assertEqual(resp_page2.status_code, 200)
        # На другій сторінці мають бути решта (6 завдань, разом із початковим)
        self.assertEqual(len(resp_page2.context['assignments']), 6)

    def test_individual_assignment_autofill_and_description_label(self):
        """Тест автозаповнення ПІБ та класу для індивідуального завдання, а також заголовка опису."""
        # 1. Створюємо індивідуальне завдання для учениці
        indiv_assignment = Assignment.objects.create(
            teacher=self.teacher,
            title="Індивідуальне завдання з алгебри",
            description="Розв'язати вправи 14-16 на сторінці 45.",
            is_individual=True,
            student_name="Мельник Софія",
            status=Assignment.STATUS_PUBLISHED
        )
        indiv_assignment.classes.add(self.class_group)

        # 2. Перевіряємо заголовок 'Опис завдання' на сторінці деталей
        resp_detail = self.client.get(reverse('assignment_detail', kwargs={'pk': indiv_assignment.pk}))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, 'Опис завдання')
        self.assertNotContains(resp_detail, 'Детальний опис та інструкція для учнів')

        # 3. Перевіряємо автозаповнення форми здачі роботи
        resp_submit = self.client.get(reverse('submit_assignment', kwargs={'pk': indiv_assignment.pk}))
        self.assertEqual(resp_submit.status_code, 200)
        form = resp_submit.context['form']
        self.assertEqual(form['full_name'].value(), 'Мельник Софія')
        self.assertEqual(form['class_group'].value(), self.class_group.pk)

    def test_code_syntax_highlighting_and_teacher_attachment_preview(self):
        """Тест підсвічування синтаксису коду для учня та API перегляду прикріплених файлів вчителем."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        py_file = SimpleUploadedFile(
            "solution.py",
            b"def calculate(a, b):\n    return a + b\n\nprint(calculate(5, 10))\n",
            content_type="text/x-python"
        )
        af = AssignmentFile.objects.create(
            assignment=self.assignment,
            file=py_file,
            original_name="solution.py"
        )

        # 1. Перевіряємо сторінку assignment_detail (для учня)
        resp_detail = self.client.get(reverse('assignment_detail', kwargs={'pk': self.assignment.pk}))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, 'prism-tomorrow.min.css')
        self.assertContains(resp_detail, 'language-python')
        self.assertContains(resp_detail, 'calculate')

        # 2. Перевіряємо ендпоінт file_preview для модального перегляду вчителем
        self.client.login(username='teacher1', password='password123')
        resp_preview = self.client.get(reverse('file_preview', kwargs={'file_id': af.id}))
        self.assertEqual(resp_preview.status_code, 200)
        data = resp_preview.json()
        self.assertEqual(data.get('type'), 'code')
        self.assertEqual(data.get('lang'), 'python')
        self.assertIn('def calculate', data.get('content', ''))

    def test_student_profile_management_and_editing(self):
        """Тест створення, редагування імені, зміни класу та синхронізації здач учня."""
        self.client.login(username='teacher1', password='password123')

        # 1. Створюємо учня
        student = Student.objects.create(
            last_name='Шевченко',
            first_name='Тарас',
            class_group=self.class_group
        )
        self.assertEqual(student.get_full_name(), 'Шевченко Тарас')
        self.assertEqual(student.get_initials(), 'ШТ')

        # 2. Створюємо здачу роботи цим учнем
        sub = Submission.objects.create(
            assignment=self.assignment,
            last_name='Шевченко',
            first_name='Тарас',
            class_group=self.class_group,
            grade='11'
        )
        self.assertEqual(student.get_submissions_count(), 1)

        # 3. Вчитель виправляє помилку в імені учня: змінює "Шевченко" на "Шевченка" -> "Шевченко Тарас Григорович"
        class_10b = ClassGroup.objects.create(grade=10, letter='Б', name='10-Б')
        resp_edit = self.client.post(reverse('teacher_student_edit', kwargs={'student_id': student.id}), {
            'last_name': 'Шевченко-Бондар',
            'first_name': 'Тарас',
            'class_group': class_10b.id,
            'sync_submissions': True,
            'notes': 'Переведений до 10-Б'
        })
        self.assertEqual(resp_edit.status_code, 302)

        student.refresh_from_db()
        self.assertEqual(student.last_name, 'Шевченко-Бондар')
        self.assertEqual(student.class_group, class_10b)
        self.assertEqual(student.notes, 'Переведений до 10-Б')

        # Перевіряємо що здача автоматично перенеслась у 10-Б та оновила ПІБ
        sub.refresh_from_db()
        self.assertEqual(sub.last_name, 'Шевченко-Бондар')
        self.assertEqual(sub.class_group, class_10b)
        self.assertEqual(sub.student, student)

    def test_student_roster_import_from_text_and_csv(self):
        """Тест імпорту списку учнів із тексту та CSV файлу з авто-створенням класів."""
        self.client.login(username='teacher1', password='password123')

        raw_roster = """
        Іваненко Тарас, 9-А
        Петренко Олена, 9-А
        Коваленко Дмитро, 11-А
        9-Б: Сидоренко Катерина
        Грищенко Максим (8-В)
        """
        resp_import = self.client.post(reverse('teacher_students_import'), {
            'import_mode': 'text',
            'raw_text': raw_roster,
            'create_missing_classes': True
        })
        self.assertEqual(resp_import.status_code, 302)

        # Перевіряємо створених учнів
        self.assertTrue(Student.objects.filter(last_name='Іваненко', first_name='Тарас', class_group__name='9-А').exists())
        self.assertTrue(Student.objects.filter(last_name='Петренко', first_name='Олена', class_group__name='9-А').exists())
        self.assertTrue(Student.objects.filter(last_name='Коваленко', first_name='Дмитро', class_group__name='11-А').exists())
        self.assertTrue(Student.objects.filter(last_name='Сидоренко', first_name='Катерина', class_group__name='9-Б').exists())
        self.assertTrue(Student.objects.filter(last_name='Грищенко', first_name='Максим', class_group__name='8-В').exists())

        # Перевіряємо що відсутні класи автоматично створені
        self.assertTrue(ClassGroup.objects.filter(name='11-А').exists())
        self.assertTrue(ClassGroup.objects.filter(name='9-Б').exists())
        self.assertTrue(ClassGroup.objects.filter(name='8-В').exists())

        # Тест дедуплікації: повторний імпорт не створює дублікатів
        count_before = Student.objects.count()
        self.client.post(reverse('teacher_students_import'), {
            'import_mode': 'text',
            'raw_text': "Іваненко Тарас, 9-А\nПетренко Олена, 9-А",
            'create_missing_classes': True
        })
        count_after = Student.objects.count()
        self.assertEqual(count_before, count_after)

    def test_student_export_and_autocomplete_api(self):
        """Тест експорту списку учнів у CSV та API автодоповнення учнів."""
        self.client.login(username='teacher1', password='password123')

        Student.objects.create(last_name='Задорожний', first_name='Олег', class_group=self.class_group)

        # 1. Тест експорту CSV
        resp_export = self.client.get(reverse('teacher_students_export') + f'?class_group={self.class_group.id}')
        self.assertEqual(resp_export.status_code, 200)
        self.assertEqual(resp_export['Content-Type'], 'text/csv; charset=utf-8-sig')
        content = resp_export.content.decode('utf-8-sig')
        self.assertIn('Задорожний', content)
        self.assertIn('Олег', content)
        self.assertIn('9-А', content)

        # 2. Тест API автодоповнення
        resp_auto = self.client.get(reverse('api_students_autocomplete') + f'?class_group_id={self.class_group.id}&query=Задорож')
        self.assertEqual(resp_auto.status_code, 200)
        data = resp_auto.json()
        self.assertTrue(any(s['last_name'] == 'Задорожний' for s in data['students']))

    def test_teacher_students_views_and_permissions(self):
        """Тест доступу до панелі учнів, фільтрації та видалення."""
        # 1. Анонімний користувач перенаправляється на логін
        self.client.logout()
        resp_anon = self.client.get(reverse('teacher_students'))
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn(reverse('teacher_login'), resp_anon.url)

        # 2. Вчитель має повний доступ
        self.client.login(username='teacher1', password='password123')
        st = Student.objects.create(last_name='Тестовий', first_name='Учень', class_group=self.class_group)

        resp_list = self.client.get(reverse('teacher_students'))
        self.assertEqual(resp_list.status_code, 200)
        self.assertContains(resp_list, 'Тестовий')
        self.assertContains(resp_list, 'Керування учнями та класами')

        # 3. Видалення учня
        resp_del = self.client.post(reverse('teacher_student_delete', kwargs={'student_id': st.id}))
        self.assertEqual(resp_del.status_code, 302)
        self.assertFalse(Student.objects.filter(id=st.id).exists())

    def test_student_portal_search_isolation_for_same_last_names(self):
        """
        Тест: коли учень шукає 'Коваленко Тарас', показуються ТІЛЬКИ його роботи,
        а не всі однофамільці ('Коваленко Олена', 'Коваленко Дмитро').
        """
        sub_taras = Submission.objects.create(
            assignment=self.assignment,
            last_name='Коваленко',
            first_name='Тарас',
            class_group=self.class_group
        )
        sub_olena = Submission.objects.create(
            assignment=self.assignment,
            last_name='Коваленко',
            first_name='Олена',
            class_group=self.class_group
        )
        sub_dmytro = Submission.objects.create(
            assignment=self.assignment,
            last_name='Коваленко',
            first_name='Дмитро',
            class_group=self.class_group
        )
        sub_shevchenko = Submission.objects.create(
            assignment=self.assignment,
            last_name='Шевченко',
            first_name='Тарас',
            class_group=self.class_group
        )

        # 1. Пошук у функції fuzzy_search_submissions: 'Коваленко Тарас'
        qs = Submission.objects.all()
        res = fuzzy_search_submissions(qs, 'Коваленко Тарас')
        self.assertEqual(res.count(), 1)
        self.assertEqual(res.first().id, sub_taras.id)

        # 2. Зворотний порядок слів: 'Тарас Коваленко'
        res_rev = fuzzy_search_submissions(qs, 'Тарас Коваленко')
        self.assertEqual(res_rev.count(), 1)
        self.assertEqual(res_rev.first().id, sub_taras.id)

        # 3. Англійська розкладка: 'Rjdfktyrj Nfhfc' (Коваленко Тарас)
        res_en = fuzzy_search_submissions(qs, 'Rjdfktyrj Nfhfc')
        self.assertEqual(res_en.count(), 1)
        self.assertEqual(res_en.first().id, sub_taras.id)

        # 4. Пошук одного прізвища 'Коваленко' знаходить усіх 3 однофамільців
        res_all_kov = fuzzy_search_submissions(qs, 'Коваленко')
        self.assertEqual(res_all_kov.count(), 3)
        self.assertNotIn(sub_shevchenko.id, set(res_all_kov.values_list('id', flat=True)))

        # 5. Тест через HTTP ендпоінт student_submissions_portal
        resp_portal = self.client.get(reverse('student_submissions_portal') + '?search=Коваленко+Тарас')
        self.assertEqual(resp_portal.status_code, 200)
        self.assertContains(resp_portal, 'Коваленко Тарас')
        self.assertNotContains(resp_portal, 'Коваленко Олена')
        self.assertNotContains(resp_portal, 'Коваленко Дмитро')
        self.assertNotContains(resp_portal, 'Шевченко Тарас')

    def test_multi_file_submission_workflow(self):
        """Тест повного життєвого циклу здачі роботи з кількома файлами одночасно."""
        import zipfile
        import io
        from .models import SubmissionFile
        from .gemini_service import extract_submission_content

        f1 = SimpleUploadedFile("page1.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4", content_type="image/png")
        f2 = SimpleUploadedFile("solution.py", b"def solve():\n    return 42\n", content_type="text/x-python")
        f3 = SimpleUploadedFile("notes.txt", b"Notes about algorithm complexity: O(1)", content_type="text/plain")

        post_data = {
            'full_name': 'Григоренко Максим',
            'class_group': self.class_group.id,
            'comment_student': 'Здаю 3 файли: фото, код та нотатки',
            'files': [f1, f2, f3]
        }

        # 1. Відправка форми учнем
        resp = self.client.post(
            reverse('submit_assignment', args=[self.assignment.id]),
            data=post_data,
            follow=True
        )
        self.assertEqual(resp.status_code, 200)

        # 2. Перевірка збереження у БД
        sub = Submission.objects.filter(last_name='Григоренко', first_name='Максим').first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.files.count(), 3)
        self.assertEqual(sub.get_files_count(), 3)
        self.assertTrue(sub.has_multiple_files())

        file_names = [sf.original_name for sf in sub.files.all()]
        self.assertIn('page1.png', file_names)
        self.assertIn('solution.py', file_names)
        self.assertIn('notes.txt', file_names)

        # 3. Перевірка вчительського File Viewer
        self.client.login(username='teacher1', password='password123')
        fv_resp = self.client.get(reverse('view_file', args=[sub.id]))
        self.assertEqual(fv_resp.status_code, 200)
        self.assertIn('submission_files', fv_resp.context)
        self.assertEqual(len(fv_resp.context['submission_files']), 3)

        # Перевірка перемикання на файл solution.py
        py_file = sub.files.filter(original_name='solution.py').first()
        fv_py_resp = self.client.get(reverse('view_file', args=[sub.id]) + f'?file_id={py_file.id}')
        self.assertEqual(fv_py_resp.status_code, 200)
        self.assertEqual(fv_py_resp.context['file_type'], 'code')
        self.assertIn('def solve():', fv_py_resp.context['content'])

        # 4. Перевірка завантаження ZIP окремої здачі учня
        zip_resp = self.client.get(reverse('download_submission_files_zip', args=[sub.id]))
        self.assertEqual(zip_resp.status_code, 200)
        self.assertEqual(zip_resp['Content-Type'], 'application/zip')

        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            zip_names = zf.namelist()
            self.assertIn('page1.png', zip_names)
            self.assertIn('solution.py', zip_names)
            self.assertIn('notes.txt', zip_names)

        # 5. Перевірка масового експорту завдань у ZIP
        all_zip_resp = self.client.get(reverse('download_assignment_submissions_zip', args=[self.assignment.id]))
        self.assertEqual(all_zip_resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(all_zip_resp.content)) as zf:
            names = zf.namelist()
            self.assertTrue(any('Григоренко_Максим' in n for n in names))

        # 6. Перевірка шляху завантаження файлу на диск за українським форматом дат (ДД.ММ.РРРР)
        import re
        self.assertTrue(bool(re.search(r'submissions/\d{2}\.\d{2}\.\d{4}/', sub.file.name)))
        self.assertIn('Григоренко_Максим', sub.file.name)

        # 7. Перевірка відображення всіх окремих файлів на сторінці зданих робіт завдання
        asg_resp = self.client.get(reverse('assignment_submissions', args=[self.assignment.id]))
        self.assertEqual(asg_resp.status_code, 200)
        self.assertContains(asg_resp, 'page1.png')
        self.assertContains(asg_resp, 'solution.py')
        self.assertContains(asg_resp, 'notes.txt')

        # 8. Перевірка видобування вмісту для ШІ Google Gemini
        text_parts, inline_media, err = extract_submission_content(sub)
        self.assertIsNone(err)
        self.assertTrue(any('def solve():' in p for p in text_parts))
        self.assertTrue(any('Notes about algorithm complexity' in p for p in text_parts))
        self.assertEqual(len(inline_media), 1)  # 1 image file

    def test_resubmission_and_ai_single_attempt_rule(self):
        """
        Перевірка сценарію повторної здачі (робота над помилками):
        1. Перша здача фіксується як Спроба #1.
        2. Учень виконує одноразову перевірку ШІ на 1-й спробі.
        3. Учень надсилає нову версію роботи: автоматично фіксується як Спроба #2 (is_resubmission=True).
        4. На 2-й спробі самоперевірка ШІ блокується (1 спроба на 1 завдання).
        5. Переглядач вчителя та сторінка здач показують статус повторної здачі та історію спроб.
        """
        self.assignment.allow_student_ai_check = True
        self.assignment.save()

        # 1. Перша здача роботи
        f1 = SimpleUploadedFile("draft_solution.py", b"def calculate(): return 10\n", content_type="text/x-python")
        post_data1 = {
            'full_name': 'Шевченко Оксана',
            'class_group': self.class_group.id,
            'files': [f1],
            'comment_student': 'Перша спроба розв\'язку',
        }
        resp1 = self.client.post(reverse('submit_assignment', args=[self.assignment.id]), post_data1, follow=True)
        self.assertEqual(resp1.status_code, 200)

        sub1 = Submission.objects.get(last_name='Шевченко', first_name='Оксана', assignment=self.assignment)
        self.assertFalse(sub1.is_resubmission)
        self.assertEqual(sub1.resubmission_attempt, 1)
        self.assertTrue(sub1.is_latest_attempt)

        # 2. Учень перевіряє 1-шу спробу через ШІ
        sub1.student_ai_checked = True
        sub1.student_ai_grade = '7'
        sub1.student_ai_summary = 'Є помилка в обчисленнях'
        sub1.save()

        self.assertTrue(sub1.has_used_student_ai_check_for_assignment())

        # 3. Учень здає роботу повторно (робота над помилками)
        f2 = SimpleUploadedFile("final_solution.py", b"def calculate(): return 42\n", content_type="text/x-python")
        post_data2 = {
            'full_name': 'Шевченко Оксана',
            'class_group': self.class_group.id,
            'files': [f2],
            'comment_student': 'Виправила помилку згідно зауважень ШІ!',
        }
        resp2 = self.client.post(reverse('submit_assignment', args=[self.assignment.id]), post_data2, follow=True)
        self.assertEqual(resp2.status_code, 200)

        sub2 = Submission.objects.filter(last_name='Шевченко', first_name='Оксана', assignment=self.assignment).order_by('-submitted_at').first()
        self.assertTrue(sub2.is_resubmission)
        self.assertEqual(sub2.resubmission_attempt, 2)
        self.assertEqual(sub2.previous_submission_id, sub1.id)
        self.assertTrue(sub2.is_latest_attempt)

        # Перевіряємо, що sub1 тепер не остання
        sub1.refresh_from_db()
        self.assertFalse(sub1.is_latest_attempt)

        # 4. Перевірка блокування повторної самоперевірки ШІ на 2-й спробі
        self.assertTrue(sub2.has_used_student_ai_check_for_assignment())
        ai_resp = self.client.post(reverse('student_ai_self_check', args=[sub2.id]))
        self.assertEqual(ai_resp.status_code, 403)

        # 5. Перевірка відображення у File Viewer для вчителя
        self.client.login(username='teacher1', password='password123')
        fv_resp = self.client.get(reverse('view_file', args=[sub2.id]))
        self.assertEqual(fv_resp.status_code, 200)
        self.assertContains(fv_resp, 'Спроба #2')
        self.assertContains(fv_resp, 'Робота над помилками')
        self.assertContains(fv_resp, 'Попередня спроба #1')

        # Якщо вчитель відкриває стару спробу #1:
        fv_old_resp = self.client.get(reverse('view_file', args=[sub1.id]))
        self.assertEqual(fv_old_resp.status_code, 200)
        self.assertContains(fv_old_resp, 'Це попередня версія роботи')

        # 6. Перевірка сторінки зданих робіт вчителя (assignment_submissions):
        # Повинен показуватися 1 запис учня в згрупованому режимі з історією спроб
        asg_sub_resp = self.client.get(reverse('assignment_submissions', args=[self.assignment.id]))
        self.assertEqual(asg_sub_resp.status_code, 200)
        self.assertEqual(len(asg_sub_resp.context['page_obj']), 1)
        self.assertContains(asg_sub_resp, 'Спроба #2')
        self.assertContains(asg_sub_resp, 'Попередні спроби здачі')

        # 7. Перевірка сторінки прийнятих робіт учнів (submit_success):
        # Має бути рівно 1 рядок на учня без дублювання
        success_resp = self.client.get(reverse('submit_success', args=[self.assignment.id]))
        self.assertEqual(success_resp.status_code, 200)
        self.assertEqual(len(success_resp.context['page_obj']), 1)
        self.assertContains(success_resp, 'Спроба #2')

    def test_ai_detection_and_allow_ai_usage(self):
        """Тест створення завдання з дозволом на ШІ та виявлення ознак використання ШІ в роботі учня."""
        self.client.login(username='teacher1', password='password123')

        # 1. Створення завдання з увімкненим дозволом на використання ШІ
        create_resp = self.client.post(reverse('assignment_create'), {
            'subject': self.subject.id,
            'title': 'Твір з використанням ШІ',
            'description': 'Напишіть твір, дозволено застосовувати ШІ для пошуку ідей.',
            'classes': [self.class_group.id],
            'publish_choice': 'now',
            'allow_ai_usage': '1',
            'allow_student_ai_check': '1',
        }, follow=True)
        self.assertEqual(create_resp.status_code, 200)
        ai_asg = Assignment.objects.get(title='Твір з використанням ШІ')
        self.assertTrue(ai_asg.allow_ai_usage)
        self.assertTrue(ai_asg.allow_student_ai_check)

        # 2. Перевірка сторінки завдання учнем (відображення дозволу на ШІ)
        self.client.logout()
        asg_view_resp = self.client.get(reverse('assignment_detail', args=[ai_asg.id]))
        self.assertEqual(asg_view_resp.status_code, 200)
        self.assertContains(asg_view_resp, 'Дозволено вчителем')

        # 3. Здача роботи учнем
        sub = Submission.objects.create(
            assignment=ai_asg,
            class_group=self.class_group,
            last_name='Петренко',
            first_name='Олег',
            comment_student='Ось мій розв\'язок.',
            ai_generated_detected=True,
            ai_generated_confidence='high',
            ai_generated_details='Виявлено характерні шаблонні формулювання нейромереж'
        )
        self.assertTrue(sub.is_ai_allowed())

        # 4. Перевірка картки учня (submission_detail)
        sub_detail_resp = self.client.get(reverse('submission_detail', args=[sub.id]))
        self.assertEqual(sub_detail_resp.status_code, 200)
        self.assertContains(sub_detail_resp, 'У роботі зафіксовано використання генеративного ШІ')
        self.assertContains(sub_detail_resp, '(дозволено вчителем)')
        self.assertContains(sub_detail_resp, 'Виявлено характерні шаблонні формулювання нейромереж')

        # 5. Перевірка сторінки вчителя (assignment_submissions) та (view_file)
        self.client.login(username='teacher1', password='password123')
        asg_subs_resp = self.client.get(reverse('assignment_submissions', args=[ai_asg.id]))
        self.assertEqual(asg_subs_resp.status_code, 200)
        self.assertContains(asg_subs_resp, 'Ознаки ШІ')

        fv_resp = self.client.get(reverse('view_file', args=[sub.id]))
        self.assertEqual(fv_resp.status_code, 200)
        self.assertContains(fv_resp, 'Виявлено ознаки використання ШІ')
        self.assertContains(fv_resp, 'Дозволено вчителем')

    def test_traditional_grading_suppresses_gr_results(self):
        """Тест: якщо обрано традиційну систему оцінювання, групи результатів не враховуються і не показуються."""
        # 1. Отримуємо або створюємо традиційний пресет
        trad_preset = AICriteriaPreset.objects.filter(evaluation_type='traditional').first()
        if not trad_preset:
            trad_preset = AICriteriaPreset.objects.create(
                name='Традиційна 1-12 балів',
                evaluation_type='traditional',
                system_prompt=DEFAULT_TRADITIONAL_SYSTEM_PROMPT
            )
        self.assertEqual(trad_preset.get_gr_list(), [])

        # 2. Створюємо завдання з традиційною системою
        trad_asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Контрольна робота (Класична система)',
            description='Класична перевірка без ГР',
            default_ai_preset=trad_preset,
            allow_student_ai_check=True
        )
        trad_asg.classes.add(self.class_group)

        # 3. Створюємо здану роботу
        sub = Submission.objects.create(
            assignment=trad_asg,
            class_group=self.class_group,
            last_name='Сидоренко',
            first_name='Максим',
            comment_student='Ось мій розв\'язок.',
            ai_suggested_grade='10',
            ai_score_level='Високий',
            ai_feedback='📌 **Висновок:** Робота виконана добре.\n\n💬 **Рекомендація учню:** Відмінний результат.'
        )

        self.assertTrue(sub.is_traditional_grading())
        self.assertFalse(sub.has_gr_results())

        # 4. Перевірка сторінки перегляду для учня (submission_detail)
        resp_student = self.client.get(reverse('submission_detail', args=[sub.id]))
        self.assertEqual(resp_student.status_code, 200)
        self.assertNotContains(resp_student, 'Оцінювання за групами результатів')
        self.assertNotContains(resp_student, 'ГР 1')

        # 5. Перевірка сторінки перегляду для вчителя (view_file)
        self.client.login(username='teacher1', password='password123')
        resp_teacher = self.client.get(reverse('view_file', args=[sub.id]))
        self.assertEqual(resp_teacher.status_code, 200)
        # Переконуємось, що блок вибору ГР прихований для традиційної системи
        self.assertContains(resp_teacher, 'id="viewer-gr-selector" style="display:none;')

    def test_submission_files_not_duplicated_on_disk(self):
        """Перевірка, що при здачі роботи файли не дублюються на диску."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import SubmissionFile

        file1 = SimpleUploadedFile("task_solution_1.txt", b"print('Hello solution 1')", content_type="text/plain")
        file2 = SimpleUploadedFile("task_solution_2.txt", b"print('Hello solution 2')", content_type="text/plain")

        response = self.client.post(
            reverse('submit_assignment', args=[self.assignment.pk]),
            {
                'full_name': 'Петренко Дмитро',
                'class_group': self.class_group.id,
                'files': [file1, file2],
            }
        )
        self.assertEqual(response.status_code, 302)

        sub = Submission.objects.filter(last_name='Петренко', first_name='Дмитро').first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.files.count(), 2)

        # Переконуємось, що primary sub.file вказує на перший файл без створення додаткового дубліката
        first_sub_file = sub.files.first()
        self.assertEqual(sub.file.name, first_sub_file.file.name)

        # Переконуємось, що назви файлів оригінальні і не містять суфікса _1 (не дублюються)
        file_names = [f.original_name for f in sub.files.all()]
        self.assertIn("task_solution_1.txt", file_names)
        self.assertIn("task_solution_2.txt", file_names)

    def test_duplicate_submission_renders_clickable_link(self):
        """Перевірка, що виявлені схожі роботи містять активне посилання на роботу першого учня."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        # 1. Перший учень здає файл
        file_content = b"# Unique Python solution code\ndef solve():\n    return 42\n"
        f1 = SimpleUploadedFile("unique_homework.py", file_content, content_type="text/x-python")
        self.client.post(
            reverse('submit_assignment', args=[self.assignment.pk]),
            {
                'full_name': 'Бондаренко Анастасія',
                'class_group': self.class_group.id,
                'files': [f1],
            }
        )
        sub1 = Submission.objects.get(last_name='Бондаренко', first_name='Анастасія')

        # 2. Другий учень здає такий самий файл
        f2 = SimpleUploadedFile("unique_homework.py", file_content, content_type="text/x-python")
        self.client.post(
            reverse('submit_assignment', args=[self.assignment.pk]),
            {
                'full_name': 'Ковальчук Артем',
                'class_group': self.class_group.id,
                'files': [f2],
            }
        )
        sub2 = Submission.objects.get(last_name='Ковальчук', first_name='Артем')

        # 3. Вчитель переглядає роботу другого учня
        self.client.login(username='teacher1', password='password123')
        resp = self.client.get(reverse('view_file', args=[sub2.id]))
        self.assertEqual(resp.status_code, 200)

        # 4. Перевіряємо наявність активного клікабельного посилання на роботу sub1
        expected_url = reverse('view_file', args=[sub1.id])
        self.assertContains(resp, f'href="{expected_url}"')
        self.assertContains(resp, 'Бондаренко Анастасія')

    def test_assignment_form_preset_select_attributes(self):
        """Перевірка коректного класу та стилізації випадаючого списку шаблону критеріїв."""
        self.client.login(username='teacher1', password='password123')
        resp = self.client.get(reverse('assignment_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="id_default_ai_preset" name="default_ai_preset" class="form-select"')

    def test_scratch_sb3_parser_and_view(self):
        """Перевірка розбору Scratch 3 (.sb3) проєктів, вкладених циклів/умов та відображення в інтерфейсі вчителя."""
        import zipfile
        import io
        import json
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .scratch_utils import parse_scratch_sb3

        # Створюємо валідний .sb3 ZIP-архів зі складним скриптом (цикли, розгалуження, оператори)
        project_json = {
            "targets": [
                {
                    "isStage": True,
                    "name": "Сцена",
                    "variables": {"v1": ["score", 10]},
                    "lists": {},
                    "broadcasts": {"b_start": "start_game"},
                    "blocks": {},
                    "costumes": [{"name": "backdrop1"}],
                    "sounds": []
                },
                {
                    "isStage": False,
                    "name": "Котик",
                    "variables": {},
                    "lists": {},
                    "broadcasts": {},
                    "costumes": [{"name": "costume1"}, {"name": "costume2"}],
                    "sounds": [{"name": "meow"}],
                    "blocks": {
                        "b1": {
                            "opcode": "event_whenflagclicked",
                            "topLevel": True,
                            "next": "b_loop",
                            "inputs": {},
                            "fields": {}
                        },
                        "b_loop": {
                            "opcode": "control_forever",
                            "topLevel": False,
                            "next": None,
                            "inputs": {"SUBSTACK": [2, "b_move"]},
                            "fields": {}
                        },
                        "b_move": {
                            "opcode": "motion_movesteps",
                            "topLevel": False,
                            "next": "b_if",
                            "inputs": {"STEPS": [1, [4, "15"]]},
                            "fields": {}
                        },
                        "b_if": {
                            "opcode": "control_if",
                            "topLevel": False,
                            "next": None,
                            "inputs": {
                                "CONDITION": [2, "b_touching"],
                                "SUBSTACK": [2, "b_say"]
                            },
                            "fields": {}
                        },
                        "b_touching": {
                            "opcode": "sensing_touchingobject",
                            "topLevel": False,
                            "next": None,
                            "inputs": {},
                            "fields": {"TOUCHINGOBJECTMENU": ["_edge_", None]}
                        },
                        "b_say": {
                            "opcode": "looks_sayforsecs",
                            "topLevel": False,
                            "next": None,
                            "inputs": {
                                "MESSAGE": [1, [10, "Ой, межа!"]],
                                "SECS": [1, [4, "2"]]
                            },
                            "fields": {}
                        }
                    }
                }
            ],
            "meta": {"semver": "3.0.0"}
        }

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('project.json', json.dumps(project_json))
        zip_bytes = zip_buffer.getvalue()

        # Тест парсера
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.sb3', delete=False) as tmp_sb3:
            tmp_sb3.write(zip_bytes)
            tmp_sb3_path = tmp_sb3.name

        try:
            html_out, text_summary, err = parse_scratch_sb3(tmp_sb3_path, raw_file_url="http://testserver/file.sb3")
            self.assertIsNone(err)
            self.assertIn("Котик", text_summary)
            self.assertIn("Коли натиснуто 🟢", text_summary)
            self.assertIn("Завжди:", text_summary)
            self.assertIn("Ой, межа!", text_summary)
            self.assertIn("scratch-project-viewer", html_out)
            self.assertIn("scratch-live-player-container", html_out)
        finally:
            if os.path.exists(tmp_sb3_path):
                os.remove(tmp_sb3_path)

        # Тест здачі учнем та перегляду вчителем
        f_sb3 = SimpleUploadedFile("game_project.sb3", zip_bytes, content_type="application/x.scratch.sb3")
        self.client.post(
            reverse('submit_assignment', args=[self.assignment.pk]),
            {
                'full_name': 'Григоренко Денис',
                'class_group': self.class_group.id,
                'files': [f_sb3],
            }
        )
        sub = Submission.objects.get(last_name='Григоренко', first_name='Денис')
        self.assertEqual(sub.get_file_type(), 'scratch')
        self.assertEqual(sub.get_file_icon(), '🐱')

        self.client.login(username='teacher1', password='password123')
        resp = self.client.get(reverse('view_file', args=[sub.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '🐱 Scratch проєкт')
        self.assertContains(resp, 'Котик')
        self.assertContains(resp, 'Завжди:')
        self.assertContains(resp, 'Ой, межа!')

    def test_microbit_hex_parser_and_view(self):
        """Перевірка розбору BBC micro:bit (.hex) файлів для MicroPython та MakeCode PXT."""
        import tempfile
        import json
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .microbit_utils import parse_microbit_hex

        # 1. Створюємо валідний Intel HEX з вбудованим рядком MicroPython (з заголовком 'MP')
        python_script = b"from microbit import *\r\nwhile True:\r\n    if button_a.is_pressed():\r\n        display.show(Image.HEART)\r\n    sleep(100)\r\n"
        py_len = len(python_script)
        mp_payload = b"MP" + bytes([py_len & 0xFF, (py_len >> 8) & 0xFF]) + python_script + b"\x00"
        hex_data_payload = mp_payload.hex().upper()
        byte_len = len(mp_payload)
        intel_hex_lines = [
            f":{byte_len:02X}000000{hex_data_payload}00\n",
            ":00000001FF\n"  # EOF record
        ]
        hex_content = "".join(intel_hex_lines)

        with tempfile.NamedTemporaryFile(suffix='.hex', mode='w', delete=False) as tmp_hex:
            tmp_hex.write(hex_content)
            tmp_hex_path = tmp_hex.name

        try:
            html_out, text_summary, err = parse_microbit_hex(tmp_hex_path)
            self.assertIsNone(err)
            self.assertIn("from microbit import", text_summary)
            self.assertIn("button_a.is_pressed", text_summary)
            self.assertIn("Світлодіодний дисплей 5x5", text_summary)
            self.assertIn("Введення з кнопок: Кнопка A", text_summary)
            self.assertIn("BBC micro:bit", html_out)
        finally:
            if os.path.exists(tmp_hex_path):
                os.remove(tmp_hex_path)

        # 2. Тест MakeCode PXT JSON зі складними функціями та фігурними дужками { ... }
        makecode_project = {
            "pxt.json": "{\"name\": \"Compass_Project\"}",
            "main.ts": "input.onButtonPressed(Button.A, function () {\n    basic.showString(\"NORTH\")\n    basic.showIcon(IconNames.Heart)\n})\ninput.onGesture(Gesture.Shake, function () {\n    basic.showNumber(42)\n})"
        }
        makecode_json_bytes = json.dumps(makecode_project).encode('utf-8')
        mc_hex_lines = []
        for i in range(0, len(makecode_json_bytes), 16):
            chunk = makecode_json_bytes[i:i + 16]
            chunk_hex = chunk.hex().upper()
            mc_hex_lines.append(f":{len(chunk):02X}{i:04X}00{chunk_hex}00\n")
        mc_hex_lines.append(":00000001FF\n")
        mc_hex_content = "".join(mc_hex_lines)

        with tempfile.NamedTemporaryFile(suffix='.hex', mode='w', delete=False) as tmp_mc_hex:
            tmp_mc_hex.write(mc_hex_content)
            tmp_mc_path = tmp_mc_hex.name

        try:
            mc_html, mc_summary, mc_err = parse_microbit_hex(tmp_mc_path)
            self.assertIsNone(mc_err)
            self.assertIn("input.onButtonPressed", mc_summary)
            self.assertIn("IconNames.Heart", mc_summary)
            self.assertIn("Світлодіодний дисплей 5x5", mc_summary)
            self.assertIn("Акселерометр", mc_summary)
        finally:
            if os.path.exists(tmp_mc_path):
                os.remove(tmp_mc_path)

        # 3. Тест здачі учнем та перегляду вчителем
        f_hex = SimpleUploadedFile("microbit_heart.hex", hex_content.encode('utf-8'), content_type="text/plain")
        self.client.post(
            reverse('submit_assignment', args=[self.assignment.pk]),
            {
                'full_name': 'Шевченко Богдан',
                'class_group': self.class_group.id,
                'files': [f_hex],
            }
        )
        sub = Submission.objects.get(last_name='Шевченко', first_name='Богдан')
        self.assertEqual(sub.get_file_type(), 'microbit')
        self.assertEqual(sub.get_file_icon(), '📟')

        self.client.login(username='teacher1', password='password123')
        resp = self.client.get(reverse('view_file', args=[sub.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '📟 BBC micro:bit')
        self.assertContains(resp, 'from microbit import')

    def test_in_browser_video_playback(self):
        """Перевірка запуску прикріплених відео (.mp4, .webm тощо) у браузері учня та вчителя."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import AssignmentFile

        # 1. Створюємо відеоматеріал вчителя до завдання
        dummy_video_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42"
        f_teacher_video = SimpleUploadedFile("lesson_demo.mp4", dummy_video_bytes, content_type="video/mp4")
        af = AssignmentFile.objects.create(
            assignment=self.assignment,
            file=f_teacher_video,
            original_name="lesson_demo.mp4"
        )
        self.assertEqual(af.get_file_type(), 'video')
        self.assertEqual(af.get_file_icon(), '🎬')

        # Перевірка inline MIME-типу у file_view
        self.client.login(username='teacher1', password='password123')
        resp_file_view = self.client.get(reverse('file_view', args=[af.id]))
        self.assertEqual(resp_file_view.status_code, 200)
        self.assertEqual(resp_file_view['Content-Type'], 'video/mp4')
        self.assertIn('inline', resp_file_view['Content-Disposition'])

        # Перевірка відображення плеєра у assignment_detail
        resp_assign = self.client.get(reverse('assignment_detail', args=[self.assignment.pk]))
        self.assertEqual(resp_assign.status_code, 200)
        self.assertContains(resp_assign, '<video src="')

        # 2. Учень здає відеороботу
        f_student_video = SimpleUploadedFile("student_presentation.webm", dummy_video_bytes, content_type="video/webm")
        self.client.logout()
        self.client.post(
            reverse('submit_assignment', args=[self.assignment.pk]),
            {
                'full_name': 'Лисенко Катерина',
                'class_group': self.class_group.id,
                'files': [f_student_video],
            }
        )
        sub = Submission.objects.get(last_name='Лисенко', first_name='Катерина')
        self.assertEqual(sub.get_file_type(), 'video')
        self.assertEqual(sub.get_file_icon(), '🎬')

        # Перевірка стрімінгу view_submission_raw_file
        self.client.login(username='teacher1', password='password123')
        resp_raw = self.client.get(reverse('view_submission_raw_file', args=[sub.id]))
        self.assertEqual(resp_raw.status_code, 200)
        self.assertEqual(resp_raw['Content-Type'], 'video/webm')
        self.assertIn('inline', resp_raw['Content-Disposition'])

        # Перевірка плеєра у переглядачі вчителя file_viewer
        resp_viewer = self.client.get(reverse('view_file', args=[sub.id]))
        self.assertEqual(resp_viewer.status_code, 200)
        self.assertContains(resp_viewer, '🎬 Відео')
        self.assertContains(resp_viewer, '<video controls')

        # Перевірка плеєра на сторінці здачі для учня submission_detail
        resp_sub_detail = self.client.get(reverse('submission_detail', args=[sub.id]))
        self.assertEqual(resp_sub_detail.status_code, 200)
        self.assertContains(resp_sub_detail, '<video src="')


class FirstRunSetupTests(TestCase):
    """Тестування майстра першого запуску системи (First-Run Setup Wizard)."""

    def setUp(self):
        from feed.middleware import set_has_admin
        set_has_admin(None)

    def tearDown(self):
        from feed.middleware import set_has_admin
        set_has_admin(None)

    def test_first_run_redirect_when_no_superuser(self):
        """Коли в системі немає суперкористувачів, запити перенаправляються на /setup/."""
        from feed.middleware import set_has_admin
        User.objects.all().delete()
        set_has_admin(None)

        resp = self.client.get('/')
        self.assertRedirects(resp, reverse('first_run_setup'))

    def test_first_run_setup_page_renders_when_no_superuser(self):
        """Сторінка /setup/ відкривається з кодом 200, якщо суперкористувачів немає."""
        from feed.middleware import set_has_admin
        User.objects.all().delete()
        set_has_admin(None)

        resp = self.client.get(reverse('first_run_setup'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ласкаво просимо до SchoolNet')
        self.assertContains(resp, 'Логін головного адміністратора')

    def test_first_run_setup_form_submission_creates_admin_and_seeds_data(self):
        """Успішне створення першого адміністратора, профілю вчителя та авто-ініціалізація даних."""
        from feed.middleware import set_has_admin
        User.objects.all().delete()
        set_has_admin(None)

        resp = self.client.post(reverse('first_run_setup'), {
            'username': 'schooladmin',
            'full_name': 'Петренко Петро Петрович',
            'email': 'admin@myschool.ua',
            'password': 'SuperPassword2026',
            'password_confirm': 'SuperPassword2026',
            'seed_default_data': True,
        })
        self.assertRedirects(resp, reverse('teacher_dashboard'))

        # Перевірка створення суперкористувача та профілю
        admin_user = User.objects.get(username='schooladmin')
        self.assertTrue(admin_user.is_superuser)
        self.assertTrue(admin_user.is_staff)
        self.assertEqual(admin_user.email, 'admin@myschool.ua')
        self.assertEqual(admin_user.teacher_profile.full_name, 'Петренко Петро Петрович')

        # Перевірка ініціалізації типових предметів та класів
        self.assertTrue(Subject.objects.filter(name='Інформатика').exists())
        self.assertTrue(ClassGroup.objects.filter(name='9А').exists())

        # Перевірка, що повторний доступ до /setup/ для авторизованого адміна редиректить у кабінет
        resp2 = self.client.get(reverse('first_run_setup'))
        self.assertRedirects(resp2, reverse('teacher_dashboard'))

        # Для неавторизованого користувача — редиректить на сторінку входу
        self.client.logout()
        resp3 = self.client.get(reverse('first_run_setup'))
        self.assertRedirects(resp3, reverse('teacher_login'))

    def test_first_run_setup_blocked_when_superuser_exists(self):
        """Якщо в системі вже є суперкористувач, /setup/ перенаправляє на teacher_login."""
        from feed.middleware import set_has_admin
        User.objects.create_superuser(username='existingadmin', password='password123')
        set_has_admin(True)

        resp = self.client.get(reverse('first_run_setup'))
        self.assertRedirects(resp, reverse('teacher_login'))













