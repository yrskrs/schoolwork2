"""Journal pages preserve every date, filter, student and period-wide average."""
import datetime
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, Client
from django.urls import reverse
from lxml import html

from .models import Assignment, AssignmentScheduleTarget, BellSchedule, Subject, Submission
from . import tests_review_workbench as workbench_tests


class JournalPaginationTests(TestCase):
    setUp = workbench_tests.ReviewWorkbenchTests.setUp

    def make_history(self, count=33):
        self.sub.delete()
        dates = []
        for index in range(count):
            day = datetime.date(2026, 8, 1) + datetime.timedelta(days=index)
            assignment = Assignment.objects.create(teacher=self.teacher, title=f'Урок {index + 1}', status='published')
            assignment.classes.add(self.group)
            AssignmentScheduleTarget.objects.create(assignment=assignment, class_group=self.group, target_date=day)
            Submission.objects.create(assignment=assignment, teacher=self.teacher, class_group=self.group,
                                      first_name='Тест', last_name='Учень', grade=str(index % 12 + 1))
            dates.append(day)
        return dates

    def journal(self, **params):
        response = self.client.get(reverse('gradebook'), {'class_group': self.group.pk, **params})
        self.assertEqual(response.status_code, 200)
        return response

    def test_default_opens_last_page_and_limits_date_columns(self):
        dates = self.make_history()
        response = self.journal()
        self.assertEqual(response.context['journal_page'].number, 3)
        self.assertEqual(response.context['dates'], dates[30:])
        page = html.fromstring(response.content)
        self.assertEqual(len(page.xpath('//table[@data-table-layout="matrix"]/thead/tr/th')), 5)
        self.assertTrue(page.xpath('//nav[@aria-label="Сторінки розгортки журналу"]//a[@aria-current="page" and text()="3"]'))

    def test_all_pages_cover_every_date_exactly_once(self):
        dates = self.make_history()
        displayed = []
        for number, expected_length in [(1, 15), (2, 15), (3, 3)]:
            response = self.journal(journal_page=number)
            self.assertEqual(len(response.context['dates']), expected_length)
            displayed.extend(response.context['dates'])
            self.assertEqual(len(response.context['students'][0]['cells']), expected_length)
        self.assertEqual(displayed, dates)

    def test_exact_fifteen_day_boundary_has_no_extra_empty_page(self):
        self.make_history(count=30)
        response = self.journal()
        self.assertEqual(response.context['journal_page'].number, 2)
        self.assertEqual(response.context['journal_page'].paginator.num_pages, 2)
        self.assertEqual(len(response.context['dates']), 15)

    def test_invalid_page_numbers_are_safe(self):
        self.make_history()
        for value, expected in [('bad', 3), ('9999', 3), ('0', 1), ('-1', 1)]:
            with self.subTest(page=value):
                self.assertEqual(self.journal(journal_page=value).context['journal_page'].number, expected)

    def test_links_preserve_filters_and_last_page_is_relative_to_filtered_dates(self):
        dates = self.make_history()
        response = self.journal(date_from='2026-08-05', date_to='2026-08-21', grade_filter='7-9')
        self.assertEqual(response.context['journal_page'].number, 2)
        self.assertEqual(response.context['journal_dates_count'], 17)
        self.assertEqual(response.context['dates'], dates[19:21])
        params = parse_qs(urlparse(response.context['journal_previous_url']).query)
        self.assertEqual(params['class_group'], [str(self.group.pk)])
        self.assertEqual(params['grade_filter'], ['7-9'])
        self.assertEqual(params['date_from'], ['2026-08-05'])
        self.assertEqual(params['date_to'], ['2026-08-21'])
        self.assertEqual(params['journal_page'], ['1'])
        self.assertEqual(params['view_mode'], ['journal'])

    def test_paging_does_not_change_period_averages_counts_or_student_membership(self):
        self.make_history()
        first, last = self.journal(journal_page=1), self.journal()
        self.assertEqual(first.context['class_avg'], last.context['class_avg'])
        self.assertEqual(first.context['total_graded'], 33)
        self.assertEqual(last.context['total_graded'], 33)
        for field in ['full_name', 'avg_score', 'graded_count', 'total_submissions']:
            self.assertEqual(first.context['students'][0][field], last.context['students'][0][field])

    def test_table_mode_keeps_the_whole_filtered_history(self):
        dates = self.make_history()
        response = self.journal(view_mode='table', journal_page=1)
        self.assertEqual(response.context['dates'], dates)
        self.assertEqual(len(response.context['students'][0]['cells']), 33)

    def test_empty_journal_has_valid_page_and_no_navigation_to_missing_dates(self):
        self.sub.delete()
        response = self.journal()
        self.assertEqual(response.context['journal_page'].number, 1)
        self.assertEqual(response.context['dates'], [])
        self.assertIsNone(response.context['journal_previous_url'])
        self.assertIsNone(response.context['journal_next_url'])

    def test_schedule_view_and_editor_keep_matrix_structure_and_live_controls(self):
        subject = Subject.objects.create(name='Інформатика')
        self.teacher.subjects.add(subject)
        BellSchedule.objects.create(lesson_number=1, start_time=datetime.time(8), end_time=datetime.time(8, 45))
        response = self.client.get(reverse('teacher_students'), {'tab': 'schedule'})
        page = html.fromstring(response.content)
        tables = page.xpath('//table[@data-table-layout="matrix"]')
        self.assertEqual(len(tables), 2)
        self.assertTrue(all(len(table.xpath('./thead/tr/th')) == 6 for table in tables))
        self.assertTrue(page.xpath('//form[@id="schedule-form"]//select[contains(@class,"schedule-class-picker")]'))
        self.assertEqual(len(page.xpath('//*[@class="table-responsive platform-matrix-region"][@tabindex="0"]')), 2)

    def test_student_results_omit_unknown_origin_panel_after_reload(self):
        self.sub.student_ai_checked = True
        self.sub.student_ai_grade = '9'
        self.sub.student_ai_feedback = '✅ **Сильні сторони:**\n• Правильний результат.'
        self.sub.ai_generated_detected = False
        self.sub.ai_generated_details = 'Походження роботи не встановлено.'
        self.sub.save()
        client = Client()
        session = client.session
        session['last_submission_id'] = self.sub.pk
        session.save()
        for url in [reverse('submit_success', args=[self.assignment.pk]), reverse('submission_detail', args=[self.sub.pk])]:
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            page = html.fromstring(response.content)
            self.assertFalse(page.xpath('//div[contains(@class,"ai-origin-details")]'))
            self.assertIn('Правильний результат.', page.text_content())
            scripts = '\n'.join(page.xpath('//script[not(@src)]/text()'))
            self.assertNotIn('Походження роботи не встановлено', scripts)

    def test_specific_ai_warning_remains_visible_for_detected_signs(self):
        self.sub.student_ai_checked = True
        self.sub.ai_generated_detected = True
        self.sub.ai_generated_details = 'Виявлено конкретну ознаку у файлі.'
        self.sub.save()
        response = Client().get(reverse('submission_detail', args=[self.sub.pk]))
        self.assertContains(response, 'У роботі виявлено ознаки використання штучного інтелекту')

    def test_compact_toolbar_keeps_navigation_grading_and_status_controls(self):
        response = self.client.get(reverse('view_file', args=[self.sub.pk]))
        page = html.fromstring(response.content)
        toolbar = page.xpath('//*[@class="fv-workspace-toolbar"]')[0]
        for element_id in ['grade-input', 'save-grade-btn', 'save-grade-next-btn', 'fv-current-grade', 'fv-quick-ai']:
            self.assertTrue(toolbar.xpath('.//*[@id=$value]', value=element_id))
        self.assertTrue(toolbar.xpath('.//nav[@aria-label="Потік робіт"]'))
