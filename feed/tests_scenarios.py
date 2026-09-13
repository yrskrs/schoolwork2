"""
Повний набір перевірок 23 обов'язкових сценаріїв з ТЗ (Розділ 11).
"""

import io
import datetime
import json
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
import docx

from feed.models import (
    Teacher, ClassGroup, Subject, Assignment, AssignmentFile,
    Submission, BellSchedule, TeacherLessonSchedule,
    AssignmentScheduleTarget, SubmissionActivityLog
)
from feed.utils import get_teacher_upcoming_notifications, sanitize_html, convert_docx_to_html


class ComprehensiveScenariosTest(TestCase):
    """
    Тестування 23 обов'язкових сценаріїв згідно з вимогами ТЗ.
    """

    def setUp(self):
        # 1. Створюємо викладача з обліковим записом
        self.user = User.objects.create_user(
            username='scenario_teacher',
            password='Password123!',
            is_staff=True,
            is_superuser=True
        )
        self.teacher = Teacher.objects.create(
            user=self.user,
            full_name='Мельник Андрій Васильович'
        )

        # 2. Класи
        self.class_7a = ClassGroup.objects.create(grade=7, letter='А', name='7-А')
        self.class_7b = ClassGroup.objects.create(grade=7, letter='Б', name='7-Б')
        self.class_7v = ClassGroup.objects.create(grade=7, letter='В', name='7-В')
        self.teacher.classes.add(self.class_7a, self.class_7b, self.class_7v)

        # 3. Предмет
        self.subject = Subject.objects.create(name='Інформатика', icon='💻', color='#3b82f6')
        self.teacher.subjects.add(self.subject)

        # 4. Дзвінки (розклад уроків)
        self.bell_1 = BellSchedule.objects.create(lesson_number=1, start_time=datetime.time(9, 0), end_time=datetime.time(9, 45))
        self.bell_2 = BellSchedule.objects.create(lesson_number=2, start_time=datetime.time(10, 0), end_time=datetime.time(10, 45))
        self.bell_3 = BellSchedule.objects.create(lesson_number=3, start_time=datetime.time(11, 0), end_time=datetime.time(11, 45))

        # 5. Тестовий клієнт
        self.client = Client()

    def test_scenario_01_and_02_draft_creation_and_published_status(self):
        """
        Сценарій 1: Створити чернетку завдання.
        Сценарій 2: Переконатися, що воно не вважається опублікованим.
        """
        draft = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Чернетка: Алгоритми',
            description='Опис чернетки',
            status=Assignment.STATUS_DRAFT
        )
        draft.classes.add(self.class_7a)

        # Перевірка статусу
        self.assertNotEqual(draft.status, Assignment.STATUS_PUBLISHED)
        self.assertEqual(draft.status, Assignment.STATUS_DRAFT)
        self.assertIsNone(draft.published_at)

        # Не повинно бути в опублікованих
        published_qs = Assignment.objects.filter(status=Assignment.STATUS_PUBLISHED)
        self.assertNotIn(draft, published_qs)

        # Не відображається у публічній стрічці завдань
        resp = self.client.get(reverse('index'))
        self.assertNotContains(resp, 'Чернетка: Алгоритми')

    def test_scenario_03_and_04_publish_assignment_and_notification_disappears(self):
        """
        Сценарій 3: Опублікувати завдання.
        Сценарій 4: Переконатися, що сповіщення про неопубліковану роботу зникає.
        """
        today = timezone.localtime(timezone.now()).date()
        today_dow = today.weekday() + 1

        TeacherLessonSchedule.objects.create(
            teacher=self.teacher,
            class_group=self.class_7a,
            subject=self.subject,
            day_of_week=today_dow,
            bell_slot=self.bell_2 # 10:00 - 10:45
        )

        # Симулюємо час за 30 хвилин до початку уроку (09:30)
        simulated_now = timezone.make_aware(datetime.datetime.combine(today, datetime.time(9, 30)))

        # До публікації завдання: є сповіщення про відсутнє опубліковане завдання
        notifs_before = get_teacher_upcoming_notifications(self.teacher, now_dt=simulated_now)
        self.assertTrue(any(n['type'] == 'missing_task' and '7-А' in n['message'] for n in notifs_before))

        # Створюємо та публікуємо завдання на цей урок
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Опубліковане завдання',
            description='Текст завдання',
            status=Assignment.STATUS_PUBLISHED,
            published_at=simulated_now
        )
        assignment.classes.add(self.class_7a)
        AssignmentScheduleTarget.objects.create(
            assignment=assignment,
            class_group=self.class_7a,
            target_day_of_week=today_dow,
            bell_slot=self.bell_2,
            target_date=today
        )

        # Після публікації: сповіщення про неопубліковану роботу на цей урок зникло!
        notifs_after = get_teacher_upcoming_notifications(self.teacher, now_dt=simulated_now)
        self.assertFalse(any(n['type'] == 'missing_task' and '7-А' in n['message'] for n in notifs_after))

    def test_scenario_05_and_06_sunday_creation_for_monday_lesson_in_calendar(self):
        """
        Сценарій 5: Створити завдання в неділю на урок понеділка.
        Сценарій 6: Переконатися, що в календарі воно відображається в понеділок.
        """
        today = timezone.localtime(timezone.now()).date()
        # Знайдемо найближчу неділю та наступний понеділок
        days_until_sunday = (6 - today.weekday()) % 7
        sunday = today + datetime.timedelta(days=days_until_sunday)
        monday = sunday + datetime.timedelta(days=1)

        # Неділя - дата створення/публікації
        sunday_dt = timezone.make_aware(datetime.datetime.combine(sunday, datetime.time(15, 0)))

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання задане в неділю на понеділок',
            description='Підготуватися до практичної',
            status=Assignment.STATUS_PUBLISHED,
            created_at=sunday_dt,
            published_at=sunday_dt
        )
        assignment.classes.add(self.class_7a)
        AssignmentScheduleTarget.objects.create(
            assignment=assignment,
            class_group=self.class_7a,
            target_day_of_week=1, # Понеділок
            bell_slot=self.bell_1,
            target_date=monday
        )

        # Перевірка дати уроку
        self.assertEqual(assignment.get_lesson_date(self.class_7a), monday)

        # Перевірка через календар: фільтр календаря на понеділок повинен показувати завдання
        resp_monday = self.client.get(reverse('index'), {'date': monday.strftime('%Y-%m-%d')})
        self.assertContains(resp_monday, 'Завдання задане в неділю на понеділок')

        # Фільтр календаря на неділю НЕ повинен показувати це завдання
        resp_sunday = self.client.get(reverse('index'), {'date': sunday.strftime('%Y-%m-%d')})
        self.assertNotContains(resp_sunday, 'Завдання задане в неділю на понеділок')

    def test_scenario_07_and_08_multiple_classes_distinct_dates_and_times(self):
        """
        Сценарій 7: Призначити завдання на 2–3 класи з різними уроками.
        Сценарій 8: Переконатися, що кожен клас має свою дату та час.
        """
        monday = datetime.date(2026, 9, 14)
        tuesday = datetime.date(2026, 9, 15)

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Python: цикли',
            description='Тема циклів for та while',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        assignment.classes.add(self.class_7a, self.class_7b, self.class_7v)

        # 7-А: понеділок 10:00 (bell_2)
        AssignmentScheduleTarget.objects.create(
            assignment=assignment,
            class_group=self.class_7a,
            target_day_of_week=1,
            bell_slot=self.bell_2,
            target_date=monday
        )
        # 7-Б: понеділок 11:00 (bell_3)
        AssignmentScheduleTarget.objects.create(
            assignment=assignment,
            class_group=self.class_7b,
            target_day_of_week=1,
            bell_slot=self.bell_3,
            target_date=monday
        )
        # 7-В: вівторок 09:00 (bell_1)
        AssignmentScheduleTarget.objects.create(
            assignment=assignment,
            class_group=self.class_7v,
            target_day_of_week=2,
            bell_slot=self.bell_1,
            target_date=tuesday
        )

        # Перевірка get_all_targets_info()
        targets = assignment.get_all_targets_info()
        self.assertEqual(len(targets), 3)

        info_by_class = {t['class_name']: t for t in targets}
        self.assertIn('7-А', info_by_class)
        self.assertIn('7-Б', info_by_class)
        self.assertIn('7-В', info_by_class)

        self.assertEqual(info_by_class['7-А']['date'], monday)
        self.assertEqual(info_by_class['7-А']['time_str'], '10:00')

        self.assertEqual(info_by_class['7-Б']['date'], monday)
        self.assertEqual(info_by_class['7-Б']['time_str'], '11:00')

        self.assertEqual(info_by_class['7-В']['date'], tuesday)
        self.assertEqual(info_by_class['7-В']['time_str'], '09:00')

        # Перевірка відображення на сторінці завдання
        resp = self.client.get(reverse('assignment_detail', args=[assignment.id]))
        self.assertContains(resp, '7-А')
        self.assertContains(resp, '7-Б')
        self.assertContains(resp, '7-В')

    def test_scenario_09_conducted_lessons_count_only_published(self):
        """
        Сценарій 9: Перевірити підрахунок проведених уроків.
        Враховуються лише опубліковані завдання, чернетки не враховуються.
        """
        past_date = datetime.date(2026, 9, 7) # минулий понеділок

        # 1. Створюємо чернетку на минулий урок
        draft_assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Чернетка на минулий урок',
            status=Assignment.STATUS_DRAFT
        )
        draft_assignment.classes.add(self.class_7a)
        AssignmentScheduleTarget.objects.create(
            assignment=draft_assignment,
            class_group=self.class_7a,
            target_day_of_week=1,
            bell_slot=self.bell_1,
            target_date=past_date
        )

        # Чернетка НЕ повинна давати проведений урок
        self.assertEqual(self.teacher.get_calculated_conducted_lessons(), 0)

        # 2. Створюємо опубліковане завдання на минулий урок
        pub_assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Проведений урок: Вступ',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.make_aware(datetime.datetime.combine(past_date, datetime.time(9, 0)))
        )
        pub_assignment.classes.add(self.class_7b)
        AssignmentScheduleTarget.objects.create(
            assignment=pub_assignment,
            class_group=self.class_7b,
            target_day_of_week=1,
            bell_slot=self.bell_2,
            target_date=past_date
        )

        # Тепер 1 урок проведено
        self.assertEqual(self.teacher.get_calculated_conducted_lessons(), 1)

    def test_scenario_10_11_12_docx_upload_and_student_content_view(self):
        """
        Сценарій 10: Завантажити DOCX із реальним текстом.
        Сценарій 11: Відкрити його від імені учня.
        Сценарій 12: Переконатися, що текст доступний.
        """
        # Створюємо реальний .docx за допомогою python-docx
        doc = docx.Document()
        doc.add_heading('Лабораторна робота з інформатики', level=1)
        doc.add_paragraph('Текст інструкції для учня: створити веб-сторінку за зразком.')
        doc_io = io.BytesIO()
        doc.save(doc_io)
        doc_io.seek(0)

        uploaded_docx = SimpleUploadedFile(
            'lab1_instruction.docx',
            doc_io.read(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Лабораторна робота DOCX',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        assignment.classes.add(self.class_7a)

        file_obj = AssignmentFile.objects.create(
            assignment=assignment,
            file=uploaded_docx,
            original_name='lab1_instruction.docx'
        )

        # Перевіряємо роботу парсера docx -> HTML
        html_content, err = convert_docx_to_html(file_obj.file.path)
        self.assertIn('Лабораторна робота з інформатики', html_content)
        self.assertIn('Текст інструкції для учня', html_content)

        # Учень відкриває сторінку завдання
        resp_detail = self.client.get(reverse('assignment_detail', args=[assignment.id]))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, 'lab1_instruction.docx')
        self.assertContains(resp_detail, 'Лабораторна робота з інформатики')

    def test_scenario_13_14_15_16_student_submits_teacher_notification_and_grading(self):
        """
        Сценарій 13: Учень здає роботу.
        Сценарій 14: Перевірити появу сповіщення для вчителя.
        Сценарій 15: Оцінити роботу вручну.
        Сценарій 16: Перевірити зникнення/оновлення сповіщення.
        """
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання для перевірки оцінювання',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        assignment.classes.add(self.class_7a)

        self.assertEqual(self.teacher.pending_reviews_count, 0)

        # 13. Учень здає роботу
        submission = Submission.objects.create(
            assignment=assignment,
            first_name='Оксана',
            last_name='Ковальчук',
            class_group=self.class_7a,
            teacher=self.teacher,
            comment_student='Здаю виконане завдання'
        )

        # 14. Сповіщення для вчителя
        self.assertEqual(self.teacher.pending_reviews_count, 1)
        self.client.login(username='scenario_teacher', password='Password123!')
        resp_poll = self.client.get(reverse('teacher_live_status'))
        data = resp_poll.json()
        self.assertEqual(data['pending_submissions_count'], 1)

        # 15. Вчитель оцінює роботу вручну
        resp_grade = self.client.post(
            reverse('grade_submission', args=[submission.id]),
            {'grade': '11', 'comment': 'Чудово виконано!'}
        )
        self.assertEqual(resp_grade.status_code, 200)

        # 16. Перевірка зникнення сповіщення про неоцінену роботу
        submission.refresh_from_db()
        self.assertEqual(submission.grade, '11')
        self.assertEqual(self.teacher.pending_reviews_count, 0)

        resp_poll2 = self.client.get(reverse('teacher_live_status'))
        data2 = resp_poll2.json()
        self.assertEqual(data2['pending_submissions_count'], 0)

    def test_scenario_17_mass_grading_resolves_all_notifications(self):
        """
        Сценарій 17: Повторити через масове оцінювання.
        Масове оцінювання одночасно прибирає сповіщення для всіх обраних робіт.
        """
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання для масового оцінювання',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        assignment.classes.add(self.class_7a)

        sub1 = Submission.objects.create(
            assignment=assignment,
            first_name='Учень1',
            last_name='Тестовий',
            class_group=self.class_7a,
            teacher=self.teacher
        )
        sub2 = Submission.objects.create(
            assignment=assignment,
            first_name='Учень2',
            last_name='Тестовий',
            class_group=self.class_7a,
            teacher=self.teacher
        )
        sub3 = Submission.objects.create(
            assignment=assignment,
            first_name='Учень3',
            last_name='Тестовий',
            class_group=self.class_7a,
            teacher=self.teacher
        )

        self.assertEqual(self.teacher.pending_reviews_count, 3)

        self.client.login(username='scenario_teacher', password='Password123!')
        resp_mass = self.client.post(
            reverse('mass_grade_submissions'),
            json.dumps({'submission_ids': [sub1.id, sub2.id, sub3.id], 'grade': '12'}),
            content_type='application/json'
        )
        self.assertEqual(resp_mass.status_code, 200)
        res_data = resp_mass.json()
        self.assertTrue(res_data.get('success'))
        self.assertEqual(res_data.get('graded_count'), 3)

        # Перевірка оновлення робіт у БД
        for sub in [sub1, sub2, sub3]:
            sub.refresh_from_db()
            self.assertEqual(sub.grade, '12')

        # Кількість неоцінених стала 0
        self.assertEqual(self.teacher.pending_reviews_count, 0)

    def test_scenario_18_and_19_rich_text_formatting_and_xss_protection(self):
        """
        Сценарій 18: Створити завдання з форматованим текстом.
        Сценарій 19: Перевірити форматування від імені учня (та захист від XSS).
        """
        raw_rich_text = (
            '<h3>Інструкція до роботи</h3>'
            '<p>Виконайте програму на <b>Python</b> з використанням <i>циклів</i> та <u>списків</u>:</p>'
            '<ul><li>Крок 1</li><li>Крок 2</li></ul>'
            '<script>alert("XSS")</script>'
        )

        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Форматоване завдання',
            description=raw_rich_text,
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        assignment.classes.add(self.class_7a)

        # Перевірка санітизації через get_formatted_description
        formatted = assignment.get_formatted_description()

        # Форматування збережене
        self.assertIn('<b>Python</b>', formatted)
        self.assertIn('<i>циклів</i>', formatted)
        self.assertIn('<u>списків</u>', formatted)
        self.assertIn('<li>Крок 1</li>', formatted)

        # Небезпечний скрипт видалено разом з вмістом
        self.assertNotIn('<script>', formatted)
        self.assertNotIn('alert("XSS")', formatted)

        # Перегляд від імені учня містить безпечне форматування
        resp = self.client.get(reverse('assignment_detail', args=[assignment.id]))
        self.assertContains(resp, '<b>Python</b>')
        self.assertNotContains(resp, 'alert("XSS")')

    def test_scenario_20_21_22_23_assignment_duplication_and_schedule_target(self):
        """
        Сценарій 20: Створити копію завдання.
        Сценарій 21: Переконатися, що створено новий запис, а оригінал не змінений.
        Сценарій 22: Переконатися, що для копії правильно відображається вибраний урок.
        Сценарій 23: Перевірити, що копія потрапила в правильну дату календаря.
        """
        original = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Оригінальне завдання',
            description='<b>Форматований оригінальний текст</b>',
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now()
        )
        original.classes.add(self.class_7a)

        new_lesson_date = datetime.date(2026, 9, 21) # Наступний понеділок

        self.client.login(username='scenario_teacher', password='Password123!')
        resp_dup = self.client.post(
            reverse('assignment_duplicate', args=[original.id]),
            {
                'duplicate_classes': [str(self.class_7b.id)],
                'duplicate_target_date': new_lesson_date.strftime('%Y-%m-%d'),
                'publish_now': '1'
            }
        )
        self.assertEqual(resp_dup.status_code, 302)

        # 21. Створено новий запис, оригінал не змінений
        duplicate = Assignment.objects.exclude(id=original.id).filter(duplicated_from=original).first()
        self.assertIsNotNone(duplicate)
        self.assertNotEqual(duplicate.id, original.id)
        self.assertEqual(duplicate.title, original.title)
        self.assertEqual(duplicate.description, original.description)
        self.assertEqual(list(duplicate.classes.all()), [self.class_7b])
        self.assertEqual(list(original.classes.all()), [self.class_7a])

        # 22. Для копії правильно відображається вибраний урок
        self.assertEqual(duplicate.get_lesson_date(self.class_7b), new_lesson_date)

        # 23. Копія потрапила в правильну дату календаря
        resp_cal_new = self.client.get(reverse('index'), {'date': new_lesson_date.strftime('%Y-%m-%d')})
        self.assertContains(resp_cal_new, 'Оригінальне завдання')
