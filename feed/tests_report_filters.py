"""Report filter contracts across shared lessons, rosters, roles and exports."""
from datetime import datetime, timedelta, timezone as dt_timezone
from itertools import product
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from lxml import html

from .middleware import set_has_admin
from .models import Assignment, ClassGroup, RosterState, School, Student, Submission, Teacher
from .reports_service import get_report_submissions


class ReportFilterTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        User = get_user_model()
        self.user = User.objects.create_user('report-teacher', password='synthetic')
        self.admin = User.objects.create_superuser('report-admin', password='synthetic')
        self.other_user = User.objects.create_user('other-teacher', password='synthetic')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Основний Учитель')
        self.admin_teacher = Teacher.objects.create(user=self.admin, full_name='Адміністратор')
        self.other = Teacher.objects.create(user=self.other_user, full_name='Інший Учитель')
        School.objects.create(name='Синтетична школа', admin=self.admin)
        self.a = ClassGroup.objects.create(name='8-А', grade=8)
        self.b = ClassGroup.objects.create(name='8-Б', grade=8)
        self.c = ClassGroup.objects.create(name='9-А', grade=9)
        self.shared = Assignment.objects.create(teacher=self.teacher, title='Спільне завдання')
        self.shared.classes.add(self.a, self.b)
        self.only_b = Assignment.objects.create(teacher=self.teacher, title='Завдання лише 8-Б')
        self.only_b.classes.add(self.b)
        self.other_task = Assignment.objects.create(teacher=self.other, title='Інше завдання')
        self.other_task.classes.add(self.a)
        self.ra = Student.objects.create(class_group=self.a, last_name='Реєстровий', first_name='А')
        self.rb = Student.objects.create(class_group=self.b, last_name='Реєстровий', first_name='Б')
        self.moved = Student.objects.create(class_group=self.b, last_name='Переведений', first_name='Учень')
        self.oa = Student.objects.create(class_group=self.a, last_name='Позакласний', first_name='А')
        self.ob = Student.objects.create(class_group=self.b, last_name='Позакласний', first_name='Б')
        self.native_c = Student.objects.create(class_group=self.c, last_name='Без', first_name='Журналу')
        imports = {}
        for teacher in (self.teacher, self.admin_teacher):
            imports[f'{teacher.pk}:a'] = {'class_id': self.a.pk, 'students': {
                'a': {'native_id': self.ra.pk}, 'moved': {'native_id': self.moved.pk}}}
            imports[f'{teacher.pk}:b'] = {'class_id': self.b.pk, 'students': {'b': {'native_id': self.rb.pk}}}
        imports[f'{self.other.pk}:a'] = {'class_id': self.a.pk, 'students': {'outside': {'native_id': self.oa.pk}}}
        RosterState.objects.update_or_create(pk=1, defaults={'data': {'journal_imports': imports}})
        self.rows = {}
        self.add('ra', self.a, self.ra, '9')
        self.add('ra_pending', self.a, self.ra, None)
        self.add('oa', self.a, self.oa, 'Доопрацювати')
        self.add('manual_a', self.a, None, None)
        Submission.objects.filter(pk=self.rows['manual_a'].pk).update(ai_suggested_grade='9', ai_status='success')
        self.add('guest_a', self.a, self.rb, '7')
        self.add('moved_a', self.a, self.moved, '10')
        self.add('legacy_a', self.a, self.ra, '6')
        Submission.objects.filter(pk=self.rows['legacy_a'].pk).update(assignment=None)
        self.add('rb', self.b, self.rb, '10')
        self.add('ob', self.b, self.ob, '')
        self.add('manual_b', self.b, None, None)
        self.add('only_b', self.b, self.rb, '8', assignment=self.only_b)
        self.add('native_c', self.c, self.native_c, '8')
        self.add('manual_c', self.c, None, None)
        service_class = ClassGroup.objects.create(name='8-А (Позакласні)', grade=8)
        self.add('service_class', service_class, None, '5')
        self.add('substitution', self.a, None, '8', assignment=self.other_task, grader=self.user)
        self.add('foreign', self.a, self.ra, '11', assignment=self.other_task, grader=self.admin)
        self.add('old_attempt', self.a, self.oa, '12', is_latest_attempt=False)
        self.class_sets = {
            str(self.a.pk): {'ra', 'ra_pending', 'oa', 'manual_a', 'guest_a', 'moved_a', 'legacy_a', 'substitution', 'foreign'},
            str(self.b.pk): {'rb', 'ob', 'manual_b', 'only_b'},
            str(self.c.pk): {'native_c', 'manual_c'},
            'all': set(self.rows) - {'old_attempt'},
        }
        self.roster_sets = {
            str(self.a.pk): {'ra', 'ra_pending', 'moved_a', 'legacy_a', 'foreign'},
            str(self.b.pk): {'rb', 'only_b'}, str(self.c.pk): set(),
            'all': {'ra', 'ra_pending', 'moved_a', 'legacy_a', 'foreign', 'rb', 'only_b', 'guest_a'},
        }
        self.client.force_login(self.user)

    def add(self, key, group, pupil, grade, assignment=None, grader=None, **extra):
        assignment = assignment or self.shared
        self.rows[key] = Submission.objects.create(
            assignment=assignment, teacher=assignment.teacher, class_group=group, student=pupil,
            first_name=pupil.first_name if pupil else 'Окремий', last_name=pupil.last_name if pupil else key,
            grade=grade, graded_by=grader or (self.user if grade else None),
            graded_at=datetime(2026, 10, 14, 10, tzinfo=dt_timezone.utc) if grade else None, **extra)

    def expected(self, cid, pupil_scope, graded, actor, scope, assignment):
        names = self.class_sets[cid].copy()
        if actor == self.admin and scope == 'mine':
            names &= {'foreign'}
        elif actor == self.user:
            names -= {'foreign'}
        if pupil_scope == 'roster':
            names &= self.roster_sets[cid]
        elif pupil_scope == 'outside':
            names -= self.roster_sets[cid]
        if graded == '1':
            names -= {'ra_pending', 'manual_a', 'ob', 'manual_b', 'manual_c'}
        if assignment != 'all':
            names -= {'only_b', 'substitution', 'foreign', 'legacy_a'}
        return {self.rows[n].pk for n in names}

    def test_192_combinations_keep_class_roster_grade_assignment_and_permissions(self):
        for cid, pupil_scope, graded, actor, scope, assignment in product(
                self.class_sets, ('all', 'roster', 'outside'), ('0', '1'),
                (self.user, self.admin), ('mine', 'all'), ('all', str(self.shared.pk))):
            with self.subTest(class_id=cid, pupils=pupil_scope, graded=graded, actor=actor.pk, scope=scope, assignment=assignment):
                filters = dict(class_id=cid, pupil_scope=pupil_scope, graded_only=graded, scope=scope, assignment_id=assignment)
                actual = set(get_report_submissions(actor, filters).values_list('pk', flat=True))
                self.assertEqual(actual, self.expected(cid, pupil_scope, graded, actor, scope, assignment))

    def test_html_and_both_pdf_modes_have_identical_filtered_rows(self):
        for cid, pupil_scope, checked in product(self.class_sets, ('all', 'roster', 'outside'), (False, True)):
            params = dict(class_id=cid, pupil_scope=pupil_scope, scope='all', assignment_id='all')
            if checked:
                params['graded_only'] = '1'
            page = self.client.get('/teacher/reports/', params)
            self.assertEqual(page.status_code, 200)
            expected = self.expected(cid, pupil_scope, '1' if checked else '0', self.user, 'all', 'all')
            self.assertEqual({s.pk for s in page.context['submissions']}, expected)
            tree = html.fromstring(page.content)
            rendered = {int(value.removeprefix('row-sub-')) for value in tree.xpath('//*[@id and starts-with(@id,"row-sub-")]/@id')}
            self.assertEqual(rendered, expected)
            self.assertEqual(page.context['stats']['total_count'], len(expected))
            for mode in ('grades_only', 'detailed'):
                with patch('feed.reports_service.generate_teacher_report_pdf') as generate:
                    pdf = self.client.get('/teacher/reports/pdf/', dict(params, mode=mode))
                self.assertEqual(pdf.status_code, 200)
                self.assertEqual({s.pk for s in generate.call_args.args[2]}, expected)
                self.assertEqual(generate.call_args.kwargs['mode'], mode)

    def test_target_teacher_never_expands_permissions_and_changes_roster_context(self):
        for actor, scope in ((self.user, 'all'), (self.user, 'mine'), (self.admin, 'mine')):
            params = dict(class_id=str(self.a.pk), scope=scope, graded_only='0', target_teacher=self.other.full_name)
            expected = self.expected(str(self.a.pk), 'all', '0', actor, scope, 'all')
            self.assertEqual(set(get_report_submissions(actor, params).values_list('pk', flat=True)), expected)
        params = dict(class_id=str(self.a.pk), scope='all', graded_only='0', target_teacher=self.other.full_name, pupil_scope='roster')
        self.assertEqual(set(get_report_submissions(self.user, params).values_list('pk', flat=True)), {self.rows['oa'].pk})
        page = self.client.get('/teacher/reports/', params)
        self.assertEqual(set(page.context['assignments']), {self.shared, self.other_task})

    def test_assignment_mismatch_is_empty_in_html_and_pdf_without_resetting_filters(self):
        params = dict(class_id=str(self.a.pk), assignment_id=str(self.only_b.pk), graded_only='0')
        page = self.client.get('/teacher/reports/', params)
        self.assertFalse(page.context['submissions'])
        self.assertEqual(page.context['filters']['assignment_id'], str(self.only_b.pk))
        self.assertTrue(page.context['selected_assignment_unavailable'])
        self.assertNotIn(self.only_b, page.context['assignments'])
        with patch('feed.reports_service.generate_teacher_report_pdf') as generate:
            self.assertEqual(self.client.get('/teacher/reports/pdf/', params).status_code, 200)
        self.assertEqual(generate.call_args.args[2], [])

    def test_dates_use_review_date_then_submission_date_in_kyiv_and_name_tokens(self):
        day_start = datetime(2026, 10, 13, 21, tzinfo=dt_timezone.utc)
        for key, submitted, reviewed in (
            ('ra', day_start - timedelta(days=10), day_start),
            ('oa', day_start, day_start - timedelta(seconds=1)),
            ('manual_a', day_start + timedelta(hours=23, minutes=59), None),
            ('ra_pending', day_start - timedelta(seconds=1), None)):
            Submission.objects.filter(pk=self.rows[key].pk).update(submitted_at=submitted, graded_at=reviewed)
        params = dict(class_id=str(self.a.pk), graded_only='0', date_from='2026-10-14', date_to='2026-10-14')
        ids = set(get_report_submissions(self.user, params).values_list('pk', flat=True))
        self.assertIn(self.rows['ra'].pk, ids)
        self.assertIn(self.rows['manual_a'].pk, ids)
        self.assertNotIn(self.rows['oa'].pk, ids)
        self.assertNotIn(self.rows['ra_pending'].pk, ids)
        params['student_name'] = 'А Реєстровий'
        self.assertEqual(set(get_report_submissions(self.user, params).values_list('pk', flat=True)), {self.rows['ra'].pk, self.rows['legacy_a'].pk})
        params['date_from'] = '2026-10-15'
        self.assertFalse(get_report_submissions(self.user, params).exists())

    def test_selected_pdf_ids_only_narrow_existing_filters(self):
        params = dict(class_id=str(self.a.pk), pupil_scope='outside', graded_only='0')
        selected = [self.rows[n].pk for n in ('oa', 'rb', 'foreign', 'old_attempt')]
        with patch('feed.reports_service.generate_teacher_report_pdf') as generate:
            response = self.client.get('/teacher/reports/pdf/', dict(params, selected_ids=','.join(map(str, selected))))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s.pk for s in generate.call_args.args[2]], [self.rows['oa'].pk])
        for selection in ('', 'invalid', '1,invalid', '9' * 50):
            self.assertFalse(get_report_submissions(self.user, dict(params, selected_ids=selection)).exists())

    def test_generated_pdf_contains_only_the_filtered_class_and_pupil_group(self):
        import io
        from pypdf import PdfReader
        params = dict(class_id=str(self.a.pk), pupil_scope='outside', graded_only='0')
        for mode in ('grades_only', 'detailed'):
            response = self.client.get('/teacher/reports/pdf/', dict(params, mode=mode))
            self.assertEqual(response.status_code, 200)
            text = '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
            self.assertIn('Позакласний А', text)
            self.assertNotIn('Позакласний Б', text)
            self.assertNotIn('Реєстровий А', text)

    def test_invalid_filters_never_expand_selection_or_break_pdf(self):
        for key, value in (('class_id', 'bad'), ('class_id', '-1'), ('class_id', '9' * 50),
                           ('assignment_id', 'bad'), ('assignment_id', '9' * 50),
                           ('pupil_scope', 'unknown'), ('date_from', 'bad'), ('date_to', '2026-02-30')):
            params = {key: value, 'graded_only': '0'}
            self.assertFalse(get_report_submissions(self.user, params).exists())
            page = self.client.get('/teacher/reports/', params)
            self.assertEqual(page.status_code, 200)
            self.assertFalse(page.context['submissions'])
            with patch('feed.reports_service.generate_teacher_report_pdf') as generate:
                self.assertEqual(self.client.get('/teacher/reports/pdf/', params).status_code, 200)
            self.assertEqual(generate.call_args.args[2], [])

    def test_initial_page_defaults_and_missing_journal_list_are_explicit(self):
        page = self.client.get('/teacher/reports/')
        self.assertEqual(page.context['filters']['graded_only'], '1')
        RosterState.objects.all().delete()
        params = dict(class_id=str(self.a.pk), pupil_scope='outside', graded_only='0')
        self.assertEqual(set(get_report_submissions(self.user, params).values_list('pk', flat=True)), {self.rows['manual_a'].pk, self.rows['substitution'].pk})
        self.client.logout()
        self.assertEqual(self.client.get('/teacher/reports/').status_code, 302)
        self.assertEqual(self.client.get('/teacher/reports/pdf/').status_code, 302)
