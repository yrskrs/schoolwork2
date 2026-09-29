from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch, MagicMock

from feed.models import (
    Teacher, Subject, ClassGroup, Student, Assignment,
    Submission, AISettings, AIErrorLog, School
)
from feed.gemini_service import evaluate_submission_with_gemini
from feed.student_matcher import auto_bind_coauthors_from_comment


class AIThinkingAndErrorLogTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='teacher1', email='t1@test.com', password='password123')
        self.school = School.objects.create(name='Тестова Школа', admin=self.user)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Тестовий')
        self.subject = Subject.objects.create(name='Інформатика')
        self.class_9g = ClassGroup.objects.create(grade=9, letter='Г', name='9-Г')
        self.class_9v = ClassGroup.objects.create(grade=9, letter='В', name='9-В')

        self.assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Поняття таблиці, поля, запису, ключа таблиці',
            description='Створіть базу даних',
            ai_thinking_mode=False
        )
        self.assignment.classes.add(self.class_9g)

        self.ai_settings = AISettings.get_solo()
        self.ai_settings.api_key = 'test-fake-key'
        self.ai_settings.default_thinking_mode = False
        self.ai_settings.save()

        # Студент Малюх в 9Г
        self.student_malukh = Student.objects.create(
            class_group=self.class_9g,
            last_name='Малюх',
            first_name='Кіріл'
        )

        # Студент Цапок в 9В (інший клас!)
        self.student_tsapok = Student.objects.create(
            class_group=self.class_9v,
            last_name='Цапок',
            first_name='Кирил'
        )

        self.submission = Submission.objects.create(
            assignment=self.assignment,
            student=self.student_malukh,
            class_group=self.class_9g,
            first_name='Кіріл',
            last_name='Малюх',
            file=SimpleUploadedFile("db.accdb", b"\x00" * 200, content_type="application/msaccess"),
            is_latest_attempt=True
        )

        self.client = Client()

    def test_thinking_mode_resolution(self):
        """Перевіряємо пріоритет увімкнення Thinking mode: force_thinking > assignment > settings."""
        # 1. За замовчуванням False
        with patch('feed.gemini_service.call_ai_api') as mock_call:
            mock_call.return_value = (200, '{"suggested_grade": "8", "feedback": "Добре"}', '', {})
            evaluate_submission_with_gemini(self.submission)
            # thinking budget = 0 при use_thinking=False
            call_kwargs = mock_call.call_args[1] if mock_call.call_args else {}
            self.assertEqual(call_kwargs.get('thinking_budget'), 0)

        # 2. force_thinking=True примусово вмикає
        with patch('feed.gemini_service.call_ai_api') as mock_call:
            mock_call.return_value = (200, '{"suggested_grade": "8", "feedback": "Добре"}', '', {})
            evaluate_submission_with_gemini(self.submission, force_thinking=True)
            call_kwargs = mock_call.call_args[1] if mock_call.call_args else {}
            self.assertEqual(call_kwargs.get('thinking_budget'), 4096)

        # 3. assignment.ai_thinking_mode=True вмикає за замовчуванням для завдання
        self.assignment.ai_thinking_mode = True
        self.assignment.save()
        with patch('feed.gemini_service.call_ai_api') as mock_call:
            mock_call.return_value = (200, '{"suggested_grade": "8", "feedback": "Добре"}', '', {})
            evaluate_submission_with_gemini(self.submission)
            call_kwargs = mock_call.call_args[1] if mock_call.call_args else {}
            self.assertEqual(call_kwargs.get('thinking_budget'), 4096)

    def test_ai_error_log_records_only_on_failure(self):
        """Журнал помилок фіксує лише невдалі запити та failover, а не успішні."""
        AIErrorLog.objects.all().delete()

        # Успішний запит не створює лог помилки
        with patch('feed.gemini_service.call_ai_api') as mock_call:
            mock_call.return_value = (200, '{"suggested_grade": "9", "score_level": "Достатній", "feedback": "Ок"}', '', {})
            evaluate_submission_with_gemini(self.submission)
            self.assertEqual(AIErrorLog.objects.count(), 0)

        # Помилка API (наприклад 429 Rate Limit) створює запис у журналі помилок
        with patch('feed.gemini_service.call_ai_api') as mock_call:
            mock_call.return_value = (429, None, 'Rate limit exceeded for gemini', {})
            evaluate_submission_with_gemini(self.submission, teacher=self.teacher)
            self.assertGreaterEqual(AIErrorLog.objects.count(), 1)
            err = AIErrorLog.objects.first()
            self.assertEqual(err.status_code, 429)
            self.assertEqual(err.submission, self.submission)
            self.assertEqual(err.teacher, self.teacher)

    def test_clear_ai_error_logs_action(self):
        """Вчитель може очистити журнал помилок ШІ через налаштування."""
        AIErrorLog.objects.create(
            teacher=self.teacher,
            submission=self.submission,
            action='evaluation',
            error_message='Тестова помилка'
        )
        self.assertEqual(AIErrorLog.objects.count(), 1)

        self.client.force_login(self.user)
        resp = self.client.post(reverse('teacher_settings'), {
            'action': 'clear_ai_error_logs'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(AIErrorLog.objects.count(), 0)

    def test_cross_class_student_isolation(self):
        """Студент з іншого класу (9-В) не повинен авто-створюватися і прив'язуватися до здачі в 9-Г."""
        self.submission.comment_student = "Виконував Цапок Кирил"
        self.submission.save()

        # Викликаємо функцію прив'язки
        auto_bind_coauthors_from_comment(self.submission)

        # Перевіряємо що в 9Г не створено студента-дубліката Цапок Кирил
        duplicate_students_in_9g = Student.objects.filter(class_group=self.class_9g, last_name='Цапок')
        self.assertEqual(duplicate_students_in_9g.count(), 0)

        # Перевіряємо що не створено фіктивну здачу для Цапок в 9Г
        peer_subs = Submission.objects.filter(assignment=self.assignment, last_name='Цапок')
        self.assertEqual(peer_subs.count(), 0)

    def test_gr_average_overrides_false_grade_ten(self):
        """Якщо оцінки за ГР низькі (наприклад ГР3=3, ГР4=3, сер=3), оцінка не повинна ставати 10."""
        with patch('feed.gemini_service.call_ai_api') as mock_call:
            mock_call.return_value = (200, '''{
                "suggested_grade": "Доопрацювати",
                "score_level": "Початковий",
                "feedback": "Файл порожній",
                "gr_results": [
                    {"code": "ГР 3", "name": "Працює в цифровому середовищі", "grade": 3, "level": "Початковий", "comment": "Пусто"},
                    {"code": "ГР 4", "name": "Безпечно працює", "grade": 3, "level": "Початковий", "comment": "Без наповнення"}
                ]
            }''', '', {})
            res = evaluate_submission_with_gemini(self.submission)
            self.assertEqual(res['status'], 'success')
            self.submission.refresh_from_db()
            # Оцінка залишається 'Доопрацювати' або початкового рівня 3, але НЕ завищується до '10' (Високий)!
            self.assertNotEqual(self.submission.ai_suggested_grade, '10')
            self.assertIn(self.submission.ai_suggested_grade, ['3', 'Доопрацювати'])
            self.assertNotIn('висок', self.submission.ai_score_level.lower())
