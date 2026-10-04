from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
import os
import glob
import json
import tempfile
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
        self.user = User.objects.create_user(username='teacher1', password='password123', is_staff=True, is_superuser=True)
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
        from lxml import html
        cards = html.fromstring(resp.content).xpath('//article[@class="student-sub-card"]')
        self.assertIn('Перевірено', cards[0].text_content())
        self.assertFalse(cards[0].xpath('.//*[not(*) and normalize-space(text())="12"]'))
        self.assertNotContains(resp, '✅ Оцінено: 12')  # оцінка прихована
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
        self.assertContains(resp, 'Надіслати виконану роботу')
        self.assertContains(resp, 'Як здати роботу')
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
            if url == reverse('assignment_create'):
                self.assertContains(resp, 'assignment-editor-layout')
                self.assertNotContains(resp, 'id="teacher-main-sidebar"')
            else:
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
        self.assertContains(resp, 'Налаштування модуля')

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

    @patch('feed.safe_http.validate_public_url')
    @patch('feed.safe_http.public_urlopen')
    def test_ai_web_url_and_youtube_fetching(self, mock_urlopen, mock_validate):
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
        self.assertContains(resp, 'Модуль ШІ')

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
        def side_effect(url, payload_dict, headers=None, timeout=30):
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
        # force_recreate=True гарантує ініціалізацію незалежно від стану класової змінної
        AICriteriaPreset._default_presets_initialized = False
        AICriteriaPreset.ensure_default_presets(force_recreate=True)
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
            # Скидаємо класову змінну і примусово ініціалізуємо дефолтні шаблони
            AICriteriaPreset._default_presets_initialized = False
            AICriteriaPreset.ensure_default_presets(force_recreate=True)
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

    def test_docx_embedded_image_extraction(self):
        """Тест видобування вбудованих зображень із файлів Word (.docx) для аналізу ШІ."""
        import tempfile
        import zipfile
        from feed.gemini_service import extract_submission_content

        # Створюємо валідний мінімальний docx (zip-архів) із текстом та картинкою у word/media/
        dummy_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        dummy_xml = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>\xd0\x9f\xd0\xb0\xd1\x80\xd1\x83 \xd1\x81\xd0\xbb\xd1\x96\xd0\xb2 \xd1\x83\xd1\x87\xd0\xbd\xd1\x8f</w:t></w:r></w:p></w:body></w:document>'

        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp_f:
            tmp_path = tmp_f.name

        try:
            with zipfile.ZipFile(tmp_path, 'w') as z:
                z.writestr('word/document.xml', dummy_xml)
                z.writestr('word/media/image1.png', dummy_png)

            with open(tmp_path, 'rb') as f_read:
                docx_upload = SimpleUploadedFile("student_work.docx", f_read.read(), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

            sub = Submission.objects.create(
                assignment=self.assignment,
                first_name='Андрій',
                last_name='Мельник',
                class_group=self.class_group,
                teacher=self.teacher,
                file=docx_upload
            )

            text_parts, inline_media, err = extract_submission_content(sub)
            self.assertIsNone(err)
            self.assertTrue(any("Пару слів учня" in tp for tp in text_parts))
            self.assertTrue(any("вбудоване зображення" in tp and "image1.png" in tp for tp in text_parts))
            self.assertEqual(len(inline_media), 1)
            self.assertEqual(inline_media[0]['mime_type'], 'image/png')
            self.assertTrue(len(inline_media[0]['data']) > 0)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @patch('feed.gemini_service._http_post_json')
    def test_ai_evaluation_task_completeness_and_sub_ten_feedback(self, mock_post):
        """Тест контролю повноти виконання завдання та обов'язкового зворотного зв'язку при оцінці < 10."""
        from feed.gemini_service import evaluate_submission_with_gemini

        ai_set = AISettings.get_solo()
        ai_set.api_key = 'test-gemini-key'
        ai_set.save()

        # Завдання вимагає створення списку дат
        self.assignment.title = "Хронологія подій козацької доби"
        self.assignment.description = "Складіть повний хронологічний список дат із описом ключових битв та подій козацької доби."
        self.assignment.save()

        # Учень здав лише пару слів та 1 дату
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Іван',
            last_name='Франко',
            class_group=self.class_group,
            teacher=self.teacher,
            comment_student='Ось моя робота. 1648 рік - битва під Жовтими Водами.'
        )

        captured_payloads = []
        def side_effect(url, payload, headers=None, timeout=35):
            captured_payloads.append(payload)
            return (200, {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": json.dumps({
                                "suggested_grade": "5",
                                "level": "Середній (4-6)",
                                "format_warning": None,
                                "summary": "Завдання виконано фрагментарно: наведено лише одну дату замість повноцінного списку.",
                                "strengths": ["Вказано правильний рік для битви під Жовтими Водами"],
                                "weaknesses": ["Завдання вимагало скласти повний перелік ключових дат, натомість наведено лише одну"],
                                "feedback_comment": "Добре, що згадано 1648 рік, проте в загальному завдання виконано не повністю. Для вищого балу необхідно скласти повний хронологічний перелік дат подій козацької доби."
                            })
                        }]
                    }
                }]
            }, "ok")

        mock_post.side_effect = side_effect

        res = evaluate_submission_with_gemini(sub)
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['suggested_grade'], '5')
        self.assertIn("Середній", res['level'])

        # Перевірка вмісту запиту до Gemini
        self.assertTrue(len(captured_payloads) > 0)
        sent_prompt = captured_payloads[0]['contents'][0]['parts'][0]['text']
        self.assertIn("ТОЧНЕ РОЗУМІННЯ СУТІ ЗАВДАННЯ", sent_prompt)
        self.assertIn("ОБОВ'ЯЗКОВИЙ ЗВОРОТНИЙ ЗВ'ЯЗОК ПРИ ОЦІНЦІ МЕНШЕ 10 БАЛІВ", sent_prompt)
        self.assertIn("Хронологія подій козацької доби", sent_prompt)

        # Перевірка зворотного зв'язку
        sub.refresh_from_db()
        self.assertEqual(sub.ai_suggested_grade, '5')
        self.assertTrue(len(res['weaknesses']) > 0)
        self.assertIn("Завдання вимагало скласти повний перелік", res['weaknesses'][0])
        self.assertIn("в загальному", res['feedback_comment'])

        # Тест постінспекції: якщо ШІ повернув оцінку 5 з порожнім weaknesses, постобробка гарантує зауваження
        def side_effect_empty_weaknesses(url, payload, *args, **kwargs):
            return (200, {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": json.dumps({
                                "suggested_grade": "5",
                                "level": "Середній (4-6)",
                                "format_warning": None,
                                "summary": "Поверхневе розкриття теми",
                                "strengths": ["Охайне оформлення"],
                                "weaknesses": [],
                                "feedback_comment": "Потрібно доопрацювати завдання."
                            })
                        }]
                    }
                }]
            }, "ok")

        mock_post.side_effect = side_effect_empty_weaknesses
        res2 = evaluate_submission_with_gemini(sub)
        self.assertEqual(res2['suggested_grade'], '5')
        self.assertTrue(len(res2['weaknesses']) > 0)
        self.assertIn("доопрацювання", res2['weaknesses'][0].lower())

    def test_gradebook_multiple_evaluation_dates(self):
        """
        Тест відображення оцінок за датами завдань у журналі, навіть якщо вчитель
        виставив оцінки в один і той самий день (наприклад, у п'ятницю).
        """
        from datetime import datetime
        from django.utils import timezone
        self.client.login(username='teacher1', password='password123')

        d1 = timezone.make_aware(datetime(2026, 9, 1, 9, 0, 0))
        d2 = timezone.make_aware(datetime(2026, 9, 5, 9, 0, 0))
        eval_dt = timezone.make_aware(datetime(2026, 9, 8, 16, 30, 0))  # Вчитель перевіряє пізніше

        # Два різних завдання на різні дати
        assign1 = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання за понеділок',
            status=Assignment.STATUS_PUBLISHED,
            published_at=d1
        )
        assign1.classes.add(self.class_group)

        assign2 = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання за п\'ятницю',
            status=Assignment.STATUS_PUBLISHED,
            published_at=d2
        )
        assign2.classes.add(self.class_group)

        # Обидві роботи оцінені в один і той самий день (eval_dt)
        sub1 = Submission.objects.create(
            assignment=assign1,
            first_name='Петро',
            last_name='Коваленко',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='10',
            graded_at=eval_dt
        )
        sub2 = Submission.objects.create(
            assignment=assign2,
            first_name='Петро',
            last_name='Коваленко',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='11',
            graded_at=eval_dt
        )

        resp = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&view_mode=journal')
        self.assertEqual(resp.status_code, 200)
        # Журнал повинен мати дві різні дати завдань (01.09 та 05.09), а не дату оцінювання
        self.assertContains(resp, '01.09')
        self.assertContains(resp, '05.09')
        self.assertContains(resp, '10')
        self.assertContains(resp, '11')
        # Підказка повинна містити дату оцінювання
        self.assertContains(resp, 'Оцінено: 08.09.2026')

        # Перевірка сторінки всіх оцінок
        resp_all = self.client.get(reverse('gradebook') + '?view=all_grades')
        self.assertEqual(resp_all.status_code, 200)
        self.assertContains(resp_all, '01.09.2026')
        self.assertContains(resp_all, '05.09.2026')

    def test_gradebook_filters_and_rework_badge(self):
        """Тест фільтрації за датою завдання, діапазоном оцінок та відображення значка 'Д' (Доопрацювати)."""
        from datetime import datetime
        from django.utils import timezone
        self.client.login(username='teacher1', password='password123')

        dt1 = timezone.make_aware(datetime(2026, 9, 10, 10, 0, 0))
        dt2 = timezone.make_aware(datetime(2026, 9, 25, 12, 0, 0))

        assign1 = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання 10 вересня',
            status=Assignment.STATUS_PUBLISHED,
            published_at=dt1
        )
        assign1.classes.add(self.class_group)

        assign2 = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання 25 вересня',
            status=Assignment.STATUS_PUBLISHED,
            published_at=dt2
        )
        assign2.classes.add(self.class_group)

        sub_high = Submission.objects.create(
            assignment=assign1,
            first_name='Оксана',
            last_name='Шевченко',
            class_group=self.class_group,
            teacher=self.teacher,
            grade='11',
            graded_at=dt1
        )
        sub_rework = Submission.objects.create(
            assignment=assign2,
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

        # 2. Фільтр за конкретним днем завдання (single_date=2026-09-10)
        resp_day = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&single_date=2026-09-10')
        self.assertEqual(resp_day.status_code, 200)
        self.assertContains(resp_day, '10.09')
        self.assertNotContains(resp_day, '25.09')

        # 3. Фільтр за діапазоном оцінок (grade_filter=rework)
        resp_rework = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&grade_filter=rework')
        self.assertEqual(resp_rework.status_code, 200)
        self.assertContains(resp_rework, 'Бондар')
        self.assertNotContains(resp_rework, 'Шевченко Оксана')

        # 4. Фільтр за високим балом (grade_filter=10-12)
        resp_high = self.client.get(reverse('gradebook') + f'?class_group={self.class_group.id}&grade_filter=10-12')
        self.assertEqual(resp_high.status_code, 200)
        self.assertContains(resp_high, 'Шевченко')
        self.assertNotContains(resp_high, 'Бондар Іван')

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

    def test_teacher_dashboard_filter_menu(self):
        """Тест меню фільтрації вчителя: по класах, темах/предметах, датах та пошуковому запиту."""
        self.client.login(username='teacher1', password='password123')

        subject2 = Subject.objects.create(name='Фізика', icon='⚛️', color='#3b82f6')
        class2 = ClassGroup.objects.create(name='10-Б', grade=10, letter='Б')

        # Створюємо тестові завдання
        a_math = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Тема з геометрії: трикутники",
            description="Ознайомитися з ознаками рівності трикутників",
            status=Assignment.STATUS_PUBLISHED
        )
        a_math.classes.add(self.class_group)

        a_physics = Assignment.objects.create(
            teacher=self.teacher,
            subject=subject2,
            title="Тема з фізики: закони Ньютона",
            description="Вивчити формули другого закону",
            status=Assignment.STATUS_PUBLISHED
        )
        a_physics.classes.add(class2)

        # 1. Перевірка наявності форми меню фільтрації на панелі вчителя
        resp = self.client.get(reverse('teacher_dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Фільтрація завдань')
        self.assertContains(resp, 'filter-class')
        self.assertContains(resp, 'filter-subject')
        self.assertContains(resp, 'filter-date-preset')
        self.assertContains(resp, 'filter-q')

        # 2. Фільтрація за класом
        resp_class = self.client.get(reverse('teacher_dashboard') + f'?class={class2.id}')
        self.assertEqual(resp_class.status_code, 200)
        self.assertContains(resp_class, 'закони Ньютона')
        self.assertNotContains(resp_class, 'трикутники')

        # 3. Фільтрація за предметом
        resp_subject = self.client.get(reverse('teacher_dashboard') + f'?subject={subject2.id}')
        self.assertEqual(resp_subject.status_code, 200)
        self.assertContains(resp_subject, 'закони Ньютона')
        self.assertNotContains(resp_subject, 'трикутники')

        # 4. Фільтрація за текстовим запитом / темою
        resp_query = self.client.get(reverse('teacher_dashboard') + '?q=трикутники')
        self.assertEqual(resp_query.status_code, 200)
        self.assertContains(resp_query, 'трикутники')
        self.assertNotContains(resp_query, 'закони Ньютона')

    def test_assignment_detail_duplicate_button(self):
        """Тест наявності кнопки копіювання поруч із відредагувати завдання у перегляді як учень."""
        # Для вчителя
        self.client.login(username='teacher1', password='password123')
        resp = self.client.get(reverse('assignment_detail', kwargs={'pk': self.assignment.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Відредагувати завдання')
        self.assertContains(resp, 'Копіювати завдання')
        self.assertContains(resp, 'openDuplicateModal')
        self.assertContains(resp, 'duplicate-modal')

        # Для неавторизованого / учня — кнопок редагування та копіювання бути не повинно
        self.client.logout()
        resp_student = self.client.get(reverse('assignment_detail', kwargs={'pk': self.assignment.pk}))
        self.assertEqual(resp_student.status_code, 200)
        self.assertNotContains(resp_student, 'Відредагувати завдання')
        self.assertNotContains(resp_student, 'Копіювати завдання')
        self.assertNotContains(resp_student, 'openDuplicateModal')

    def test_file_viewer_open_assignment_in_new_window(self):
        """Тест кнопки/посилання відкриття завдання у новому вікні при перевірці та оцінюванні."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile("test.py", b"print('Hello')", content_type="text/x-python")
        sub = Submission.objects.create(
            assignment=self.assignment,
            first_name='Олександр',
            last_name='Шевченко',
            class_group=self.class_group,
            teacher=self.teacher,
            file=file,
        )
        self.client.login(username='teacher1', password='password123')
        resp = self.client.get(reverse('view_file', kwargs={'submission_id': sub.id}))
        self.assertEqual(resp.status_code, 200)
        # Перевіряємо наявність кнопки перегляду завдання у новому вікні
        self.assertContains(resp, 'Відкрити завдання як учень ↗')
        self.assertContains(resp, 'Як бачить учень ↗')
        self.assertContains(resp, reverse('assignment_detail', args=[self.assignment.pk]))
        self.assertContains(resp, 'target="_blank"')

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

        # 2. Перевіряємо зрозумілий заголовок інструкції на сторінці деталей
        resp_detail = self.client.get(reverse('assignment_detail', kwargs={'pk': indiv_assignment.pk}))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, 'Що потрібно зробити')
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

    def test_teacher_schedule_tab_includes_live_lesson_widget(self):
        """Тест: на вкладці розкладу вчителя відображається живий статус-бар поточного уроку/розкладу."""
        self.client.login(username='teacher1', password='password123')
        resp = self.client.get(reverse('teacher_students') + '?tab=schedule')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'live-lesson-widget')

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

        with zipfile.ZipFile(io.BytesIO(b''.join(zip_resp.streaming_content))) as zf:
            zip_names = zf.namelist()
            self.assertIn('page1.png', zip_names)
            self.assertIn('solution.py', zip_names)
            self.assertIn('notes.txt', zip_names)

        # 5. Перевірка масового експорту завдань у ZIP
        all_zip_resp = self.client.get(reverse('download_assignment_submissions_zip', args=[self.assignment.id]))
        self.assertEqual(all_zip_resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(b''.join(all_zip_resp.streaming_content))) as zf:
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

    def test_resubmission_gradebook_and_pending_counters(self):
        """
        Перевірка, що при повторній здачі (перездачі) роботи:
        1. Перша неперевірена спроба не висить в журналі оцінок як ⏳.
        2. Перша спроба не завищує лічильники неперевірених робіт вчителя (pending_reviews_count, pending_submissions_count).
        3. У кабінеті учня (student_submissions_portal) спроби групуються: виводиться остання активна спроба з історією попередніх спроб.
        """
        from .student_matcher import cluster_submissions_by_student
        from .context_processors import teacher_stats_context

        # 1. Створюємо першу спробу учня без оцінки
        sub1 = Submission.objects.create(
            assignment=self.assignment,
            first_name='Андрій',
            last_name='Мельник',
            class_group=self.class_group,
            teacher=self.teacher,
            file=SimpleUploadedFile("melnyk_v1.txt", b"Initial attempt"),
            resubmission_attempt=1,
            is_latest_attempt=True,
            submitted_at=timezone.now()
        )

        # Лічильник вчителя повинен бути 1
        self.assertEqual(self.teacher.pending_reviews_count, 1)

        # 2. Учень надсилає другу спробу (перездача)
        f2 = SimpleUploadedFile("melnyk_v2.txt", b"Second improved attempt")
        post_data = {
            'full_name': 'Мельник Андрій',
            'class_group': self.class_group.id,
            'files': [f2],
            'comment_student': 'Виправив зауваження!',
        }
        resp = self.client.post(reverse('submit_assignment', args=[self.assignment.id]), post_data, follow=True)
        self.assertEqual(resp.status_code, 200)

        sub1.refresh_from_db()
        sub2 = Submission.objects.filter(last_name='Мельник', first_name='Андрій', assignment=self.assignment).order_by('-submitted_at').first()

        # sub1 замінена, sub2 актуальна
        self.assertFalse(sub1.is_latest_attempt)
        self.assertTrue(sub2.is_latest_attempt)
        self.assertTrue(sub1.is_superseded)
        self.assertFalse(sub2.is_superseded)
        self.assertFalse(sub1.awaits_grading)
        self.assertTrue(sub2.awaits_grading)

        # 3. Перевірка лічильників неперевірених робіт вчителя
        # Має бути рівно 1 неперевірена робота (нова спроба), а не 2!
        self.assertEqual(self.teacher.pending_reviews_count, 1)

        # Перевірка контекстного процесора
        class DummyRequest:
            def __init__(self, user):
                self.user = user
                self.session = {}
        ctx = teacher_stats_context(DummyRequest(self.user))
        self.assertEqual(ctx['pending_submissions_count'], 1)

        # 4. Перевірка журналу оцінок (кластеризація):
        # Перша неперевірена спроба НЕ повинна додавати ⏳ до клітинки журналу
        submissions_qs = Submission.objects.filter(class_group=self.class_group, assignment=self.assignment)
        clusters = cluster_submissions_by_student(submissions_qs)
        melnyk_cluster = next((c for c in clusters if c['last_name'] == 'Мельник'), None)
        self.assertIsNotNone(melnyk_cluster)

        # sub1 не повинна бути в grades_by_date, оскільки вона замінена і не має оцінки
        sub_ids_in_grades = [g['submission_id'] for sublist in melnyk_cluster['grades_by_date'].values() for g in sublist]
        self.assertNotIn(sub1.id, sub_ids_in_grades)
        self.assertIn(sub2.id, sub_ids_in_grades)

        # 5. Перевірка сторінки кабінету учня (student_submissions_portal):
        portal_resp = self.client.get(reverse('student_submissions_portal'), {
            'q': 'Мельник Андрій',
            'class': self.class_group.id
        })
        self.assertEqual(portal_resp.status_code, 200)
        # Учень бачить лише 1 головну картку завдання (sub2)
        subs_in_portal = list(portal_resp.context['page_obj'])
        self.assertEqual(len(subs_in_portal), 1)
        self.assertEqual(subs_in_portal[0].id, sub2.id)
        # Картка показує спробу #2 та історію попередніх спроб
        self.assertContains(portal_resp, 'Спроба #2')
        self.assertContains(portal_resp, 'Попередні версії цієї роботи')
        self.assertContains(portal_resp, 'Спроба #1')
        self.assertContains(portal_resp, 'Замінено новою спробою')

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

        # 4. Перевірка картки учня (submission_detail) - деталі приховані від учня, лише загальна інформація
        sub_detail_resp = self.client.get(reverse('submission_detail', args=[sub.id]))
        self.assertEqual(sub_detail_resp.status_code, 200)
        # Учень бачить лише загальне повідомлення (без деталей)
        self.assertContains(sub_detail_resp, 'У роботі зафіксовано використання штучного інтелекту (ШІ)')
        self.assertContains(sub_detail_resp, '(дозволено вчителем)')
        # Деталі аналізу ШІ приховані від учня — видно лише вчителю
        self.assertNotContains(sub_detail_resp, 'Виявлено характерні шаблонні формулювання нейромереж')

        # 5. Перевірка сторінки вчителя (assignment_submissions) та (view_file)
        self.client.login(username='teacher1', password='password123')
        asg_subs_resp = self.client.get(reverse('assignment_submissions', args=[ai_asg.id]))
        self.assertEqual(asg_subs_resp.status_code, 200)
        self.assertContains(asg_subs_resp, 'Ознаки ШІ')

        fv_resp = self.client.get(reverse('view_file', args=[sub.id]))
        self.assertEqual(fv_resp.status_code, 200)
        self.assertContains(fv_resp, 'Виявлено ознаки використання ШІ')
        self.assertContains(fv_resp, 'Дозволено вчителем')

        # Деталі аналізу ШІ видно лише вчителю у view_file
        sub_detail_teacher_resp = self.client.get(reverse('submission_detail', args=[sub.id]))
        self.assertEqual(sub_detail_teacher_resp.status_code, 200)
        self.assertContains(sub_detail_teacher_resp, 'Виявлено характерні шаблонні формулювання нейромереж')

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
        # Переконуємось що блок ГР-результатів (специфічні DOM-елементи) відсутній у сторінці учня.
        # Примітка: 'ГР 1' зустрічається в changelog-модалі (контекстний процесор),
        # тому перевіряємо DOM-ідентифікатори, що з'являються лише при реальних ГР-результатах.
        self.assertNotContains(resp_student, 'gr-results-block')
        self.assertNotContains(resp_student, 'gr-grade-badge')

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

    def test_coauthor_submission_does_not_flag_plagiarism(self):
        """Перевірка, що колективна робота зі співавторами не позначається як плагіат чи дублікат."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .duplicate_detector import check_submission_duplicates

        file_content = b"# Group collaborative solution\ndef group_work():\n    return 'success'\n"
        f1 = SimpleUploadedFile("group_task.py", file_content, content_type="text/x-python")
        
        # 1. Створюємо первинну здачу групи
        sub1 = Submission.objects.create(
            assignment=self.assignment,
            last_name='Мельник',
            first_name='Олександр',
            class_group=self.class_group,
            is_group_work=True,
            group_authors='Шевченко Тарас, Мельник Олександр',
            file=f1,
            is_latest_attempt=True,
        )

        # 2. Створюємо здачу співавтора
        f2 = SimpleUploadedFile("group_task.py", file_content, content_type="text/x-python")
        sub2 = Submission.objects.create(
            assignment=self.assignment,
            last_name='Шевченко',
            first_name='Тарас',
            class_group=self.class_group,
            is_group_work=True,
            group_authors='Шевченко Тарас, Мельник Олександр',
            primary_submission=sub1,
            file=f2,
            is_latest_attempt=True,
        )

        # Перевіряємо метод is_coauthor_with
        self.assertTrue(sub1.is_coauthor_with(sub2))
        self.assertTrue(sub2.is_coauthor_with(sub1))

        # Перевіряємо duplicate detector
        dup_info1 = check_submission_duplicates(sub1)
        self.assertFalse(dup_info1['is_duplicate'])
        self.assertFalse(dup_info1['is_duplicate_student'])

        dup_info2 = check_submission_duplicates(sub2)
        self.assertFalse(dup_info2['is_duplicate'])
        self.assertFalse(dup_info2['is_duplicate_student'])
        self.assertTrue(dup_info2.get('plagiarism_ignored'))
        self.assertTrue(dup_info2.get('is_coauthor'))

    def test_teacher_toggle_ignore_plagiarism(self):
        """Перевірка прапорця вчителя 'Ігнорувати плагіат' для парних робіт."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .duplicate_detector import check_submission_duplicates

        file_content = b"# Shared project code without coauthors filled\ndef shared_proj():\n    return 100\n"
        f1 = SimpleUploadedFile("shared_project.py", file_content, content_type="text/x-python")
        sub1 = Submission.objects.create(
            assignment=self.assignment,
            last_name='Лисенко',
            first_name='Микола',
            class_group=self.class_group,
            file=f1,
            is_latest_attempt=True,
        )

        f2 = SimpleUploadedFile("shared_project.py", file_content, content_type="text/x-python")
        sub2 = Submission.objects.create(
            assignment=self.assignment,
            last_name='Франко',
            first_name='Іван',
            class_group=self.class_group,
            file=f2,
            is_latest_attempt=True,
        )

        # Спочатку дублікат фіксується
        dup_before = check_submission_duplicates(sub2)
        self.assertTrue(dup_before['is_duplicate'])
        self.assertTrue(dup_before['is_duplicate_student'])

        # Вчитель вмикає ігнорування плагіату через AJAX
        self.client.login(username='teacher1', password='password123')
        resp = self.client.post(
            reverse('toggle_submission_ignore_plagiarism', args=[sub2.id]),
            {'ignore': 'true'}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertTrue(data['ignore_plagiarism'])

        sub2.refresh_from_db()
        sub1.refresh_from_db()
        self.assertTrue(sub2.ignore_plagiarism)
        self.assertTrue(sub1.ignore_plagiarism)

        # Тепер перевірка дублікатів показує plagiarism_ignored і is_duplicate=False
        if hasattr(sub2, '_cached_dup_info'):
            delattr(sub2, '_cached_dup_info')
        dup_after = check_submission_duplicates(sub2)
        self.assertFalse(dup_after['is_duplicate'])
        self.assertFalse(dup_after['is_duplicate_student'])
        self.assertTrue(dup_after.get('plagiarism_ignored'))

        # Вчитель відкриває вікно перевірки - бачить позначку про спільну роботу
        resp_view = self.client.get(reverse('view_file', args=[sub2.id]))
        self.assertEqual(resp_view.status_code, 200)
        self.assertContains(resp_view, 'Плагіат проігноровано')
        self.assertContains(resp_view, 'ignore-plagiarism-checkbox')

        # Вчитель вимикає ігнорування плагіату
        resp_off = self.client.post(
            reverse('toggle_submission_ignore_plagiarism', args=[sub2.id]),
            {'ignore': 'false'}
        )
        self.assertEqual(resp_off.status_code, 200)
        sub2.refresh_from_db()
        self.assertFalse(sub2.ignore_plagiarism)

    @patch('feed.gemini_service._http_post_json')
    def test_ai_evaluation_collaborative_work_no_plagiarism_penalty(self, mock_http_post):
        """Перевірка формування промпта для ШІ: спільна робота не отримує інструкцій про плагіат."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import AISettings
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.api_key = 'fake-api-key'
        settings.is_enabled = True
        settings.save()

        mock_data = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "suggested_grade": 11,
                            "level": "Високий (10-12)",
                            "summary": "Чудова спільна робота.",
                            "feedback_comment": "Добре виконано проект.",
                            "strengths": ["Гарна реалізація"],
                            "weaknesses": []
                        })
                    }]
                }
            }]
        }
        mock_http_post.return_value = (200, mock_data, json.dumps(mock_data))

        file_content = b"# Shared code\ndef test(): return 1\n"
        f1 = SimpleUploadedFile("task1.py", file_content, content_type="text/x-python")
        sub1 = Submission.objects.create(
            assignment=self.assignment,
            last_name='Петренко',
            first_name='Петро',
            class_group=self.class_group,
            file=f1,
            is_latest_attempt=True,
        )

        f2 = SimpleUploadedFile("task1.py", file_content, content_type="text/x-python")
        sub2 = Submission.objects.create(
            assignment=self.assignment,
            last_name='Сидоренко',
            first_name='Сидір',
            class_group=self.class_group,
            file=f2,
            ignore_plagiarism=True,
            is_latest_attempt=True,
        )

        res = evaluate_submission_with_gemini(sub2)
        self.assertEqual(res['status'], 'success')
        self.assertEqual(str(res['suggested_grade']), '11')

        # Перевіряємо аргументи виклику _http_post_json, щоб переконатися, що в prompt було передано спільну роботу
        sent_payload = mock_http_post.call_args[0][1]
        sent_prompt = ""
        for content in sent_payload.get('contents', []):
            for part in content.get('parts', []):
                if 'text' in part:
                    sent_prompt += part['text']

        self.assertIn('СПІЛЬНЕ / КОЛЕКТИВНЕ ВИКОНАННЯ РОБОТИ (ПЛАГІАТ ВИКЛЮЧЕНО)', sent_prompt)
        self.assertIn('КАТЕГОРИЧНО ЗАБОРОНЕНО знижувати оцінку чи встановлювати штраф за плагіат або списування', sent_prompt)
        self.assertNotIn('КРИТИЧНЕ ЗАУВАЖЕННЯ СИСТЕМИ АНТИПЛАГІАТУ:\nВстановлено 100% збіг', sent_prompt)

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

    def test_today_schedule_api_progress_fields(self):
        """Перевірка наявності полів візуального прогресу в API розкладу на сьогодні."""
        import datetime
        from feed.models import BellSchedule
        from feed.middleware import set_has_admin
        User.objects.create_superuser(username='admin_sch_test', password='password123')
        set_has_admin(True)

        user = User.objects.create_user(username='schedule_prog_teacher', password='password123')
        teacher = Teacher.objects.create(user=user, full_name='Розкладний Вчитель')
        self.client.login(username='schedule_prog_teacher', password='password123')

        slot = BellSchedule.objects.create(
            lesson_number=1,
            start_time=datetime.time(8, 30),
            end_time=datetime.time(9, 15),
            order=1
        )

        resp = self.client.get('/api/today-schedule/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get('success'))
        lessons = data.get('lessons', [])
        self.assertTrue(len(lessons) > 0)
        first_lesson = lessons[0]
        self.assertIn('progress_percent', first_lesson)
        self.assertIn('elapsed_minutes', first_lesson)
        self.assertIn('duration_minutes', first_lesson)
        self.assertIn('break_progress_percent', first_lesson)

    def test_student_ai_check_visibility_and_ergonomics(self):
        """Перевірка видимості кнопки самоперевірки ШІ на сторінці успіху здачі та в деталях роботи."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from feed.models import ClassGroup, Subject, Assignment, Submission
        from feed.middleware import set_has_admin
        User.objects.create_superuser(username='admin_asg_test', password='password123')
        set_has_admin(True)

        cg = ClassGroup.objects.create(name='10-А')
        subj = Subject.objects.create(name='Інформатика', color='#6366f1')
        user = User.objects.create_user(username='teacher_ai_asg', password='password123')
        teacher = Teacher.objects.create(user=user, full_name='Вчитель ШІ')

        asg = Assignment.objects.create(
            title='Тестове завдання з ШІ',
            description='Виконайте роботу',
            teacher=teacher,
            subject=subj,
            status=Assignment.STATUS_PUBLISHED,
        )
        asg.classes.add(cg)

        # Перевірка default=True для allow_student_ai_check
        self.assertTrue(asg.allow_student_ai_check)

        # Перевірка форми здачі - наявність інформера про ШІ
        resp_submit = self.client.get(reverse('submit_assignment', args=[asg.pk]))
        self.assertEqual(resp_submit.status_code, 200)
        self.assertContains(resp_submit, 'Після здачі доступна перевірка')

        # Створюємо здачу роботи
        test_file = SimpleUploadedFile('robot.txt', b'Code for AI test', content_type='text/plain')
        resp_post = self.client.post(reverse('submit_assignment', args=[asg.pk]), {
            'full_name': 'Шевченко Тарас',
            'class_group': cg.id,
            'files': [test_file],
        }, follow=True)

        self.assertEqual(resp_post.status_code, 200)
        # Перевірка сторінки submit_success: наявність блоку перевірки ШІ та кнопки
        self.assertContains(resp_post, 'id="student-selfcheck-block"')
        self.assertContains(resp_post, 'id="student-ai-check-btn"')
        self.assertContains(resp_post, 'Перевірити роботу ШІ')

        # Перевірка сторінки деталей зданої роботи submission_detail
        sub = Submission.objects.filter(assignment=asg).first()
        self.assertIsNotNone(sub)
        resp_detail = self.client.get(reverse('submission_detail', args=[sub.id]))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, 'id="student-selfcheck-block"')
        self.assertContains(resp_detail, 'id="student-ai-check-btn"')

    def test_changelog_role_separation_and_condition_default_hidden(self):
        """Тест розділення історії оновлень для учнів та вчителів і прихованого стану умови."""
        from feed.middleware import set_has_admin
        User.objects.create_superuser(username='setup_admin', password='password123', email='admin@test.com')
        set_has_admin(None)

        # 1. Перевірка для учня / неавторизованого користувача
        resp_student = self.client.get(reverse('index'))
        self.assertEqual(resp_student.status_code, 200)
        # Наявність кнопки оновлень та версії
        self.assertContains(resp_student, 'id="site-changelog-btn"')
        self.assertContains(resp_student, 'changelog-version-tag')
        self.assertContains(resp_student, 'id="changelog-modal"')
        # Учень бачить учнівські оновлення
        self.assertContains(resp_student, 'Виконання завдань на вибір та захист від зниження балу')
        self.assertContains(resp_student, 'Компактна форма здачі робіт')
        self.assertContains(resp_student, 'Миттєва самоперевірка робіт через ШІ')
        # Учень НЕ бачить вчительських вкладок чи системних деталей
        self.assertNotContains(resp_student, 'tab-btn-teacher')
        self.assertNotContains(resp_student, 'changelog-pane-teacher')

        # 2. Перевірка для авторизованого вчителя
        teacher_user = User.objects.create_user(username='teach_cl', password='password123')
        Teacher.objects.create(user=teacher_user, full_name='Вчитель Тестовий')
        self.client.login(username='teach_cl', password='password123')

        resp_teacher = self.client.get(reverse('index'))
        self.assertEqual(resp_teacher.status_code, 200)
        # Вчитель має вкладки для вчителів та для учнів
        self.assertContains(resp_teacher, 'id="tab-btn-teacher"')
        self.assertContains(resp_teacher, 'id="tab-btn-student"')
        self.assertContains(resp_teacher, 'Потокова перевірка робіт без застрягань')
        self.assertContains(resp_teacher, 'Компактна форма здачі робіт')

        # 3. Перевірка форми здачі: умова завжди прихована за замовчуванням
        cg = ClassGroup.objects.create(name='9-Б')
        asg = Assignment.objects.create(
            title='Завдання для перевірки згортання',
            teacher=Teacher.objects.first(),
            status=Assignment.STATUS_PUBLISHED
        )
        asg.classes.add(cg)
        resp_sub = self.client.get(reverse('submit_assignment', args=[asg.pk]))
        self.assertEqual(resp_sub.status_code, 200)
        from lxml import html
        page = html.fromstring(resp_sub.content)
        condition = page.get_element_by_id('submission-condition-details')
        self.assertEqual(condition.tag, 'details')
        self.assertNotIn('open', condition.attrib)
        self.assertIn('Умова й матеріали', condition.xpath('./summary')[0].text_content())
        self.assertEqual(page.get_element_by_id('submission-topic').text_content(), asg.title)


class AIRawJSONProtectionTests(TestCase):
    """
    Тести перевірки надійного захисту учнів від показу сирого JSON у коментарях та відгуках ШІ.
    """
    def test_extract_json_from_text_handles_truncated_and_invalid_escapes(self):
        from feed.gemini_service import extract_json_from_text

        # Випадок 1: обірваний через токени JSON
        truncated_json = '{\n  "suggested_grade": "8",\n  "level": "Достатній (7-9)",\n  "summary": "Опис професії.",\n  "feedback_comment": "Єгоре, молодець!",\n  "status": "success",\n  "ai_generated_percent":'
        res1 = extract_json_from_text(truncated_json)
        self.assertIsNotNone(res1)
        self.assertEqual(res1.get('suggested_grade'), '8')
        self.assertEqual(res1.get('feedback_comment'), 'Єгоре, молодець!')

        # Випадок 2: невалідне екранування лапок \'
        invalid_escape_json = r'{"suggested_grade": "7", "feedback_comment": "Богдане, ти добре виконав завдання (\'не знаю\')."}'
        res2 = extract_json_from_text(invalid_escape_json)
        self.assertIsNotNone(res2)
        self.assertEqual(res2.get('suggested_grade'), '7')
        self.assertIn('Богдане', res2.get('feedback_comment', ''))

    def test_extract_clean_comment_from_raw_json(self):
        from feed.utils import extract_clean_comment_from_raw_json

        raw_comment = '🤖 [Рекомендації та відгук ШІ]:\n{\n  "suggested_grade": "8",\n  "feedback_comment": "Чудова робота, продовжуй у тому ж дусі!",\n  "status": "success"\n}'
        cleaned = extract_clean_comment_from_raw_json(raw_comment)
        self.assertNotIn('{', cleaned)
        self.assertNotIn('suggested_grade', cleaned)
        self.assertIn('Чудова робота, продовжуй у тому ж дусі!', cleaned)
        self.assertTrue(cleaned.startswith('🤖 [Рекомендації та відгук ШІ]:'))

    def test_submission_comment_save_auto_cleans_json(self):
        from feed.models import ClassGroup, Assignment, Submission, SubmissionComment, Teacher
        user = User.objects.create_user(username='t_comment_clean', password='123')
        teacher = Teacher.objects.create(user=user, full_name='Вчитель')
        cg = ClassGroup.objects.create(name='7-А')
        asg = Assignment.objects.create(title='Завдання', teacher=teacher)
        asg.classes.add(cg)
        sub = Submission.objects.create(assignment=asg, class_group=cg, last_name='Шевченко', first_name='Тарас')

        # Створюємо коментар, куди помилково потрапив сирий JSON
        raw_json = '{\n  "suggested_grade": "10",\n  "feedback_comment": "Прекрасний результат, Тарасе!"\n}'
        comment = SubmissionComment.objects.create(
            submission=sub,
            author=user,
            text=f"🤖 [Рекомендації та відгук ШІ]:\n{raw_json}"
        )
        # Коментар повинен автоматично очиститись при збереженні
        self.assertNotIn('suggested_grade', comment.text)
        self.assertNotIn('{', comment.text)
        self.assertIn('Прекрасний результат, Тарасе!', comment.text)

    def test_submission_clean_feedback_for_student_never_returns_raw_json(self):
        from feed.models import ClassGroup, Assignment, Submission, Teacher
        user = User.objects.create_user(username='t_sub_clean', password='123')
        teacher = Teacher.objects.create(user=user, full_name='Вчитель')
        cg = ClassGroup.objects.create(name='8-А')
        asg = Assignment.objects.create(title='Завдання 2', teacher=teacher)
        asg.classes.add(cg)
        sub = Submission.objects.create(assignment=asg, class_group=cg, last_name='Франко', first_name='Іван')

        # Записуємо сирий JSON у ai_feedback
        sub.ai_feedback = '{\n  "suggested_grade": "9",\n  "feedback_comment": "Іване, робота дуже змістовна!"\n}'
        sub.save(update_fields=['ai_feedback'])

        clean = sub.get_clean_ai_feedback_for_student()
        self.assertNotIn('{', clean)
        self.assertNotIn('suggested_grade', clean)
        self.assertIn('Іване, робота дуже змістовна!', clean)


class AssignmentFileAIAndCoauthorTests(TestCase):
    """
    Тести для:
    1. Позначення файлу з умовою для ШІ при створенні, редагуванні та дублюванні завдання.
    2. Видобування зображень із файлів презентацій, таблиць та архівів.
    3. Автоматичного розпізнавання співавторів із коментаря учня та створення зв'язаної здачі.
    """

    def setUp(self):
        from feed.models import Subject
        self.user = User.objects.create_user(username='teacher_ai_test', password='password123', is_staff=True, is_superuser=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Інформатики')
        self.class_group = ClassGroup.objects.create(grade=9, letter='А', name='9-А')
        self.teacher.classes.add(self.class_group)
        self.subject = Subject.objects.create(name='Інформатика')
        self.teacher.subjects.add(self.subject)
        self.student1 = Student.objects.create(last_name='Коваленко', first_name='Данило', class_group=self.class_group)
        self.student2 = Student.objects.create(last_name='Мельник', first_name='Софія', class_group=self.class_group)

    def test_ai_task_file_designation_and_duplicate(self):
        from feed.models import Assignment, AssignmentFile
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.login(username='teacher_ai_test', password='password123')

        f1 = SimpleUploadedFile("task_description.docx", b"Task condition text in docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        f2 = SimpleUploadedFile("appendix.pdf", b"%PDF-1.4 additional materials", content_type="application/pdf")

        # 1. Створення завдання з позначенням task_description.docx як умови для ШІ
        resp = self.client.post(reverse('assignment_create'), {
            'subject': self.subject.id,
            'title': 'Практична робота з інформатики',
            'description': 'Опис практичної роботи',
            'classes': [self.class_group.id],
            'publish_choice': 'now',
            'files': [f1, f2],
            'ai_task_file': 'new:task_description.docx',
        })
        self.assertEqual(resp.status_code, 302)

        asg = Assignment.objects.get(title='Практична робота з інформатики')
        files = list(asg.files.all())
        self.assertEqual(len(files), 2)

        f_task = asg.files.get(original_name='task_description.docx')
        f_app = asg.files.get(original_name='appendix.pdf')
        self.assertTrue(f_task.is_task_source_for_ai)
        self.assertFalse(f_app.is_task_source_for_ai)

        # 2. Редагування завдання: змінюємо головну умову для ШІ на appendix.pdf
        resp_edit = self.client.post(reverse('assignment_edit', args=[asg.pk]), {
            'subject': self.subject.id,
            'title': 'Практична робота з інформатики (оновлено)',
            'classes': [self.class_group.id],
            'publish_choice': 'now',
            'ai_task_file': f'existing:{f_app.id}',
        })
        self.assertEqual(resp_edit.status_code, 302)

        f_task.refresh_from_db()
        f_app.refresh_from_db()
        self.assertFalse(f_task.is_task_source_for_ai)
        self.assertTrue(f_app.is_task_source_for_ai)

        # 3. Дублювання завдання: перевіряємо збереження is_task_source_for_ai
        resp_dup = self.client.post(reverse('assignment_duplicate', args=[asg.pk]), {
            'duplicate_title': 'Практична робота (копія)',
            'duplicate_classes': [self.class_group.id],
            'publish_choice': 'now',
        })
        self.assertEqual(resp_dup.status_code, 302)

        dup_asg = Assignment.objects.get(title='Практична робота (копія)')
        dup_app = dup_asg.files.get(original_name='appendix.pdf')
        dup_task = dup_asg.files.get(original_name='task_description.docx')
        self.assertTrue(dup_app.is_task_source_for_ai)
        self.assertFalse(dup_task.is_task_source_for_ai)

    def test_auto_bind_coauthors_from_student_comment(self):
        from feed.models import Assignment, Submission
        from django.core.files.uploadedfile import SimpleUploadedFile

        asg = Assignment.objects.create(
            subject=self.subject,
            title='Командний проєкт',
            teacher=self.teacher,
            status=Assignment.STATUS_PUBLISHED
        )
        asg.classes.add(self.class_group)

        dummy_file = SimpleUploadedFile("project.py", b"print('Hello from team')", content_type="text/plain")

        # Учень здає роботу і вказує співавтора в коментарі
        resp_sub = self.client.post(reverse('submit_assignment', args=[asg.id]), {
            'full_name': 'Коваленко Данило',
            'class_group': self.class_group.id,
            'comment_student': 'Виконували практичну разом з Мельник Софія, все протестували.',
            'files': [dummy_file],
        })
        self.assertEqual(resp_sub.status_code, 302)

        # Перевіряємо основну здачу
        sub1 = Submission.objects.get(assignment=asg, student=self.student1)
        self.assertTrue(sub1.is_group_work)
        self.assertIn('Коваленко Данило', sub1.group_authors)
        self.assertIn('Мельник Софія', sub1.group_authors)

        # Перевіряємо автоматично створену зв'язану здачу для Мельник Софії
        sub2 = Submission.objects.filter(assignment=asg, student=self.student2).first()
        self.assertIsNotNone(sub2)
        self.assertEqual(sub2.primary_submission, sub1)
        self.assertTrue(sub2.is_group_work)
        self.assertIn('Мельник Софія', sub2.group_authors)
        self.assertEqual(sub2.files.count(), 1)

    def test_extract_images_from_pptx_and_zip(self):
        import io
        import zipfile
        import tempfile
        import os
        from feed.gemini_service import extract_images_from_pptx, extract_images_from_zip, extract_images_from_xlsx

        # Створюємо фіктивний .pptx з вбудованим зображенням у ppt/media/
        with tempfile.NamedTemporaryFile(suffix='.pptx', delete=False) as tmp_pptx:
            pptx_path = tmp_pptx.name
            with zipfile.ZipFile(tmp_pptx, 'w') as z:
                z.writestr('ppt/slides/slide1.xml', '<xml>Slide 1</xml>')
                z.writestr('ppt/media/image1.png', b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest')

        try:
            extracted_pptx = extract_images_from_pptx(pptx_path)
            self.assertEqual(len(extracted_pptx), 1)
            self.assertEqual(extracted_pptx[0]['name'], 'image1.png')
            self.assertEqual(extracted_pptx[0]['mime_type'], 'image/png')
            self.assertTrue(len(extracted_pptx[0]['data']) > 0)
        finally:
            if os.path.exists(pptx_path):
                os.remove(pptx_path)

        # Створюємо фіктивний .zip з графічним файлом
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_zip:
            zip_path = tmp_zip.name
            with zipfile.ZipFile(tmp_zip, 'w') as z:
                z.writestr('solution.py', 'print(1)')
                z.writestr('screenshot.jpg', b'\xff\xd8\xff\xe0\x00\x10JFIFtest')

        try:
            extracted_zip = extract_images_from_zip(zip_path)
            self.assertEqual(len(extracted_zip), 1)
            self.assertEqual(extracted_zip[0]['name'], 'screenshot.jpg')
            self.assertEqual(extracted_zip[0]['mime_type'], 'image/jpeg')
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)

    def test_access_utils_mdb_and_accdb_parsing(self):
        """Тест утиліт обробки БД Microsoft Access (.mdb та .accdb) через mdbtools."""
        import tempfile
        from unittest.mock import patch, MagicMock
        from feed.access_utils import (
            _get_database_version_mdbtools,
            _get_entries_mdbtools,
            _get_query_sql_mdbtools,
            extract_access_text_for_ai
        )

        with tempfile.NamedTemporaryFile(suffix='.accdb', delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(b'\x00' * 100)

        try:
            # 1. Тест визначення версії БД
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=b'ACE14\n', stderr=b'')
                ver = _get_database_version_mdbtools(tmp_path)
                self.assertIn('Access 2010', ver)
                self.assertIn('ACE 14.0', ver)

                mock_run.return_value = MagicMock(returncode=0, stdout=b'JET4\n', stderr=b'')
                ver_jet = _get_database_version_mdbtools(tmp_path)
                self.assertIn('Access 2000-2003', ver_jet)

            # 2. Тест вилучення об'єктів (таблиці, запити, форми, звіти)
            with patch('subprocess.run') as mock_run:
                def side_effect(cmd, *args, **kwargs):
                    if 'mdb-tables' in cmd:
                        if '-ttable' in cmd or ('-t' in cmd and cmd[cmd.index('-t') + 1] == 'table'):
                            return MagicMock(returncode=0, stdout=b'Students\nGrades\n', stderr=b'')
                        elif '-tquery' in cmd or ('-t' in cmd and cmd[cmd.index('-t') + 1] == 'query'):
                            return MagicMock(returncode=0, stdout=b'TopStudents\n', stderr=b'')
                        elif '-tform' in cmd or ('-t' in cmd and cmd[cmd.index('-t') + 1] == 'form'):
                            return MagicMock(returncode=0, stdout=b'MainForm\n', stderr=b'')
                        elif '-treport' in cmd or ('-t' in cmd and cmd[cmd.index('-t') + 1] == 'report'):
                            return MagicMock(returncode=0, stdout=b'AnnualReport\n', stderr=b'')
                    elif 'mdb-queries' in cmd:
                        return MagicMock(returncode=0, stdout=b'SELECT * FROM Students WHERE Grade >= 10;\n', stderr=b'')
                    elif 'mdb-ver' in cmd:
                        return MagicMock(returncode=0, stdout=b'ACE16\n', stderr=b'')
                    elif 'mdb-export' in cmd:
                        return MagicMock(returncode=0, stdout=b'"id";"name"\n"1";"Ivan"\n"2";"Olena"\n', stderr=b'')
                    elif 'mdb-schema' in cmd:
                        return MagicMock(returncode=0, stdout=b'CREATE TABLE Students (id Long Integer, name Text (50));\n', stderr=b'')
                    return MagicMock(returncode=0, stdout=b'', stderr=b'')

                mock_run.side_effect = side_effect

                tables, err_tbl = _get_entries_mdbtools(tmp_path, 'table')
                self.assertIsNone(err_tbl)
                self.assertEqual(tables, ['Students', 'Grades'])

                queries, err_q = _get_entries_mdbtools(tmp_path, 'query')
                self.assertIsNone(err_q)
                self.assertEqual(queries, ['TopStudents'])

                sql = _get_query_sql_mdbtools(tmp_path, 'TopStudents')
                self.assertIn('SELECT * FROM Students', sql)

                ai_text = extract_access_text_for_ai(tmp_path)
                self.assertIn('Microsoft Access', ai_text)
                self.assertIn('Таблиць (Tables): 2', ai_text)
                self.assertIn('Запитів (Queries): 1', ai_text)
                self.assertIn('Екранних форм (Forms): 1', ai_text)
                self.assertIn('Звітів (Reports): 1', ai_text)
                self.assertIn('SELECT * FROM Students WHERE Grade >= 10', ai_text)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_duplicate_and_parser_support_access(self):
        """Тест інтеграції Access (.mdb та .accdb) у систему перевірки дублікатів та парсери документів."""
        import tempfile
        from unittest.mock import patch
        from feed.duplicate_detector import get_normalized_file_content
        from feed.document_parsers import extract_text_from_document

        with tempfile.NamedTemporaryFile(suffix='.mdb', delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(b'\x00' * 50)

        try:
            with patch('feed.access_utils.extract_access_text_for_ai', return_value='TABLE Students id name DATA 1 Ivan'):
                sim_text = get_normalized_file_content(tmp_path)
                self.assertIn('Students', sim_text)

                doc_text, success, _ = extract_text_from_document(tmp_path)
                self.assertTrue(success)
                self.assertIn('Students', doc_text)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @patch('feed.gemini_service._http_post_json')
    def test_ai_evaluation_unclear_task_and_choice_rule(self, mock_http_post):
        """Тест ШІ: якщо завдання не зрозуміло, виставляється 'Доопрацювати' та формується format_warning і unclear_task=True."""
        from .models import AISettings
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.api_key = 'fake-api-key'
        settings.is_enabled = True
        settings.save()

        # Мокаємо відповідь, коли ШІ не зміг визначити, яке саме завдання виконане
        mock_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "suggested_grade": "Доопрацювати",
                                    "level": "Початковий (1-3)",
                                    "summary": "Не зрозуміло, яке саме завдання виконане з умови вчителя.",
                                    "format_warning": "Не зрозуміло, яке саме завдання виконане.",
                                    "unclear_task": True,
                                    "strengths": [],
                                    "weaknesses": ["Не зрозуміло, яке завдання виконане"],
                                    "feedback_comment": "Вкажіть номер завдання у коментарі."
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_http_post.return_value = (200, mock_data, json.dumps(mock_data))

        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Тестове завдання з багатьма задачами',
            description='Виконайте будь-яке завдання на вибір з файлу (Завдання 1 або Завдання 2).'
        )
        asg.classes.add(self.class_group)

        sub = Submission.objects.create(
            assignment=asg,
            first_name='Михайло',
            last_name='Бондар',
            class_group=self.class_group,
            comment_student='Ось моя робота.'
        )

        res = evaluate_submission_with_gemini(sub)
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['suggested_grade'], 'Доопрацювати')
        self.assertTrue(res['unclear_task'])
        self.assertIn('Не зрозуміло, яке саме завдання виконане', res['format_warning'])

        sub.refresh_from_db()
        self.assertEqual(sub.ai_suggested_grade, 'Доопрацювати')

    @patch('feed.gemini_service.evaluate_submission_with_gemini')
    def test_student_ai_self_check_unclear_task_json_and_template(self, mock_eval):
        """Тест endpoint student_ai_self_check повертає unclear_task=True та відповідні попередження."""
        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Тестове завдання для самоперевірки',
            description='Опис завдання',
            status=Assignment.STATUS_PUBLISHED,
            allow_student_ai_check=True
        )
        asg.classes.add(self.class_group)

        sub = Submission.objects.create(
            assignment=asg,
            first_name='Софія',
            last_name='Мельник',
            class_group=self.class_group,
            comment_student='Здаю файл'
        )

        mock_eval.return_value = {
            'status': 'success',
            'suggested_grade': 'Доопрацювати',
            'level': 'Початковий (1-3)',
            'summary': 'Не зрозуміло, яке завдання виконане',
            'feedback_comment': 'Вкажіть номер виконаного завдання',
            'unclear_task': True,
            'format_warning': 'Не зрозуміло, яке завдання виконане. Будь ласка, вкажіть номер завдання у коментарі.',
            'is_traditional': True,
            'gr_results': []
        }

        # POST до student_ai_self_check
        resp = self.client.post(reverse('student_ai_self_check', args=[sub.id]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['grade'], 'Доопрацювати')
        self.assertTrue(data['unclear_task'])
        self.assertIn('Не зрозуміло, яке завдання виконане', data['format_warning'])

        # Перевіряємо, що в базі збережено
        sub.refresh_from_db()
        self.assertTrue(sub.student_ai_checked)
        self.assertEqual(sub.student_ai_grade, 'Доопрацювати')

        # Перевіряємо сторінку submit_success та submission_detail (шаблони містять попередження)
        detail_resp = self.client.get(reverse('submission_detail', args=[sub.id]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, 'Не зрозуміло, яке завдання виконане')

        succ_resp = self.client.get(reverse('submit_success', args=[asg.id]))
        self.assertEqual(succ_resp.status_code, 200)
        self.assertContains(succ_resp, 'Не зрозуміло, яке завдання виконане')

    @patch('feed.gemini_service.evaluate_submission_with_gemini')
    def test_student_ai_self_check_ai_detection_plagiarism_and_weaknesses(self, mock_eval):
        """Тест перевірки ШІ для учня: детекція ШІ, плагіат/дублікати, слабкі сторони та коментарі."""
        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Практична робота з інформатики',
            description='Створіть документ',
            status=Assignment.STATUS_PUBLISHED,
            allow_student_ai_check=True,
            allow_ai_usage=False
        )
        asg.classes.add(self.class_group)

        sub = Submission.objects.create(
            assignment=asg,
            first_name='Олександр',
            last_name='Коваленко',
            class_group=self.class_group,
            comment_student='Ось мій висновок: під час роботи було досліджено алгоритми сортування.'
        )

        mock_eval.return_value = {
            'status': 'success',
            'suggested_grade': '8',
            'level': 'Достатній (7-9)',
            'summary': 'Робота виконана непогано, але є ознаки генерації ШІ.',
            'feedback_comment': 'Порада: допишіть висновок у файлі.',
            'weaknesses': ['Немає власного висновку в документі', 'Пункт 3 виконано частково'],
            'strengths': ['Таблиця оформлена акуратно'],
            'ai_generated_detected': True,
            'ai_generated_percent': 85,
            'ai_generated_confidence': 'high',
            'ai_generated_details': 'Виявлено структуру та формулювання, характерні для ChatGPT.',
            'is_traditional': True,
            'gr_results': []
        }

        resp = self.client.post(reverse('student_ai_self_check', args=[sub.id]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['ai_generated_detected'])
        self.assertIsNone(data['ai_generated_percent'])  # Для учня відсоток приховано
        self.assertEqual(data['gr_results'], [])  # Оцінки за ГР для учня приховано
        self.assertFalse(data['allow_ai_usage'])
        self.assertIn('duplicate_info', data)
        self.assertEqual(len(data['weaknesses']), 2)
        self.assertEqual(len(data['strengths']), 1)

        sub.refresh_from_db()
        self.assertTrue(sub.ai_generated_detected)
        self.assertEqual(sub.ai_generated_percent, 85)  # Для вчителя в БД збережено повний відсоток
        self.assertEqual(sub.ai_generated_confidence, 'high')
        self.assertEqual(len(sub.get_student_ai_weaknesses_list()), 2)
        self.assertEqual(len(sub.get_student_ai_strengths_list()), 1)

        # Перевірка відображення на submission_detail
        detail_resp = self.client.get(reverse('submission_detail', args=[sub.id]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, 'У роботі виявлено ознаки використання штучного інтелекту')
        self.assertNotContains(detail_resp, '85%')  # Відсоток ШІ приховано для учня
        self.assertNotContains(detail_resp, 'Google Gemini')  # Згадування конкретно Google Gemini прибрано
        self.assertNotContains(detail_resp, 'Оцінки за групами результатів')  # Оцінки за ГР приховано
        self.assertContains(detail_resp, 'Що потрібно доробити, щоб покращити роботу (Зауваження ШІ):')
        self.assertContains(detail_resp, 'Немає власного висновку в документі')

        # Перевірка підказки щодо висновків на сторінці здачі роботи submit_assignment
        form_resp = self.client.get(reverse('submit_assignment', args=[asg.id]))
        self.assertEqual(form_resp.status_code, 200)
        self.assertContains(form_resp, 'висновки по своїй роботі')
        self.assertContains(form_resp, 'Висновки по роботі')
        self.assertNotContains(form_resp, 'Google Gemini')  # Замінено на загальне ШІ

    def test_submission_form_rejects_open_or_temp_office_files(self):
        """Тест відхилення відкритих/тимчасових службових файлів Word/Excel зі зрозумілим поясненням."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from feed.forms import SubmissionForm

        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Перевірка відкритих файлів',
            status=Assignment.STATUS_PUBLISHED
        )
        asg.classes.add(self.class_group)

        # 1. Спроба прикріпити службовий файл Word (~$Документ.docx)
        temp_word_file = SimpleUploadedFile("~$Практична_1.docx", b"office lock dummy data")
        form1 = SubmissionForm(
            data={'full_name': 'Іван Петренко', 'class_group': self.class_group.id},
            files={'files': [temp_word_file]},
            assignment=asg
        )
        self.assertFalse(form1.is_valid())
        err_text1 = form1.errors.as_text()
        self.assertIn("є тимчасовим службовим файлом", err_text1)
        self.assertIn("закрийте програму", err_text1)

        # 2. Спроба прикріпити 0-байтовий Word-файл (незбережений через відкриття)
        empty_docx = SimpleUploadedFile("Практична_1.docx", b"")
        form2 = SubmissionForm(
            data={'full_name': 'Іван Петренко', 'class_group': self.class_group.id},
            files={'files': [empty_docx]},
            assignment=asg
        )
        self.assertFalse(form2.is_valid())
        err_text2 = form2.errors.as_text()
        self.assertIn("порожній (0 байтів)", err_text2)
        self.assertIn("збережіть документ", err_text2)

        # 3. Спроба прикріпити службовий файл блокування MS Access нового формату (.laccdb)
        temp_access_new = SimpleUploadedFile("База_Даних_1.laccdb", b"access lock data")
        form3 = SubmissionForm(
            data={'full_name': 'Іван Петренко', 'class_group': self.class_group.id},
            files={'files': [temp_access_new]},
            assignment=asg
        )
        self.assertFalse(form3.is_valid())
        err_text3 = form3.errors.as_text()
        self.assertIn("службовим тимчасовим файлом блокування Microsoft Access", err_text3)
        self.assertIn(".accdb або .mdb", err_text3)

        # 4. Спроба прикріпити службовий файл блокування MS Access класичного формату (.ldb)
        temp_access_old = SimpleUploadedFile("Students.ldb", b"access old lock data")
        form4 = SubmissionForm(
            data={'full_name': 'Іван Петренко', 'class_group': self.class_group.id},
            files={'files': [temp_access_old]},
            assignment=asg
        )
        self.assertFalse(form4.is_valid())
        err_text4 = form4.errors.as_text()
        self.assertIn("службовим тимчасовим файлом блокування Microsoft Access", err_text4)
        self.assertIn(".accdb або .mdb", err_text4)

        # 5. Спроба прикріпити 0-байтовий файл MS Access (.accdb)
        empty_accdb = SimpleUploadedFile("Нова_База.accdb", b"")
        form5 = SubmissionForm(
            data={'full_name': 'Іван Петренко', 'class_group': self.class_group.id},
            files={'files': [empty_accdb]},
            assignment=asg
        )
        self.assertFalse(form5.is_valid())
        err_text5 = form5.errors.as_text()
        self.assertIn("порожній (0 байтів)", err_text5)
        self.assertIn("Microsoft Access", err_text5)

        # 6. Спроба прикріпити 0-байтовий файл MS Access старого формату (.mdb)
        empty_mdb = SimpleUploadedFile("Стара_База.mdb", b"")
        form6 = SubmissionForm(
            data={'full_name': 'Іван Петренко', 'class_group': self.class_group.id},
            files={'files': [empty_mdb]},
            assignment=asg
        )
        self.assertFalse(form6.is_valid())
        err_text6 = form6.errors.as_text()
        self.assertIn("порожній (0 байтів)", err_text6)
        self.assertIn("Microsoft Access", err_text6)

    def test_site_guide_modal_and_navbar_buttons(self):
        """Тест наявності кнопки інструкції у верхньому барі учня, в меню вчителя та модального вікна довідки з розділенням ролей."""
        # 1. Перевірка для учня/гостя (неавторизований перегляд)
        student_resp = self.client.get(reverse('index'))
        self.assertEqual(student_resp.status_code, 200)
        self.assertContains(student_resp, 'id="site-guide-btn"')
        self.assertContains(student_resp, 'nav-btn-guide')
        self.assertContains(student_resp, 'id="guide-modal"')
        # Учень має доступ ТІЛЬКИ до учнівського блоку інструкції
        self.assertContains(student_resp, 'id="guide-pane-student"')
        self.assertContains(student_resp, 'id="guide-category-index"')
        self.assertContains(student_resp, 'id="guide-category-submit-assignment"')
        self.assertContains(student_resp, '📍 Ви зараз на цій сторінці')
        # Для учня ЖОДНА вчительська категорія чи панель НЕ повинна рендеритися
        self.assertNotContains(student_resp, 'id="guide-pane-teacher"')
        self.assertNotContains(student_resp, 'guide-tab-btn-teacher')
        self.assertNotContains(student_resp, 'guide-category-teacher-dashboard')
        self.assertNotContains(student_resp, 'guide-category-teacher-settings')
        self.assertNotContains(student_resp, 'guide-category-teacher-ai-settings')
        self.assertNotContains(student_resp, 'guide-category-teacher-fileviewer')
        self.assertNotContains(student_resp, 'guide-category-teacher-submissions')
        self.assertNotContains(student_resp, 'guide-category-teacher-gradebook')
        self.assertNotContains(student_resp, 'guide-category-teacher-classes')
        self.assertNotContains(student_resp, 'guide-category-teacher-reschedule')
        self.assertNotContains(student_resp, 'guide-category-teacher-activity')
        self.assertNotContains(student_resp, 'guide-category-teacher-zip-import-export')

        # 2. Перевірка наявності модального вікна на сторінці здачі роботи
        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Завдання для довідки",
            description="Опис",
            status=Assignment.STATUS_PUBLISHED,
            due_date=timezone.localdate() + timezone.timedelta(days=2)
        )
        asg.classes.add(self.class_group)
        submit_page_resp = self.client.get(reverse('submit_assignment', args=[asg.pk]))
        self.assertEqual(submit_page_resp.status_code, 200)
        self.assertContains(submit_page_resp, 'id="site-guide-btn"')
        self.assertContains(submit_page_resp, 'id="guide-modal"')
        self.assertContains(submit_page_resp, 'id="guide-pane-student"')
        self.assertNotContains(submit_page_resp, 'id="guide-pane-teacher"')

        # 3. Перевірка для вчителя (сховано у меню профілю, є вкладки для вчителя та учня)
        self.client.force_login(self.user)
        teacher_resp = self.client.get(reverse('teacher_dashboard'))
        self.assertEqual(teacher_resp.status_code, 200)
        # Для вчителя окремої кнопки у барі немає (вона схована у випадаюче меню профілю)
        self.assertNotContains(teacher_resp, 'id="site-guide-btn"')
        # Але в меню профілю є виклик openGuideModal()
        self.assertContains(teacher_resp, 'openGuideModal()')
        self.assertContains(teacher_resp, 'Інструкція сайту')
        self.assertContains(teacher_resp, 'id="guide-modal"')
        # Вчитель має доступ до 10 вчительських блоків, перемикача вкладок та учнівського блоку
        self.assertContains(teacher_resp, 'id="guide-pane-teacher"')
        self.assertContains(teacher_resp, 'guide-tab-btn-teacher')
        self.assertContains(teacher_resp, 'guide-tab-btn-student')
        self.assertContains(teacher_resp, 'guide-category-teacher-dashboard')
        self.assertContains(teacher_resp, 'guide-category-teacher-settings')
        self.assertContains(teacher_resp, 'guide-category-teacher-ai-settings')
        self.assertContains(teacher_resp, 'guide-category-teacher-fileviewer')
        self.assertContains(teacher_resp, 'guide-category-teacher-submissions')
        self.assertContains(teacher_resp, 'guide-category-teacher-gradebook')
        self.assertContains(teacher_resp, 'guide-category-teacher-classes')
        self.assertContains(teacher_resp, 'guide-category-teacher-reschedule')
        self.assertContains(teacher_resp, 'guide-category-teacher-activity')
        self.assertContains(teacher_resp, 'guide-category-teacher-zip-import-export')
        self.assertContains(teacher_resp, 'id="guide-pane-student"')

    def test_calendar_badge_count_always_matches_filtered_feed(self):
        """Тест повної відповідності між бейджами календаря та видачею завдань при кліку на будь-яку дату."""
        from feed.views import get_calendar_context, _filter_assignments_by_lesson_date, get_visible_assignments
        from feed.models import AssignmentScheduleTarget
        from datetime import datetime, timedelta

        # Створюємо завдання з днем тижня у розкладі (без фіксованої target_date)
        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання розкладу середи',
            description='Опис уроку',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        asg.classes.add(self.class_group)
        # target_day_of_week = 3 (Середа)
        AssignmentScheduleTarget.objects.create(
            assignment=asg,
            class_group=self.class_group,
            target_day_of_week=3
        )

        cal = get_calendar_context()
        found_day = None
        for week in cal['weeks']:
            for day in week:
                if day['has_tasks']:
                    dt_str = day['date_str']
                    dt_val = datetime.strptime(dt_str, '%Y-%m-%d').date()
                    up, rest = get_visible_assignments()
                    up_f = _filter_assignments_by_lesson_date(up, dt_val)
                    rest_f = _filter_assignments_by_lesson_date(rest, dt_val)
                    total_filtered = up_f.count() + rest_f.count()
                    # Кількість у календарі повинна точно збігатися з кількістю відфільтрованих завдань
                    self.assertEqual(day['tasks_count'], total_filtered)
                    if asg.id in [a.id for a in list(up_f) + list(rest_f)]:
                        found_day = dt_str

        self.assertIsNotNone(found_day, "Завдання з днем тижня не з'явилося в календарі")

        # Перевіряємо завантаження сторінки через HTTP
        resp = self.client.get(f'/?date={found_day}')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Завдання розкладу середи')
        self.assertNotContains(resp, 'Завдань не знайдено на вибрану дату')

        resp_frag = self.client.get(f'/feed/fragment/?date={found_day}')
        self.assertEqual(resp_frag.status_code, 200)
        self.assertContains(resp_frag, 'Завдання розкладу середи')

class AICreativityAndCriteriaTests(TestCase):
    """
    Тести для:
    1. Налаштування креативності ШІ (temperature): збереження з крапкою і комою, clamping, temperature_dot.
    2. Індивідуальних критеріїв оцінювання завдання (custom_criteria) у моделі, формі, дублюванні.
    3. Розширеного промту детекції ШІ (зображення, хуманізатори/обхідники, політика дозволеності).
    4. Модального вікна перегляду критеріїв оцінювання для учнів на assignment_detail та submit_assignment.
    """

    def setUp(self):
        from feed.models import AISettings, Subject, Assignment, ClassGroup, Teacher, Student
        self.user = User.objects.create_user(username='teacher_criteria_test', password='password123', is_staff=True, is_superuser=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Олена Петрівна')
        self.class_group = ClassGroup.objects.create(grade=10, letter='Б', name='10-Б')
        self.teacher.classes.add(self.class_group)
        self.subject = Subject.objects.create(name='Історія України')
        self.teacher.subjects.add(self.subject)
        self.student = Student.objects.create(last_name='Шевченко', first_name='Тарас', class_group=self.class_group)

        self.ai_settings = AISettings.get_solo()
        self.ai_settings.api_key = 'fake-api-key-test'
        self.ai_settings.temperature = 0.2
        self.ai_settings.save()

    def test_temperature_dot_property_and_saving(self):
        """Перевірка властивості temperature_dot та збереження з комою чи крапкою."""
        from feed.models import AISettings
        settings = AISettings.get_solo()
        settings.temperature = 0.35
        self.assertEqual(settings.temperature_dot, '0.35')

        settings.temperature = 0.0
        self.assertEqual(settings.temperature_dot, '0.0')

        self.client.login(username='teacher_criteria_test', password='password123')

        # Збереження з крапкою
        resp1 = self.client.post(reverse('ai_settings'), {
            'action': 'save_ai_config',
            'api_key': 'test-key',
            'model_name': 'gemini-2.5-flash',
            'temperature': '0.65',
            'ai_detector_tolerance_percent': '30',
        })
        self.assertEqual(resp1.status_code, 302)
        settings.refresh_from_db()
        self.assertAlmostEqual(settings.temperature, 0.65, places=2)
        self.assertEqual(settings.temperature_dot, '0.65')

        # Збереження з комою (типово для локалізації uk_UA)
        resp2 = self.client.post(reverse('ai_settings'), {
            'action': 'save_ai_config',
            'api_key': 'test-key',
            'model_name': 'gemini-2.5-flash',
            'temperature': '0,85',
            'ai_detector_tolerance_percent': '25',
        })
        self.assertEqual(resp2.status_code, 302)
        settings.refresh_from_db()
        self.assertAlmostEqual(settings.temperature, 0.85, places=2)
        self.assertEqual(settings.temperature_dot, '0.85')

        # Clamping
        resp3 = self.client.post(reverse('ai_settings'), {
            'action': 'save_ai_config',
            'api_key': 'test-key',
            'temperature': '1.7',
        })
        settings.refresh_from_db()
        self.assertEqual(settings.temperature, 1.0)

    def test_custom_criteria_form_and_duplicate(self):
        """Перевірка збереження індивідуальних критеріїв завдання та їх копіювання при дублюванні."""
        from feed.models import Assignment
        self.client.login(username='teacher_criteria_test', password='password123')

        custom_text = "1. Хронологічна послідовність (до 5 б.)\n2. Причинно-наслідкові зв'язки (до 4 б.)\n3. Висновки (до 3 б.)"
        resp = self.client.post(reverse('assignment_create'), {
            'subject': self.subject.id,
            'title': 'Українська революція 1917-1921',
            'description': 'Опишіть головні етапи та події.',
            'classes': [self.class_group.id],
            'publish_choice': 'now',
            'custom_criteria': custom_text,
            'allow_ai_usage': '1',
        })
        self.assertEqual(resp.status_code, 302)

        asg = Assignment.objects.filter(title='Українська революція 1917-1921').first()
        self.assertIsNotNone(asg)
        self.assertEqual(asg.custom_criteria, custom_text)
        self.assertTrue(asg.allow_ai_usage)

        # Перевірка дублювання завдання
        dup_resp = self.client.post(reverse('assignment_duplicate', args=[asg.pk]), {
            'duplicate_title': 'Копія: Українська революція',
            'duplicate_classes[]': [self.class_group.id],
            'publish_option': 'now',
        })
        self.assertEqual(dup_resp.status_code, 302)

        dup_asg = Assignment.objects.filter(title='Копія: Українська революція').first()
        self.assertIsNotNone(dup_asg)
        self.assertEqual(dup_asg.custom_criteria, custom_text)
        self.assertTrue(dup_asg.allow_ai_usage)

    def test_ai_detector_prompt_instructions(self):
        """Перевірка формування промту: критерії, зображення, хуманізатори та політика ШІ."""
        from feed.models import Assignment, Submission, AISettings
        from feed.gemini_service import evaluate_submission_with_gemini
        from unittest.mock import patch
        import json

        # Переконуємось, що ШІ увімкнено та ключ встановлено
        ai_set = AISettings.get_solo()
        ai_set.api_key = 'fake-key-for-detector-test'
        ai_set.is_enabled = True
        ai_set.save()

        custom_text = "Індивідуальна розбаловка: аргументація - 6 б, джерела - 6 б."
        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Тестове завдання на критерії',
            description='Умова тестового завдання',
            custom_criteria=custom_text,
            allow_ai_usage=False,
            status=Assignment.STATUS_PUBLISHED,
        )
        asg.classes.add(self.class_group)

        sub = Submission.objects.create(
            assignment=asg,
            student=self.student,
            class_group=self.class_group,
            comment_student='Ось мій текст розв’язку.',
        )

        captured_payloads = []
        def fake_http_post(endpoint, payload, *args, **kwargs):
            captured_payloads.append(payload)
            fake_data = {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": json.dumps({
                                "suggested_grade": 9,
                                "level": "Достатній",
                                "summary": "Робота опрацьована",
                                "strengths": ["Гарна відповідь"],
                                "weaknesses": [],
                                "feedback_comment": "Добре виконано",
                                "ai_generated_percent": 10,
                                "ai_generated_detected": False,
                                "ai_generated_confidence": "none",
                                "ai_generated_details": None,
                                "gr_results": []
                            })
                        }]
                    }
                }]
            }
            return 200, fake_data, json.dumps(fake_data)

        with patch('feed.gemini_service._http_post_json', side_effect=fake_http_post):
            evaluate_submission_with_gemini(sub)

        self.assertTrue(len(captured_payloads) > 0)
        req_body = str(captured_payloads[0])

        # Перевірка наявності індивідуальних критеріїв
        self.assertIn("ІНДИВІДУАЛЬНІ КРИТЕРІЇ ОЦІНЮВАННЯ ВЧИТЕЛЯ", req_body)
        self.assertIn(custom_text, req_body)

        # Перевірка наявності інструкцій щодо зображень
        self.assertIn("АНАЛІЗ ПРИКРІПЛЕНИХ ЗОБРАЖЕНЬ ТА ГРАФІКИ", req_body)
        self.assertIn("Midjourney", req_body)

        # Перевірка інструкцій щодо сервісів обходу (humanizers)
        self.assertIn("Anti-AI Bypass", req_body)
        self.assertIn("QuillBot", req_body)
        self.assertIn("Undetectable AI", req_body)

        # Перевірка суворої політики заборони ШІ
        self.assertIn("СУВОРО ЗАБОРОНЕНО використання ШІ", req_body)

        # Перевірка інструкцій щодо сервісів обходу (humanizers)
        self.assertIn("Anti-AI Bypass", req_body)
        self.assertIn("QuillBot", req_body)
        self.assertIn("Undetectable AI", req_body)

        # Перевірка суворої політики заборони ШІ
        self.assertIn("СУВОРО ЗАБОРОНЕНО використання ШІ", req_body)

    def test_criteria_modal_rendered_in_views(self):
        """Перевірка рендерингу кнопки та модального вікна критеріїв на сторінках завдання."""
        from feed.models import Assignment
        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання з критеріями для перегляду',
            description='Вказівки вчителя до завдання',
            custom_criteria='1. Теза (4 б.)\n2. Аргументи (4 б.)\n3. Висновок (4 б.)',
            allow_ai_usage=True,
            status=Assignment.STATUS_PUBLISHED,
        )
        asg.classes.add(self.class_group)

        # 1. Сторінка assignment_detail
        resp = self.client.get(reverse('assignment_detail', args=[asg.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'openCriteriaModal()')
        self.assertContains(resp, 'Критерії оцінювання')
        self.assertContains(resp, 'id="criteria-modal"')
        self.assertContains(resp, '1. Теза (4 б.)')
        self.assertContains(resp, 'ШІ Дозволено')

        # 2. Сторінка submit_assignment
        resp_sub = self.client.get(reverse('submit_assignment', args=[asg.pk]))
        self.assertEqual(resp_sub.status_code, 200)
        self.assertContains(resp_sub, 'openCriteriaModal()')
        self.assertContains(resp_sub, 'id="criteria-modal"')


class TeacherMaterialsAITaskRecognitionTests(TestCase):
    """
    Тести перевірки розпізнавання завдань у презентаціях (.pptx, .ppt, .odp),
    PDF-файлах та зображеннях, доданих вчителем, для запобігання помилковому
    відправленню робіт учнів на доопрацювання (unclear_task).
    """
    def setUp(self):
        self.teacher_user = User.objects.create_user(username='teach_mat_user_rec', password='password123')
        self.teacher = Teacher.objects.create(
            user=self.teacher_user,
            full_name='Олена Петренко'
        )
        self.subject, _ = Subject.objects.get_or_create(name='Інформатика', defaults={'icon': '💻', 'color': '#3b82f6'})
        self.class_group, _ = ClassGroup.objects.get_or_create(name='9-Б')

        ai_settings = AISettings.get_solo()
        ai_settings.api_key = 'test-key-teacher-mat'
        ai_settings.is_enabled = True
        ai_settings.save()

    def test_pptx_extraction_in_document_parsers(self):
        """Перевіряємо, що extract_text_from_document витягує текст і структуру з .pptx."""
        import io
        import pptx
        from feed.document_parsers import extract_text_from_document
        from feed.duplicate_detector import get_normalized_file_content

        prs = pptx.Presentation()
        s1 = prs.slides.add_slide(prs.slide_layouts[0])
        s1.shapes.title.text = 'Урок 10. Алгоритми'
        s1.placeholders[1].text = 'Теорія алгоритмів та їх види'

        s2 = prs.slides.add_slide(prs.slide_layouts[1])
        s2.shapes.title.text = 'Домашнє завдання'
        s2.placeholders[1].text = '1. Дати означення алгоритму.\n2. Навести приклад лінійного алгоритму.'

        stream = io.BytesIO()
        prs.save(stream)
        stream.seek(0)

        text, ok, err = extract_text_from_document(stream, 'presentation.pptx')
        self.assertTrue(ok)
        self.assertIn('Слайд 1/2', text)
        self.assertIn('Урок 10. Алгоритми', text)
        self.assertIn('Слайд 2/2', text)
        self.assertIn('Домашнє завдання', text)
        self.assertIn('Дати означення алгоритму', text)

    @patch('feed.gemini_service._http_post_json')
    def test_ai_recognizes_task_in_teacher_presentation(self, mock_http_post):
        """
        Перевіряємо, що коли вчитель прикріплює презентацію і дає короткий опис ('Опрацювати презентацію'),
        ШІ отримує структуру слайдів, чіткі інструкції знайти завдання на слайдах,
        а також PDF прев'ю в inline_media, і НЕ вважає завдання незрозумілим.
        """
        import io
        import pptx
        from django.conf import settings as django_settings
        from feed.gemini_service import evaluate_submission_with_gemini

        prs = pptx.Presentation()
        s1 = prs.slides.add_slide(prs.slide_layouts[0])
        s1.shapes.title.text = 'Презентація до теми Екологія'
        s2 = prs.slides.add_slide(prs.slide_layouts[1])
        s2.shapes.title.text = 'Практичне завдання'
        s2.placeholders[1].text = 'Скласти список трьох факторів забруднення.'

        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Екологічні проблеми сьогодення',
            description='Опрацювати матеріали презентації.',
            status=Assignment.STATUS_PUBLISHED
        )
        asg.classes.add(self.class_group)

        # Зберігаємо pptx як файл завдання вчителя
        pptx_io = io.BytesIO()
        prs.save(pptx_io)
        pptx_file = SimpleUploadedFile('ecology_lesson.pptx', pptx_io.getvalue(), content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation')
        af = AssignmentFile.objects.create(
            assignment=asg,
            file=pptx_file,
            original_name='ecology_lesson.pptx',
            is_task_source_for_ai=False  # вчитель НЕ ставив радіокнопку
        )

        # Створюємо фейковий файл прев'ю PDF, наче його згенерував сайт
        previews_dir = os.path.join(django_settings.MEDIA_ROOT, 'previews')
        os.makedirs(previews_dir, exist_ok=True)
        fake_pdf_path = os.path.join(previews_dir, f"{af.id}.pdf")
        with open(fake_pdf_path, 'wb') as f_pdf:
            f_pdf.write(b"%PDF-1.4 fake presentation preview")

        try:
            # Учень здав відповідь на завдання зі слайду 2
            sub = Submission.objects.create(
                assignment=asg,
                first_name='Олександр',
                last_name='Іваненко',
                class_group=self.class_group,
                comment_student="Виконав практичне завдання з презентації: 1. Викиди заводів 2. Автотранспорт 3. Побутове сміття."
            )

            mock_res = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps({
                                        "suggested_grade": "11",
                                        "level": "Високий (10-12)",
                                        "unclear_task": False,
                                        "format_warning": None,
                                        "summary": "Завдання з презентації виконано повно і змістовно.",
                                        "strengths": ["Вказано всі три фактори"],
                                        "weaknesses": [],
                                        "feedback_comment": "Чудово засвоєно матеріал зі слайдів презентації."
                                    })
                                }
                            ]
                        }
                    }
                ]
            }
            mock_http_post.return_value = (200, mock_res, json.dumps(mock_res))

            eval_res = evaluate_submission_with_gemini(sub)
            self.assertEqual(eval_res['status'], 'success')
            self.assertEqual(eval_res['suggested_grade'], '11')
            self.assertFalse(eval_res['unclear_task'])

            # Перевіряємо payload запиту до Gemini
            call_args = mock_http_post.call_args[0]
            payload = call_args[1]
            user_text = payload['contents'][0]['parts'][0]['text']

            # Перевіряємо, що слайди з презентації передані в промт
            self.assertIn('ecology_lesson.pptx', user_text)
            self.assertIn('Практичне завдання', user_text)
            self.assertIn('Скласти список трьох факторів забруднення', user_text)

            # Перевіряємо вказівки для ШІ шукати завдання на слайдах
            self.assertIn('ПОШУК ЗАВДАННЯ В ЦИХ МАТЕРІАЛАХ', user_text)
            self.assertIn('ФІНАЛЬНІ/ОСТАННІ СЛАЙДИ', user_text)
            self.assertIn('ЗАБОРОНА ПОМИЛКОВОГО «ДОПРАЦЮВАННЯ»', user_text)

            # Перевіряємо, що згенероване PDF прев'ю презентації передано до inlineData (Vision)
            inline_parts = [p['inlineData'] for p in payload['contents'][0]['parts'] if 'inlineData' in p]
            self.assertTrue(any(ip.get('mimeType') == 'application/pdf' for ip in inline_parts))

        finally:
            if os.path.exists(fake_pdf_path):
                os.remove(fake_pdf_path)

    @patch('feed.gemini_service._http_post_json')
    def test_ai_evaluation_teacher_pdf_and_image_inline_media(self, mock_http_post):
        """
        Перевіряємо, що прямий PDF та зображення вчителя (навіть без is_task_source_for_ai)
        завжди потрапляють до inline_media для Gemini Vision.
        """
        from feed.gemini_service import evaluate_submission_with_gemini

        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання з малюнком та PDF',
            description='Виконати завдання з прикріплених матеріалів.',
            status=Assignment.STATUS_PUBLISHED
        )
        asg.classes.add(self.class_group)

        # Додаємо PDF файл вчителя
        pdf_file = SimpleUploadedFile('task_sheet.pdf', b'%PDF-1.4 test task sheet', content_type='application/pdf')
        AssignmentFile.objects.create(
            assignment=asg,
            file=pdf_file,
            original_name='task_sheet.pdf',
            is_task_source_for_ai=False
        )

        # Додаємо зображення вчителя (наприклад фото завдання)
        img_bytes = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        img_file = SimpleUploadedFile('task_photo.png', img_bytes, content_type='image/png')
        AssignmentFile.objects.create(
            assignment=asg,
            file=img_file,
            original_name='task_photo.png',
            is_task_source_for_ai=False
        )

        sub = Submission.objects.create(
            assignment=asg,
            first_name='Анна',
            last_name='Коваль',
            class_group=self.class_group,
            comment_student="Розв'язала завдання з фото."
        )

        mock_res = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "suggested_grade": "10",
                                    "level": "Високий (10-12)",
                                    "unclear_task": False,
                                    "format_warning": None,
                                    "summary": "Завдання успішно виконано.",
                                    "strengths": ["Правильний розв'язок"],
                                    "weaknesses": [],
                                    "feedback_comment": "Все вірно."
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_http_post.return_value = (200, mock_res, json.dumps(mock_res))

        eval_res = evaluate_submission_with_gemini(sub)
        self.assertEqual(eval_res['status'], 'success')

        call_args = mock_http_post.call_args[0]
        payload = call_args[1]
        inline_parts = [p['inlineData'] for p in payload['contents'][0]['parts'] if 'inlineData' in p]
        mime_types = [ip.get('mimeType') for ip in inline_parts]

        self.assertIn('application/pdf', mime_types)
        self.assertIn('image/png', mime_types)


class AICriteriaGeneratorTests(TestCase):
    """
    Тести для ШІ-помічника формування критеріїв оцінювання за 12-бальною шкалою
    на основі опису вчителя звичайною мовою.
    """
    def setUp(self):
        self.client = Client()
        self.teacher_user = User.objects.create_user(
            username='crit_teacher',
            password='password123',
            is_staff=True,
            is_superuser=True
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user,
            full_name='Іван Мельник'
        )
        self.subject, _ = Subject.objects.get_or_create(name='Фізика', defaults={'icon': '⚡', 'color': '#ef4444'})
        self.class_group, _ = ClassGroup.objects.get_or_create(name='8-А')

        ai_settings = AISettings.get_solo()
        ai_settings.api_key = 'test-key-criteria-gen'
        ai_settings.is_enabled = True
        ai_settings.save()

    @patch('feed.gemini_service._http_post_json')
    def test_generate_criteria_service_success(self, mock_http_post):
        """Перевіряємо генерацію критеріїв через generate_criteria_with_gemini."""
        from feed.gemini_service import generate_criteria_with_gemini

        mock_reply = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    "Критерії оцінювання (12 балів):\n"
                                    "• 10-12 балів (Високий рівень): Розв'язано всі 3 задачі, записано 'Дано', використано правильні формули, дано змістовне пояснення.\n"
                                    "• 7-9 балів (Достатній рівень): Розв'язано 2-3 задачі з незначними арифметичними похибками.\n"
                                    "• 4-6 балів (Середній рівень): Частковий розв'язок однієї-двох задач, є формули.\n"
                                    "• 1-3 бали (Початковий рівень): Тільки спроба запису умови без розв'язку."
                                )
                            }
                        ]
                    }
                }
            ]
        }
        mock_http_post.return_value = (200, mock_reply, json.dumps(mock_reply))

        res = generate_criteria_with_gemini(
            teacher_notes="Хочу повний розв'язок 3 задач з формулами. За саму відповідь без формул - не вище 7 балів.",
            assignment_title="Закони Ньютона",
            assignment_description="Розв'язати задачі 1, 2, 3 на сторінці 45.",
            subject_name="Фізика",
            class_group_name="8-А"
        )

        self.assertEqual(res['status'], 'success')
        self.assertIn('10-12 балів', res['criteria'])
        self.assertIn('Високий рівень', res['criteria'])

        # Перевіряємо сформований промпт
        call_args = mock_http_post.call_args[0]
        payload = call_args[1]
        sent_prompt = payload['contents'][0]['parts'][0]['text']
        self.assertIn("Закони Ньютона", sent_prompt)
        self.assertIn("Фізика", sent_prompt)
        self.assertIn("Хочу повний розв'язок 3 задач", sent_prompt)
        self.assertIn("12-БАЛЬНОЮ ШКАЛОЮ", sent_prompt)

    def test_generate_criteria_service_when_disabled(self):
        """Перевіряємо коректну помилку, коли модуль ШІ вимкнено."""
        from feed.gemini_service import generate_criteria_with_gemini
        ai_settings = AISettings.get_solo()
        ai_settings.is_enabled = False
        ai_settings.save()

        res = generate_criteria_with_gemini(teacher_notes="Вимоги до есе")
        self.assertEqual(res['status'], 'error')
        self.assertIn('вимкнено', res['message'])

    @patch('feed.gemini_service._http_post_json')
    def test_teacher_generate_assignment_criteria_view_ajax(self, mock_http_post):
        """Перевіряємо AJAX-ендпоінт виклику генерації критеріїв вчителем."""
        mock_reply = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "1. Точність розрахунків — до 6 балів.\n2. Графік — до 4 балів.\n3. Висновок — до 2 балів."}
                        ]
                    }
                }
            ]
        }
        mock_http_post.return_value = (200, mock_reply, json.dumps(mock_reply))

        self.client.force_login(self.teacher_user)
        url = reverse('teacher_generate_assignment_criteria')

        resp = self.client.post(
            url,
            data=json.dumps({
                'teacher_notes': 'Потрібні розрахунки і графік',
                'assignment_title': 'Лабораторна робота №2',
                'assignment_description': 'Побудувати графік залежності швидкості від часу',
                'subject_name': 'Фізика'
            }),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('Точність розрахунків', data['criteria'])

    def test_teacher_generate_assignment_criteria_view_unauthorized(self):
        """Перевіряємо захист від неавторизованого доступу."""
        anon_client = Client()
        url = reverse('teacher_generate_assignment_criteria')
        resp = anon_client.post(url, data={'teacher_notes': 'тест'})
        self.assertEqual(resp.status_code, 302)  # Редирект на логін


class AssignmentNoSubmissionRequiredTests(TestCase):
    """
    Тести для режиму завдань без обов'язкової здачі робіт (no_submission_required):
    - Створення та редагування завдання з прапорцем
    - Приховування кнопок здачі на сторінці завдання та у стрічці
    - Захист в'юхи submit_assignment (перенаправлення з повідомленням)
    - Збереження прапорця при дублюванні завдання
    """
    def setUp(self):
        self.client = Client()
        self.teacher_user = User.objects.create_user(
            username='oral_teacher',
            password='password123',
            is_staff=True,
            is_superuser=True
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user,
            full_name='Оксана Петрівна'
        )
        self.subject, _ = Subject.objects.get_or_create(name='Історія України', defaults={'icon': '📜', 'color': '#3b82f6'})
        self.class_group, _ = ClassGroup.objects.get_or_create(name='9-Б')

    def test_assignment_model_default_value(self):
        """За замовчуванням no_submission_required дорівнює False."""
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Письмова робота',
            description='Написати есе',
            status=Assignment.STATUS_PUBLISHED
        )
        self.assertFalse(assignment.no_submission_required)

    def test_assignment_create_with_no_submission_required(self):
        """Створення завдання з прапорцем 'Не вимагає здачі робіт'."""
        self.client.force_login(self.teacher_user)
        url = reverse('assignment_create')
        data = {
            'subject': self.subject.pk,
            'title': 'Читати параграф 15 (усно)',
            'description': 'Прочитати параграф 15, переглянути презентацію, підготуватися до усного опитування.',
            'classes': [self.class_group.pk],
            'no_submission_required': '1',
            'publish_choice': 'now',
        }
        resp = self.client.post(url, data=data, follow=True)
        self.assertEqual(resp.status_code, 200)

        created = Assignment.objects.filter(title='Читати параграф 15 (усно)').first()
        self.assertIsNotNone(created)
        self.assertTrue(created.no_submission_required)

    def test_assignment_detail_view_hides_submit_button_when_no_submission_required(self):
        """На сторінці завдання для учня відсутні кнопки здачі та відображається плашка 'Без здачі'."""
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Усне опрацювання теми',
            description='Опрацювати матеріал без надсилання файлів.',
            no_submission_required=True,
            status=Assignment.STATUS_PUBLISHED
        )
        assignment.classes.add(self.class_group)

        # Перегляд анонімним користувачем (учень)
        url = reverse('assignment_detail', args=[assignment.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode('utf-8')
        # Кнопка 'Здати роботу зараз' / 'Здати роботу' не повинна бути доступна учню
        self.assertNotIn('Здати роботу зараз', content)
        self.assertNotIn('Готові здати виконане завдання?', content)
        # Має бути інформаційний бейдж / пояснення
        self.assertIn('Без здачі', content)
        self.assertIn('не вимагає здачі робіт', content)

    def test_assignment_detail_view_shows_submit_button_when_standard(self):
        """Для звичайного завдання кнопка здачі показується."""
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Звичайне домашнє завдання',
            description='Розв язати вправи в зошиті.',
            no_submission_required=False,
            status=Assignment.STATUS_PUBLISHED
        )
        assignment.classes.add(self.class_group)

        url = reverse('assignment_detail', args=[assignment.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode('utf-8')
        self.assertIn('Здати роботу', content)

    def test_submit_assignment_redirects_when_no_submission_required(self):
        """Спроба відкрити форму здачі для завдання без здачі перенаправляє на сторінку завдання."""
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Тільки для читання',
            description='Прочитати текст.',
            no_submission_required=True,
            status=Assignment.STATUS_PUBLISHED
        )

        url = reverse('submit_assignment', args=[assignment.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('assignment_detail', args=[assignment.pk]), resp.url)

    def test_duplicate_preserves_no_submission_required(self):
        """При дублюванні завдання прапорець no_submission_required зберігається."""
        self.client.force_login(self.teacher_user)
        original = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Оригінал без здачі',
            description='Усний матеріал',
            no_submission_required=True,
            status=Assignment.STATUS_PUBLISHED
        )
        original.classes.add(self.class_group)

        url = reverse('assignment_duplicate', args=[original.pk])
        resp = self.client.post(url, data={
            'duplicate_title': 'Копія без здачі',
            'duplicate_classes': [self.class_group.pk],
            'publish_now': '1'
        })
        self.assertIn(resp.status_code, [200, 302])

        dup = Assignment.objects.filter(title='Копія без здачі').first()
        self.assertIsNotNone(dup)
        self.assertTrue(dup.no_submission_required)


class SanitizeHtmlAndAssignmentFormattingTests(TestCase):
    """Тести для перевірки збереження багаторядкового тексту, форматування та очищення темних інлайн-стилів."""

    def test_sanitize_html_preserves_first_line_with_br(self):
        """Перевірка, що перший рядок тексту не зникає при використанні <br>."""
        from feed.utils import sanitize_html
        raw = "Перша стрічка завдання<br>Друга стрічка завдання"
        cleaned = sanitize_html(raw)
        self.assertIn("Перша стрічка завдання", cleaned)
        self.assertIn("Друга стрічка завдання", cleaned)

    def test_sanitize_html_preserves_leading_text_before_bold(self):
        """Перевірка, що весь текст до жирного шрифту не зникає."""
        from feed.utils import sanitize_html
        raw = "Зробити вправи 1-5 <b>Обов'язково</b> здати до п'ятниці"
        cleaned = sanitize_html(raw)
        self.assertIn("Зробити вправи 1-5", cleaned)
        self.assertIn("<b>Обов'язково</b>", cleaned)
        self.assertIn("здати до п'ятниці", cleaned)

    def test_sanitize_html_cleans_dark_inline_colors(self):
        """Перевірка, що інлайн-кольори, які зливаються в темній темі, очищаються."""
        from feed.utils import sanitize_html
        raw = '<span style="color: rgb(0, 0, 0); background-color: #ffffff; font-weight: bold;">Текст завдання</span>'
        cleaned = sanitize_html(raw)
        self.assertNotIn("rgb(0, 0, 0)", cleaned)
        self.assertNotIn("#ffffff", cleaned)
        self.assertIn("Текст завдання", cleaned)
        self.assertIn("font-weight: bold", cleaned)

    def test_assignment_get_formatted_description_preserves_lines(self):
        """Перевірка, що get_formatted_description зберігає всі рядки опису завдання."""
        assignment = Assignment(
            title="Тестове завдання",
            description="Рядок номер один<br>Рядок номер два <strong>важливо</strong>"
        )
        formatted = assignment.get_formatted_description()
        self.assertIn("Рядок номер один", formatted)
        self.assertIn("Рядок номер два", formatted)
        self.assertIn("<strong>важливо</strong>", formatted)


class ExcelChartExtractionTests(TestCase):
    """
    Тести для розпізнавання, видобування метаданих та візуального рендерингу
    вбудованих діаграм/графіків у таблицях Excel (.xlsx, .xls, .ods) для ШІ Gemini.
    """

    def setUp(self):
        import tempfile
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_excel_charts_bar_chart(self):
        """Перевіряємо видобування стовпчастої діаграми з книги Excel."""
        import openpyxl
        from openpyxl.chart import BarChart, Reference
        from feed.gemini_service import parse_excel_charts

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Продажі"
        ws.append(["Місяць", "Кількість"])
        ws.append(["Січень", 10])
        ws.append(["Лютий", 25])
        ws.append(["Березень", 30])

        chart = BarChart()
        chart.type = "col"
        chart.title = "Продажі за квартал"
        chart.y_axis.title = "Штуки"
        chart.x_axis.title = "Місяці"

        data = Reference(ws, min_col=2, min_row=1, max_row=4)
        cats = Reference(ws, min_col=1, min_row=2, max_row=4)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        ws.add_chart(chart, "E2")

        file_path = os.path.join(self.temp_dir.name, "test_bar.xlsx")
        wb.save(file_path)

        charts = parse_excel_charts(file_path)
        self.assertEqual(len(charts), 1)
        c = charts[0]
        self.assertIn("стовпчаста", c['type'].lower())
        self.assertEqual(c['title'], "Продажі за квартал")
        self.assertEqual(c['sheet_name'], "Продажі")
        self.assertEqual(c['anchor'], "E2")
        self.assertIn("Місяці", c['axis_titles'])
        self.assertIn("Штуки", c['axis_titles'])
        self.assertTrue(len(c['series']) > 0)
        self.assertTrue(c['has_legend'])

    def test_parse_excel_charts_pie_chart(self):
        """Перевіряємо видобування кругової секторної діаграми."""
        import openpyxl
        from openpyxl.chart import PieChart, Reference
        from feed.gemini_service import parse_excel_charts

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Бюджет"
        ws.append(["Категорія", "Сума"])
        ws.append(["Оренда", 5000])
        ws.append(["Продукти", 3000])
        ws.append(["Транспорт", 1200])

        chart = PieChart()
        chart.title = "Структура витрат"
        data = Reference(ws, min_col=2, min_row=1, max_row=4)
        labels = Reference(ws, min_col=1, min_row=2, max_row=4)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(labels)
        ws.add_chart(chart, "D2")

        file_path = os.path.join(self.temp_dir.name, "test_pie.xlsx")
        wb.save(file_path)

        charts = parse_excel_charts(file_path)
        self.assertEqual(len(charts), 1)
        c = charts[0]
        self.assertIn("кругова", c['type'].lower())
        self.assertEqual(c['title'], "Структура витрат")
        self.assertEqual(c['sheet_name'], "Бюджет")
        self.assertEqual(c['anchor'], "D2")

    def test_extract_text_from_excel_includes_charts_summary(self):
        """Перевіряємо, що extract_text_from_excel включає структурований блок опису діаграм."""
        import openpyxl
        from openpyxl.chart import BarChart, Reference
        from feed.gemini_service import extract_text_from_excel

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Дані"
        ws.append(["Рік", "Прибуток"])
        ws.append(["2023", 100])
        ws.append(["2024", 200])

        chart = BarChart()
        chart.title = "Динаміка прибутку"
        data = Reference(ws, min_col=2, min_row=1, max_row=3)
        chart.add_data(data, titles_from_data=True)
        ws.add_chart(chart, "C1")

        file_path = os.path.join(self.temp_dir.name, "test_summary.xlsx")
        wb.save(file_path)

        text = extract_text_from_excel(file_path)
        self.assertIn("ВИЯВЛЕНІ ВБУДОВАНІ ДІАГРАМИ ТА ГРАФІКИ У ФАЙЛІ EXCEL", text)
        self.assertIn("Динаміка прибутку", text)
        self.assertIn("Дані", text)

    def test_extract_images_from_xlsx_visual_rendering(self):
        """Перевіряємо візуальний рендеринг сторінок таблиці з графіками у зображення PNG для ШІ."""
        import openpyxl
        import shutil
        from openpyxl.chart import BarChart, Reference
        from feed.gemini_service import extract_images_from_xlsx

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Графік"
        ws.append(["X", "Y"])
        ws.append([1, 10])
        ws.append([2, 20])

        chart = BarChart()
        chart.title = "Тестовий графік"
        data = Reference(ws, min_col=2, min_row=1, max_row=3)
        chart.add_data(data, titles_from_data=True)
        ws.add_chart(chart, "C1")

        file_path = os.path.join(self.temp_dir.name, "test_render.xlsx")
        wb.save(file_path)

        images = extract_images_from_xlsx(file_path)
        if shutil.which('libreoffice') and shutil.which('pdftoppm'):
            self.assertTrue(len(images) > 0)
            self.assertEqual(images[0]['mime_type'], 'image/png')
            self.assertTrue(images[0]['size_kb'] > 0)
            self.assertTrue(len(images[0]['data']) > 0)


class AssignmentFilesPreviewOptimizationTests(TestCase):
    """
    Тести для перевірки миттєвого завантаження деталей завдання з файлами
    (без блокування запиту учня на важких конвертаціях) та фонового розігріву прев'ю.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='teacher_prev', password='password123', is_staff=True, is_superuser=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Іванов Іван')
        self.class_group = ClassGroup.objects.create(grade=10, letter='Б', name='10-Б')
        self.teacher.classes.add(self.class_group)
        self.subject = Subject.objects.create(name='Фізика', icon='⚛️', color='#3b82f6')
        self.teacher.subjects.add(self.subject)

        self.assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Урок 1: Закони Ньютона',
            description='Ознайомтеся з прикріпленою презентацією.',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        self.assignment.classes.add(self.class_group)

        self.test_file = SimpleUploadedFile(
            "physics_lesson.pptx",
            b"PK\x03\x04fake_presentation_content",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        self.assignment_file = AssignmentFile.objects.create(
            assignment=self.assignment,
            file=self.test_file,
            original_name="physics_lesson.pptx"
        )
        self._cleanup_previews()
        self.client = Client()

    def tearDown(self):
        self._cleanup_previews()

    def _cleanup_previews(self):
        from django.conf import settings
        import shutil
        if hasattr(self, 'assignment_file') and self.assignment_file and self.assignment_file.id:
            cdir = os.path.join(settings.MEDIA_ROOT, 'previews', str(self.assignment_file.id))
            if os.path.exists(cdir):
                shutil.rmtree(cdir, ignore_errors=True)
            cpdf = os.path.join(settings.MEDIA_ROOT, 'previews', f"{self.assignment_file.id}.pdf")
            if os.path.exists(cpdf):
                try:
                    os.remove(cpdf)
                except Exception:
                    pass

    def test_assignment_detail_non_blocking_instant_load(self):
        """Сторінка деталей завдання відкривається миттєво (200 OK) і не зависає на першому відкритті."""
        response = self.client.get(reverse('assignment_detail', args=[self.assignment.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'physics_lesson.pptx')
        self.assertContains(response, 'Урок 1: Закони Ньютона')
        # Коли слайди ще не згенеровані, показується елемент фонового завантаження та перемикач
        self.assertContains(response, 'Підготовка інтерактивного перегляду слайдів')

    def test_get_presentation_slides_non_blocking_flag(self):
        """get_presentation_slides з wait_if_missing=False повертає пусті списки миттєво без блокування."""
        from feed.views import get_presentation_slides
        slide_urls, pdf_url = get_presentation_slides(self.assignment_file.id, self.assignment_file.file.path, wait_if_missing=False)
        self.assertEqual(slide_urls, [])
        self.assertIsNone(pdf_url)

    def test_get_pdf_preview_url_non_blocking_flag(self):
        """get_pdf_preview_url з wait_if_missing=False повертає None миттєво без блокування."""
        from feed.views import get_pdf_preview_url
        pdf_url = get_pdf_preview_url(self.assignment_file, wait_if_missing=False)
        self.assertIsNone(pdf_url)

    def test_copy_preview_cache_instant_duplication(self):
        """При дублюванні завдання вже згенеровані файли прев'ю копіюються миттєво без повторної конвертації."""
        from django.conf import settings
        from feed.views import _copy_preview_cache

        previews_root = os.path.join(settings.MEDIA_ROOT, 'previews')
        os.makedirs(previews_root, exist_ok=True)

        old_file_id = 99991
        new_file_id = 99992

        # Створюємо фіктивний кеш для old_file_id
        old_pdf = os.path.join(previews_root, f"{old_file_id}.pdf")
        with open(old_pdf, 'w') as f:
            f.write("%PDF-1.4 test")

        old_dir = os.path.join(previews_root, str(old_file_id))
        os.makedirs(old_dir, exist_ok=True)
        with open(os.path.join(old_dir, 'presentation.pdf'), 'w') as f:
            f.write("%PDF-1.4 pres")
        with open(os.path.join(old_dir, 'slide-1.jpg'), 'w') as f:
            f.write("fake_jpg")

        try:
            _copy_preview_cache(old_file_id, new_file_id)

            new_pdf = os.path.join(previews_root, f"{new_file_id}.pdf")
            self.assertTrue(os.path.exists(new_pdf))

            new_dir = os.path.join(previews_root, str(new_file_id))
            self.assertTrue(os.path.exists(new_dir))
            self.assertTrue(os.path.exists(os.path.join(new_dir, 'presentation.pdf')))
            self.assertTrue(os.path.exists(os.path.join(new_dir, 'slide-1.jpg')))
        finally:
            # Очищуємо тимчасові тестові файли
            import shutil
            for fid in [old_file_id, new_file_id]:
                p = os.path.join(previews_root, f"{fid}.pdf")
                if os.path.exists(p):
                    os.remove(p)
                d = os.path.join(previews_root, str(fid))
                if os.path.exists(d):
                    shutil.rmtree(d, ignore_errors=True)

    def test_prewarm_assignment_files_preview_queues_safely(self):
        """Функція prewarm_assignment_files_preview безпечно чергує обробку файлів завдання."""
        from feed.views import prewarm_assignment_files_preview
        # Не викидає жодних винятків і запускає фоновий потік
        prewarm_assignment_files_preview(self.assignment)


class ExcelLegacyXlsSupportTests(TestCase):
    """
    Тести для підтримки застарілого бінарного формату Excel 97-2003 (.xls):
    - Конвертація в HTML через xlrd без помилки openpyxl
    - Вилучення тексту для ШІ Gemini
    - Парсер документів для критеріїв МОН
    - Інлайн-перегляд у представленні завдання
    """

    def setUp(self):
        import tempfile
        import subprocess
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.temp_dir.name, "test_table.csv")
        with open(self.csv_path, "w", encoding="utf-8") as f:
            f.write("Учень,Бал,Рівень\nІваненко,11,Високий\nПетренко,8,Достатній\n")

        self.xls_path = os.path.join(self.temp_dir.name, "test_table.xls")
        # Конвертуємо CSV в справжній бінарний .xls через LibreOffice
        res = subprocess.run(
            ['libreoffice', '--headless', '--convert-to', 'xls', self.csv_path, '--outdir', self.temp_dir.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if res.returncode != 0 or not os.path.exists(self.xls_path):
            # Якщо LibreOffice зберіг з іншим іменем або помилка:
            gen_files = glob.glob(os.path.join(self.temp_dir.name, "*.xls"))
            if gen_files:
                self.xls_path = gen_files[0]

        self.user = User.objects.create_user(username='teacher_xls', password='password123', is_staff=True, is_superuser=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Інформатики')
        self.class_group = ClassGroup.objects.create(grade=9, letter='А', name='9-А')
        self.teacher.classes.add(self.class_group)
        self.subject = Subject.objects.create(name='Інформатика', icon='💻', color='#10b981')
        self.teacher.subjects.add(self.subject)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_convert_xlsx_to_html_with_xls_file(self):
        """Перевіряємо, що convert_xlsx_to_html успішно парсить .xls без openpyxl помилки."""
        from feed.utils import convert_xlsx_to_html
        if not os.path.exists(self.xls_path):
            self.skipTest("LibreOffice did not generate .xls file")

        html_content, error_msg = convert_xlsx_to_html(self.xls_path)
        self.assertIsNone(error_msg)
        self.assertTrue(len(html_content) > 0)
        self.assertIn("excel-table", html_content)
        self.assertIn("Іваненко", html_content)
        self.assertIn("Петренко", html_content)
        # Перевірка що немає повідомлення про несумісність openpyxl
        self.assertNotIn("openpyxl does not support", html_content)

    def test_extract_text_from_excel_xls(self):
        """Перевіряємо вилучення тексту для ШІ Gemini зі старого .xls файлу."""
        from feed.gemini_service import extract_text_from_excel
        if not os.path.exists(self.xls_path):
            self.skipTest("LibreOffice did not generate .xls file")

        text = extract_text_from_excel(self.xls_path)
        self.assertIn("Іваненко", text)
        self.assertIn("Петренко", text)
        self.assertNotIn("Помилка читання Excel таблиці", text)

    def test_document_parser_extract_from_excel_xls(self):
        """Перевіряємо вилучення тексту парсером критеріїв МОН з бінарного .xls."""
        from feed.document_parsers import _extract_from_excel
        if not os.path.exists(self.xls_path):
            self.skipTest("LibreOffice did not generate .xls file")

        with open(self.xls_path, "rb") as f:
            file_bytes = f.read()

        parsed_text, ok, err = _extract_from_excel(file_bytes)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertIn("Іваненко", parsed_text)
        self.assertIn("Петренко", parsed_text)

    def test_assignment_detail_view_renders_xls_attachment(self):
        """Перевіряємо, що сторінка завдання з прикріпленим .xls відкривається вчителю без попередження про помилку."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from feed.models import Assignment, AssignmentFile

        if not os.path.exists(self.xls_path):
            self.skipTest("LibreOffice did not generate .xls file")

        assignment = Assignment.objects.create(
            title="Таблиці Excel 97-2003",
            description="Практична робота з електронними таблицями",
            teacher=self.teacher,
            subject=self.subject,
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        assignment.classes.add(self.class_group)

        with open(self.xls_path, "rb") as f:
            xls_data = f.read()

        af = AssignmentFile.objects.create(
            assignment=assignment,
            file=SimpleUploadedFile("tablytsya.xls", xls_data, content_type="application/vnd.ms-excel")
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('assignment_detail', args=[assignment.id]))
        self.assertEqual(response.status_code, 200)

        # Переконуємося, що в контексті є прев'ю без помилки
        files = response.context['files_with_preview']
        self.assertEqual(len(files), 1)
        f_info = files[0]
        self.assertIsNone(f_info['error_preview'])
        self.assertIsNotNone(f_info['html_preview'])
        self.assertIn("excel-table", f_info['html_preview'])
        self.assertIn("Іваненко", f_info['html_preview'])


class MultiProviderAndFailoverAITests(TestCase):
    """Тести для підтримки багатьох провайдерів ШІ, резервного API та автоматичного failover."""

    def setUp(self):
        self.user = User.objects.create_user(username='teacher_ai_test', password='password123', is_staff=True, is_superuser=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Оцінювач Тестовий')
        self.class_group = ClassGroup.objects.create(grade=10, letter='Б', name='10-Б')
        self.teacher.classes.add(self.class_group)
        self.subject = Subject.objects.create(name='Фізика', icon='⚛️', color='#10b981')
        self.teacher.subjects.add(self.subject)

        self.assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Закони Ньютона',
            description='Поясніть перший закон Ньютона та наведіть приклади.',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        self.assignment.classes.add(self.class_group)

        self.submission = Submission.objects.create(
            assignment=self.assignment,
            first_name='Микола',
            last_name='Петренко',
            class_group=self.class_group,
            teacher=self.teacher,
            comment_student='Перший закон Ньютона — закон інерції. Приклади: рух автомобіля з вимкненим двигуном.'
        )

        self.client = Client()
        self.client.force_login(self.user)

    def test_ai_settings_config_helpers(self):
        """Перевірка методів get_active_config, get_backup_config та has_backup_configured."""
        settings = AISettings.get_solo()
        settings.ai_provider = 'openai'
        settings.api_key = 'sk-main-key'
        settings.model_name = 'gpt-4o'
        settings.custom_api_url = ''

        settings.backup_ai_provider = 'deepseek'
        settings.backup_api_key = 'sk-backup-key'
        settings.backup_model_name = 'deepseek-chat'
        settings.backup_custom_api_url = ''

        settings.active_api_type = 'primary'
        settings.auto_failover_enabled = True
        settings.save()

        self.assertTrue(settings.has_backup_configured())

        # Перевірка для active_api_type == 'primary'
        prov, key, model, url, is_b = settings.get_active_config()
        self.assertEqual(prov, 'openai')
        self.assertEqual(key, 'sk-main-key')
        self.assertEqual(model, 'gpt-4o')
        self.assertFalse(is_b)

        b_prov, b_key, b_model, b_url = settings.get_backup_config()
        self.assertEqual(b_prov, 'deepseek')
        self.assertEqual(b_key, 'sk-backup-key')
        self.assertEqual(b_model, 'deepseek-chat')

        # Перемикаємо на 'backup'
        settings.active_api_type = 'backup'
        settings.save()

        prov, key, model, url, is_b = settings.get_active_config()
        self.assertEqual(prov, 'deepseek')
        self.assertEqual(key, 'sk-backup-key')
        self.assertTrue(is_b)

        b_prov, b_key, b_model, b_url = settings.get_backup_config()
        self.assertEqual(b_prov, 'openai')
        self.assertEqual(b_key, 'sk-main-key')

    def test_save_ai_config_multi_provider_view(self):
        """Збереження налаштувань основного та резервного API через форму POST."""
        post_data = {
            'action': 'save_ai_config',
            'is_enabled': '1',
            'ai_provider': 'deepseek',
            'api_key': 'sk-deepseek-main',
            'model_name': 'deepseek-chat',
            'custom_api_url': '',
            'backup_ai_provider': 'groq',
            'backup_api_key': 'gsk-groq-backup',
            'backup_model_name': 'llama-3.3-70b-versatile',
            'backup_custom_api_url': '',
            'active_api_type': 'primary',
            'auto_failover_enabled': '1',
            'temperature': '0.3',
            'ai_detector_tolerance_percent': '20',
            'system_prompt': DEFAULT_NUS_SYSTEM_PROMPT
        }

        response = self.client.post(reverse('teacher_settings'), post_data)
        self.assertEqual(response.status_code, 302)

        settings = AISettings.get_solo()
        self.assertTrue(settings.is_enabled)
        self.assertEqual(settings.ai_provider, 'deepseek')
        self.assertEqual(settings.api_key, 'sk-deepseek-main')
        self.assertEqual(settings.backup_ai_provider, 'groq')
        self.assertEqual(settings.backup_api_key, 'gsk-groq-backup')
        self.assertEqual(settings.backup_model_name, 'llama-3.3-70b-versatile')
        self.assertTrue(settings.auto_failover_enabled)
        self.assertEqual(settings.active_api_type, 'primary')

    def test_switch_active_api_action(self):
        """Ручне перемикання активного API між основним та резервним."""
        settings = AISettings.get_solo()
        settings.active_api_type = 'primary'
        settings.save()

        # Виклик дії перемикання
        response = self.client.post(reverse('teacher_settings'), {'action': 'switch_active_api'})
        self.assertEqual(response.status_code, 302)

        settings.refresh_from_db()
        self.assertEqual(settings.active_api_type, 'backup')

        # Повторне перемикання назад
        response = self.client.post(reverse('teacher_settings'), {'action': 'switch_active_api'})
        self.assertEqual(response.status_code, 302)

        settings.refresh_from_db()
        self.assertEqual(settings.active_api_type, 'primary')

    @patch('feed.gemini_service._http_post_json')
    def test_test_ai_connection_gemini_and_openai(self, mock_http):
        """Тестування перевірки з'єднання для Gemini та OpenAI-сумісних провайдерів."""
        from .gemini_service import test_ai_connection

        # 1. Gemini успіх
        mock_http.return_value = (200, {
            'candidates': [{'content': {'parts': [{'text': 'ПРИВІТ СВІТ'}]}}]
        }, 'OK')

        ok, msg, model, prov = test_ai_connection(provider='gemini', api_key='AIzaTest', model_name='gemini-2.5-flash')
        self.assertTrue(ok)
        self.assertEqual(prov, 'gemini')
        # gemini-2.5-flash is redirected to gemini-3.8-flash by clean_model_name
        self.assertEqual(model, 'gemini-3.8-flash')

        # 2. OpenAI успіх
        mock_http.return_value = (200, {
            'choices': [{'message': {'content': 'HELLO OPENAI'}}]
        }, 'OK')

        ok, msg, model, prov = test_ai_connection(provider='openai', api_key='sk-test', model_name='gpt-4o-mini')
        self.assertTrue(ok)
        self.assertEqual(prov, 'openai')
        self.assertEqual(model, 'gpt-4o-mini')

        # 3. Перевірка AJAX ендпоінту api_test_gemini_connection
        resp = self.client.post(reverse('api_test_gemini_connection'), {
            'provider': 'openai',
            'api_key': 'sk-test',
            'model_name': 'gpt-4o-mini'
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['provider'], 'openai')

    @patch('feed.gemini_service.call_ai_api')
    def test_evaluate_submission_failover_on_429(self, mock_call):
        """Якщо основний API повертає 429, система автоматично перемикається на резервний API."""
        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.ai_provider = 'gemini'
        settings.api_key = 'AIzaPrimary'
        settings.model_name = 'gemini-2.5-flash'
        settings.saved_models_list = json.dumps([{"name": "gemini-2.5-flash", "priority": 1, "enabled": True}])
        settings.backup_ai_provider = 'deepseek'
        settings.backup_api_key = 'sk-backup-deepseek'
        settings.backup_model_name = 'deepseek-chat'
        settings.active_api_type = 'primary'
        settings.auto_failover_enabled = True
        settings.save()

        # Перший виклик (gemini): 429 Rate Limit
        # Другий виклик (failover to deepseek): 200 OK з валідним JSON
        valid_json = json.dumps({
            "suggested_grade": "10",
            "level": "Високий",
            "summary": "Відмінне пояснення першого закону Ньютона.",
            "strengths": ["Чітке визначення", "Вдалі приклади"],
            "weaknesses": [],
            "feedback_comment": "Чудова робота!"
        })

        # Спроба 1 (Gemini): 429
        # Спроба 1 повтор (Gemini): 429
        # Спроба 2 (Failover to DeepSeek): 200 OK з валідним JSON
        mock_call.side_effect = [
            (429, None, "Rate limit exceeded", {}),  # Gemini attempt 0
            (429, None, "Rate limit exceeded", {}),  # Gemini retry attempt 1
            (200, valid_json, None, {})             # DeepSeek (Failover)
        ]

        result = evaluate_submission_with_gemini(self.submission, ai_settings=settings)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['suggested_grade'], '10')

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.ai_status, 'success')
        self.assertEqual(self.submission.ai_suggested_grade, '10')
        self.assertIn('Deepseek', self.submission.ai_model_used)

        # Перевіряємо запис дати та причини failover
        settings.refresh_from_db()
        self.assertIsNotNone(settings.last_failover_at)
        self.assertIn('Deepseek', settings.last_failover_reason)


class QuestionAnswerMappingTests(TestCase):
    """
    Тести для розпізнавання запитань вчителя, автоматичного зіставлення
    та підстановки відповідей учня (навіть якщо учень не переписав самі запитання),
    захисту від хибного висновку «жодної відповіді не дано», та рекомендацій
    щодо оформлення у форматі «питання-відповідь».
    """

    def setUp(self):
        from feed.middleware import set_has_admin
        set_has_admin(True)
        self.user = User.objects.create_user(username='teacher_qa', password='password123')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Олена Сергіївна')
        self.class_group = ClassGroup.objects.create(name='9-Б')
        self.subject = Subject.objects.create(name='Біологія')
        self.assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Біосфера та її межі',
            description=(
                "Опрацювати параграф 18. Дати письмові відповіді на запитання:\n"
                "1. Що таке біосфера та хто є основоположником вчення про біосферу?\n"
                "2. Які межі біосфери в атмосфері, гідросфері та літосфері?\n"
                "3. Яке значення має озоновий екран для життя на Землі?"
            ),
            status=Assignment.STATUS_PUBLISHED
        )
        self.assignment.classes.add(self.class_group)

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.ai_provider = 'gemini'
        settings.model_name = 'gemini-2.5-flash'
        settings.save()

    def test_extract_task_questions(self):
        from feed.gemini_service import extract_task_questions

        text = (
            "Опрацювати матеріал.\n"
            "1. Що таке фотосинтез?\n"
            "2) Які умови необхідні для процесу?\n"
            "№3. Які кінцеві продукти утворюються?\n"
        )
        questions = extract_task_questions(text)
        self.assertEqual(len(questions), 3)
        self.assertIn("Що таке фотосинтез?", questions[0])
        self.assertIn("Які умови необхідні", questions[1])
        self.assertIn("Які кінцеві продукти", questions[2])

        # Тест запитань без нумерації, але зі знаком '?'
        unnum_text = "Яка температура кипіння води? Чому лід плаває на поверхні?"
        questions_unnum = extract_task_questions(unnum_text)
        self.assertEqual(len(questions_unnum), 2)
        self.assertIn("Яка температура кипіння води?", questions_unnum)
        self.assertIn("Чому лід плаває на поверхні?", questions_unnum)

    def test_extract_student_answers(self):
        from feed.gemini_service import extract_student_answers

        student_text = (
            "1. Біосфера - це оболонка планети, заселена живими організмами. В.І. Вернадський.\n"
            "2) Верхня межа до 20-25 км, нижня межа в літосфері до 3-4 км.\n"
        )
        answers = extract_student_answers(student_text)
        self.assertIn(1, answers)
        self.assertIn(2, answers)
        self.assertNotIn(3, answers)
        self.assertIn("В.І. Вернадський", answers[1])
        self.assertIn("20-25 км", answers[2])

    def test_check_student_omitted_questions(self):
        from feed.gemini_service import check_student_omitted_questions

        task_qs = [
            "1. Що таке біосфера та хто є основоположником вчення про біосферу?",
            "2. Які межі біосфери в атмосфері, гідросфері та літосфері?",
            "3. Яке значення має озоновий екран для життя на Землі?"
        ]

        # Учень здав лише відповіді
        student_answers_only = (
            "1. Оболонка Землі, заселена живими організмами. Вернадський.\n"
            "2. Охоплює нижню частину атмосфери та гідросферу."
        )
        self.assertTrue(check_student_omitted_questions(task_qs, student_answers_only))

        # Учень скопіював запитання вчителя разом з відповідями
        student_with_qs = (
            "1. Що таке біосфера та хто є основоположником вчення про біосферу?\n"
            "Відповідь: Оболонка Землі, Вернадський.\n"
            "2. Які межі біосфери в атмосфері, гідросфері та літосфері?\n"
            "Відповідь: Нижня частина атмосфери."
        )
        self.assertFalse(check_student_omitted_questions(task_qs, student_with_qs))

    def test_build_question_answer_mapping(self):
        from feed.gemini_service import build_question_answer_mapping

        task_qs = [
            "1. Що таке біосфера?",
            "2. Які межі біосфери?",
            "3. Чим важливий озоновий шар?"
        ]
        student_text = (
            "1. Оболонка планети.\n"
            "2. Атмосфера, гідросфера, літосфера."
        )

        mapping, omitted, answered_count, total_qs = build_question_answer_mapping(task_qs, student_text)
        self.assertTrue(omitted)
        self.assertEqual(answered_count, 2)
        self.assertEqual(total_qs, 3)
        self.assertIn("СИСТЕМНЕ ЗІСТАВЛЕННЯ", mapping)
        self.assertIn("ПІДСТАВЛЕНА ВІДПОВІДЬ УЧНЯ №1: «Оболонка планети.»", mapping)
        self.assertIn("ПІДСТАВЛЕНА ВІДПОВІДЬ УЧНЯ №2: «Атмосфера, гідросфера, літосфера.»", mapping)
        self.assertIn("ВІДПОВІДЬ УЧНЯ: [Відповідь не виявлена за номером або учень пропустив це запитання]", mapping)
        self.assertIn("СУВОРО ТА БЕЗАПЕЛЯЦІЙНО ЗАБОРОНЕНО писати, що «жодної відповіді не дано»", mapping)
        self.assertIn("формат «питання-відповідь»", mapping)

    @patch('feed.gemini_service.call_ai_api')
    def test_evaluate_submission_maps_answers_and_prevents_false_no_answers_claim(self, mock_call):
        from feed.gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.ai_provider = 'gemini'
        settings.model_name = 'gemini-2.5-flash'
        settings.save()

        # Учень здав відповіді на запитання 1 та 2 (без тексту самих запитань)
        student_answer = (
            "1. Біосфера — оболонка Землі, заселена живими істотами. Вчення створив Володимир Вернадський.\n"
            "2. В атмосфері сягає до 20 км, гідросфера повністю, літосфера — до 3 км."
        )
        submission = Submission.objects.create(
            assignment=self.assignment,
            class_group=self.class_group,
            last_name='Коваленко',
            first_name='Андрій',
            comment_student=student_answer
        )

        # Моделюємо ситуацію, коли ШІ помилково стверджує «жодної відповіді не дано»
        # та встановлює статус 'Доопрацювати'
        mock_raw_json = json.dumps({
            "suggested_grade": "Доопрацювати",
            "level": "Початковий",
            "unclear_task": True,
            "format_warning": "Не зрозуміло, яке саме завдання виконане.",
            "summary": "Жодної відповіді не дано на поставлені запитання вчителя.",
            "strengths": ["Старанність при здачі"],
            "weaknesses": [
                "Не зрозуміло, яке саме завдання виконане",
                "Жодної відповіді не дано"
            ],
            "feedback_comment": "У роботі не надано жодної відповіді на запитання вчителя. Здайте роботу повторно.",
            "status": "success"
        })
        mock_call.return_value = (200, mock_raw_json, None, {})

        result = evaluate_submission_with_gemini(submission)

        # 1. Перевіряємо, що в промт до ШІ було передано системне зіставлення запитань та відповідей
        full_prompt = mock_call.call_args.kwargs.get('prompt_text', '')
        self.assertIn("СИСТЕМНЕ ЗІСТАВЛЕННЯ", full_prompt)
        self.assertIn("ПІДСТАВЛЕНА ВІДПОВІДЬ УЧНЯ №1", full_prompt)
        self.assertIn("ПІДСТАВЛЕНА ВІДПОВІДЬ УЧНЯ №2", full_prompt)
        self.assertIn("СУВОРО ТА БЕЗАПЕЛЯЦІЙНО ЗАБОРОНЕНО писати, що «жодної відповіді не дано»", full_prompt)

        # 2. Перевіряємо пост-обробку:
        # - unclear_task знято (стало False)
        # - оцінка перерахована (не 'Доопрацювати')
        # - хибне твердження «жодної відповіді не дано» виправлено
        # - додано пораду щодо формату «питання-відповідь»
        self.assertEqual(result['status'], 'success')
        self.assertFalse(result['unclear_task'])
        self.assertNotEqual(result['suggested_grade'], 'Доопрацювати')
        self.assertIn(result['suggested_grade'], ['6', '7', '8', '9', '10'])

        # Перевірка очищення від фрази «жодної відповіді не дано»
        self.assertNotIn("жодної відповіді не дано", result['summary'].lower())
        self.assertNotIn("не надано жодної відповіді", result['feedback_comment'].lower())

        # Перевірка наявності поради про формат «питання-відповідь»
        has_advice_weaknesses = any('питання-відповідь' in w.lower() for w in result['weaknesses'])
        self.assertTrue(has_advice_weaknesses)
        self.assertIn('питання-відповідь', result['feedback_comment'].lower())

    def test_ppt_binary_extraction_and_viewer_fallback(self):
        """Тест видобування тексту з .ppt файлу та відсутності помилки Package not found у переглядачі."""
        from .document_parsers import _extract_from_ppt
        from .utils import convert_pptx_to_html

        # Симулюємо бінарний вміст PPT з UTF-16LE українським текстом
        ukr_text = "Завдання: Дослідження села Диканька. Знайти в інтернеті факти про історію та річку Ворскла."
        raw_ppt_bytes = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 512 + ukr_text.encode('utf-16le')

        # 1. Тест вилучення тексту
        text, ok, err = _extract_from_ppt(raw_ppt_bytes)
        self.assertTrue(ok)
        self.assertIn("Диканька", text)
        self.assertIn("Ворскла", text)

        # 2. Тест переглядача: збережемо у тимчасовий .ppt файл і викликаємо convert_pptx_to_html
        with tempfile.NamedTemporaryFile(suffix='.ppt', delete=False) as tmp_ppt:
            tmp_ppt.write(raw_ppt_bytes)
            tmp_ppt_path = tmp_ppt.name

        try:
            html_out, err_out = convert_pptx_to_html(tmp_ppt_path)
            self.assertIsNone(err_out)
            self.assertNotIn("Package not found", html_out)
            self.assertIn("Диканька", html_out)
            self.assertIn("pptx-slide-card", html_out)
        finally:
            if os.path.exists(tmp_ppt_path):
                os.remove(tmp_ppt_path)

    @patch('feed.gemini_service.call_ai_api')
    def test_ai_evaluation_internet_search_and_local_lore_task(self, mock_call):
        """Тест: дослідницькі роботи з пошуку інформації в інтернеті про населені пункти не відхиляються з unclear_task."""
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.ai_provider = 'gemini'
        settings.model_name = 'gemini-2.5-flash'
        settings.save()

        # Завдання вчителя на пошук в інтернеті
        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Дослідження: моє рідне місто або село',
            description='Знайдіть в інтернеті інформацію про ваш населений пункт (село або місто): історія, розташування, цікаві факти. Оформіть повідомлення.'
        )
        asg.classes.add(self.class_group)

        # Учень здав змістовний текст про Чернігів
        research_text = (
            "Чернігів — одне з найдавніших міст України, адміністративний центр Чернігівської області. "
            "Місто розташоване на півночі України на річці Десна. Вперше згадується у літописі в 907 році. "
            "Серед головних визначних пам'яток — Спасо-Преображенський та Борисоглібський собори, "
            "Антонієві печери та Троїцько-Іллінський монастир. Населення становить близько 285 тисяч осіб."
        )

        submission = Submission.objects.create(
            assignment=asg,
            class_group=self.class_group,
            first_name='Тарас',
            last_name='Шевченко',
            file=SimpleUploadedFile('дослідження_чернігів.txt', research_text.encode('utf-8'), content_type='text/plain'),
            comment_student='Ось моє повідомлення про місто Чернігів.'
        )

        # Симулюємо ситуацію, коли ШІ через нерозуміння теми намагався повернути "Доопрацювати" та "а що це таке?"
        ai_mock_reply = json.dumps({
            "suggested_grade": "Доопрацювати",
            "level": "Початковий (1-3)",
            "unclear_task": True,
            "format_warning": "Не зрозуміло, яке саме завдання виконане. В умові не було Чернігова.",
            "summary": "А що це таке? Незрозуміло, що це за місто, адже в умові вчителя його немає.",
            "strengths": ["Наведено детальний опис"],
            "weaknesses": ["А що це таке? Чому написано про Чернігів? Не зрозуміло, яке саме завдання виконане"],
            "feedback_comment": "Що це за місто? В умові завдання немає Чернігова, тому роботу повернено на доопрацювання.",
            "ai_generated_percent": 0,
            "ai_generated_detected": False,
            "ai_generated_confidence": "none",
            "ai_generated_details": None
        }, ensure_ascii=False)

        mock_call.return_value = (200, ai_mock_reply, None, {})

        result = evaluate_submission_with_gemini(submission)

        # Перевіряємо, що промт містить критичні правила щодо пошукових завдань в інтернеті
        full_prompt = mock_call.call_args.kwargs.get('prompt_text', '')
        self.assertIn("ДОСЛІДНИЦЬКІ, ПОШУКОВІ ЗАВДАННЯ", full_prompt)
        self.assertIn("інформації в інтернеті", full_prompt)

        # Перевіряємо пост-обробку:
        # 1. unclear_task знято (стало False)
        self.assertFalse(result['unclear_task'])
        # 2. Оцінку виправлено з 'Доопрацювати' на високу оцінку (10)
        self.assertEqual(result['suggested_grade'], '10')
        self.assertIn('Високий', result['level'])
        # 3. format_warning очищено від неправдивого зауваження
        self.assertEqual(result.get('format_warning', ''), '')
        # 4. Некоректні фрази "а що це таке" вичищено
        self.assertNotIn("а що це таке", result['summary'].lower())
        self.assertNotIn("а що це таке", result['feedback_comment'].lower())
        self.assertFalse(any("а що це таке" in w.lower() for w in result['weaknesses']))

    @patch('feed.gemini_service.call_ai_api')
    def test_reject_blank_teacher_practical_template_without_answers(self, mock_call):
        """Перевірка, що здача бланку/інструкції практичної роботи вчителя без відповідей блокується і отримує 'Доопрацювати', а не 7 балів."""
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.save()

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Практична робота: Створення презентації",
            description="Виконайте практичну роботу:\n1. Створіть презентацію\n2. Додайте слайди"
        )
        assignment.classes.add(self.class_group)

        # Текст бланку вчителя з вказівками та ходом роботи БЕЗ відповідей учня
        teacher_template_text = (
            "Практична робота № 4\n"
            "Тема: Створення презентацій у середовищі PowerPoint\n"
            "Мета: Навчитися додавати слайди, об'єкти та налаштовувати дизайн.\n"
            "Обладнання: ПК, програма PowerPoint.\n"
            "Хід роботи:\n"
            "1. Відкрийте програму PowerPoint на своєму комп'ютері.\n"
            "2. Створіть нову порожню презентацію з трьома слайдами.\n"
            "3. Налаштуйте колірну схему та макет оформлення.\n"
            "4. Збережіть файл під назвою Робота.pptx.\n"
        )
        sub_file = SimpleUploadedFile("pract_blank.txt", teacher_template_text.encode('utf-8'), content_type="text/plain")

        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Іван',
            last_name='Петренко',
            class_group=self.class_group,
            file=sub_file,
            is_latest_attempt=True
        )

        # Імітуємо відповідь ШІ, де ШІ помилково поставив 7 балів за сам факт прикріплення
        ai_mock_reply = json.dumps({
            "suggested_grade": "7",
            "level": "Достатній (7-9)",
            "unclear_task": False,
            "format_warning": None,
            "summary": "Учень здав бланк практичної роботи вчителя, але відповіді відсутні.",
            "strengths": ["Прикріплено файл"],
            "weaknesses": ["Здано текст завдань вчителя замість виконаної учнем роботи (відповіді відсутні)."],
            "feedback_comment": "Ви прикріпили інструкцію до практичної роботи без власних відповідей.",
            "gr_results": [{"code": "ГР 1", "name": "Практична частина", "grade": "7", "level": "Достатній", "comment": "Бланк"}]
        }, ensure_ascii=False)
        mock_call.return_value = (200, ai_mock_reply, None, {})

        result = evaluate_submission_with_gemini(submission)

        # Переконуємось, що система НЕ дозволила поставити 7 балів і встановила "Доопрацювати"
        self.assertEqual(result['suggested_grade'], 'Доопрацювати')
        self.assertIn('Початковий', result['level'])
        self.assertTrue(result['unclear_task'])
        self.assertTrue(any("практичн" in w.lower() or "бланк" in w.lower() or "відповід" in w.lower() for w in result['weaknesses']))

    @patch('feed.gemini_service.call_ai_api')
    def test_reject_mismatched_class_submission(self, mock_call):
        """Перевірка, що здача роботи для іншого класу (наприклад 9 клас замість 6 класу) отримує 'Доопрацювати', а не позитивний бал."""
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.save()

        cg_6 = ClassGroup.objects.create(name='6-А')
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Урок інформатики",
            description="Опрацюйте тему уроку"
        )
        assignment.classes.add(cg_6)

        # Учень 6 класу здав документ з матеріалами 9 класу
        mismatched_text = (
            "Практична робота для 9 класу\n"
            "Тема: Бази даних та СУБД Access\n"
            "Хід роботи:\n"
            "1. Запустіть MS Access 2019.\n"
            "2. Створіть таблицю з полями Код, Прізвище, Клас.\n"
        )
        sub_file = SimpleUploadedFile("db_9_class.txt", mismatched_text.encode('utf-8'), content_type="text/plain")

        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Оксана',
            last_name='Ковальчук',
            class_group=cg_6,
            file=sub_file,
            is_latest_attempt=True
        )

        ai_mock_reply = json.dumps({
            "suggested_grade": "7",
            "level": "Достатній (7-9)",
            "unclear_task": False,
            "format_warning": None,
            "summary": "Робота містить завдання для 9 класу.",
            "strengths": ["Файл відкрито"],
            "weaknesses": ["Робота для іншого класу (9 клас замість 6 класу)."],
            "feedback_comment": "Здано роботу для 9 класу.",
            "gr_results": [{"code": "ГР 1", "name": "Результат", "grade": "7", "level": "Достатній", "comment": "9 клас"}]
        }, ensure_ascii=False)
        mock_call.return_value = (200, ai_mock_reply, None, {})

        result = evaluate_submission_with_gemini(submission)

        # Перевірка: оцінка скасована, встановилось "Доопрацювати", unclear_task = True
        self.assertEqual(result['suggested_grade'], 'Доопрацювати')
        self.assertIn('Початковий', result['level'])
        self.assertTrue(result['unclear_task'])
        self.assertTrue(any("клас" in w.lower() for w in result['weaknesses']))

    @patch('feed.gemini_service.call_ai_api')
    def test_no_points_awarded_for_mere_attachment_with_zero_answers(self, mock_call):
        """Перевірка, що за просте прикріплення тексту (довжиною > 30 симв.) без відповідей не виставляється 7 балів."""
        from .gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.save()

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Контрольні запитання",
            description="Дайте відповіді на запитання:\n1. Що таке Інтернет?\n2. Що таке браузер?"
        )
        assignment.classes.add(self.class_group)

        # Незв'язний сторонній текст понад 30 символів (раніше помилково ставилось 7 балів)
        raw_text = "Добрий вечір, я не встиг зробити ці запитання, відправляю файл просто так."
        sub_file = SimpleUploadedFile("note.txt", raw_text.encode('utf-8'), content_type="text/plain")

        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Михайло',
            last_name='Сидоренко',
            class_group=self.class_group,
            file=sub_file,
            is_latest_attempt=True
        )

        ai_mock_reply = json.dumps({
            "suggested_grade": "Доопрацювати",
            "level": "Початковий (1-3)",
            "unclear_task": True,
            "format_warning": "Не зрозуміло, яке саме завдання виконане.",
            "summary": "Жодної відповіді на питання не надано.",
            "strengths": [],
            "weaknesses": ["Жодної відповіді не дано", "Не зрозуміло, яке саме завдання виконане"],
            "feedback_comment": "Будь ласка, виконайте завдання та надайте відповіді на поставлені запитання.",
        }, ensure_ascii=False)
        mock_call.return_value = (200, ai_mock_reply, None, {})

        result = evaluate_submission_with_gemini(submission)

        # Перевірка: старий баг (len >= 30 -> 7) НЕ спрацював, оцінка залишилась "Доопрацювати"
        self.assertEqual(result['suggested_grade'], 'Доопрацювати')
        self.assertIn('Початковий', result['level'])
        self.assertTrue(result['unclear_task'])

    def test_reject_teacher_duplicate_across_assignments(self):
        """Перевірка, що здача файлу вчителя з іншого завдання визначається як дублікат матеріалів вчителя."""
        from .models import AssignmentFile
        from .duplicate_detector import check_submission_duplicates

        # Створюємо перше завдання з файлом вчителя
        assignment1 = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Завдання 1 для іншого класу",
            description="Опис завдання 1"
        )
        t_file = SimpleUploadedFile("teacher_pract_8.docx", b"Teacher practical guide for 8th grade content here", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        af = AssignmentFile.objects.create(assignment=assignment1, file=t_file, original_name="teacher_pract_8.docx")

        # Створюємо друге завдання (для поточного класу)
        assignment2 = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Завдання 2 для нашого класу",
            description="Опис завдання 2"
        )
        assignment2.classes.add(self.class_group)

        # Учень прикріплює до другого завдання той самий файл вчителя з першого завдання
        sub_file = SimpleUploadedFile("teacher_pract_8.docx", b"Teacher practical guide for 8th grade content here", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        submission = Submission.objects.create(
            assignment=assignment2,
            first_name='Петро',
            last_name='Поліщук',
            class_group=self.class_group,
            file=sub_file,
            is_latest_attempt=True
        )

        dup_info = check_submission_duplicates(submission)
        self.assertTrue(dup_info['is_duplicate'])
        self.assertTrue(dup_info.get('is_duplicate_teacher'))
        self.assertIn("матеріалами вчителя", dup_info.get('warning_message', ''))

    def test_extract_task_questions_from_presentation_slides(self):
        """Перевірка точного видобування завдань зі слайдів презентації без витоку службових заголовків."""
        from feed.gemini_service import extract_task_questions, detect_expected_task_count

        slide_text = """
        [Слайд 1]
        Практичне завдання
        Завдання 1.
        Знайдіть в Інтернет-енциклопедії Вікіпедія відомості про рідне місто (село).

        [Слайд 2]
        Практичне завдання
        Завдання 2.
        Знайдіть в онлайн-словниках пояснення крилатого вислову «ахіллесова п’ята».

        [Слайд 3]
        Практичне завдання
        Завдання 3.
        Використовуючи онлайн-перекладач перекладіть знайдене пояснення крилатого вислову «ахіллесова п’ята» англійською мовою.
        """
        qs = extract_task_questions(slide_text)
        self.assertEqual(len(qs), 3)
        self.assertIn("Вікіпедія", qs[0])
        self.assertNotIn("[Слайд 2]", qs[0])
        self.assertNotIn("Практичне завдання", qs[0])
        self.assertIn("онлайн-словниках", qs[1])
        self.assertNotIn("[Слайд 3]", qs[1])
        self.assertIn("онлайн-перекладач", qs[2])

        # Перевірка визначення очікуваної кількості завдань за текстом інструкції
        desc_with_typo = "З презентації Завдання виконати всі 3 звадання в одному документі, та здати"
        self.assertEqual(detect_expected_task_count(desc_with_typo), 3)

        desc_correct = "Будь ласка, виконати всі 4 завдання з файлу."
        self.assertEqual(detect_expected_task_count(desc_correct), 4)

    @patch('feed.gemini_service.call_ai_api')
    def test_multi_task_ceiling_blocks_high_grade_for_missing_task_user_scenario(self, mock_call):
        """
        Тест ситуації користувача:
        У презентації 3 завдання (Вікіпедія, Словник, Перекладач).
        Учень здав лише 2 завдання (Вікіпедія + англійське речення, пропустивши тлумачення в онлайн-словнику).
        ШІ помилково галюцинував 11 балів.
        Система ПОВИННА знизити бал до максимуму 8 балів (Достатній рівень),
        прибрати похвалу за завдання 2, зазначити у зауваженнях пропуск завдання 2 та виправити висновок.
        """
        from feed.gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.save()

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Практична робота: Пошук інформації в Інтернеті",
            description="З презентації Завдання виконати всі 3 звадання в одному документі, та здати"
        )
        assignment.classes.add(self.class_group)

        # Текст презентації як матеріал вчителя
        slide_text = (
            "[Слайд 1]\nПрактичне завдання\nЗавдання 1.\n"
            "Знайдіть в Інтернет-енциклопедії Вікіпедія відомості про рідне місто (село).\n\n"
            "[Слайд 2]\nПрактичне завдання\nЗавдання 2.\n"
            "Знайдіть в онлайн-словниках пояснення крилатого вислову «ахіллесова п’ята».\n\n"
            "[Слайд 3]\nПрактичне завдання\nЗавдання 3.\n"
            "Використовуючи онлайн-перекладач перекладіть знайдене пояснення крилатого вислову «ахіллесова п’ята» англійською мовою."
        )
        t_file = SimpleUploadedFile("presentation.txt", slide_text.encode('utf-8'), content_type="text/plain")
        AssignmentFile.objects.create(assignment=assignment, file=t_file, original_name="presentation.txt")

        # Відповідь учня: тільки Завдання 1 (Жашків) та Завдання 3 (англійський переклад), без словника
        student_text = (
            "Жашків — місто в Україні, в Уманському районі Черкаської області, адміністративний центр "
            "Жашківської міської громади. Населення становить 13 242 особи.\n\n"
            "\"Achilles' heel\" is an idiom referring to a weak or vulnerable point in a person or any system."
        )
        sub_file = SimpleUploadedFile("student_work.txt", student_text.encode('utf-8'), content_type="text/plain")
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Аліса',
            last_name='Петренко',
            class_group=self.class_group,
            file=sub_file,
            is_latest_attempt=True
        )

        # ШІ галюцинує 11 балів і стверджує, що всі 3 завдання виконано
        hallucinated_ai_reply = json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "unclear_task": False,
            "format_warning": None,
            "summary": "Учень успішно виконав практичні завдання, продемонструвавши вміння працювати з онлайн-ресурсами для навчання. Робота містить правильні відповіді на завдання 1, 2 та 3.",
            "strengths": [
                "Успішний пошук інформації про рідне місто в енциклопедії.",
                "Коректне знаходження пояснення крилатого вислову в онлайн-словнику.",
                "Правильне виконання перекладу знайденого пояснення англійською мовою."
            ],
            "weaknesses": [
                "Відсутність формату «питання-відповідь» або нумерації завдань, що ускладнює перевірку."
            ],
            "feedback_comment": "Алісо, ти чудово впоралася з практичними завданнями! Ти правильно знайшла інформацію про своє місто, пояснила значення вислову та якісно виконала його переклад.",
            "gr_results": [
                {"code": "ГР 2", "name": "Створює інформаційні продукти", "grade": "11", "level": "Високий", "comment": "Практичні завдання виконані повністю"},
                {"code": "ГР 3", "name": "Працює в цифровому середовищі", "grade": "11", "level": "Високий", "comment": "Впевнене володіння"},
                {"code": "ГР 4", "name": "Безпечно та відповідально працює з ІТ", "grade": "10", "level": "Високий", "comment": "Самостійно"}
            ]
        }, ensure_ascii=False)
        mock_call.return_value = (200, hallucinated_ai_reply, None, {})

        result = evaluate_submission_with_gemini(submission)

        # Перевірка: оцінка знижена до 8 балів (Достатній рівень, максимум для 2/3 завдань)
        self.assertEqual(result['suggested_grade'], '8')
        self.assertIn('Достатній', result['level'])

        # Перевірка: групи результатів обмежені до 8 балів
        for gr in result['gr_results']:
            self.assertLessEqual(int(gr['grade']), 8)

        # Перевірка: висновок виправлено (не стверджує, що виконано завдання 1, 2 та 3)
        self.assertNotIn("відповіді на завдання 1, 2 та 3", result['summary'])
        self.assertIn("2 із 3", result['summary'])

        # Перевірка: у сильних сторонах відсутнє Завдання 2
        for s in result['strengths']:
            self.assertNotIn("пояснення крилатого вислову в онлайн-словнику", s.lower())

        # Перевірка: у зауваженнях чітко зазначено пропуск Завдання 2
        self.assertTrue(any("завдання 2" in w.lower() for w in result['weaknesses']))

    @patch('feed.gemini_service.call_ai_api')
    def test_multi_task_one_of_three_capped_at_five(self, mock_call):
        """Перевірка, що виконання лише 1 із 3 завдань (~33%) обмежується максимум 5 балами (Середній рівень)."""
        from feed.gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.save()

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Контрольні 3 вправи",
            description="Обов'язково виконати всі 3 завдання:\n1. Що таке база даних?\n2. Що таке первинний ключ?\n3. Що таке зв'язок один-до-багатьох?"
        )
        assignment.classes.add(self.class_group)

        # Учень відповів лише на 1 питання
        student_text = "1. База даних — це впорядкована сукупність взаємопов'язаних даних."
        sub_file = SimpleUploadedFile("ans.txt", student_text.encode('utf-8'), content_type="text/plain")
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Олег',
            last_name='Коваленко',
            class_group=self.class_group,
            file=sub_file,
            is_latest_attempt=True
        )

        ai_reply = json.dumps({
            "suggested_grade": "9",
            "level": "Достатній (7-9)",
            "summary": "Учень відповів на перше запитання. Завдання 2 та 3 пропущено.",
            "strengths": ["Правильне визначення бази даних."],
            "weaknesses": ["Завдання 2 пропущено.", "Завдання 3 не виконано."],
            "feedback_comment": "Добре відповіли на перше питання, проте не виконано завдання 2 та 3.",
            "gr_results": [
                {"code": "ГР 2", "name": "Створює інформаційні продукти", "grade": "9", "level": "Достатній", "comment": "Частково"}
            ]
        }, ensure_ascii=False)
        mock_call.return_value = (200, ai_reply, None, {})

        result = evaluate_submission_with_gemini(submission)

        # Перевірка: оцінка 9 знижена до 5 (Середній рівень, оскільки лише 1/3)
        self.assertLessEqual(int(result['suggested_grade']), 5)
        self.assertIn('Середній', result['level'])

    @patch('feed.gemini_service.call_ai_api')
    def test_choice_assignment_not_capped_for_single_task(self, mock_call):
        """Перевірка, що завдання з вибором («одне завдання на вибір») НЕ обмежується стелею часткового виконання."""
        from feed.gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.save()

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Завдання на вибір",
            description="Виконайте одне завдання на вибір:\n1. Напишіть есе про штучний інтелект.\n2. Створіть презентацію про історію комп'ютерів."
        )
        assignment.classes.add(self.class_group)

        student_text = "Есе про штучний інтелект: Штучний інтелект стрімко розвивається..."
        sub_file = SimpleUploadedFile("essay.txt", student_text.encode('utf-8'), content_type="text/plain")
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Софія',
            last_name='Ткаченко',
            class_group=self.class_group,
            file=sub_file,
            comment_student="Виконувала завдання 1",
            is_latest_attempt=True
        )

        ai_reply = json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "summary": "Чудове есе на обрану тему.",
            "strengths": ["Глибоке розкриття теми штучного інтелекту."],
            "weaknesses": [],
            "feedback_comment": "Відмінна робота, Софіє!",
            "gr_results": [
                {"code": "ГР 2", "name": "Створює інформаційні продукти", "grade": "11", "level": "Високий", "comment": "Відмінно"}
            ]
        }, ensure_ascii=False)
        mock_call.return_value = (200, ai_reply, None, {})

        result = evaluate_submission_with_gemini(submission)

        # Оцінка 11 залишається без обмеження, оскільки це завдання на вибір
        self.assertEqual(result['suggested_grade'], '11')
        self.assertIn('Високий', result['level'])

    def test_extract_task_questions_smart_filters_slides_when_practical_tasks_present(self):
        """Перевірка, що ШІ виділяє саме 3 практичні завдання зі слайдів, а не 18 теоретичних пунктів лекції."""
        from feed.gemini_service import extract_task_questions, detect_expected_task_count

        presentation_text = """
        [Слайд 1] Мережа Інтернет
        1. Історія розвитку інтернету
        2. Поняття про протокол TCP/IP
        [Слайд 2] Пошукові системи
        1. Google Пошук
        2. Пошукові каталоги
        3. Енциклопедії онлайн
        [Слайд 10] Онлайн-перекладачі
        1. Google Translate
        2. DeepL
        [Слайд 17] Повторення матеріалу
        1. Що таке браузер?
        2. Що таке пошукова система?
        [Слайд 18] Практичне завдання
        Завдання 1. Знайдіть в Інтернет-енциклопедії Вікіпедія відомості про рідне місто (село)
        Завдання 2. Знайдіть в онлайн-словниках пояснення крилатого вислову «ахіллесова п'ята»
        Завдання 3. Використовуючи онлайн-перекладач перекладіть знайдене пояснення крилатого вислову «ахіллесова п'ята» англійською мовою
        """
        teacher_instruction = "З презентації Завдання виконати всі 3 звадання в одному документі, та здати"

        explicit_count = detect_expected_task_count(teacher_instruction)
        self.assertEqual(explicit_count, 3)

        questions = extract_task_questions(presentation_text, explicit_count=explicit_count)
        self.assertEqual(len(questions), 3)
        self.assertIn("Вікіпедія відомості про рідне місто", questions[0])
        self.assertIn("онлайн-словниках пояснення крилатого вислову", questions[1])
        self.assertIn("онлайн-перекладач перекладіть", questions[2])
        # Перевірка: теоретичні пункти лекції НЕ потрапили до завдань
        self.assertFalse(any("Історія розвитку інтернету" in q for q in questions))
        self.assertFalse(any("Що таке браузер" in q for q in questions))

    def test_detect_expected_task_count_user_formats(self):
        """Перевірка розпізнавання кількості завдань за різними формулюваннями вчителя."""
        from feed.gemini_service import detect_expected_task_count

        self.assertEqual(detect_expected_task_count("З презентації Завдання виконати всі 3 звадання в одному документі, та здати"), 3)
        self.assertEqual(detect_expected_task_count("Виконати всі 3 завдання з презентації"), 3)
        self.assertEqual(detect_expected_task_count("Виконайте завдання 1-3 у зошиті"), 3)
        self.assertEqual(detect_expected_task_count("Зробити 4 вправи на закріплення"), 4)
        self.assertEqual(detect_expected_task_count("3 практичні завдання з файлу"), 3)
        self.assertEqual(detect_expected_task_count("Звичайний опис уроку без вказівки числа"), 0)

    @patch('feed.gemini_service.call_ai_api')
    def test_multi_task_ceiling_blocks_false_18_tasks_and_grades_at_eight(self, mock_call):
        """
        Перевірка випадку користувача:
        У презентації 18 слайдів/пунктів, вчитель вказав виконати всі 3 завдання.
        Учень здав 2 з 3 завдань (Жашків + англійський переклад, без українського словника).
        ШІ помилково поставив 11 балів.
        Запобіжник повинен:
        1. Встановити total_tasks = 3 (НЕ 18!).
        2. Обмежити оцінку 8 балами (Достатній рівень, НЕ 5 балів!).
        3. Зазначити '2 із 3 завдань' у зауваженнях та відгуку.
        """
        from feed.gemini_service import evaluate_submission_with_gemini

        settings = AISettings.get_solo()
        settings.is_enabled = True
        settings.api_key = 'fake-api-key'
        settings.save()

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Пошук інформації в Інтернеті",
            description="З презентації Завдання виконати всі 3 звадання в одному документі, та здати"
        )
        assignment.classes.add(self.class_group)

        # Додаємо файл презентації до завдання
        presentation_file_content = (
            "[Слайд 1] Теорія 1\n1. Вступ\n2. Огляд\n"
            "[Слайд 17] Теорія 17\n1. Підсумок\n"
            "[Слайд 18] Практичне завдання\n"
            "Завдання 1. Знайдіть в Інтернет-енциклопедії Вікіпедія відомості про рідне місто (село)\n"
            "Завдання 2. Знайдіть в онлайн-словниках пояснення крилатого вислову «ахіллесова п'ята»\n"
            "Завдання 3. Використовуючи онлайн-перекладач перекладіть знайдене пояснення крилатого вислову «ахіллесова п'ята» англійською мовою\n"
        )
        af_file = SimpleUploadedFile("Internet_lesson.txt", presentation_file_content.encode('utf-8'), content_type="text/plain")
        AssignmentFile.objects.create(
            assignment=assignment,
            file=af_file,
            original_name="Internet_lesson.txt"
        )

        # Робота Тимура: Завдання 1 (Жашків) та Завдання 3 (Achilles' heel), Завдання 2 (словник) відсутнє
        student_text = (
            "Жашків — місто в Уманському районі Черкаської області України, центр Жашківської міської громади.\n\n"
            '"Achilles\' heel" is an idiom referring to a weak or vulnerable point in a person or any system.'
        )
        sub_file = SimpleUploadedFile("timur_work.txt", student_text.encode('utf-8'), content_type="text/plain")
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Тимур',
            last_name='Гончаренко',
            class_group=self.class_group,
            file=sub_file,
            is_latest_attempt=True
        )

        # Моделюємо сиру галюцинацію ШІ на 11 балів із похвалою за всі 3 завдання
        ai_reply = json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "summary": "📌 Висновок: Учень виконав усі три практичні завдання, продемонструвавши вміння користуватися інтернет-ресурсами. Робота виконана якісно та відповідає вимогам уроку.",
            "strengths": [
                "Повне та правильне виконання всіх практичних завдань.",
                "Вміння використовувати інтернет для пошуку та перекладу інформації."
            ],
            "weaknesses": [
                "Відсутність формату «питання-відповідь» або чіткої нумерації завдань."
            ],
            "feedback_comment": "Тимур, ти чудово впорався з практичними завданнями! Ти самостійно знайшов інформацію про рідне місто, пояснив значення фразеологізму та правильно переклав його англійською мовою.",
            "gr_results": [
                {"code": "ГР 2", "name": "Створює інформаційні продукти", "grade": "11", "level": "Високий", "comment": "Інформаційний продукт створено"},
                {"code": "ГР 3", "name": "Працює в цифровому середовищі", "grade": "11", "level": "Високий", "comment": "Орієнтується в сервісах"}
            ]
        }, ensure_ascii=False)
        mock_call.return_value = (200, ai_reply, None, {})

        result = evaluate_submission_with_gemini(submission)

        # 1. Оцінка обмежена рівно 8 балами (Достатній рівень, НЕ 11 і НЕ 5 балів!)
        self.assertEqual(result['suggested_grade'], '8')
        self.assertIn('Достатній', result['level'])

        # 2. Немає жодної згадки про "18 завдань"!
        self.assertNotIn("18", result['summary'])
        self.assertNotIn("18", ' '.join(result['weaknesses']))
        self.assertNotIn("18", result['feedback_comment'])

        # 3. Чітко зафіксовано виконання 2 із 3 завдань
        self.assertIn("2 із 3", ' '.join(result['weaknesses']))
        self.assertIn("2 із 3", result['feedback_comment'])

        # 4. Пропущене Завдання 2 названо у зауваженнях
        self.assertTrue(any('завдання 2' in w.lower() for w in result['weaknesses']))

        # 5. Хибна похвала "виконав усі три завдання" усунена
        self.assertNotIn("виконав усі три", result['summary'].lower())
        self.assertFalse(any("виконання всіх практичних завдань" in s.lower() for s in result['strengths']))

    def test_assignment_ai_understanding_endpoint(self):
        """Тест endpoint'у розуміння завдання ШІ (/teacher/assignment/<id>/ai-understanding/)."""
        self.client.login(username='teacher_qa', password='password123')

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Практична робота: Пошук у Вікіпедії",
            description="З презентації Завдання виконати всі 3 звадання в одному документі, та здати"
        )
        assignment.classes.add(self.class_group)

        # 1. GET-запит (генерує або повертає аналіз розуміння завдання)
        url = reverse('assignment_ai_understanding', args=[assignment.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('data', data)
        report = data['data']
        self.assertEqual(report['tasks_total_count'], 3)
        self.assertIn('grading_breakdown', report)

        # Перевірка оновлення моделі
        assignment.refresh_from_db()
        self.assertTrue(bool(assignment.ai_task_understanding))
        self.assertIsNotNone(assignment.ai_task_understanding_updated_at)

        # 2. POST-запит (примусове оновлення аналізу)
        resp_post = self.client.post(url)
        self.assertEqual(resp_post.status_code, 200)
        self.assertEqual(resp_post.json()['status'], 'success')

        # 3. Перевірка обмеження доступу для стороннього користувача
        other_user = User.objects.create_user(username='other_teacher', password='password123')
        self.client.login(username='other_teacher', password='password123')
        resp_forbidden = self.client.get(url)
        self.assertEqual(resp_forbidden.status_code, 403)

    def test_ai_apply_suggested_grade_updates_existing_comment(self):
        """Тест оновлення існуючого коментаря з висновком ШІ при повторному прийнятті/публікації оцінки."""
        self.client.login(username=self.user.username, password='password123')
        sub = Submission.objects.create(
            assignment=self.assignment,
            class_group=self.class_group,
            first_name='Іван',
            last_name='Петренко',
            ai_suggested_grade='10',
            ai_feedback='📌 **Висновок:** Початковий висновок ШІ.\n\n✅ **Сильні сторони:**\n• Пункт 1\n\n💬 **Рекомендація учню:** Добре.',
            ai_status='success'
        )

        # 1. Перша публікація оцінки та відгуку ШІ
        resp1 = self.client.post(reverse('ai_apply_suggested_grade', args=[sub.id]), follow=True)
        self.assertEqual(resp1.status_code, 200)

        sub.refresh_from_db()
        self.assertEqual(sub.grade, '10')
        self.assertEqual(sub.comments.count(), 1)
        first_comment = sub.comments.first()
        self.assertIn('Початковий висновок ШІ', first_comment.text)

        # 2. Зміна аналізу ШІ (наприклад після повторної перевірки)
        sub.ai_suggested_grade = '11'
        sub.ai_feedback = '📌 **Висновок:** Оновлений виправлений висновок ШІ після перевірки.\n\n✅ **Сильні сторони:**\n• Доопрацьовано завдання\n\n💬 **Рекомендація учню:** Відмінно.'
        sub.save()

        # 3. Повторна публікація оцінки та відгуку ШІ
        resp2 = self.client.post(reverse('ai_apply_suggested_grade', args=[sub.id]), follow=True)
        self.assertEqual(resp2.status_code, 200)

        sub.refresh_from_db()
        self.assertEqual(sub.grade, '11')
        # Коментар не дублюється, а оновлюється на новий висновок
        self.assertEqual(sub.comments.count(), 1)
        updated_comment = sub.comments.first()
        self.assertEqual(updated_comment.id, first_comment.id)
        self.assertIn('Оновлений виправлений висновок ШІ після перевірки', updated_comment.text)
        self.assertNotIn('Початковий висновок ШІ', updated_comment.text)
        self.assertEqual(sub.teacher_comment, sub.get_clean_ai_feedback_for_student())

    def test_parse_teacher_specific_task_numbers_variations(self):
        """Тест точного розпізнавання номерів завдань/вправ у різних граматичних формах."""
        from feed.gemini_service import parse_teacher_specific_task_numbers
        test_cases = [
            ("виконати вправа 2", [2]),
            ("виконати вправу 2", [2]),
            ("зробити вправи 1, 2", [1, 2]),
            ("вправа 2", [2]),
            ("впр. 2", [2]),
            ("номер 3", [3]),
            ("№ 2", [2]),
            ("№2", [2]),
            ("завдання 1", [1]),
            ("завд. 1", [1]),
            ("пункт 4", [4]),
            ("виконати вправу № 2 з практичної роботи", [2]),
            ("зробити завдання 2-4", [2, 3, 4]),
            ("опрацювати презентацію і виконати вправа 2", [2]),
            ("опрацювати презентацію", []),
            ("виконати всі 3 завдання", []),
        ]
        for text, expected in test_cases:
            res = parse_teacher_specific_task_numbers(text)
            self.assertEqual(res, expected, f"Failed for text: '{text}', expected {expected}, got {res}")

    def test_get_assignment_target_grades_and_ages(self):
        """Тест коректного визначення класу та орієнтовного віку учнів."""
        from feed.gemini_service import get_assignment_target_grades_and_ages
        self.assignment.classes.clear()
        cls5 = ClassGroup.objects.create(name='5-А')
        self.assignment.classes.add(cls5)
        grade_str, age_str = get_assignment_target_grades_and_ages(self.assignment)
        self.assertEqual(grade_str, '5-й клас')
        self.assertEqual(age_str, '10–11 років')

        cls6 = ClassGroup.objects.create(name='6-Б')
        self.assignment.classes.add(cls6)
        grade_str2, age_str2 = get_assignment_target_grades_and_ages(self.assignment)
        self.assertEqual(grade_str2, '5–6 класи')
        self.assertEqual(age_str2, '10–12 років')

    def test_student_ai_understanding_access_control(self):
        """Тест доступу учнів до перегляду роз'яснення ШІ: дозволено тільки якщо вчитель увімкнув функцію."""
        student_user = User.objects.create_user(username='student_user', password='password123')
        url = reverse('assignment_ai_understanding', args=[self.assignment.pk])
        alt_url = reverse('assignment_ai_understanding_student', args=[self.assignment.pk])

        # 1. За замовчуванням (allow_student_ai_understanding = False) -> 403 Forbidden для учня
        self.assignment.allow_student_ai_understanding = False
        self.assignment.status = Assignment.STATUS_PUBLISHED
        self.assignment.save()

        self.client.login(username='student_user', password='password123')
        resp_denied = self.client.get(url)
        self.assertEqual(resp_denied.status_code, 403)

        resp_alt_denied = self.client.get(alt_url)
        self.assertEqual(resp_alt_denied.status_code, 403)

        # 2. Якщо вчитель увімкнув доступ (allow_student_ai_understanding = True) -> 200 OK для учня
        self.assignment.allow_student_ai_understanding = True
        self.assignment.description = "Опрацюйте слайди та виконайте вправа 2"
        self.assignment.save()

        resp_allowed = self.client.get(url)
        self.assertEqual(resp_allowed.status_code, 200)
        data = resp_allowed.json()
        self.assertEqual(data['status'], 'success')
        self.assertFalse(data['is_teacher'])
        self.assertIn('student_explanation', data['data'])
        self.assertIn('target_audience', data['data'])
        self.assertEqual(data['data']['tasks_total_count'], 1)  # Тільки вправа 2!

        # 3. Перевірка для неавторизованого учня (гість/анонімний доступ)
        self.client.logout()
        resp_anon = self.client.get(url)
        self.assertEqual(resp_anon.status_code, 200)
        self.assertFalse(resp_anon.json()['is_teacher'])

        # Якщо опцію вимкнено — анонімний учень отримує 403
        self.assignment.allow_student_ai_understanding = False
        self.assignment.save()
        resp_anon_denied = self.client.get(url)
        self.assertEqual(resp_anon_denied.status_code, 403)

    def test_student_comment_conclusion_guardrail_sanitization(self):
        """Тест захисту від галюцинацій: висновок у коментарі учня зараховується, а хибні скарги на відсутність висновку прибираються."""
        sub = Submission.objects.create(
            assignment=self.assignment,
            class_group=self.class_group,
            first_name='Оксана',
            last_name='Коваленко',
            comment_student='Мій висновок по роботі: я навчилася створювати презентації та структурувати інформацію за темою.'
        )

        # Симулюємо сценарій, коли ШІ галюцинує скаргу на відсутність висновку
        raw_summary = "Учениця виконала практичні завдання, але відсутній висновок до роботи."
        raw_feedback = "Робота виконана добре, проте відсутній висновок до практичної роботи."
        raw_weaknesses = ["Відсутній висновок до роботи"]

        student_comment_text = (sub.comment_student or "").strip()
        has_conclusion_in_comment = bool(
            student_comment_text and (
                any(w in student_comment_text.lower() for w in [
                    'висновок', 'висновки', 'підсумок', 'підсумки', 'робота показала',
                    'я зробив висновок', 'я зробила висновок', 'я навчилася'
                ]) or len(student_comment_text) >= 20
            )
        )
        self.assertTrue(has_conclusion_in_comment)

        no_conclusion_phrases = ['відсутній висновок']
        cleaned_summary = raw_summary
        cleaned_weaknesses = [w for w in raw_weaknesses]
        for phrase in no_conclusion_phrases:
            if phrase in cleaned_summary.lower():
                import re
                cleaned_summary = re.sub(re.escape(phrase), 'висновок до роботи надано у коментарі до здачі', cleaned_summary, flags=re.IGNORECASE)
            cleaned_weaknesses = [w for w in cleaned_weaknesses if phrase not in w.lower()]

        self.assertNotIn('відсутній висновок', cleaned_summary.lower())
        self.assertIn('висновок до роботи надано у коментарі до здачі', cleaned_summary.lower())
        self.assertEqual(len(cleaned_weaknesses), 0)

    # ═══════════════════════════════════════════════════════════════════════════
    # ДЕДИКОВАНІ ТЕСТИ ДЛЯ SCOPE OF WORK (ТЕСТОВІ СЦЕНАРІЇ 1-6)
    # ═══════════════════════════════════════════════════════════════════════════

    @patch('feed.gemini_service.call_ai_api')
    def test_scope_scenario_1_project_work_no_exercise_hallucination(self, mock_call):
        """
        ТЕСТ 1: Вчитель: «Робота над проєктом.». Файл презентації містить 4 вправи.
        Учень надав готовий проєкт.
        Очікування: assigned_task_count = 1, tasks_total_count = 1, відсутні фрази «1 з 4» чи скарги на вправи.
        """
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Робота над проєктом",
            description="Робота над проєктом."
        )
        assignment.classes.add(self.class_group)

        pres_text = (
            "[Слайд 1] Теорія проєкту\n"
            "[Слайд 2] Вправа 1. Створити схему\n"
            "[Слайд 3] Вправа 2. Заповнити таблицю\n"
            "[Слайд 4] Вправа 3. Відповісти на питання\n"
            "[Слайд 5] Вправа 4. Дослідити явище\n"
        )
        t_file = SimpleUploadedFile("slides.txt", pres_text.encode('utf-8'), content_type="text/plain")
        AssignmentFile.objects.create(assignment=assignment, file=t_file, original_name="slides.txt")

        # Перевірка Scope
        from .gemini_service import resolve_assignment_scope
        scope = resolve_assignment_scope(
            assignment_title=assignment.title,
            assignment_desc=assignment.description,
            teacher_files_content=[pres_text]
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertTrue(scope['is_single_complex_task'])
        self.assertEqual(len(scope['ignored_found_tasks']), 4)

        # Створення здачі учня
        sub_file = SimpleUploadedFile("project.txt", "Готовий проєкт учня з повним описом та дослідженням.".encode('utf-8'), content_type="text/plain")
        submission = Submission.objects.create(
            assignment=assignment,
            class_group=self.class_group,
            first_name='Максим',
            last_name='Шевченко',
            file=sub_file,
            is_latest_attempt=True
        )

        # ШІ галюцинує «1 з 4 завдань» та скарги на вправи 1, 2, 3
        hallucinated_ai_reply = json.dumps({
            "suggested_grade": "10",
            "level": "Високий (10-12)",
            "unclear_task": False,
            "format_warning": None,
            "summary": "Учень виконав 1 з 4 завдань. Здано проєкт.",
            "strengths": ["Гарно виконаний проєкт."],
            "weaknesses": [
                "Виконано 1 з 4 завдань",
                "Не виконано завдання 2",
                "Не виконано вправу 3",
                "Відсутня відповідь на питання 4"
            ],
            "feedback_comment": "Виконано 1 з 4 завдань з презентації. Не виконано завдання 2.",
            "task_resolution": {
                "scope_source": "teacher_files",
                "assigned_task_count": 4
            },
            "tasks_total_count": 4,
            "tasks_completed_count": 1,
            "tasks_evaluated": [
                {"task_num": 1, "task_title": "Проєкт", "status": "completed"},
                {"task_num": 2, "task_title": "Вправа 2", "status": "missing"},
                {"task_num": 3, "task_title": "Вправа 3", "status": "missing"},
                {"task_num": 4, "task_title": "Вправа 4", "status": "missing"}
            ]
        }, ensure_ascii=False)
        mock_call.return_value = (200, hallucinated_ai_reply, None, {})

        result = evaluate_submission_with_gemini(submission)

        # Перевірки
        self.assertEqual(result['tasks_total_count'], 1)
        self.assertEqual(result['tasks_completed_count'], 1)
        self.assertEqual(len(result['tasks_evaluated']), 1)
        self.assertNotIn("1 з 4", result['summary'])
        self.assertNotIn("1 з 4", result['feedback_comment'])
        self.assertFalse(any("1 з 4" in w for w in result['weaknesses']))
        self.assertFalse(any("не виконано" in w.lower() for w in result['weaknesses']))
        self.assertEqual(result['suggested_grade'], '10')

    @patch('feed.gemini_service.call_ai_api')
    def test_scope_scenario_2_specific_exercise_2_only(self, mock_call):
        """
        ТЕСТ 2: Вчитель: «Виконати вправу 2.». Файл містить вправи 1–5. Учень виконав вправу 2.
        Очікування: assigned_task_count = 1, оцінюється ТІЛЬКИ вправа 2, решта незадані.
        """
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Домашня робота",
            description="Виконати вправу 2."
        )
        assignment.classes.add(self.class_group)

        pres_text = (
            "[Слайд 1] Вправа 1. Розв'язати рівняння\n"
            "[Слайд 2] Вправа 2. Побудувати графік функції y = 2x + 1\n"
            "[Слайд 3] Вправа 3. Скласти таблицю значень\n"
            "[Слайд 4] Вправа 4. Знайти нулі функції\n"
            "[Слайд 5] Вправа 5. Дослідити на монотонність\n"
        )
        t_file = SimpleUploadedFile("tasks.txt", pres_text.encode('utf-8'), content_type="text/plain")
        AssignmentFile.objects.create(assignment=assignment, file=t_file, original_name="tasks.txt")

        from .gemini_service import resolve_assignment_scope
        scope = resolve_assignment_scope(
            assignment_title=assignment.title,
            assignment_desc=assignment.description,
            teacher_files_content=[pres_text]
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertEqual(scope['teacher_specific_task_nums'], [2])
        self.assertTrue(scope['is_single_complex_task'])
        self.assertEqual(len(scope['ignored_found_tasks']), 4)

        sub_file = SimpleUploadedFile("work.txt", "Вправа 2. Графік побудовано за точками (0, 1), (1, 3).".encode('utf-8'), content_type="text/plain")
        submission = Submission.objects.create(
            assignment=assignment,
            class_group=self.class_group,
            first_name='Софія',
            last_name='Бондар',
            file=sub_file,
            is_latest_attempt=True
        )

        mock_call.return_value = (200, json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "unclear_task": False,
            "format_warning": None,
            "summary": "Учениця чудово виконала вправу 2, побудувавши графік функції.",
            "strengths": ["Правильно розраховані координати точок."],
            "weaknesses": ["Не виконано вправу 1", "Не виконано вправу 3"],
            "feedback_comment": "Гарна робота! Не виконано вправу 1.",
            "tasks_total_count": 1,
            "tasks_completed_count": 1
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertEqual(result['tasks_total_count'], 1)
        self.assertEqual(result['suggested_grade'], '11')
        self.assertFalse(any('вправу 1' in w.lower() for w in result['weaknesses']))
        self.assertFalse(any('вправу 3' in w.lower() for w in result['weaknesses']))
        self.assertNotIn('вправу 1', result['feedback_comment'].lower())

    @patch('feed.gemini_service.call_ai_api')
    def test_scope_scenario_3_specific_exercises_1_2_3_partial(self, mock_call):
        """
        ТЕСТ 3: Вчитель: «Виконати вправи 1, 2 та 3.». Учень виконав 1 і 2.
        Очікування: assigned_task_count = 3, вправа 3 може бути позначена як невиконана (стеля 8 балів).
        """
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Практичні вправи",
            description="Виконати вправи 1, 2 та 3."
        )
        assignment.classes.add(self.class_group)

        from .gemini_service import resolve_assignment_scope
        scope = resolve_assignment_scope(
            assignment_title=assignment.title,
            assignment_desc=assignment.description,
        )
        self.assertEqual(scope['assigned_task_count'], 3)
        self.assertEqual(scope['teacher_specific_task_nums'], [1, 2, 3])
        self.assertFalse(scope['is_single_complex_task'])

        sub_file = SimpleUploadedFile("work.txt", "Вправа 1: виконано.\nВправа 2: виконано.".encode('utf-8'), content_type="text/plain")
        submission = Submission.objects.create(
            assignment=assignment,
            class_group=self.class_group,
            first_name='Іван',
            last_name='Франко',
            file=sub_file,
            is_latest_attempt=True
        )

        mock_call.return_value = (200, json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "unclear_task": False,
            "format_warning": None,
            "summary": "Учень виконав завдання 1 та 2, але пропущено завдання 3.",
            "strengths": ["Вправи 1 і 2 виконано точно."],
            "weaknesses": ["Не виконано завдання 3", "Не виконано завдання 4"],
            "feedback_comment": "Робота добра, але завдання 3 не виконано.",
            "tasks_total_count": 3,
            "tasks_completed_count": 2,
            "tasks_evaluated": [
                {"task_num": 1, "task_title": "Вправа 1", "status": "completed"},
                {"task_num": 2, "task_title": "Вправа 2", "status": "completed"},
                {"task_num": 3, "task_title": "Вправа 3", "status": "missing"}
            ]
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertEqual(result['tasks_total_count'], 3)
        # 2 із 3 завдань -> стеля максимум 8 балів
        self.assertEqual(result['suggested_grade'], '8')
        # Завдання 3 залишається у зауваженнях (оскільки задане)
        self.assertTrue(any('завдання 3' in w.lower() for w in result['weaknesses']))
        # Завдання 4 видалено (оскільки НЕ задане)
        self.assertFalse(any('завдання 4' in w.lower() for w in result['weaknesses']))

    @patch('feed.gemini_service.call_ai_api')
    def test_scope_scenario_4_presentation_with_custom_criteria(self, mock_call):
        """
        ТЕСТ 4: Вчитель: «Створити презентацію.». custom_criteria: 8 слайдів; титульний слайд; висновок; джерела.
        Очікування: assigned_task_count = 1, оцінюється презентація за критеріями, критерії мають пріоритет.
        """
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Створити презентацію",
            description="Створити презентацію.",
            custom_criteria="• 8 слайдів\n• титульний слайд\n• висновок\n• джерела"
        )
        assignment.classes.add(self.class_group)

        from .gemini_service import resolve_assignment_scope
        scope = resolve_assignment_scope(
            assignment_title=assignment.title,
            assignment_desc=assignment.description,
            custom_criteria=assignment.custom_criteria
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertTrue(scope['is_single_complex_task'])
        self.assertEqual(len(scope['custom_criteria_rules']), 4)
        self.assertIn('8 слайдів', scope['custom_criteria_rules'])

        sub_file = SimpleUploadedFile("pres.txt", "Презентація на 8 слайдів з висновками та джерелами.".encode('utf-8'), content_type="text/plain")
        submission = Submission.objects.create(
            assignment=assignment,
            class_group=self.class_group,
            first_name='Дарина',
            last_name='Мельник',
            file=sub_file,
            is_latest_attempt=True
        )

        mock_call.return_value = (200, json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "unclear_task": False,
            "format_warning": None,
            "summary": "Презентація повністю відповідає критеріям: 8 слайдів, титульний, висновки, джерела.",
            "strengths": ["Дотримано всіх індивідуальних критеріїв."],
            "weaknesses": [],
            "feedback_comment": "Чудова презентація!",
            "tasks_total_count": 1,
            "tasks_completed_count": 1
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertEqual(result['tasks_total_count'], 1)
        self.assertEqual(result['suggested_grade'], '11')

    def test_scope_scenario_5_project_mixed_content_presentation(self):
        """
        ТЕСТ 5: Вчитель: «Робота над проєктом.». У презентації: теорія, приклад, 3 вправи,
        питання для самоперевірки, домашнє завдання.
        Очікування: жоден із цих елементів не стає обов'язковим окремим завданням, assigned_task_count = 1.
        """
        from .gemini_service import resolve_assignment_scope
        mixed_presentation = (
            "[Слайд 1] Теорія: поняття штучного інтелекту\n"
            "[Слайд 2] Приклад: робота експертної системи\n"
            "[Слайд 3] Вправа 1. Навести приклади ШІ\n"
            "[Слайд 4] Вправа 2. Порівняти підходи\n"
            "[Слайд 5] Вправа 3. Скласти блок-схему\n"
            "[Слайд 6] Питання для самоперевірки: Що таке машинне навчання?\n"
            "[Слайд 7] Домашнє завдання: повторити параграф 12\n"
        )
        scope = resolve_assignment_scope(
            assignment_title="Проєкт з інформатики",
            assignment_desc="Робота над проєктом.",
            teacher_files_content=[mixed_presentation]
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertTrue(scope['is_single_complex_task'])
        self.assertEqual(scope['task_questions'], [])
        # Усі знайдені вправи/питання з презентації ігноруються
        self.assertGreaterEqual(len(scope['ignored_found_tasks']), 3)

    def test_scope_scenario_6_homework_slide_15_specific(self):
        """
        ТЕСТ 6: Вчитель: «Опрацювати матеріал і виконати домашнє завдання зі слайду 15.».
        Очікування: Scope включає конкретне завдання зі слайду 15, але не вправи з інших слайдів.
        """
        from .gemini_service import resolve_assignment_scope
        presentation_text = (
            "[Слайд 2] Вправа 1. Класна робота\n"
            "[Слайд 5] Вправа 2. Тренувальна вправа\n"
            "[Слайд 15] Домашнє завдання: скласти таблицю порівняння алгоритмів пошуку\n"
            "[Слайд 16] Джерела та література\n"
        )
        scope = resolve_assignment_scope(
            assignment_title="Алгоритми пошуку",
            assignment_desc="Опрацювати матеріал і виконати домашнє завдання зі слайду 15.",
            teacher_files_content=[presentation_text]
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertTrue(scope['is_single_complex_task'])
        self.assertIn("15", scope['assigned_tasks'][0]['description'])
        self.assertIn("порівняння алгоритмів", scope['task_questions'][0])
        # Вправи зі слайдів 2 та 5 ігноруються
        ignored_descriptions = [item['description'] for item in scope['ignored_found_tasks']]
        self.assertTrue(any("Вправа 1" in d for d in ignored_descriptions))
        self.assertTrue(any("Вправа 2" in d for d in ignored_descriptions))


class IntelligentStudentEvaluationRequirementsTests(TestCase):
    """
    10 обов'язкових автоматизованих тестів інтелектуальної перевірки учнівських робіт:
    1. Комплексне завдання та сторонні вправи
    2. Конкретний номер вправи (надійне непозиційне зіставлення)
    3. Явно заданий перелік
    4. Критерії в окремому полі
    5. Критерії в окремому файлі
    6. Коментар учня містить висновок
    7. Вимога до формату (висновок у презентації)
    8. Непідтверджена заява учня
    9. Недоступний файл або посилання
    10. Зворотний зв'язок і перездача
    """
    def setUp(self):
        self.user = User.objects.create_user(username='teacher_eval', password='password123', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Оксана Василівна')
        self.class_group = ClassGroup.objects.create(grade=9, letter='А', name='9-А')
        self.teacher.classes.add(self.class_group)
        self.subject = Subject.objects.create(name='Інформатика', icon='💻', color='#3b82f6')
        self.teacher.subjects.add(self.subject)
        self.client = Client()
        self.client.login(username='teacher_eval', password='password123')

        self.ai_settings = AISettings.get_solo()
        self.ai_settings.is_enabled = True
        self.ai_settings.api_key = "AIzaSyFakeKeyForEvaluationTesting"
        self.ai_settings.ai_provider = 'gemini'
        self.ai_settings.model_name = 'gemini-2.5-flash'
        self.ai_settings.save()

    @patch('feed.gemini_service.call_ai_api')
    def test_1_complex_task_and_extraneous_exercises(self, mock_ai):
        """
        Тест 1. Комплексне завдання та сторонні вправи:
        Умова вчителя: «Робота над проєктом».
        Файл учителя містить теорію, приклад і 4 вправи.
        Учень подав готовий проєкт.
        Очікування: призначено 1 комплексне завдання, сторонні вправи не стають обов'язковими,
        немає твердження «виконано 1 із 4».
        """
        from .gemini_service import resolve_assignment_scope, evaluate_submission_with_gemini

        teacher_file_text = (
            "Теорія: Основи реляційних баз даних.\n"
            "Приклад: Створення таблиці Клієнти.\n"
            "Вправа 1. Створити поле ID.\n"
            "Вправа 2. Налаштувати первинний ключ.\n"
            "Вправа 3. Додати 5 записів.\n"
            "Вправа 4. Створити запит на вибірку."
        )

        scope = resolve_assignment_scope(
            assignment_title="Бази даних",
            assignment_desc="Робота над проєктом.",
            teacher_files_content=[teacher_file_text]
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertTrue(scope['is_single_complex_task'])
        self.assertGreaterEqual(len(scope['ignored_found_tasks']), 4)

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Бази даних",
            description="Робота над проєктом.",
            status=Assignment.STATUS_PUBLISHED
        )
        file = SimpleUploadedFile("project.accdb", b"Mock database content", content_type="application/msaccess")
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Михайло',
            last_name='Коваленко',
            class_group=self.class_group,
            teacher=self.teacher,
            file=file
        )

        # Моделюємо відповідь моделі, яка спробувала написати «виконано 1 з 4»
        mock_ai.return_value = (200, json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "tasks_total_count": 4,
            "tasks_completed_count": 1,
            "summary": "Проєкт виконано успішно, проте виконано лише 1 з 4 завдань із файлу.",
            "weaknesses": ["Не виконано вправу 2 з презентації", "Виконано 1 з 4 завдань"],
            "strengths": ["Чудова реалізація проєкту бази даних"],
            "feedback_comment": "Гарна робота над проєктом, але виконано 1 з 4 завдань."
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertEqual(result['tasks_total_count'], 1)
        self.assertEqual(result['tasks_completed_count'], 1)
        self.assertFalse(any("1 з 4" in w for w in result['weaknesses']))
        self.assertNotIn("1 з 4", result['summary'])

    def test_2_specific_exercise_number_non_positional(self):
        """
        Тест 2. Конкретний номер вправи:
        Умова: «Виконати вправу 2».
        Файл містить вправи 1–5, а також заголовки й теоретичні запитання.
        Очікування: визначено саме вправу 2 за її номером/міткою, а не за індексом 1.
        Інші вправи не впливають на оцінку.
        """
        from .gemini_service import resolve_assignment_scope, find_question_by_task_num

        questions_list = [
            "Тема: Основи алгоритмів. Що таке алгоритм?",
            "Вправа 1. Складіть лінійний алгоритм приготування чаю.",
            "Вправа 2. Складіть розгалужений алгоритм обчислення функції з умовою if.",
            "Вправа 3. Складіть циклічний алгоритм підрахунку суми.",
            "Вправа 4. Намалюйте блок-схему алгоритму.",
            "Вправа 5. Протестуйте програму у середовищі IDLE."
        ]

        # Перевіряємо семантичний пошукач номера
        matched = find_question_by_task_num(questions_list, 2)
        self.assertIsNotNone(matched)
        self.assertIn("розгалужений алгоритм", matched)
        self.assertNotIn("лінійний алгоритм", matched)

        scope = resolve_assignment_scope(
            assignment_title="Алгоритми",
            assignment_desc="Виконати вправу 2",
            teacher_files_content=["\n".join(questions_list)]
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertEqual(scope['teacher_specific_task_nums'], [2])
        self.assertIn("розгалужений", scope['assigned_tasks'][0]['description'])
        self.assertNotIn("лінійний", scope['assigned_tasks'][0]['description'])

        ignored_descs = [item['description'] for item in scope['ignored_found_tasks']]
        self.assertTrue(any("Вправа 1" in d for d in ignored_descs))
        self.assertTrue(any("Вправа 3" in d for d in ignored_descs))
        self.assertTrue(any("Вправа 4" in d for d in ignored_descs))

    def test_3_explicit_list_of_exercises(self):
        """
        Тест 3. Явно заданий перелік:
        Умова: «Виконати вправи 1, 2 і 3».
        Файл учителя містить вправи 1–5.
        Очікування: перевіряються саме 3 призначені вправи, вправи 4 і 5 ігноруються.
        """
        from .gemini_service import resolve_assignment_scope

        files_content = (
            "Вправа 1. Оголосити змінні.\n"
            "Вправа 2. Ввести дані з клавіатури.\n"
            "Вправа 3. Вивести результат.\n"
            "Вправа 4. Додати графічний інтерфейс.\n"
            "Вправа 5. Скомпілювати у виконуваний файл."
        )
        scope = resolve_assignment_scope(
            assignment_title="Програмування Python",
            assignment_desc="Виконати вправи 1, 2 і 3",
            teacher_files_content=[files_content]
        )
        self.assertEqual(scope['assigned_task_count'], 3)
        self.assertEqual(scope['teacher_specific_task_nums'], [1, 2, 3])
        self.assertEqual(len(scope['assigned_tasks']), 3)

        ignored_descs = [item['description'] for item in scope['ignored_found_tasks']]
        self.assertTrue(any("Вправа 4" in d for d in ignored_descs))
        self.assertTrue(any("Вправа 5" in d for d in ignored_descs))

    def test_4_custom_criteria_separate_field(self):
        """
        Тест 4. Критерії в окремому полі:
        Умова: «Створити презентацію».
        Спеціальні критерії: 8 слайдів, титульний слайд, висновок, джерела.
        Очікування: усі 4 вимоги перевіряються окремо і входять до плану оцінювання.
        """
        from .gemini_service import resolve_assignment_scope

        custom_criteria = (
            "- не менше 8 слайдів\n"
            "- титульний слайд\n"
            "- висновок\n"
            "- список використаних джерел\n"
            "- щонайменше 3 ілюстрації"
        )
        scope = resolve_assignment_scope(
            assignment_title="Комп'ютерні презентації",
            assignment_desc="Створити презентацію",
            custom_criteria=custom_criteria
        )
        self.assertEqual(len(scope['custom_criteria_rules']), 5)
        eval_plan = scope['evaluation_plan']
        self.assertEqual(len(eval_plan['criteria']), 5)
        criterion_names = [c['name'] for c in eval_plan['criteria']]
        self.assertTrue(any("8 слайдів" in n for n in criterion_names))
        self.assertTrue(any("титульний" in n for n in criterion_names))
        self.assertTrue(any("висновок" in n for n in criterion_names))
        self.assertTrue(any("джерел" in n for n in criterion_names))
        self.assertTrue(any("ілюстраці" in n for n in criterion_names))

    def test_5_criteria_in_attached_file(self):
        """
        Тест 5. Критерії в окремому файлі:
        Умова: «Виконайте завдання за прикріпленим документом».
        Документ містить умову, критерії оцінювання, теорію та додаткові вправи.
        Очікування: критерії оцінювання виявлені та враховані, додаткові вправи не стають обов'язковими.
        """
        from .gemini_service import resolve_assignment_scope

        document_text = (
            "Практична робота: Дослідження функцій.\n"
            "Теоретичні відомості: Поняття функції...\n\n"
            "Критерії оцінювання:\n"
            "- Побудова графіка функції (4 бали)\n"
            "- Знаходження нулів функції (3 бали)\n"
            "- Дослідження монотонності (3 бали)\n"
            "- Загальні висновки (2 бали)\n\n"
            "Вимоги до оформлення:\n"
            "- Акуратне оформлення графіка\n\n"
            "Додаткові вправи для самостійного опрацювання:\n"
            "Вправа 1. Дослідити складну функцію.\n"
            "Вправа 2. Побудувати дотичну."
        )

        scope = resolve_assignment_scope(
            assignment_title="Дослідження функцій",
            assignment_desc="Виконайте завдання за прикріпленим документом",
            teacher_files_content=[document_text]
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        eval_plan = scope['evaluation_plan']
        self.assertGreaterEqual(len(eval_plan['criteria']), 3)

        crit_names = [c['name'] for c in eval_plan['criteria']]
        self.assertTrue(any("Побудова графіка" in n for n in crit_names))
        self.assertTrue(any("нулів функції" in n for n in crit_names))

        # Додаткові вправи 1 та 2 не є обов'язковими завданнями
        ignored_descs = [item['description'] for item in scope['ignored_found_tasks']]
        self.assertTrue(any("Вправа 1" in d for d in ignored_descs))
        self.assertTrue(any("Вправа 2" in d for d in ignored_descs))

    @patch('feed.gemini_service.call_ai_api')
    def test_6_student_comment_contains_conclusion(self, mock_ai):
        """
        Тест 6. Коментар учня містить висновок:
        Умова вимагає зробити висновок.
        Учень прикріпив файл без висновку, але в полі коментаря навів змістовний висновок.
        Очікування: коментар аналізується, критерій висновку зараховується, немає заперечення про відсутність.
        """
        from .gemini_service import analyze_student_comment_nuance, evaluate_submission_with_gemini

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Лабораторна робота №3",
            description="Дослідити швидкість сортування. Зробити висновок.",
            status=Assignment.STATUS_PUBLISHED
        )
        file = SimpleUploadedFile("sort.py", b"def sort(): pass", content_type="text/x-python")
        student_comment = "Висновок: експеримент показав, що швидке сортування QuickSort працює в рази швидше за BubbleSort."

        nuance = analyze_student_comment_nuance(
            comment_student=student_comment,
            desc=assignment.description
        )
        self.assertTrue(nuance['has_substance'])
        self.assertEqual(nuance['substance_type'], 'conclusion')
        self.assertFalse(nuance['format_strictly_requires_file'])

        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Анна',
            last_name='Бойко',
            class_group=self.class_group,
            teacher=self.teacher,
            file=file,
            comment_student=student_comment
        )

        mock_ai.return_value = (200, json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "summary": "Роботу виконано успішно.",
            "weaknesses": ["Висновок відсутній у коді файлу"],
            "strengths": ["Правильний алгоритм"],
            "feedback_comment": "Добре виконано.",
            "criteria_results": [
                {"criterion": "Зробити висновок", "status": "missing", "evidence": "", "recommendation": "Надати висновок"}
            ]
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertFalse(any("висновок відсутній" in str(w).lower() for w in result['weaknesses']))
        # Критерій висновку зараховано
        crit_res = {c['criterion']: c['status'] for c in result['criteria_results']}
        self.assertEqual(crit_res.get("Зробити висновок"), "completed")

    @patch('feed.gemini_service.call_ai_api')
    def test_7_format_requirement_conclusion_on_slide(self, mock_ai):
        """
        Тест 7. Вимога до формату:
        Умова вимагає, щоб висновок був безпосередньо в презентації на слайді.
        Учень подав висновок лише в коментарі.
        Очікування: зміст висновку враховано, вимога формату перевіряється окремо,
        пояснюється перенесення на слайд без твердження про повну відсутність.
        """
        from .gemini_service import analyze_student_comment_nuance, evaluate_submission_with_gemini

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Презентація",
            description="Створити презентацію. Висновок на окремому слайді презентації є обов'язковим.",
            status=Assignment.STATUS_PUBLISHED
        )
        file = SimpleUploadedFile("presentation.pptx", b"Mock presentation bytes", content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
        student_comment = "Висновок: розроблені слайди демонструють перспективи квантових комп'ютерів."

        nuance = analyze_student_comment_nuance(
            comment_student=student_comment,
            desc=assignment.description
        )
        self.assertTrue(nuance['has_substance'])
        self.assertTrue(nuance['format_strictly_requires_file'])

        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Денис',
            last_name='Ткаченко',
            class_group=self.class_group,
            teacher=self.teacher,
            file=file,
            comment_student=student_comment
        )

        mock_ai.return_value = (200, json.dumps({
            "suggested_grade": "9",
            "level": "Достатній (7-9)",
            "summary": "Гарна презентація, але висновок відсутній у роботі.",
            "weaknesses": ["Висновок відсутній"],
            "strengths": ["Якісні ілюстрації"],
            "feedback_comment": "Додайте висновок.",
            "criteria_results": [
                {"criterion": "Висновок", "status": "missing", "evidence": "", "recommendation": "Додати висновок"}
            ]
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        # Немає неправдивого твердження про повну відсутність висновку
        self.assertFalse(any(w == "Висновок відсутній" for w in result['weaknesses']))
        # Присутнє зауваження про перенесення на слайд
        self.assertTrue(any("слайд" in str(w).lower() and "висновок" in str(w).lower() for w in result['weaknesses']))
        # Статус критерію частковий (зміст є, формат потребує розміщення на слайді)
        concl_entry = [c for c in result['criteria_results'] if "висновок" in c['criterion'].lower()][0]
        self.assertEqual(concl_entry['status'], 'partial')

    def test_8_unsubstantiated_student_claim(self):
        """
        Тест 8. Непідтверджена заява учня:
        Учень пише в коментарі, що виконав усі вимоги («Я все зробив ідеально, поставте 12»),
        але змістовних доказів немає.
        Очікування: заява розпізнається як порожня декларація, не зараховується автоматично.
        """
        from .gemini_service import analyze_student_comment_nuance

        empty_comment = "Я все зробив! Усі вимоги виконав на 100%, поставте 12 балів."
        nuance = analyze_student_comment_nuance(empty_comment)
        self.assertTrue(nuance['is_unsubstantiated_declaration'])
        self.assertFalse(nuance['has_substance'])

    @patch('feed.gemini_service.fetch_url_content')
    @patch('feed.gemini_service.call_ai_api')
    def test_9_inaccessible_url_or_file(self, mock_ai, mock_fetch):
        """
        Тест 9. Недоступний файл або посилання:
        Учень подав посилання, вміст якого неможливо отримати.
        Очікування: технічне обмеження зафіксоване, не маскується під доведену помилку учня.
        """
        from .gemini_service import extract_submission_content, evaluate_submission_with_gemini

        mock_fetch.return_value = (None, None, "404 Not Found: сервер недоступний")

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Веб-розробка",
            description="Опублікувати сайт та надати посилання.",
            status=Assignment.STATUS_PUBLISHED
        )
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Василь',
            last_name='Поліщук',
            class_group=self.class_group,
            teacher=self.teacher,
            link="https://inaccessible-student-site.ua/project"
        )

        text_parts, media, err = extract_submission_content(submission)
        inacc = getattr(submission, '_inaccessible_materials', [])
        self.assertEqual(len(inacc), 1)
        self.assertIn("404 Not Found", inacc[0]['error'])

        # Моделюємо AI, що помилково стверджує про помилку в коді
        mock_ai.return_value = (200, json.dumps({
            "suggested_grade": "2",
            "level": "Початковий (1-3)",
            "summary": "Учень написав код з помилками.",
            "weaknesses": ["Помилка в коді програми", "Не працює веб-сайт"],
            "strengths": [],
            "feedback_comment": "Виправте помилки в коді."
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertEqual(result['suggested_grade'], 'Доопрацювати')
        self.assertIn('inaccessible_materials', result['submission_evidence'])
        self.assertEqual(len(result['submission_evidence']['inaccessible_materials']), 1)
        # Помилка в коді видалена, зафіксовано технічну причину
        self.assertFalse(any("помилка в коді" in str(w).lower() for w in result['weaknesses']))
        self.assertTrue(any("технічна недоступність" in str(w).lower() or "недоступн" in str(w).lower() for w in result['weaknesses']))

    @patch('feed.gemini_service.call_ai_api')
    def test_10_ai_total_and_qs_cannot_alter_tasks_total_count(self, mock_ai):
        """
        Тест 10. Перевірити, що ai_total, len(tasks_evaluated), len(raw_found_questions),
        len(qs_count) не можуть змінити tasks_total_count, якщо evaluation_plan уже сформований.
        """
        from .gemini_service import evaluate_submission_with_gemini

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Створити презентацію",
            description="Створити презентацію про штучний інтелект.",
            status=Assignment.STATUS_PUBLISHED
        )
        file = SimpleUploadedFile("presentation.pptx", b"Mock presentation bytes", content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Олег',
            last_name='Сидоренко',
            class_group=self.class_group,
            teacher=self.teacher,
            file=file
        )

        # AI намагається повернути tasks_total_count = 6 та 6 оцінених завдань
        mock_ai.return_value = (200, json.dumps({
            "suggested_grade": "10",
            "level": "Високий (10-12)",
            "tasks_total_count": 6,
            "tasks_completed_count": 5,
            "tasks_evaluated": [{"task_num": i, "status": "completed"} for i in range(1, 7)],
            "summary": "Виконано 5 із 6 завдань.",
            "weaknesses": ["Не виконано завдання 6"],
            "strengths": ["Гарна робота"],
            "feedback_comment": "Добре."
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        # tasks_total_count береться суворо з evaluation_plan/teacher scope (1), а не з відповіді AI (6)
        self.assertEqual(result['tasks_total_count'], 1)
        self.assertEqual(result['tasks_completed_count'], 1)
        self.assertFalse(any("5 із 6" in w for w in result['weaknesses']))
        self.assertNotIn("5 із 6", result['summary'])

    @patch('feed.gemini_service.call_ai_api')
    def test_11_feedback_and_revision_advice_consistency(self, mock_ai):
        """
        Тест 10. Зворотний зв'язок і перездача:
        У роботі є сильні сторони та 2 підтверджені недоліки.
        Очікування: сильні сторони, недоліки та рекомендації для перездачі взаємоузгоджені.
        """
        from .gemini_service import evaluate_submission_with_gemini

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Аналіз даних",
            description="Виконати аналіз даних за інструкцією.",
            status=Assignment.STATUS_PUBLISHED
        )
        file = SimpleUploadedFile("analysis.docx", b"Mock docx data", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Софія',
            last_name='Мороз',
            class_group=self.class_group,
            teacher=self.teacher,
            file=file
        )

        mock_ai.return_value = (200, json.dumps({
            "suggested_grade": "8",
            "level": "Достатній (7-9)",
            "summary": "Роботу виконано на достатньому рівні.",
            "strengths": ["Правильно розраховано статистичні показники", "Наведено інформативні графіки"],
            "weaknesses": ["Відсутній список використаних джерел", "Не проведено аналіз граничних значень"],
            "revision_advice": [
                "Додати список використаних джерел та посилань на літературу",
                "Виконати аналіз поведінки моделі на граничних значеннях вибірки"
            ],
            "feedback_comment": "Добре пораховано статистику, але обов'язково додайте джерела та аналіз граничних значень."
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertEqual(len(result['strengths']), 2)
        self.assertEqual(len(result['weaknesses']), 2)
        self.assertGreaterEqual(len(result['revision_advice']), 2)
        self.assertIn("джерел", result['revision_advice'][0].lower())
        self.assertIn("граничн", result['revision_advice'][1].lower())
        self.assertEqual(result['suggested_grade'], '8')

    def test_database_assignment_resolved_as_single_holistic_task(self):
        """
        Тест: Практичне завдання з проектування реляційної бази даних MS Access
        (таблиці, сутності, атрибути, первинні/зовнішні ключі, зв'язки 1:M, кроки 3..4 та критерії оцінювання)
        має розпізнаватися як ОДНЕ комплексне практичне завдання, а не 14 окремих вправ.
        """
        from .gemini_service import resolve_assignment_scope, analyze_assignment_task_understanding

        title = "Поняття сутності, атрибута, ключа, зв’язку"
        desc = (
            "Ознайомтеся зі структурою реляційної бази даних для сервісу «Online Cinema».\n"
            "Вона складається з 3 сутностей (таблиць).\n"
            "Запишіть або перенесіть у документ структури таблиць з відповідними типами даних і ключами:\n"
            "Таблиця 1: Users (Користувачі)\n"
            "user_id: Числовий (INT, AutoIncrement) Primary Key (PK)\n"
            "full_name: Текстовий (VARCHAR)\n"
            "Таблиця 2: Movies (Фільми)\n"
            "movie_id: Числовий (INT, AutoIncrement) Primary Key (PK)\n"
            "Таблиця 3: Reviews (Рецензії)\n"
            "review_id: Числовий (INT, AutoIncrement) Primary Key (PK)\n"
            "КРОК 3. Класифікація та побудова зв'язків (10 хв)\n"
            "1. Зв'язок: Users ➔ Reviews (1 : M)\n"
            "2. Зв'язок: Movies ➔ Reviews (1 : M)\n"
            "3. Побудова ER-схеми\n"
            "КРОК 4. Експрес-контроль (5 хв)\n"
            "1. Чому поля user_id та movie_id є Зовнішніми ключами?\n"
            "2. Чим відрізняється PK від звичайного поля?\n"
            "3. Який тип даних обрати для поля rating?\n"
            "КРИТЕРІЇ ОЦІНЮВАННЯ:\n"
            "· 3 бали: Дотримано правил БЖД + Крок 2.\n"
            "· 3 бали: Визначено PK та FK.\n"
            "· 3 бали: Класифіковано зв'язки (1:M).\n"
            "· 3 бали: Відповіді на експрес-контроль."
        )

        scope = resolve_assignment_scope(title, desc)
        self.assertTrue(scope['is_single_complex_task'])
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertEqual(scope['teacher_specific_task_nums'], [])

        # Перевірка розуміння завдання для учня (вікно «Як ШІ розуміє завдання»)
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title=title,
            description=desc
        )
        assignment.classes.add(self.class_group)

        understanding = analyze_assignment_task_understanding(assignment)
        self.assertEqual(understanding['status'], 'success')
        data = understanding['data']
        self.assertEqual(data['tasks_total_count'], 1)
        self.assertEqual(len(data['tasks']), 1)
        self.assertNotIn("вправу 13", str(data).lower())

    def test_ai_task_understanding_age_appropriate_and_no_html_markup(self):
        """
        Перевірка, що:
        1. strip_html_tags коректно вирізає будь-яку Word/HTML розмітку (span, b, u, style).
        2. Вік дитини визначається точно і генерується покрокове роз'яснення (Крок 1, Крок 2...).
        3. Завдання з HTML-розміткою ("Що потрібно зробити: <span...>") повністю очищаються від тегів
           і формулюються природною українською мовою без залишків коду.
        """
        from .gemini_service import (
            strip_html_tags,
            get_assignment_min_grade,
            generate_age_appropriate_student_guide,
            analyze_assignment_task_understanding
        )

        # 1. Перевірка функції strip_html_tags
        raw_html = '<span style=\'font-size: 14pt; line-height: 115%; font-family: "Times New Roman", serif;\'><u>Виконати </u><b>Вправа 3.</b></span>'
        cleaned = strip_html_tags(raw_html)
        self.assertEqual(cleaned, "Виконати Вправа 3.")
        self.assertNotIn("<", cleaned)
        self.assertNotIn(">", cleaned)
        self.assertNotIn("span", cleaned)

        # 2. Перевірка генератора покрокового пояснення для молодших (5 клас) та старших (11 клас)
        guide_5 = generate_age_appropriate_student_guide(
            grade_str="5-й клас",
            age_str="10–11 років",
            min_grade=5,
            assignment_title="Табличний процесор",
            assignment_desc="Вправа 3",
            tasks_list=[{"title": "Вправа 3", "expected_actions": "Побудувати діаграму"}]
        )
        self.assertIn("5-й клас", guide_5)
        self.assertIn("Крок 1", guide_5)
        self.assertIn("Крок 2", guide_5)
        self.assertIn("Крок 3", guide_5)
        self.assertIn("Крок 4", guide_5)
        self.assertNotIn("<", guide_5)

        guide_11 = generate_age_appropriate_student_guide(
            grade_str="11-й клас",
            age_str="16–17 років",
            min_grade=11,
            assignment_title="Бази даних",
            assignment_desc="Створити таблиці",
            tasks_list=[{"title": "Проєкт БД", "expected_actions": "Створити зв'язки"}],
            is_single_task=True
        )
        self.assertIn("старшокласник", guide_11.lower())
        self.assertIn("Крок 1", guide_11)
        self.assertIn("Крок 2", guide_11)

        # 3. Перевірка analyze_assignment_task_understanding із вбудованою HTML-розміткою
        raw_desc = (
            "<p><span style='font-size: 14pt; line-height: 115%; font-family: \"Times New Roman\", serif;'>"
            "<u>Виконати </u><b>Вправа 3.</b>"
            "</span></p>"
        )
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Інформатика. Практикум",
            description=raw_desc
        )
        assignment.classes.add(self.class_group)

        res = analyze_assignment_task_understanding(assignment, force_refresh=True)
        self.assertEqual(res['status'], 'success')
        data = res['data']

        # Перевірка відсутності HTML-тегів у всіх полях
        data_str = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("<span", data_str)
        self.assertNotIn("font-family", data_str)
        self.assertNotIn("<u>", data_str)
        self.assertNotIn("</u>", data_str)
        self.assertNotIn("<b>", data_str)
        self.assertNotIn("</b>", data_str)

        # Перевірка покрокового пояснення
        student_expl = data.get('student_explanation', '')
        self.assertTrue(len(student_expl) > 50)
        self.assertIn("Крок 1", student_expl)
        self.assertIn("Крок 2", student_expl)

        # Перевірка списку завдань та відсутності сміття у expected_actions
        tasks = data.get('tasks', [])
        self.assertEqual(len(tasks), 1)
        expected_act = tasks[0].get('expected_actions', '')
        self.assertNotIn("<", expected_act)
        self.assertIn("Виконати вправу 3", expected_act)


class DynamicTaskTypeAndDeliverableTests(TestCase):
    """
    10 обов'язкових автоматизованих тестів нової моделі AI-оцінювання
    (типи завдань, очікуваний deliverable, questions_expected, узгоджений контекст):
    1. test_task_type_presentation
    2. test_task_type_programming
    3. test_task_type_question_answer
    4. test_task_type_project
    5. test_task_type_practical
    6. test_teacher_file_with_questions_for_presentation
    7. test_criteria_in_teacher_file_extracted
    8. test_age_appropriate_guide
    9. test_table_no_text_answers_no_penalty
    10. test_unified_context_evaluation_and_guide
    """

    def setUp(self):
        self.user = User.objects.create_user(username='teacher_dyn', password='password123', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Іванченко Тамара Олексіївна')
        self.class_group = ClassGroup.objects.create(grade=7, letter='Б', name='7-Б')
        self.teacher.classes.add(self.class_group)
        self.subject = Subject.objects.create(name='Інформатика', icon='💻', color='#10b981')
        self.teacher.subjects.add(self.subject)
        self.client = Client()
        self.client.login(username='teacher_dyn', password='password123')

        self.ai_settings = AISettings.get_solo()
        self.ai_settings.is_enabled = True
        self.ai_settings.api_key = "AIzaSyFakeKeyForDynEvaluationTesting"
        self.ai_settings.ai_provider = 'gemini'
        self.ai_settings.model_name = 'gemini-2.5-flash'
        self.ai_settings.save()

    def test_task_type_presentation(self):
        """
        Тест 1. Завдання «Створити презентацію на тему 'Будова клітини'»:
        task_type == 'presentation', questions_expected is False.
        """
        from .gemini_service import determine_assignment_task_type, is_questions_expected, resolve_assignment_scope

        task_type = determine_assignment_task_type(
            title="Створити презентацію на тему 'Будова клітини'",
            description="Підготуйте слайдову презентацію в PowerPoint або Canva."
        )
        self.assertEqual(task_type, 'presentation')
        self.assertFalse(is_questions_expected(task_type, "Підготуйте слайдову презентацію"))

        scope = resolve_assignment_scope(
            assignment_title="Створити презентацію на тему 'Будова клітини'",
            assignment_desc="Підготуйте слайдову презентацію в PowerPoint."
        )
        self.assertEqual(scope['task_type'], 'presentation')
        self.assertFalse(scope['questions_expected'])
        self.assertIn('deliverable', scope)
        self.assertIn(scope['deliverable']['type'], ['presentation', 'file'])
        self.assertEqual(scope['task_interpretation']['task_type'], 'presentation')

    def test_task_type_programming(self):
        """
        Тест 2. Завдання «Написати програму на Python для обчислення факторіалу»:
        task_type == 'programming', questions_expected is False.
        """
        from .gemini_service import determine_assignment_task_type, is_questions_expected, resolve_assignment_scope

        task_type = determine_assignment_task_type(
            title="Написати програму на Python для обчислення факторіалу",
            description="Складіть програмний код мовою Python, який зчитує число n та виводить факторіал."
        )
        self.assertEqual(task_type, 'programming')
        self.assertFalse(is_questions_expected(task_type, "Складіть програмний код"))

        scope = resolve_assignment_scope(
            assignment_title="Написати програму на Python для обчислення факторіалу",
            assignment_desc="Складіть код програми на Python."
        )
        self.assertEqual(scope['task_type'], 'programming')
        self.assertFalse(scope['questions_expected'])
        self.assertIn(scope['deliverable']['type'], ['programming', 'code', 'file'])

    def test_task_type_question_answer(self):
        """
        Тест 3. Завдання «Дайте відповіді на запитання 1-5 на сторінці 45»:
        task_type == 'question_answer', questions_expected is True.
        """
        from .gemini_service import determine_assignment_task_type, is_questions_expected, resolve_assignment_scope

        task_type = determine_assignment_task_type(
            title="Дайте відповіді на запитання 1-5 на сторінці 45",
            description="Письмово дайте відповіді на контрольні запитання підручника."
        )
        self.assertEqual(task_type, 'question_answer')
        self.assertTrue(is_questions_expected(task_type, "Дайте відповіді на контрольні запитання"))

        scope = resolve_assignment_scope(
            assignment_title="Дайте відповіді на запитання",
            assignment_desc="Дайте письмові відповіді на запитання 1-5."
        )
        self.assertEqual(scope['task_type'], 'question_answer')
        self.assertTrue(scope['questions_expected'])

    def test_task_type_project(self):
        """
        Тест 4. Завдання «Робота над навчальним проєктом 'Розумний дім'»:
        task_type == 'project', questions_expected is False.
        """
        from .gemini_service import determine_assignment_task_type, is_questions_expected, resolve_assignment_scope

        task_type = determine_assignment_task_type(
            title="Робота над навчальним проєктом 'Розумний дім'",
            description="Розробіть концепцію та матеріали для навчального проєкту."
        )
        self.assertEqual(task_type, 'project')
        self.assertFalse(is_questions_expected(task_type, "Розробіть навчальний проєкт"))

        scope = resolve_assignment_scope(
            assignment_title="Робота над навчальним проєктом 'Розумний дім'",
            assignment_desc="Проєкт 'Розумний дім'."
        )
        self.assertEqual(scope['task_type'], 'project')
        self.assertFalse(scope['questions_expected'])

    def test_task_type_practical(self):
        """
        Тест 5. Завдання «Практична робота: налаштування мережі»:
        task_type in ('practical', 'other'), questions_expected is False.
        """
        from .gemini_service import determine_assignment_task_type, is_questions_expected, resolve_assignment_scope

        task_type = determine_assignment_task_type(
            title="Практична робота: налаштування мережі",
            description="Виконайте покрокове налаштування локальної мережі за інструкцією."
        )
        self.assertIn(task_type, ['practical', 'other'])
        self.assertFalse(is_questions_expected(task_type, "Виконайте налаштування"))

        scope = resolve_assignment_scope(
            assignment_title="Практична робота: налаштування мережі",
            assignment_desc="Виконайте послідовність дій для налаштування мережі."
        )
        self.assertIn(scope['task_type'], ['practical', 'other'])
        self.assertFalse(scope['questions_expected'])

    def test_teacher_file_with_questions_for_presentation(self):
        """
        Тест 6. Вчитель задав створити презентацію, але прикріплений файл містить контрольні запитання:
        questions_expected is False, запитання з файлу не стають обов'язковими для учня.
        """
        from .gemini_service import resolve_assignment_scope

        teacher_file = (
            "Матеріали уроку: Історія комп'ютерної техніки.\n"
            "Контрольні запитання для самоперевірки:\n"
            "1. Хто сконструював першу ЕОМ?\n"
            "2. Які елементи використовувалися у першому поколінні?\n"
            "3. Що таке інтегральна схема?\n"
            "4. Які переваги персональних комп'ютерів?"
        )

        scope = resolve_assignment_scope(
            assignment_title="Історія комп'ютерів",
            assignment_desc="Створити презентацію на тему 'Історія комп'ютерної техніки'.",
            teacher_files_content=[teacher_file]
        )
        self.assertEqual(scope['task_type'], 'presentation')
        self.assertFalse(scope['questions_expected'])
        # Оскільки questions_expected is False, task_questions не нав'язуються як обов'язкові
        self.assertEqual(scope['task_questions'], [])
        self.assertEqual(scope['assigned_task_count'], 1)

    def test_criteria_in_teacher_file_extracted(self):
        """
        Тест 7. Критерії з файлу вчителя («не менше 8 слайдів», «наявність висновку»,
        «список джерел») витягуються у requirements / criteria.
        """
        from .gemini_service import resolve_assignment_scope, extract_task_requirements

        teacher_file = (
            "Презентація 'Сучасні хмарні сервіси'.\n"
            "Вимоги до оформлення роботи:\n"
            "- обсяг презентації: не менше 8 слайдів\n"
            "- обов'язкова наявність висновку на передостанньому слайді\n"
            "- останній слайд: список використаних джерел\n"
            "- титульний слайд із темою та прізвищем автора"
        )

        reqs = extract_task_requirements(
            title="Хмарні сервіси",
            desc="Створити презентацію",
            custom_criteria="",
            teacher_files_content=[teacher_file]
        )
        reqs_str = " ".join(reqs).lower()
        self.assertTrue("8 слайдів" in reqs_str or "слайд" in reqs_str)
        self.assertTrue("висновк" in reqs_str)
        self.assertTrue("джерел" in reqs_str)

        scope = resolve_assignment_scope(
            assignment_title="Хмарні сервіси",
            assignment_desc="Створити презентацію",
            teacher_files_content=[teacher_file]
        )
        all_reqs = " ".join(scope.get('requirements', []) + scope.get('task_interpretation', {}).get('requirements', [])).lower()
        self.assertTrue("висновк" in all_reqs or "джерел" in all_reqs or "слайд" in all_reqs)

    def test_age_appropriate_guide(self):
        """
        Тест 8. Перевірка, що покрокова інструкція для учня генерується відповідно до віку,
        зрозумілою мовою, без вигаданих вимог, за структурою із 4 обов'язкових блоків.
        """
        from .gemini_service import generate_age_appropriate_student_guide

        # Для 6-го класу
        guide_6 = generate_age_appropriate_student_guide(
            grade_str="6-й клас",
            age_str="11–12 років",
            min_grade=6,
            assignment_title="Створити презентацію 'Моя улюблена книга'",
            assignment_desc="Підготуйте презентацію про улюблену книжку.",
            tasks_list=[{"title": "Презентація", "expected_actions": "Створити 5-6 слайдів"}],
            task_interpretation={
                "task_type": "presentation",
                "questions_expected": False,
                "deliverable": {"description": "Слайдова презентація", "format": "Файл презентації"}
            }
        )
        self.assertIn("Коротко:", guide_6)
        self.assertIn("Покроковий план виконання:", guide_6)
        self.assertIn("Крок 1", guide_6)
        self.assertIn("Крок 2", guide_6)
        self.assertIn("Що має бути в результаті:", guide_6)
        self.assertIn("Перед здачею перевір:", guide_6)
        # Жодних вигаданих вимог чи формулювань про запитання
        self.assertNotIn("питання-відповідь", guide_6.lower())

        # Для 10-го класу
        guide_10 = generate_age_appropriate_student_guide(
            grade_str="10-й клас",
            age_str="15–16 років",
            min_grade=10,
            assignment_title="Розробка бази даних в Access",
            assignment_desc="Спроєктуйте структуру БД.",
            tasks_list=[{"title": "Проєкт БД", "expected_actions": "Створити таблиці"}],
            task_interpretation={
                "task_type": "database",
                "questions_expected": False,
                "deliverable": {"description": "База даних", "format": "Файл .accdb"}
            }
        )
        self.assertIn("старшокласник", guide_10.lower())
        self.assertIn("Покроковий план", guide_10)
        self.assertIn("Перед здачею перевір", guide_10)

    @patch('feed.gemini_service.call_ai_api')
    def test_table_no_text_answers_no_penalty(self, mock_ai):
        """
        Тест 9. Завдання — створити таблицю в Excel. Учень здав .xlsx файл без текстових
        відповідей на запитання. ШІ не знижує оцінку за «відсутність відповідей на питання»,
        а оцінює саму таблицю.
        """
        from .gemini_service import evaluate_submission_with_gemini

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Електронні таблиці",
            description="Створити таблицю в Excel для підрахунку витрат родини з формулами.",
            status=Assignment.STATUS_PUBLISHED
        )
        assignment.classes.add(self.class_group)

        excel_file = SimpleUploadedFile(
            "budget.xlsx",
            b"PK\x03\x04MockExcelTableContent",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Андрій',
            last_name='Мельник',
            class_group=self.class_group,
            teacher=self.teacher,
            file=excel_file
        )

        mock_ai.return_value = (200, json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "tasks_total_count": 1,
            "tasks_completed_count": 1,
            "tasks_evaluated": [{"task_num": 1, "status": "completed"}],
            "summary": "Таблиця побудована правильно, формули обраховані.",
            "weaknesses": ["Немає відповідей на контрольні запитання."],
            "strengths": ["Коректні формули", "Акуратне форматування"],
            "feedback_comment": "Рекомендується дотримуватися формату «питання-відповідь»."
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertEqual(result['task_type'], 'table')
        self.assertFalse(result['questions_expected'])
        # Перевірка санітизації: заборонено штрафувати за відсутність відповідей та вимагати питання-відповідь
        all_weaknesses = " ".join(result.get('weaknesses', [])).lower()
        self.assertNotIn("немає відповідей на", all_weaknesses)
        self.assertNotIn("відсутність відповідей на запитання", all_weaknesses)
        self.assertNotIn("питання-відповідь", (result.get('feedback_comment') or '').lower())
        self.assertNotIn("питання-відповідь", all_weaknesses)

    def test_unified_context_evaluation_and_guide(self):
        """
        Тест 10. Перевірка, що task_interpretation використовується узгоджено як для
        генерації evaluation_plan, так і для формування student_explanation.
        """
        from .gemini_service import (
            resolve_assignment_scope,
            interpret_assignment_task,
            analyze_assignment_task_understanding
        )

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Створення сайту",
            description="Створити вебсторінку про видатних діячів науки за допомогою HTML.",
            status=Assignment.STATUS_PUBLISHED
        )
        assignment.classes.add(self.class_group)

        # 1. resolve_assignment_scope формує узгоджену інтерпретацію
        scope = resolve_assignment_scope(
            assignment_title=assignment.title,
            assignment_desc=assignment.description
        )
        interp = scope.get('task_interpretation')
        self.assertIsNotNone(interp)
        self.assertEqual(interp['task_type'], scope['task_type'])
        self.assertEqual(interp['questions_expected'], scope['questions_expected'])
        self.assertEqual(interp['deliverable'], scope['deliverable'])

        # 2. analyze_assignment_task_understanding використовує ту саму інтерпретацію
        analysis = analyze_assignment_task_understanding(assignment, force_refresh=True)
        self.assertEqual(analysis['status'], 'success')
        data = analysis['data']
        self.assertEqual(data['task_type'], interp['task_type'])
        self.assertEqual(data['questions_expected'], interp['questions_expected'])
        self.assertEqual(data['deliverable']['description'], interp['deliverable']['description'])
        self.assertIn("Покроковий план", data['student_explanation'])


class ContextualDeliverableAndCompositeTaskTests(TestCase):
    """
    12 обов'язкових автоматизованих тестів фінального доопрацювання моделі AI-оцінювання:
    1. test_01_presentation_no_questions_expected
    2. test_02_composite_presentation_and_questions
    3. test_03_file_questions_are_reference_material
    4. test_04_file_criteria_of_another_exercise_ignored
    5. test_05_file_criteria_matching_presentation_applied
    6. test_06_programming_task_no_qa_expected
    7. test_07_no_hallucinated_presentation_requirements
    8. test_08_student_guide_explains_both_composite_parts
    9. test_09_submission_only_presentation_no_qa_penalty
    10. test_10_specific_exercise_2_non_positional
    11. test_11_project_with_4_exercises_is_single_task
    12. test_12_fallback_without_ai_api_maintains_truth
    """

    def setUp(self):
        self.user = User.objects.create_user(username='teacher_ctx', password='password123', is_staff=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Коваленко Сергій Петрович')
        self.class_group = ClassGroup.objects.create(grade=8, letter='А', name='8-А')
        self.teacher.classes.add(self.class_group)
        self.subject = Subject.objects.create(name='Інформатика', icon='💻', color='#2563eb')
        self.teacher.subjects.add(self.subject)
        self.client = Client()
        self.client.login(username='teacher_ctx', password='password123')

        self.ai_settings = AISettings.get_solo()
        self.ai_settings.is_enabled = True
        self.ai_settings.api_key = "AIzaSyFakeKeyForCtxDeliverableTesting"
        self.ai_settings.ai_provider = 'gemini'
        self.ai_settings.model_name = 'gemini-2.5-flash'
        self.ai_settings.save()

    def test_01_presentation_no_questions_expected(self):
        """
        Тест 1. Завдання «Створити презентацію»:
        task_type == 'presentation', questions_expected is False,
        task_components містить лише presentation.
        """
        from .gemini_service import (
            determine_assignment_task_type,
            is_questions_expected,
            identify_task_components,
            resolve_assignment_scope
        )

        title = "Створити презентацію"
        desc = "Підготуйте комп'ютерну презентацію на тему 'Штучний інтелект у сучасному світі'."

        task_type = determine_assignment_task_type(title=title, description=desc)
        self.assertEqual(task_type, 'presentation')

        components = identify_task_components(title=title, description=desc)
        self.assertIn('presentation', components)
        self.assertNotIn('question_answer', components)

        self.assertFalse(is_questions_expected(task_type, desc, components=components))

        scope = resolve_assignment_scope(assignment_title=title, assignment_desc=desc)
        self.assertEqual(scope['task_type'], 'presentation')
        self.assertFalse(scope['questions_expected'])
        self.assertEqual(scope['assigned_task_count'], 1)

    def test_02_composite_presentation_and_questions(self):
        """
        Тест 2. Складене завдання «Створити презентацію про штучний інтелект та відповісти на 5 контрольних запитань»:
        task_components містить 'presentation' та 'question_answer',
        questions_expected is True.
        """
        from .gemini_service import (
            determine_assignment_task_type,
            is_questions_expected,
            identify_task_components,
            resolve_assignment_scope
        )

        title = "Створити презентацію та відповісти на питання"
        desc = "Створити презентацію про штучний інтелект та відповісти на 5 контрольних запитань."

        components = identify_task_components(title=title, description=desc)
        self.assertIn('presentation', components)
        self.assertIn('question_answer', components)

        task_type = determine_assignment_task_type(title=title, description=desc)
        self.assertEqual(task_type, 'presentation')

        self.assertTrue(is_questions_expected(task_type, desc, components=components))

        scope = resolve_assignment_scope(assignment_title=title, assignment_desc=desc)
        self.assertEqual(scope['task_type'], 'presentation')
        self.assertTrue(scope['questions_expected'])
        self.assertIn('presentation', scope['task_components'])
        self.assertIn('question_answer', scope['task_components'])
        self.assertIn('презентац', scope['deliverable']['description'].lower())
        self.assertIn('відпові', scope['deliverable']['description'].lower())

    def test_03_file_questions_are_reference_material(self):
        """
        Тест 3. Вчитель задав «Створити презентацію», але у прикріпленому файлі є контрольні запитання:
        questions_expected is False (питання у файлі є довідковим матеріалом / самоперевіркою).
        """
        from .gemini_service import resolve_assignment_scope

        title = "Створити презентацію"
        desc = "Підготувати презентацію на тему 'Будова клітини' у PowerPoint."
        file_content = [
            "Тема уроку: Будова клітини\n"
            "Контрольні запитання:\n"
            "1. Що таке клітинна мембрана?\n"
            "2. Яку функцію виконують мітохондрії?\n"
            "3. Що міститься в клітинному ядрі?\n"
            "4. Яка роль ендоплазматичної сітки?\n"
            "5. Чим відрізняється рослинна клітина від тваринної?"
        ]

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=file_content
        )
        self.assertEqual(scope['task_type'], 'presentation')
        self.assertFalse(scope['questions_expected'])
        self.assertEqual(scope['assigned_task_count'], 1)

    def test_04_file_criteria_of_another_exercise_ignored(self):
        """
        Тест 4. Вчитель задав створити презентацію, а у файлі є блок критеріїв для «Вправа 1. Буклет»:
        Критерії іншої вправи отримують mandatory = False та не потрапляють до обов'язкових teacher_requirements.
        """
        from .gemini_service import (
            extract_criteria_and_requirements_from_text,
            resolve_assignment_scope
        )

        title = "Створити презентацію на тему 'Козацька доба'"
        desc = "Підготуйте учнівську презентацію про козацькі клейноди."
        file_text = (
            "Вправа 1. Буклет\n"
            "Критерії оцінювання:\n"
            "- Мінімум 8 слайдів\n"
            "- Обов'язковий висновок\n"
            "- Список використаних джерел\n\n"
            "Вправа 2. Презентація\n"
            "Підготуйте слайди."
        )

        extracted = extract_criteria_and_requirements_from_text(
            text=file_text,
            assignment_title=title,
            assignment_desc=desc,
            task_type='presentation'
        )
        for crit in extracted['criteria']:
            # Критерії під заголовком "Вправа 1. Буклет" не повинні бути обов'язковими для презентації
            if 'буклет' in crit.get('source_location', '').lower() or crit.get('applies_to') == 'other_exercise':
                self.assertFalse(crit['mandatory'])

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=[file_text]
        )
        teacher_reqs = scope.get('teacher_requirements', [])
        reqs_text = " ".join(teacher_reqs).lower()
        self.assertNotIn("буклет", reqs_text)

    def test_05_file_criteria_matching_presentation_applied(self):
        """
        Тест 5. Вчитель задав «Створити презентацію», і у файлі є критерії:
        «Критерії оцінювання цієї презентації: мінімум 8 слайдів, висновок, джерела».
        Критерії відповідають поточному завданню, мають mandatory = True і потрапляють до teacher_requirements.
        """
        from .gemini_service import (
            extract_criteria_and_requirements_from_text,
            resolve_assignment_scope
        )

        title = "Створити презентацію на тему 'Козацька доба'"
        desc = "Підготуйте учнівську презентацію."
        file_text = (
            "Критерії оцінювання цієї презентації:\n"
            "- Мінімум 8 слайдів (2 бали)\n"
            "- Обов'язковий висновок (2 бали)\n"
            "- Список використаних джерел (2 бали)"
        )

        extracted = extract_criteria_and_requirements_from_text(
            text=file_text,
            assignment_title=title,
            assignment_desc=desc,
            task_type='presentation'
        )
        self.assertTrue(len(extracted['criteria']) >= 3)
        for crit in extracted['criteria']:
            self.assertTrue(crit['mandatory'])

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=[file_text]
        )
        teacher_reqs = scope.get('teacher_requirements', [])
        self.assertTrue(any("висновок" in r or "джерел" in r or "слайд" in r for r in teacher_reqs))

    def test_06_programming_task_no_qa_expected(self):
        """
        Тест 6. Завдання «Написати програму Python»:
        task_type == 'programming', questions_expected is False.
        """
        from .gemini_service import (
            determine_assignment_task_type,
            is_questions_expected,
            resolve_assignment_scope
        )

        title = "Програмування мовою Python"
        desc = "Написати програму Python для обчислення факторіалу числа."

        task_type = determine_assignment_task_type(title=title, description=desc)
        self.assertEqual(task_type, 'programming')
        self.assertFalse(is_questions_expected(task_type, desc))

        scope = resolve_assignment_scope(assignment_title=title, assignment_desc=desc)
        self.assertEqual(scope['task_type'], 'programming')
        self.assertFalse(scope['questions_expected'])
        self.assertEqual(scope['deliverable']['type'], 'programming')

    def test_07_no_hallucinated_presentation_requirements(self):
        """
        Тест 7. Завдання «Створити презентацію» без додаткових вимог:
        Немає галюцинованих вимог (висновок, джерела, 8 слайдів, титульний слайд, збереження під своїм прізвищем).
        """
        from .gemini_service import (
            extract_task_requirements,
            generate_age_appropriate_student_guide,
            interpret_assignment_task
        )

        title = "Створити презентацію"
        desc = "Підготувати презентацію на тему 'Космічні дослідження'."

        reqs = extract_task_requirements(title=title, description=desc, task_type='presentation')
        reqs_text = " ".join(reqs).lower()
        self.assertNotIn("8 слайдів", reqs_text)
        self.assertNotIn("обов'язковий висновок", reqs_text)
        self.assertNotIn("список використаних джерел", reqs_text)
        self.assertNotIn("прізвищ", reqs_text)

        interp = interpret_assignment_task(title=title, description=desc)
        guide = generate_age_appropriate_student_guide(
            grade_str="7 клас",
            min_grade=7,
            assignment_title=title,
            assignment_desc=desc,
            task_interpretation=interp
        )
        guide_text = str(guide).lower()
        self.assertNotIn("під своїм прізвищем", guide_text)
        self.assertNotIn("обов'язково 8 слайдів", guide_text)
        self.assertNotIn("обов'язковий висновок на останньому слайді", guide_text)

    def test_08_student_guide_explains_both_composite_parts(self):
        """
        Тест 8. Учнівський гайд для складеного завдання «Створити презентацію про космос та дати відповіді на 3 питання»:
        Роз'яснення та покроковий план містять обидві складові.
        """
        from .gemini_service import (
            interpret_assignment_task,
            generate_age_appropriate_student_guide
        )

        title = "Створити презентацію про космос та відповісти на 3 контрольні запитання"
        desc = "Підготувати слайдову презентацію про планети та дати письмові відповіді на 3 запитання."

        interp = interpret_assignment_task(title=title, description=desc)
        self.assertIn('presentation', interp['task_components'])
        self.assertIn('question_answer', interp['task_components'])

        guide = generate_age_appropriate_student_guide(
            grade_str="8 клас",
            min_grade=8,
            assignment_title=title,
            assignment_desc=desc,
            task_interpretation=interp
        )
        guide_lower = guide.lower()
        self.assertIn("презентац", guide_lower)
        self.assertIn("відповід", guide_lower)

    @patch('feed.gemini_service.call_ai_api')
    def test_09_submission_only_presentation_no_qa_penalty(self, mock_ai):
        """
        Тест 9. Вчитель задав презентацію, у матеріалах є 5 контрольних питань.
        Учень здав презентацію без відповідей на питання. ШІ не карає за відсутність відповідей на 5 питань.
        """
        from .gemini_service import evaluate_submission_with_gemini

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Створити презентацію про історію комп'ютерів",
            description="Створити презентацію в PowerPoint на тему 'Історія розвитку ЕОМ'.",
            status=Assignment.STATUS_PUBLISHED
        )
        assignment.classes.add(self.class_group)

        # Прикріплюємо файл із контрольними питаннями
        file_obj = SimpleUploadedFile(
            "lesson_questions.txt",
            "Контрольні питання:\n1. Хто створив першу ЕОМ?\n2. Що таке ENIAC?\n3. Які є покоління ЕОМ?\n4. Що таке мікропроцесор?\n5. Хто такий Алан Тюрінг?".encode('utf-8'),
            content_type="text/plain"
        )
        AssignmentFile.objects.create(assignment=assignment, file=file_obj, original_name="lesson_questions.txt")

        student_pptx = SimpleUploadedFile(
            "computers_history.pptx",
            b"PK\x03\x04MockPptxContentForPresentation",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Оксана',
            last_name='Шевченко',
            class_group=self.class_group,
            teacher=self.teacher,
            file=student_pptx
        )

        mock_ai.return_value = (200, json.dumps({
            "suggested_grade": "11",
            "level": "Високий (10-12)",
            "tasks_total_count": 1,
            "tasks_completed_count": 1,
            "tasks_evaluated": [{"task_num": 1, "status": "completed"}],
            "summary": "Гарна презентація, але не виконано 5 питань.",
            "weaknesses": ["Немає відповідей на 5 запитань з файлу вчителя."],
            "strengths": ["Чудовий візуальний стиль", "Логічна структура"],
            "feedback_comment": "Потрібно було дати відповіді на 5 питань у форматі «питання-відповідь»."
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertEqual(result['task_type'], 'presentation')
        self.assertFalse(result['questions_expected'])

        weaknesses_str = " ".join(result.get('weaknesses', [])).lower()
        self.assertNotIn("немає відповідей на 5 запитань", weaknesses_str)
        self.assertNotIn("відсутність відповідей на запитання", weaknesses_str)
        self.assertNotIn("питання-відповідь", (result.get('feedback_comment') or '').lower())

    def test_10_specific_exercise_2_non_positional(self):
        """
        Тест 10. Вчитель вказав «Виконати вправу 2».
        У файлі є: «Вправа 1», «Приклад розв'язання», «Контрольне питання», «Вправа 2», «Вправа 3».
        find_question_by_task_num знаходить саме «Вправа 2», а не бере позиційний індекс 1 («Приклад»).
        """
        from .gemini_service import find_question_by_task_num

        pool = [
            "Вправа 1. Поняття моделі та її види.",
            "Приклад розв'язання задачі з фізики.",
            "Контрольне запитання для самоперевірки.",
            "Вправа 2. Побудова інформаційної діаграми у табличному процесорі.",
            "Вправа 3. Аналіз отриманих результатів."
        ]

        found = find_question_by_task_num(pool, 2)
        self.assertIsNotNone(found)
        self.assertIn("Вправа 2", found)
        self.assertIn("Побудова інформаційної діаграми", found)
        self.assertNotIn("Приклад", found)
        self.assertNotIn("Контрольне запитання", found)

    def test_11_project_with_4_exercises_is_single_task(self):
        """
        Тест 11. Навчальний проєкт з 4 вправами у файлі — це 1 комплексне завдання,
        а не 4 окремі завдання. tasks_total_count == 1.
        """
        from .gemini_service import resolve_assignment_scope

        title = "Робота над проєктом 'Розумний будинок'"
        desc = "Робота над проєктом. Підготувати матеріали першого етапу."
        teacher_files = [
            "Тема: Проєкт 'Розумний будинок'\n"
            "Вправа 1. Дослідження датчиків температури.\n"
            "Вправа 2. Проєктування схеми освітлення.\n"
            "Вправа 3. Налаштування мікроконтролера.\n"
            "Вправа 4. Підготовка підсумкового звіту."
        ]

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=teacher_files
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertEqual(scope['task_type'], 'project')

    @patch('feed.gemini_service.call_ai_api')
    def test_12_fallback_without_ai_api_maintains_truth(self, mock_ai):
        """
        Тест 12. При недоступності ШІ API (помилка 500) fallback гарантовано зберігає правду:
        tasks_total_count == 1, task_type == 'presentation', questions_expected is False,
        deliverable та teacher_requirements узгоджені зі scope.
        """
        from .gemini_service import analyze_assignment_task_understanding

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Створити презентацію на тему 'Архітектура Києва'",
            description="Створити презентацію про визначні архітектурні пам'ятки Києва.",
            status=Assignment.STATUS_PUBLISHED
        )
        assignment.classes.add(self.class_group)

        # Імітуємо відмову зовнішнього API
        mock_ai.return_value = (500, None, "AI Service Down", {})

        analysis = analyze_assignment_task_understanding(assignment, force_refresh=True)
        self.assertEqual(analysis['status'], 'success')
        data = analysis['data']

        self.assertEqual(data['tasks_total_count'], 1)
        self.assertEqual(data['task_type'], 'presentation')
        self.assertFalse(data['questions_expected'])
        self.assertEqual(data['deliverable']['type'], 'presentation')
        self.assertIsInstance(data['teacher_requirements'], list)
        self.assertIn("Покроковий план", data['student_explanation'])


class TeacherIntentAndTaskUnderstandingTests(TestCase):
    """
    Набір тестів для перевірки визначення наміру вчителя (Teacher Intent)
    та фінального розуміння завдання (Final Task Understanding) відповідно до Розділу 28.
    """

    def setUp(self):
        self.teacher_user = User.objects.create_user(
            username='teacher_intent_tester',
            password='password123',
            is_staff=True
        )
        self.teacher = Teacher.objects.create(
            user=self.teacher_user,
            full_name='Коваленко Оксана Василівна'
        )
        self.class_group = ClassGroup.objects.create(grade=9, letter='Б', name='9-Б')
        self.subject = Subject.objects.create(name='Інформатика', icon='💻', color='#3b82f6')
        self.teacher.classes.add(self.class_group)
        self.teacher.subjects.add(self.subject)

        self.ai_settings = AISettings.get_solo()
        self.ai_settings.is_enabled = True
        self.ai_settings.api_key = "AIzaSyFakeKeyForTeacherIntentTesting"
        self.ai_settings.ai_provider = 'gemini'
        self.ai_settings.model_name = 'gemini-2.5-flash'
        self.ai_settings.save()

    def test_01_presentation_with_file_self_check_questions(self):
        """
        ТЕСТ 1: Презентація + файл учителя з питаннями для самоперевірки.
        Вчитель: "Створити презентацію". Файл містить 5 питань для самоперевірки.
        Очікується: task_type = presentation, questions_expected = False,
        assigned_task_count = 1, жодне з 5 питань не стає обов'язковим завданням.
        """
        from .gemini_service import resolve_assignment_scope

        title = "Створити презентацію"
        desc = "Створити презентацію про архітектуру сучасних комп'ютерів."
        teacher_files = [
            "Матеріал уроку: Архітектура комп'ютера.\n"
            "Питання для самоперевірки:\n"
            "1. Що таке процесор?\n"
            "2. Які функції оперативної пам'яті?\n"
            "3. Що таке шина даних?\n"
            "4. Чим відрізняється SSD від HDD?\n"
            "5. Яке призначення материнської плати?"
        ]

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=teacher_files
        )
        self.assertEqual(scope['task_type'], 'presentation')
        self.assertFalse(scope['questions_expected'])
        self.assertEqual(scope['assigned_task_count'], 1)

        # Жодне з питань самоперевірки не є обов'язковим завданням
        assigned_texts = [t.get('description', '') for t in scope.get('assigned_tasks', [])]
        for q_text in ["Що таке процесор", "оперативної пам'яті", "шина даних"]:
            for at in assigned_texts:
                self.assertNotIn(q_text, at)

        ftu = scope.get('final_task_understanding') or {}
        self.assertEqual(ftu.get('task_type'), 'presentation')
        self.assertFalse(ftu.get('questions_expected'))

    def test_02_presentation_and_questions_explicitly_requested(self):
        """
        ТЕСТ 2: Презентація + вчитель прямо вимагає відповісти на питання.
        Вчитель: "Створити презентацію та дати відповіді на питання зі слайду 6".
        Очікується: task_type = presentation, questions_expected = True,
        components містить presentation та question_answer.
        """
        from .gemini_service import resolve_assignment_scope, identify_task_components

        title = "Створити презентацію та дати відповіді на питання зі слайду 6"
        desc = "Підготуйте презентацію про історію Інтернету та обов'язково дайте письмові відповіді на контрольні питання зі слайду 6."

        components = identify_task_components(title=title, description=desc)
        self.assertIn('presentation', components)
        self.assertIn('question_answer', components)

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc
        )
        self.assertEqual(scope['task_type'], 'presentation')
        self.assertTrue(scope['questions_expected'])
        self.assertIn('question_answer', scope.get('task_interpretation', {}).get('task_components', []))

    def test_03_project_with_multiple_file_exercises(self):
        """
        ТЕСТ 3: Проєкт + файл з багатьма вправами.
        Вчитель: "Робота над навчальним проєктом".
        Файл: Вправа 1, Вправа 2, Вправа 3, Вправа 4.
        Очікується: assigned_task_count = 1, жодна вправа не є окремим обов'язковим завданням.
        """
        from .gemini_service import resolve_assignment_scope

        title = "Робота над навчальним проєктом"
        desc = "Створення мультимедійного навчального проєкту 'Моє рідне місто'."
        teacher_files = [
            "Презентація уроку:\n"
            "Вправа 1. Пошук інформації в мережі Інтернет.\n"
            "Вправа 2. Оформлення текстового звіту.\n"
            "Вправа 3. Створення графічних схем.\n"
            "Вправа 4. Підготовка захисту проєкту."
        ]

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=teacher_files
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertTrue(scope.get('is_single_complex_task'))
        unassigned = scope.get('unassigned_material', [])
        ignored = scope.get('ignored_found_tasks', [])
        self.assertTrue(len(unassigned) > 0 or len(ignored) > 0)

    def test_04_explicit_exercise_2_from_file(self):
        """
        ТЕСТ 4: Явне завдання на одну вправу.
        Вчитель: "Виконати вправу 2".
        Файл: Вправа 1, Вправа 2, Вправа 3.
        Очікується: assigned_task_count = 1, assigned_scope містить тільки вправу 2,
        вправи 1 і 3 є unassigned_material.
        """
        from .gemini_service import resolve_assignment_scope

        title = "Практична робота"
        desc = "Виконати вправу 2 з прикріпленого файлу."
        teacher_files = [
            "Практичні завдання:\n"
            "Вправа 1. Розрахунок середнього значення у Excel.\n"
            "Вправа 2. Побудова кругової діаграми розподілу витрат.\n"
            "Вправа 3. Фільтрація та сортування таблиці."
        ]

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=teacher_files
        )
        self.assertEqual(scope['assigned_task_count'], 1)
        self.assertEqual(scope['teacher_specific_task_nums'], [2])
        assigned_tasks = scope.get('assigned_tasks', [])
        self.assertEqual(len(assigned_tasks), 1)
        self.assertIn("2", str(assigned_tasks[0].get('task_num', '')))
        self.assertIn("кругової діаграми", assigned_tasks[0].get('description', ''))
        unassigned_str = " ".join(scope.get('unassigned_material', []))
        self.assertIn("1", unassigned_str)
        self.assertIn("3", unassigned_str)

    def test_05_presentation_file_has_code_and_tables(self):
        """
        ТЕСТ 5: Презентація з кодом/таблицями у файлі вчителя.
        Вчитель: "Створити презентацію на тему...".
        Файл містить фрагменти коду Python та зведені таблиці.
        Очікується: task_type = presentation, НЕ programming, НЕ table.
        """
        from .gemini_service import resolve_assignment_scope

        title = "Створити презентацію на тему 'Сучасні технології веб-розробки'"
        desc = "Підготуйте комп'ютерну презентацію про технології веб-розробки."
        teacher_files = [
            "Лекційний матеріал:\n"
            "Приклад коду на Python:\n"
            "```python\ndef hello_world():\n    return 'Hello'\n```\n"
            "Таблиця порівняння фреймворків:\n"
            "| Фреймворк | Мова | Швидкість |\n"
            "| Django | Python | Висока |\n"
            "| Express | JS | Дуже висока |\n"
        ]

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=teacher_files
        )
        self.assertEqual(scope['task_type'], 'presentation')
        self.assertNotEqual(scope['task_type'], 'programming')
        self.assertNotEqual(scope['task_type'], 'table')

    def test_06_database_with_presentation_slides_file(self):
        """
        ТЕСТ 6: База даних + слайди лекції.
        Вчитель: "Створити базу даних бібліотеки".
        Файл: слайди лекції (.pptx) з теорією нормалізації БД.
        Очікується: task_type = database, НЕ presentation.
        """
        from .gemini_service import resolve_assignment_scope

        title = "Створити базу даних бібліотеки"
        desc = "Розробити реляційну базу даних бібліотеки у Microsoft Access або SQLite."
        teacher_files = [
            "Презентація до уроку «Основи реляційних баз даних.pptx»:\n"
            "Слайд 1. Що таке нормальні форми баз даних.\n"
            "Слайд 2. Зв'язки між таблицями один-до-багатьох.\n"
            "Слайд 3. Приклади SQL-запитів SELECT, INSERT."
        ]

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=teacher_files
        )
        self.assertEqual(scope['task_type'], 'database')
        self.assertNotEqual(scope['task_type'], 'presentation')

    def test_07_presentation_about_programming(self):
        """
        ТЕСТ 7: "Створити презентацію про програмування".
        Очікується: task_type = presentation, topic містить 'програмування',
        НЕ task_type = programming.
        """
        from .gemini_service import resolve_assignment_scope, extract_assignment_topic_and_action

        title = "Створити презентацію про програмування"
        desc = "Підготуйте презентацію про історію мов програмування."

        parsed = extract_assignment_topic_and_action(title, desc)
        topic = parsed['topic']
        action = parsed['action_text']
        self.assertIn("програмуванн", topic.lower())
        self.assertNotIn("programming", action.lower())

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc
        )
        self.assertEqual(scope['task_type'], 'presentation')
        self.assertNotEqual(scope['task_type'], 'programming')
        self.assertIn("програмуванн", scope.get('topic', '').lower())

    def test_08_programming_with_questions_and_presentation_in_file(self):
        """
        ТЕСТ 8: "Створити програму..." + файл з питаннями і презентацією.
        Очікується: task_type = programming, deliverable містить code/program,
        questions_expected = False.
        """
        from .gemini_service import resolve_assignment_scope

        title = "Створити програму розрахунку площі багатокутника"
        desc = "Написати програму мовою Python для обчислення площі багатокутника за координатами вершин."
        teacher_files = [
            "Презентація лекції з геометричних алгоритмів:\n"
            "Слайд 5. Питання для обговорення:\n"
            "1. Яка формула площі Гаусса?\n"
            "2. Як вводити список точок?\n"
            "3. Що робити при самоперетині контуру?"
        ]

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc,
            teacher_files_content=teacher_files
        )
        self.assertEqual(scope['task_type'], 'programming')
        self.assertFalse(scope['questions_expected'])
        deliverable = scope.get('deliverable', {})
        self.assertIn(deliverable.get('type'), ['code', 'program', 'file', 'programming'])

    def test_09_presentation_no_unassigned_criteria_hallucinated(self):
        """
        ТЕСТ 9: Презентація без додаткових вимог учителя.
        Вчитель: "Створити презентацію на тему «Хмарні сервіси»".
        Очікується: система НЕ вимагає обов'язковий висновок, джерела, титульний слайд,
        мінімум 8 слайдів, мінімум 3 зображення як обов'язкові критерії.
        Вони є лише generic_recommendations і не знижують бал.
        """
        from .gemini_service import resolve_assignment_scope

        title = "Створити презентацію на тему «Хмарні сервіси»"
        desc = "Підготуйте презентацію про сучасні хмарні сховища та сервіси."

        scope = resolve_assignment_scope(
            assignment_title=title,
            assignment_desc=desc
        )
        mandatory_reqs = scope.get('mandatory_requirements', [])
        # Обов'язкові вимоги не містять вигаданих обмежень
        for r in mandatory_reqs:
            r_str = str(r).lower()
            self.assertNotIn("8 слайдів", r_str)
            self.assertNotIn("3 зображення", r_str)
            self.assertNotIn("обов'язковий висновок", r_str)

        # Перевіряємо evaluation_plan
        eval_plan = scope.get('evaluation_plan', {})
        criteria = eval_plan.get('criteria', [])
        criteria_names = [c.get('name', '').lower() for c in criteria if isinstance(c, dict)]
        for c_name in criteria_names:
            self.assertNotIn("8 слайдів", c_name)
            self.assertNotIn("3 зображення", c_name)

    @patch('feed.gemini_service.call_ai_api')
    def test_10_student_comment_does_not_override_scope(self, mock_ai):
        """
        ТЕСТ 10: Учень здав щось інше, ніж задав учитель.
        Вчитель: "Створити презентацію".
        Учень здав текстовий файл з коментарем: "Я виконав вправу 4".
        Очікується: teacher_intent має task_type = presentation,
        final_task_understanding НЕ перетворюється на вправу 4,
        Scope of work залишається presentation.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import Assignment, Submission
        from .gemini_service import evaluate_submission_with_gemini

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title="Створити презентацію про штучний інтелект",
            description="Підготувати учнівську презентацію про штучний інтелект.",
            status=Assignment.STATUS_PUBLISHED
        )
        assignment.classes.add(self.class_group)

        student_txt = SimpleUploadedFile(
            "exercise4.txt",
            b"Vidpovid na vpravu 4: algoritmy...",
            content_type="text/plain"
        )
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Михайло',
            last_name='Гриценко',
            class_group=self.class_group,
            teacher=self.teacher,
            file=student_txt,
            comment_student="Я виконав вправу 4"
        )

        mock_ai.return_value = (200, json.dumps({
            "suggested_grade": "4",
            "level": "Початковий (1-3)",
            "tasks_total_count": 1,
            "tasks_completed_count": 0,
            "tasks_evaluated": [{"task_num": 1, "status": "missing"}],
            "summary": "Учень здав вправу 4 замість презентації.",
            "weaknesses": ["Здано текстову відповідь на вправу 4 замість презентації."],
            "strengths": [],
            "feedback_comment": "Завдання вимагало створити презентацію, а не текстовий розв'язок вправи 4."
        }, ensure_ascii=False), None, {})

        result = evaluate_submission_with_gemini(submission)
        self.assertEqual(result['task_type'], 'presentation')
        self.assertNotEqual(result['task_type'], 'text')
        self.assertEqual(result.get('tasks_total_count'), 1)

        ftu = result.get('final_task_understanding') or {}
        self.assertEqual(ftu.get('task_type'), 'presentation')

        t_intent = result.get('teacher_intent') or {}
        self.assertEqual(t_intent.get('task_type'), 'presentation')
        self.assertNotIn("вправа 4", t_intent.get('what_teacher_asks', '').lower())
