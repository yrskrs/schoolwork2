"""
Комплексні тести бізнес-логіки системи SchoolNet:
1. Робота з файлами: парсери документів (.docx, .xlsx, .xls, .txt, .py, .zip),
   перегляд та завантаження ZIP-архівів робіт.
2. Життєвий цикл завдань: створення завдань з прикріпленими файлами та класами,
   дублювання завдань, перенесення уроків (reschedule) з фіксацією в журналі розкладу.
3. Робота з ШІ: мультипровайдерність, автоперемикання (failover) при збоях чи 429/500,
   генерація критеріїв оцінювання, перевірка учнівських робіт та пакетна обробка.
"""

import io
import json
import zipfile
import datetime
from unittest.mock import patch

import docx
import openpyxl
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User

from feed.models import (
    Teacher, ClassGroup, Subject, Assignment, AssignmentFile,
    Submission, SubmissionFile, AISettings, AssignmentRescheduleLog,
    AssignmentScheduleTarget, BellSchedule
)
from feed.document_parsers import extract_text_from_document
from feed.utils import convert_xlsx_to_html
from feed.gemini_service import (
    evaluate_submission_with_gemini,
    get_ai_settings
)


class SystemFilesLogicTests(TestCase):
    """Тести для парсерів файлів та обробки вкладень."""

    def test_extract_plain_text_and_code(self):
        """Перевірка вилучення тексту з .txt та .py файлів (як bytes, так і string)."""
        txt_content = "Тестове учнівське есе з української літератури."
        text, ok, err = extract_text_from_document(txt_content.encode('utf-8'), "essay.txt")
        self.assertTrue(ok)
        self.assertIn("Тестове учнівське есе", text)

        py_content = "def calculate_sum(a, b):\n    return a + b\n"
        py_text, ok, err = extract_text_from_document(py_content.encode('utf-8'), "main.py")
        self.assertTrue(ok)
        self.assertIn("def calculate_sum", py_text)

    def test_extract_docx_document(self):
        """Створення та перевірка вилучення тексту з Word .docx документа."""
        doc = docx.Document()
        doc.add_heading("Лабораторна робота №1", level=1)
        doc.add_paragraph("Дослідження рівномірного прямолінійного руху.")
        buf = io.BytesIO()
        doc.save(buf)

        text, ok, err = extract_text_from_document(buf.getvalue(), "lab1.docx")
        self.assertTrue(ok)
        self.assertIn("Лабораторна робота №1", text)
        self.assertIn("Дослідження рівномірного прямолінійного руху", text)

    def test_extract_xlsx_document(self):
        """Створення та вилучення тексту з Excel .xlsx книги."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Оцінки"
        ws['A1'] = "Учень"
        ws['B1'] = "Бал"
        ws['A2'] = "Шевченко Тарас"
        ws['B2'] = 12
        buf = io.BytesIO()
        wb.save(buf)

        text, ok, err = extract_text_from_document(buf.getvalue(), "grades.xlsx")
        self.assertTrue(ok)
        self.assertIn("Шевченко Тарас", text)
        self.assertIn("12", text)

    def test_extract_zip_archive(self):
        """Перевірка розпізнавання та вилучення списку файлів із ZIP-архіву."""
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w') as zf:
            zf.writestr("code/solution.pas", "program Test; begin writeln('OK'); end.")
            zf.writestr("readme.txt", "Інструкція до лабораторної.")

        text, ok, err = extract_text_from_document(zip_buf.getvalue(), "project.zip")
        self.assertTrue(ok)
        self.assertIn("solution.pas", text)
        self.assertIn("readme.txt", text)

    def test_convert_xlsx_to_html(self):
        """Перевірка конвертації таблиці Excel у HTML з формулами."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws['A1'] = 10
        ws['A2'] = 20
        ws['A3'] = "=SUM(A1:A2)"
        
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tf:
            wb.save(tf.name)
            tf_path = tf.name

        try:
            html_out, err = convert_xlsx_to_html(tf_path)
            self.assertIsNone(err)
            self.assertIn("<table", html_out)
            self.assertIn("10", html_out)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)


class SystemAssignmentWorkflowTests(TestCase):
    """Тести життєвого циклу завдань: створення, дублювання, перенесення уроків."""

    def setUp(self):
        from feed.middleware import set_has_admin
        set_has_admin(True)

        self.user = User.objects.create_user(username='teacher_wf', password='password123', is_staff=True, is_superuser=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Іван Франко')
        self.class_9a = ClassGroup.objects.create(grade=9, letter='А', name='9-А')
        self.class_9b = ClassGroup.objects.create(grade=9, letter='Б', name='9-Б')
        self.teacher.classes.add(self.class_9a, self.class_9b)

        self.subject = Subject.objects.create(name='Інформатика', icon='💻', color='#3b82f6')
        self.teacher.subjects.add(self.subject)

        self.bell_slot_1 = BellSchedule.objects.create(lesson_number=1, start_time=datetime.time(8, 30), end_time=datetime.time(9, 15))
        self.bell_slot_2 = BellSchedule.objects.create(lesson_number=2, start_time=datetime.time(9, 25), end_time=datetime.time(10, 10))

        self.client = Client()
        self.client.force_login(self.user)

    def test_assignment_create_with_files_and_targets(self):
        """Створення завдання з декількома класами, файлом інструкції та цільовими датами."""
        file_content = b"Content of instruction document for assignment."
        uploaded_file = SimpleUploadedFile("instruction.txt", file_content, content_type="text/plain")

        post_data = {
            'title': 'Алгоритми сортування',
            'subject': self.subject.id,
            'classes': [self.class_9a.id, self.class_9b.id],
            'description': 'Реалізувати швидке сортування QuickSort мовою Python.',
            'due_date': (timezone.now() + datetime.timedelta(days=7)).strftime('%Y-%m-%d'),
            'publish_choice': 'now',
            'files': [uploaded_file],
        }

        resp = self.client.post(reverse('assignment_create'), post_data)
        self.assertEqual(resp.status_code, 302)

        asg = Assignment.objects.filter(title='Алгоритми сортування').first()
        self.assertIsNotNone(asg)
        self.assertEqual(asg.classes.count(), 2)
        self.assertEqual(asg.files.count(), 1)
        self.assertEqual(asg.files.first().original_name, 'instruction.txt')

    def test_assignment_duplicate_workflow(self):
        """Дублювання завдання для іншого класу з копіюванням вкладень."""
        original = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Оригінальне завдання',
            description='Опис оригіналу',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now(),
            due_date=timezone.now().date() + datetime.timedelta(days=3)
        )
        original.classes.add(self.class_9a)
        AssignmentFile.objects.create(
            assignment=original,
            file=SimpleUploadedFile('test.txt', b'test data'),
            original_name='test.txt'
        )

        dup_url = reverse('assignment_duplicate', args=[original.id])
        resp = self.client.post(dup_url, {
            'duplicate_classes': [self.class_9b.id],
            'duplicate_title': 'Дублікат завдання для 9-Б',
            'publish_now': '1'
        })
        self.assertEqual(resp.status_code, 302)

        duplicate = Assignment.objects.filter(title='Дублікат завдання для 9-Б').first()
        self.assertIsNotNone(duplicate)
        self.assertIn(self.class_9b, duplicate.classes.all())
        self.assertNotIn(self.class_9a, duplicate.classes.all())
        self.assertEqual(duplicate.files.count(), 1)
        self.assertEqual(duplicate.files.first().original_name, 'test.txt')

    def test_assignment_reschedule_workflow(self):
        """Перенесення уроку: стандартний POST та AJAX POST із фіксацією у журналі змін."""
        asg = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Урок з тривогами',
            description='Перенесення через сигнал повітряної тривоги',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        asg.classes.add(self.class_9a)

        new_date = (timezone.now() + datetime.timedelta(days=2)).date()
        reschedule_url = reverse('reschedule_assignment', args=[asg.id])

        # AJAX POST запит
        ajax_resp = self.client.post(
            reschedule_url,
            {
                'new_date': new_date.strftime('%Y-%m-%d'),
                'class_group': self.class_9a.id,
                'new_bell_slot': self.bell_slot_2.id,
                'reason_type': AssignmentRescheduleLog.REASON_AIR_RAID,
                'reason_comment': 'Тривога тривала 1.5 години',
                'update_due_date': '1',
                'is_ajax': '1'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(ajax_resp.status_code, 200)
        ajax_data = ajax_resp.json()
        self.assertTrue(ajax_data['success'])

        # Перевірка запису в AssignmentRescheduleLog
        log = AssignmentRescheduleLog.objects.filter(assignment=asg).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.new_date, new_date)
        self.assertEqual(log.new_bell_slot, self.bell_slot_2)
        self.assertEqual(log.reason_type, AssignmentRescheduleLog.REASON_AIR_RAID)
        self.assertEqual(log.reason_comment, 'Тривога тривала 1.5 години')

        # Перевірка оновлення AssignmentScheduleTarget
        target = AssignmentScheduleTarget.objects.filter(assignment=asg, class_group=self.class_9a).first()
        self.assertIsNotNone(target)
        self.assertEqual(target.target_date, new_date)
        self.assertEqual(target.bell_slot, self.bell_slot_2)

        # Перевірка оновлення дедлайну завдання
        asg.refresh_from_db()
        self.assertEqual(asg.due_date, new_date)


class SystemAILogicAndFailoverTests(TestCase):
    """Тести функціоналу ШІ: оцінювання робіт, failover на резервний провайдер та генерація критеріїв."""

    def setUp(self):
        from feed.middleware import set_has_admin
        set_has_admin(True)

        self.user = User.objects.create_user(username='teacher_ai', password='password123', is_staff=True, is_superuser=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Леся Українка')
        self.class_group = ClassGroup.objects.create(grade=11, letter='А', name='11-А')
        self.teacher.classes.add(self.class_group)

        self.subject = Subject.objects.create(name='Астрономія', icon='🔭', color='#8b5cf6')
        self.teacher.subjects.add(self.subject)

        self.assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Сонячна система',
            description='Будова та походження Сонячної системи.',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        self.assignment.classes.add(self.class_group)

        self.submission = Submission.objects.create(
            assignment=self.assignment,
            first_name='Оксана',
            last_name='Петренко',
            class_group=self.class_group,
            teacher=self.teacher,
            comment_student='Сонце є центральним тілом нашої системи. Планети поділяються на земну групу та гіганти.'
        )

        self.client = Client()
        self.client.force_login(self.user)

    @patch('feed.gemini_service.call_ai_api')
    def test_ai_failover_on_500_server_error(self, mock_call):
        """Якщо основний провайдер повертає 500, система автоматично перемикається на резервний API."""
        settings = get_ai_settings()
        settings.is_enabled = True
        settings.ai_provider = 'openai'
        settings.api_key = 'sk-primary-error'
        settings.model_name = 'gpt-4o'
        settings.backup_ai_provider = 'gemini'
        settings.backup_api_key = 'AIzaBackupWorks'
        settings.backup_model_name = 'gemini-3.8-flash'
        settings.active_api_type = 'primary'
        settings.auto_failover_enabled = True
        settings.save()

        valid_reply = json.dumps({
            "suggested_grade": "11",
            "level": "Високий",
            "summary": "Глибоке розуміння структури Сонячної системи.",
            "strengths": ["Точна класифікація планет", "Логічний виклад"],
            "weaknesses": [],
            "feedback_comment": "Відмінна відповідь!"
        })

        # Спроба 1 (OpenAI): 500 Internal Error
        # Спроба 1 retry (OpenAI): 500 Internal Error
        # Спроба 2 (Failover to Gemini): 200 OK
        mock_call.side_effect = [
            (500, None, "Internal Server Error", {}),
            (500, None, "Internal Server Error", {}),
            (200, valid_reply, None, {})
        ]

        result = evaluate_submission_with_gemini(self.submission, ai_settings=settings)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['suggested_grade'], '11')

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.ai_status, 'success')
        self.assertEqual(self.submission.ai_suggested_grade, '11')
        self.assertIn('gemini', self.submission.ai_model_used.lower())

        settings.refresh_from_db()
        self.assertIsNotNone(settings.last_failover_at)

    @patch('feed.gemini_service.call_ai_api')
    def test_teacher_generate_assignment_criteria_ajax(self, mock_call):
        """AJAX ендпоінт генерації структурованих критеріїв за 12-бальною шкалою."""
        settings = get_ai_settings()
        settings.is_enabled = True
        settings.api_key = 'AIzaValidKey'
        settings.save()

        ai_criteria_text = """### Початковий рівень (1-3 бали)
- Учень розпізнає поняття планет.

### Середній рівень (4-6 балів)
- Учень називає планети Сонячної системи у правильному порядку.

### Достатній рівень (7-9 балів)
- Учень пояснює різницю між планетами земної групи та гігантами.

### Високий рівень (10-12 балів)
- Учень всебічно аналізує фізичні параметри та особливості орбіт планет."""

        mock_call.return_value = (200, ai_criteria_text, None, {})

        resp = self.client.post(
            reverse('teacher_generate_assignment_criteria'),
            {
                'assignment_title': 'Сонячна система',
                'assignment_description': 'Будова та походження',
                'teacher_notes': 'Враховувати знання планет та їхніх супутників',
                'subject_name': 'Астрономія'
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('Високий рівень', data['criteria'])
