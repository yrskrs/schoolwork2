"""
Тести для REST API інтеграції з локальними та зовнішніми журналами оцінок.
"""

import json
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from .models import Teacher, ClassGroup, Student, Subject, Assignment, Submission, JournalAPIKey


class JournalAPITests(TestCase):
    def setUp(self):
        from .middleware import set_has_admin
        set_has_admin(True)
        self.client = Client()
        self.user = User.objects.create_user(username='teacher1', password='pass123', is_staff=True, is_superuser=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Петренко Олена Іванівна')

        self.class_10a = ClassGroup.objects.create(name='10-А', grade=10, letter='А')
        self.class_9b = ClassGroup.objects.create(name='9-Б', grade=9, letter='Б')
        self.teacher.classes.add(self.class_10a, self.class_9b)

        self.subject = Subject.objects.create(name='Інформатика', icon='💻', color='#6366f1')
        self.teacher.subjects.add(self.subject)

        # Учень та завдання
        self.student = Student.objects.create(
            first_name='Тарас',
            last_name='Шевченко',
            class_group=self.class_10a
        )

        self.assignment = Assignment.objects.create(
            title='Практична робота №1: Бази даних',
            teacher=self.teacher,
            subject=self.subject,
            status=Assignment.STATUS_PUBLISHED
        )
        self.assignment.classes.add(self.class_10a)

        self.submission = Submission.objects.create(
            assignment=self.assignment,
            student=self.student,
            first_name='Тарас',
            last_name='Шевченко',
            class_group=self.class_10a,
            teacher=self.teacher,
            grade='11',
            teacher_comment='Чудово виконана робота!',
            is_latest_attempt=True,
            graded_by=self.user,
            graded_at=timezone.now()
        )

        # Ключ для тестів
        self.api_key_token = JournalAPIKey.generate_key()
        self.api_key = JournalAPIKey.objects.create(
            name='Тестовий локальний журнал',
            key=self.api_key_token,
            teacher=self.teacher,
            can_export_grades=True,
            can_import_roster=True,
            is_active=True
        )

    def test_auth_missing_key_returns_401(self):
        url = reverse('api_journal_export_grades')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data['status'], 'error')

    def test_auth_invalid_key_returns_403_or_401(self):
        url = reverse('api_journal_export_grades')
        response = self.client.get(url, HTTP_AUTHORIZATION='Bearer invalid_key_12345')
        self.assertEqual(response.status_code, 403)

    def test_auth_inactive_key_returns_403(self):
        self.api_key.is_active = False
        self.api_key.save()

        url = reverse('api_journal_export_grades')
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.api_key_token}')
        self.assertEqual(response.status_code, 403)
        self.assertIn('деактивовано', response.json()['detail'])

    def test_export_permission_denied_when_flag_false(self):
        self.api_key.can_export_grades = False
        self.api_key.save()

        url = reverse('api_journal_export_grades')
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.api_key_token}')
        self.assertEqual(response.status_code, 403)
        self.assertIn('can_export_grades', response.json()['detail'])

    def test_export_grades_success_via_bearer_header(self):
        url = reverse('api_journal_export_grades')
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.api_key_token}')
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['count'], 1)
        grade_item = data['grades'][0]
        self.assertEqual(grade_item['student']['last_name'], 'Шевченко')
        self.assertEqual(grade_item['student']['first_name'], 'Тарас')
        self.assertEqual(grade_item['class']['name'], '10-А')
        self.assertEqual(grade_item['grade'], '11')
        self.assertEqual(grade_item['assignment']['title'], 'Практична робота №1: Бази даних')

        # Статистика використання ключа оновилась
        self.api_key.refresh_from_db()
        self.assertEqual(self.api_key.requests_count, 1)
        self.assertIsNotNone(self.api_key.last_used_at)

    def test_export_grades_via_x_api_key_header(self):
        url = reverse('api_journal_export_grades')
        response = self.client.get(url, HTTP_X_API_KEY=self.api_key_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)

    def test_export_grades_via_query_param(self):
        url = f"{reverse('api_journal_export_grades')}?api_key={self.api_key_token}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)

    def test_export_grades_filtering_by_class(self):
        # Додаємо роботу для іншого класу
        student_9b = Student.objects.create(first_name='Іван', last_name='Франко', class_group=self.class_9b)
        Submission.objects.create(
            assignment=self.assignment,
            student=student_9b,
            first_name='Іван',
            last_name='Франко',
            class_group=self.class_9b,
            teacher=self.teacher,
            grade='10',
            is_latest_attempt=True
        )

        url = reverse('api_journal_export_grades')
        # Фільтр по 10-А
        resp_10a = self.client.get(f"{url}?class_name=10-А", HTTP_AUTHORIZATION=f'Bearer {self.api_key_token}')
        self.assertEqual(resp_10a.json()['count'], 1)
        self.assertEqual(resp_10a.json()['grades'][0]['class']['name'], '10-А')

        # Фільтр по 9-Б
        resp_9b = self.client.get(f"{url}?class_name=9-Б", HTTP_AUTHORIZATION=f'Bearer {self.api_key_token}')
        self.assertEqual(resp_9b.json()['count'], 1)
        self.assertEqual(resp_9b.json()['grades'][0]['class']['name'], '9-Б')

    def test_get_roster_returns_classes_and_students(self):
        url = reverse('api_journal_roster')
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.api_key_token}')
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertGreaterEqual(data['count_classes'], 2)
        class_names = [c['name'] for c in data['classes']]
        self.assertIn('10-А', class_names)

    def test_post_roster_sync_hierarchical_format(self):
        url = reverse('api_journal_roster')
        payload = {
            "classes": [
                {
                    "name": "10-А",
                    "students": [
                        {"last_name": "Шевченко", "first_name": "Тарас"},
                        {"last_name": "Костенко", "first_name": "Ліна"}
                    ]
                },
                {
                    "name": "11-В",
                    "students": [
                        {"last_name": "Сковорода", "first_name": "Григорій"}
                    ]
                }
            ]
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {self.api_key_token}'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        # Ліна Костенко та Григорій Сковорода мають бути створені, Тарас Шевченко оновлено/наявний
        self.assertEqual(data['created_students'], 2)
        self.assertIn('11-В', data['created_classes'])

        # Перевіряємо наявність у БД
        self.assertTrue(Student.objects.filter(last_name='Костенко', class_group__name='10-А').exists())
        self.assertTrue(Student.objects.filter(last_name='Сковорода', class_group__name='11-В').exists())

    def test_post_roster_sync_flat_format(self):
        url = reverse('api_journal_roster')
        payload = {
            "students": [
                {"last_name": "Котляревський", "first_name": "Іван", "class_name": "8-А"},
                {"last_name": "Нечуй-Левицький", "first_name": "Іван", "class_name": "8-А"}
            ]
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {self.api_key_token}'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['created_students'], 2)

        self.assertTrue(ClassGroup.objects.filter(name='8-А').exists())
        self.assertTrue(Student.objects.filter(last_name='Котляревський', class_group__name='8-А').exists())

    def test_settings_tab_api_view_and_actions(self):
        self.api_key.teacher = self.teacher
        self.api_key.save()
        self.client.force_login(self.user)
        settings_url = reverse('teacher_settings')

        # 1. Відкриття вкладки ?tab=api
        resp = self.client.get(f"{settings_url}?tab=api")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'API ключі для локального журналу')
        self.assertContains(resp, 'Тестовий локальний журнал')

        # 2. Створення нового ключа через форму
        resp_create = self.client.post(settings_url, {
            'action': 'create_journal_api_key',
            'key_name': 'Журнал 2.0',
            'can_export_grades': '1',
            'can_import_roster': '1'
        }, follow=True)
        self.assertEqual(resp_create.status_code, 200)
        self.assertTrue(JournalAPIKey.objects.filter(name='Журнал 2.0').exists())

        new_k = JournalAPIKey.objects.get(name='Журнал 2.0')

        # 3. Призупинення ключа (toggle)
        self.client.post(settings_url, {
            'action': 'toggle_journal_api_key',
            'key_id': new_k.id
        })
        new_k.refresh_from_db()
        self.assertFalse(new_k.is_active)

        # 4. Видалення ключа
        self.client.post(settings_url, {
            'action': 'delete_journal_api_key',
            'key_id': new_k.id
        })
        self.assertFalse(JournalAPIKey.objects.filter(id=new_k.id).exists())

    def test_student_autocomplete_returns_class_roster(self):
        # Перевірка що /api/students-autocomplete/ повертає список класу з параметром limit
        Student.objects.create(first_name='Леся', last_name='Українка', class_group=self.class_10a)
        url = reverse('api_students_autocomplete')
        resp = self.client.get(f"{url}?class_group_id={self.class_10a.id}&limit=100")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        names = [s['full_name'] for s in data['students']]
        self.assertIn('Шевченко Тарас', names)
        self.assertIn('Українка Леся', names)
