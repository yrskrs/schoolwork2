"""Search contracts for the public feed and student submissions portal."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html

from .middleware import set_has_admin
from .models import Assignment, ClassGroup, School, Student, Subject, Submission, Teacher


class PublicSearchWorkflowsTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        user = User.objects.create_superuser('search-fixture', password='test-only')
        self.teacher = Teacher.objects.create(user=user, full_name='Тестовий Учитель')
        School.objects.create(name='Школа', admin=user)
        self.subject = Subject.objects.create(name='Інформатика')
        self.a = ClassGroup.objects.create(name='7-А')
        self.b = ClassGroup.objects.create(name='8-Б')
        self.teacher.classes.add(self.a, self.b)
        self.teacher.subjects.add(self.subject)
        self.table = self.assignment('Таблиці обчислень', self.a)
        self.slides = self.assignment('Презентація безпеки', self.b)
        self.assignment('Секретна чернетка', self.a, status='draft')
        for first, group in [('Тарас', self.a), ('Іван', self.a), ('Тарас', self.b)]:
            student = Student.objects.create(class_group=group, last_name='Коваленко', first_name=first)
            Submission.objects.create(assignment=self.table if group == self.a else self.slides,
                teacher=self.teacher, class_group=group, student=student, last_name='Коваленко', first_name=first)

    def assignment(self, title, group, status='published'):
        item = Assignment.objects.create(teacher=self.teacher, subject=self.subject, title=title,
            description='Виконай навчальну роботу', status=status, published_at=timezone.now())
        item.classes.add(group)
        return item

    def feed_titles(self, route, **params):
        response = self.client.get(reverse(route), params)
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content.decode(response.charset)).xpath('//article//h2/a/text()')

    def test_initial_and_ajax_global_search_agree_and_accept_wrong_keyboard_layout(self):
        for route in ['index', 'feed_fragment']:
            for query in ['таблиці', 'nf,kbw']:
                with self.subTest(route=route, query=query):
                    self.assertEqual(self.feed_titles(route, q=query), [self.table.title])

    def test_global_search_preserves_class_filter_and_hides_drafts(self):
        self.assertEqual(self.feed_titles('index', q='презентація', **{'class': self.a.pk}), [])
        self.assertEqual(self.feed_titles('feed_fragment', q='Секретна'), [])
        self.assertEqual(self.feed_titles('index', q='нічогонемає123456'), [])

    def test_student_portal_search_keeps_class_and_distinguishes_same_last_names(self):
        for query in ['Коваленко Тарас', 'Тарас Коваленко', 'Rjdfktyrj Nfhfc']:
            response = self.client.get(reverse('student_submissions_portal'), {'class': self.a.pk, 'search': query})
            self.assertEqual(response.status_code, 200)
            rows = list(response.context['page_obj'])
            self.assertEqual([(row.first_name, row.class_group_id) for row in rows], [('Тарас', self.a.pk)])

    def test_student_portal_empty_result_is_valid_and_does_not_reset_class(self):
        response = self.client.get(reverse('student_submissions_portal'), {'class': self.a.pk, 'search': 'невідомий123456'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['page_obj']), [])
        self.assertEqual(response.context['selected_class_id'], str(self.a.pk))
