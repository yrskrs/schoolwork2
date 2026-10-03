from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html

from .middleware import set_has_admin
from .models import Assignment, ClassGroup, School, Subject, Submission, Teacher


class PlatformNavigationTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        self.user = User.objects.create_user('platform_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель')
        School.objects.create(name='Тестова школа', admin=self.user)
        self.classes = [ClassGroup.objects.create(name=f'{grade}-А', grade=grade, letter='А') for grade in [5, 12]]
        self.subject = Subject.objects.create(name='Інформатика')
        self.teacher.classes.add(*self.classes)
        self.teacher.subjects.add(self.subject)
        self.assignments = []
        for group in self.classes:
            assignment = Assignment.objects.create(
                teacher=self.teacher, subject=self.subject, title=f'Завдання для {group.name}',
                description='Виконай практичну роботу.', status=Assignment.STATUS_PUBLISHED,
                published_at=timezone.now(),
            )
            assignment.classes.add(group)
            self.assignments.append(assignment)

    def page(self, response):
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content.decode())

    def cards(self, page):
        return page.xpath('//article[contains(concat(" ", @class, " "), " assignment-card ")]')

    def test_visible_class_selector_filters_initial_and_ajax_feed(self):
        for group, assignment in zip(self.classes, self.assignments):
            with self.subTest(grade=group.grade):
                client = Client()
                initial = self.page(client.get(reverse('index'), {'class': group.pk}))
                fragment = self.page(client.get(reverse('feed_fragment'), {'class': group.pk}))
                selector = initial.get_element_by_id('home-class-select')
                self.assertEqual(selector.xpath('./option[@selected]/@value'), [str(group.pk)])
                self.assertEqual(len(self.cards(initial)), 1)
                self.assertEqual(len(self.cards(fragment)), 1)
                self.assertIn(assignment.title, self.cards(initial)[0].text_content())
                self.assertEqual(
                    html.tostring(self.cards(initial)[0]), html.tostring(self.cards(fragment)[0]),
                )

    def test_both_native_links_preserve_class_through_student_workflow(self):
        for group, assignment in zip(self.classes, self.assignments):
            with self.subTest(grade=group.grade):
                page = self.page(self.client.get(reverse('index'), {'class': group.pk}))
                card = self.cards(page)[0]
                detail = reverse('assignment_detail', args=[assignment.pk])
                submit = reverse('submit_assignment', args=[assignment.pk])
                for target in [detail, submit]:
                    links = [link for link in card.xpath('.//a/@href') if urlparse(link).path == target]
                    self.assertTrue(links)
                    self.assertEqual(set(links), {target + f'?class={group.pk}'})
                detail_page = self.page(self.client.get(detail + f'?class={group.pk}'))
                self.assertTrue(detail_page.xpath('//a[@href=$target]', target=submit + f'?class={group.pk}'))
                submit_page = self.page(self.client.get(submit + f'?class={group.pk}'))
                self.assertEqual(submit_page.get_element_by_id('id_class_group').xpath('.//option[@selected]/@value'), [str(group.pk)])

    def test_clearing_class_restores_all_cards_and_unfiltered_links(self):
        self.client.get(reverse('index'), {'class': self.classes[0].pk})
        page = self.page(self.client.get(reverse('index'), {'class': 'all'}))
        self.assertEqual(len(self.cards(page)), 2)
        self.assertFalse(page.get_element_by_id('home-class-select').xpath('./option[@selected]'))
        for card in self.cards(page):
            for link in card.xpath('.//a[starts-with(@href, "/assignment/")]/@href'):
                self.assertNotIn('class', parse_qs(urlparse(link).query))

    def test_each_card_has_keyboard_accessible_title_link_without_nested_button_role(self):
        page = self.page(self.client.get(reverse('index')))
        for card in self.cards(page):
            link = card.xpath('.//h3/a')[0]
            self.assertTrue(link.get('href').startswith('/assignment/'))
            self.assertTrue(link.text_content().strip())
            self.assertNotEqual(card.get('role'), 'button')
            self.assertIsNone(card.get('tabindex'))
        self.assertEqual(len(page.xpath('//h1')), 1)

    def test_student_and_teacher_routes_load_the_same_layout_after_page_styles(self):
        assignment = self.assignments[0]
        self.client.force_login(self.user)
        routes = [
            reverse('index'), reverse('assignment_detail', args=[assignment.pk]),
            reverse('submit_assignment', args=[assignment.pk]), reverse('teacher_dashboard'),
            reverse('assignment_create'), reverse('assignment_edit', args=[assignment.pk]),
            reverse('all_submissions_dashboard'), reverse('assignment_submissions', args=[assignment.pk]),
            reverse('gradebook'), reverse('teacher_reports'), reverse('teacher_students'),
            reverse('teacher_students') + '?tab=schedule', reverse('teacher_settings') + '?tab=profile',
            reverse('teacher_settings') + '?tab=environment', reverse('teacher_settings') + '?tab=ai',
            reverse('ai_batch_check'), reverse('activity_log'), reverse('teacher_rescheduled_calendar'),
        ]
        for url in routes:
            with self.subTest(url=url):
                page = self.page(self.client.get(url))
                styles = page.xpath('//head/link[@rel="stylesheet"]/@href')
                self.assertEqual(styles[-1], '/static/css/platform_layout.css')
                self.assertEqual(len(page.xpath('//*[contains(concat(" ", @class, " "), " page-wrapper ")]')), 1)

    def test_teacher_mobile_navigation_has_read_only_links_and_is_not_on_student_pages(self):
        self.client.force_login(self.user)
        page = self.page(self.client.get(reverse('teacher_dashboard')))
        links = page.xpath('//nav[@aria-label="Розділи кабінету вчителя"]//a/@href')
        for name in ['teacher_dashboard', 'assignment_create', 'all_submissions_dashboard', 'gradebook',
                     'teacher_reports', 'teacher_students', 'ai_batch_check', 'teacher_settings',
                     'teacher_rescheduled_calendar', 'activity_log', 'index']:
            self.assertIn(reverse(name), links)
        self.assertTrue(page.xpath('//nav[@aria-label="Розділи кабінету вчителя"]/details/summary'))
        self.assertFalse(page.xpath('//nav[@aria-label="Розділи кабінету вчителя"]//form'))
        self.assertFalse(self.page(self.client.get(reverse('index'))).xpath('//nav[@aria-label="Розділи кабінету вчителя"]'))
        self.client.logout()
        self.assertFalse(self.page(self.client.get(reverse('teacher_login'))).xpath('//nav[@aria-label="Розділи кабінету вчителя"]'))

    def test_submission_filters_and_rows_respect_teacher_and_admin_scope(self):
        other_user = User.objects.create_user('other_platform_teacher')
        other_teacher = Teacher.objects.create(user=other_user, full_name='Інший вчитель')
        other_class = ClassGroup.objects.create(name='8-Б', grade=8, letter='Б')
        other_assignment = Assignment.objects.create(teacher=other_teacher, title='Інше завдання', status='published')
        other_assignment.classes.add(other_class)
        for assignment, teacher, group in [
            (self.assignments[0], self.teacher, self.classes[0]),
            (other_assignment, other_teacher, other_class),
        ]:
            Submission.objects.create(assignment=assignment, teacher=teacher, class_group=group, last_name='Тестовий', first_name='Учень')
        self.client.force_login(self.user)
        response = self.client.get(reverse('all_submissions_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['all_classes']), set(self.classes))
        self.assertEqual(set(response.context['all_assignments']), set(self.assignments))
        self.assertEqual(response.context['total_count'], 1)
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        response = self.client.get(reverse('all_submissions_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['all_classes']), {*self.classes, other_class})
        self.assertEqual(set(response.context['all_assignments']), {*self.assignments, other_assignment})
        self.assertEqual(response.context['total_count'], 2)
