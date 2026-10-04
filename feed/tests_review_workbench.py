"""Review content stays accessible while the queue and grading remain intact."""
import json
from urllib.parse import parse_qs, urlparse
from collections import Counter
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from lxml import html
from .middleware import set_has_admin
from .models import Assignment, AssignmentFile, AssignmentLink, ClassGroup, School, Submission, SubmissionFile, Teacher


class ReviewWorkbenchTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        user = User.objects.create_superuser('workbench_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=user, full_name='Вчитель')
        School.objects.create(name='Тестова школа', admin=user)
        self.group = ClassGroup.objects.create(name='7-А', grade=7, letter='А')
        self.teacher.classes.add(self.group)
        self.assignment = Assignment.objects.create(teacher=self.teacher, title='Створити презентацію', description='Порівняйте два способи розв’язання.', status='published')
        self.assignment.classes.add(self.group)
        self.sub = Submission.objects.create(assignment=self.assignment, teacher=self.teacher, class_group=self.group, first_name='Тест', last_name='Учень', file=SimpleUploadedFile('answer.txt', b'Answer'))
        self.client.force_login(user)

    def page(self, **params):
        response = self.client.get(reverse('view_file', args=[self.sub.pk]), params)
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content)

    def test_task_materials_and_long_ai_recommendations_have_separate_accessible_panels(self):
        material = AssignmentFile.objects.create(assignment=self.assignment, file=SimpleUploadedFile('instructions.txt', b'Task'), original_name='Матеріал до завдання.txt')
        AssignmentLink.objects.create(assignment=self.assignment, url='https://example.com/material', label='Додаткове пояснення')
        feedback = 'Врахуйте виправлення. ' * 100
        self.sub.ai_status = 'success'; self.sub.ai_suggested_grade = '9'; self.sub.ai_feedback = feedback
        self.sub.student_ai_checked = True
        gr_results = [{'code':'ГР1', 'grade':'8', 'name':'Зіставлення <прикладів>', 'comment':'Поясніть результат.'}]
        self.sub.student_ai_gr_results = json.dumps(gr_results, ensure_ascii=False)
        self.sub.save()
        page = self.page()
        task = page.get_element_by_id('fv-panel-task')
        ai = page.get_element_by_id('fv-panel-ai')
        self.assertIn(self.assignment.description, task.text_content())
        self.assertTrue(task.xpath('.//button[@data-material-preview]'))
        self.assertTrue(task.xpath('.//a[@href="https://example.com/material"]'))
        self.assertIn(reverse('file_view', args=[material.pk]), html.tostring(task).decode())
        self.assertIn('Врахуйте виправлення.', ai.text_content())
        self.assertEqual(json.loads(ai.get_element_by_id('student-gr-detail-list').get('data-gr')), gr_results)
        self.assertFalse(ai.get_element_by_id('ai-evaluation-box').xpath('ancestor::details'))
        self.assertFalse(task.xpath('ancestor::details'))
        for panel in page.xpath('//*[@data-review-panel]'):
            self.assertEqual(panel.get('role'), 'tabpanel')
            self.assertIsNotNone(page.get_element_by_id(panel.get('aria-labelledby')))
        self.assertFalse([key for key, count in Counter(page.xpath('//*[@id]/@id')).items() if count > 1])
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.grade)
        self.assertEqual(self.sub.ai_feedback, feedback)

    def test_switching_files_keeps_queue_filters_and_active_file(self):
        first = SubmissionFile.objects.create(submission=self.sub, file=SimpleUploadedFile('first.txt', b'First'))
        second = SubmissionFile.objects.create(submission=self.sub, file=SimpleUploadedFile('second.txt', b'Second'))
        filters = {'from':'all_submissions', 'class':str(self.group.pk), 'grade_filter':'ungraded', 'sort':'student', 'order':'asc', 'file_id':str(second.pk)}
        page = self.page(**filters)
        picker = page.get_element_by_id('fv-file-picker')
        self.assertEqual(picker.xpath('./option[@selected]/@value'), [picker.xpath('./option/@value')[1]])
        links = picker.xpath('./option/@value') + page.xpath('//*[@id="prev-file-nav-btn"]/@href')
        for link in links:
            parsed = parse_qs(urlparse(link).query)
            for key in ['from', 'class', 'grade_filter', 'sort', 'order']:
                self.assertEqual(parsed[key], [filters[key]])
            self.assertEqual(len(parsed['file_id']), 1)
        self.assertEqual(parse_qs(urlparse(links[0]).query)['file_id'], [str(first.pk)])

    def test_group_grading_and_feedback_are_available_without_hiding_main_save(self):
        self.sub.is_group_work = True; self.sub.save()
        member = Submission.objects.create(assignment=self.assignment, teacher=self.teacher, class_group=self.group, first_name='Другий', last_name='Учень', primary_submission=self.sub)
        page = self.page()
        grade = page.get_element_by_id('fv-grading-card')
        self.assertIsNotNone(grade.get_element_by_id('member_grade_' + str(member.pk)))
        self.assertFalse(grade.get_element_by_id('save-grade-btn').xpath('ancestor::details'))
        self.assertFalse(grade.get_element_by_id('save-grade-next-btn').xpath('ancestor::details'))
        self.assertIsNotNone(page.get_element_by_id('fv-panel-comments').get_element_by_id('comment-textarea'))
        self.assertFalse(page.get_element_by_id('assignment-file-modal').xpath('ancestor::*[@data-review-panel]'))
