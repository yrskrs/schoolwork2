from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch, MagicMock

from feed.models import (
    Teacher, Subject, ClassGroup, Student, Assignment,
    Submission, AISettings, AIErrorLog, AIRequestLog, School
)
from feed.gemini_service import evaluate_submission_with_gemini
from feed.student_matcher import auto_bind_coauthors_from_comment


class AIThinkingAndErrorLogTestCase(TestCase):
    def setUp(self):
        from django.core.cache import caches
        caches['default'].clear()
        caches['ai_materials'].clear()
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

    def test_automatic_fallback_on_high_demand_503_or_429(self):
        """Перевіряємо що при 503/429 (High Demand) на основній моделі, система реально перемикається на резервну модель."""
        self.ai_settings.model_name = 'gemini-3.8-flash'
        self.ai_settings.backup_model_name = 'gemini-3.6-flash'
        self.ai_settings.active_fallback_chain = ['gemini-3.8-flash', 'gemini-3.6-flash']
        self.ai_settings.auto_fallback_enabled = True
        self.ai_settings.save()

        AIErrorLog.objects.all().delete()
        AIRequestLog.objects.all().delete()

        # Перший виклик (gemini-3.8-flash) видає 503 High Demand
        # Другий виклик (gemini-3.6-flash) успішно оцінює роботу
        def fake_call_ai_api(**kwargs):
            m_name = kwargs.get('model_name')
            if m_name == 'gemini-3.8-flash':
                return (503, None, 'This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.', {})
            elif m_name == 'gemini-3.6-flash':
                return (200, '{"suggested_grade": "11", "score_level": "Високий", "feedback": "Відмінна робота"}', '', {'prompt_tokens': 150, 'completion_tokens': 45, 'total_tokens': 195})
            return (500, None, 'Unknown model', {})

        with patch('feed.gemini_service.call_ai_api', side_effect=fake_call_ai_api):
            res = evaluate_submission_with_gemini(self.submission, teacher=self.teacher)

            self.assertEqual(res['status'], 'success')
            self.submission.refresh_from_db()
            # Перевіряємо що зберіглась оцінка і саме резервна модель яка реально виконала запит!
            self.assertEqual(self.submission.ai_suggested_grade, '11')
            self.assertEqual(self.submission.ai_model_used, 'gemini-3.6-flash')

            # Перевіряємо що в налаштуваннях зафіксовано спрацювання failover та його причину
            self.ai_settings.refresh_from_db()
            self.assertIsNotNone(self.ai_settings.last_failover_at)
            self.assertIn('gemini-3.8-flash', self.ai_settings.last_failover_reason)
            self.assertIn('503', self.ai_settings.last_failover_reason)

            # Перевіряємо що журнал помилок зафіксував failover_triggered=True
            err = AIErrorLog.objects.filter(model_name='gemini-3.8-flash').first()
            self.assertIsNotNone(err)
            self.assertTrue(err.failover_triggered)
            self.assertEqual(err.status_code, 503)

    def test_ai_request_log_metrics_and_used_model_stats(self):
        """Перевіряємо логування запитів у AIRequestLog та формування статистики тільки для використаних моделей."""
        AIRequestLog.objects.all().delete()

        # Створюємо запити для двох моделей
        AIRequestLog.objects.create(
            model_name='gemini-3.6-flash',
            provider='gemini',
            action='evaluation',
            status_code=200,
            is_success=True,
            prompt_tokens=200,
            completion_tokens=60,
            total_tokens=260
        )
        AIRequestLog.objects.create(
            model_name='gemini-3.8-flash',
            provider='gemini',
            action='evaluation',
            status_code=503,
            is_success=False,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0
        )

        self.client.force_login(self.user)
        resp = self.client.get(reverse('teacher_settings') + '?tab=ai&stats_days=7')
        self.assertEqual(resp.status_code, 200)

        # Перевіряємо наявність контексту
        used_stats = resp.context.get('used_model_usage_stats', [])
        used_model_names = [s['name'] for s in used_stats]

        # Тільки використані моделі повинні бути в статистиці
        self.assertIn('gemini-3.6-flash', used_model_names)
        self.assertIn('gemini-3.8-flash', used_model_names)
        self.assertNotIn('gemini-1.5-pro', used_model_names) # Невикористана модель не повинна відображатися!

        # Перевіряємо що передано каталог моделей Gemini
        groups = resp.context['provider_catalog']
        catalog = next(group['models'] for group in groups if group['provider'] == 'gemini')
        self.assertGreater(len(catalog), 0)
        catalog_names = [m['name'] for m in catalog]
        self.assertIn('gemini-3.6-flash', catalog_names)

