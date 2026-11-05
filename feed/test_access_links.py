"""Exercise public access end to end: identities, uploads, files, revocation and LAN isolation."""
from datetime import timedelta
from unittest.mock import patch
from pathlib import Path
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import (Teacher, ClassGroup, Student, Subject, Assignment, AssignmentFile,
                     Submission, AccessLink, AccessVisitor, TailscaleConnection, AccessLinkEvent)
from .access_control import COOKIE
from .access_forms import AccessLinkForm, ConnectionForm
from . import tailscale_service

HOST = 'school.tail123.ts.net'
PUBLIC = {'HTTP_HOST': HOST, 'HTTP_TAILSCALE_FUNNEL_REQUEST': '?1', 'REMOTE_ADDR': '127.0.0.1'}
LAN = {'HTTP_HOST': '192.168.1.13', 'REMOTE_ADDR': '192.168.1.42'}


class PublicAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('teacher', password='pass', is_superuser=True, is_staff=True)
        cls.teacher = Teacher.objects.create(user=cls.user, full_name='Учитель')
        cls.other_user = User.objects.create_user('other', password='pass')
        cls.other_teacher = Teacher.objects.create(user=cls.other_user, full_name='Інший учитель')
        cls.a = ClassGroup.objects.create(name='7-А', grade=7, letter='А')
        cls.b = ClassGroup.objects.create(name='8-Б', grade=8, letter='Б')
        cls.teacher.classes.add(cls.a)
        cls.other_teacher.classes.add(cls.b)
        cls.pupil = Student.objects.create(class_group=cls.a, last_name='Петренко', first_name='Олена')
        cls.peer = Student.objects.create(class_group=cls.a, last_name='Іваненко', first_name='Іван')
        cls.outsider = Student.objects.create(class_group=cls.b, last_name='Інший', first_name='Учень')
        subject = Subject.objects.create(name='Інформатика')
        cls.teacher.subjects.add(subject)
        cls.one = Assignment.objects.create(teacher=cls.teacher, subject=subject, title='Дозволене завдання', status='published', published_at=timezone.now())
        cls.one.classes.add(cls.a)
        cls.two = Assignment.objects.create(teacher=cls.other_teacher, subject=subject, title='Чуже завдання', status='published', published_at=timezone.now())
        cls.two.classes.add(cls.b)
        cls.draft = Assignment.objects.create(teacher=cls.teacher, title='Чернетка', status='draft')
        cls.draft.classes.add(cls.a)
        cls.own_sub = Submission.objects.create(assignment=cls.one, student=cls.pupil, class_group=cls.a, last_name=cls.pupil.last_name, first_name=cls.pupil.first_name)
        cls.peer_sub = Submission.objects.create(assignment=cls.one, student=cls.peer, class_group=cls.a, last_name=cls.peer.last_name, first_name=cls.peer.first_name)
        cls.other_sub = Submission.objects.create(assignment=cls.two, student=cls.outsider, class_group=cls.b, last_name=cls.outsider.last_name, first_name=cls.outsider.first_name)
        TailscaleConnection.objects.create(enabled=True, public_url='https://' + HOST)

    def link(self, kind='site', **kwargs):
        return AccessLink.objects.create(teacher=self.teacher, name='Урок', kind=kind, **kwargs)

    def enter(self, link, client=None):
        client = client or self.client
        return client.post(reverse('access_enter', args=[link.token]), **PUBLIC)

    def get(self, url, **kwargs):
        return self.client.get(url, **PUBLIC, **kwargs)

    def test_fresh_optional_installation_leaves_lan_and_existing_proxy_unchanged(self):
        TailscaleConnection.objects.all().delete()
        self.assertEqual(self.client.get('/', **LAN).status_code, 200)
        self.assertEqual(self.client.get('/', HTTP_HOST='school.example.org', HTTP_X_FORWARDED_FOR='8.8.8.8').status_code, 200)
        self.assertEqual(self.get('/').status_code, 403)

    def test_all_anonymous_public_entrypoints_are_closed(self):
        for url in ['/', '/feed/fragment/', '/api/classes/', '/api/students-autocomplete/',
                    f'/assignment/{self.one.pk}/', f'/assignment/{self.one.pk}/submit/',
                    '/media/private.txt', '/setup/', '/api/v2/ping/', '/teacher/']:
            with self.subTest(url=url):
                self.assertEqual(self.get(url).status_code, 403)
        self.assertEqual(self.client.post(f'/assignment/{self.one.pk}/submit/', {}, **PUBLIC).status_code, 403)

    def test_spoofed_host_and_forwarded_headers_never_grant_lan_access(self):
        self.assertEqual(self.client.get('/', HTTP_HOST='localhost', HTTP_TAILSCALE_FUNNEL_REQUEST='?1', REMOTE_ADDR='127.0.0.1').status_code, 403)
        self.assertEqual(self.client.get('/', HTTP_HOST='localhost', HTTP_X_FORWARDED_FOR='127.0.0.1', REMOTE_ADDR='127.0.0.1').status_code, 403)
        self.assertEqual(self.client.get('/', HTTP_HOST='localhost', REMOTE_ADDR='8.8.8.8').status_code, 403)

    def test_lan_is_unchanged_even_after_public_revocation(self):
        link = self.link()
        self.enter(link)
        link.revoked_at = timezone.now(); link.save()
        self.assertEqual(self.get('/').status_code, 403)
        self.assertEqual(self.client.get('/', **LAN).status_code, 200)

    def test_confirmation_does_not_count_previews_or_head_requests(self):
        link = self.link()
        for method in ('get', 'head'):
            response = getattr(self.client, method)(reverse('access_enter', args=[link.token]), **PUBLIC)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(COOKIE, response.cookies)
        link.refresh_from_db()
        self.assertEqual(link.visits, 0)
        self.assertEqual(link.browsers.count(), 0)

    def test_grant_cookie_and_no_cache_and_redirect_without_secret(self):
        link = self.link()
        response = self.enter(link)
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertTrue(response.cookies[COOKIE]['secure'])
        self.assertTrue(response.cookies[COOKIE]['httponly'])
        self.assertEqual(response.cookies[COOKIE]['samesite'], 'Lax')
        response = self.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'same-origin')

    def test_repeated_visits_one_browser_and_independent_links(self):
        first, second = self.link(), self.link()
        self.enter(first); self.enter(first); self.enter(second)
        self.enter(first, Client())
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.visits, first.browsers.count()), (3, 2))
        self.assertEqual((second.visits, second.browsers.count()), (1, 1))
        self.assertIsNotNone(first.last_used_at)

    def test_revoked_expired_and_disabled_connections_fail_closed(self):
        link = self.link()
        self.enter(link)
        link.expires_at = timezone.now() - timedelta(seconds=1); link.save()
        self.assertEqual(self.get('/').status_code, 403)
        self.assertEqual(self.enter(link).status_code, 403)
        link.expires_at = None; link.save()
        self.enter(link)
        TailscaleConnection.objects.update(enabled=False)
        self.assertEqual(self.get('/').status_code, 403)
        self.assertEqual(self.enter(link).status_code, 403)
        self.assertTrue(AccessLink.objects.filter(pk=link.pk).exists())

    def test_tampered_cookie_is_rejected(self):
        self.enter(self.link())
        self.client.cookies[COOKIE] = 'arbitrary:1:invalid'
        self.assertEqual(self.get('/').status_code, 403)

    def test_class_scope_feed_fragments_and_api_and_direct_objects(self):
        self.enter(self.link('class', class_group=self.a))
        for url in ['/', '/feed/fragment/']:
            response = self.get(url)
            self.assertContains(response, self.one.title)
            self.assertNotContains(response, self.two.title)
        self.assertEqual([r['id'] for r in self.get('/api/classes/').json()['classes']], [self.a.pk])
        data = self.get('/api/students-autocomplete/').json()['students']
        self.assertEqual({r['id'] for r in data}, {self.pupil.pk, self.peer.pk})
        self.assertEqual(self.get(f'/assignment/{self.two.pk}/').status_code, 404)
        self.assertEqual(self.get(f'/assignment/{self.two.pk}/submit/').status_code, 404)
        self.assertEqual(self.get(f'/submission/{self.other_sub.pk}/').status_code, 404)
        self.assertEqual(self.get('/?class=' + str(self.b.pk)).status_code, 404)
        self.assertEqual(self.get('/feed/check-updates/').json()['count'], 1)

    def test_assignment_scope_hides_other_tasks_and_drafts(self):
        self.enter(self.link('assignment', assignment=self.one))
        self.assertEqual(self.get(f'/assignment/{self.one.pk}/').status_code, 200)
        self.assertEqual(self.get(f'/assignment/{self.two.pk}/').status_code, 404)
        self.assertEqual(self.get(f'/assignment/{self.draft.pk}/').status_code, 404)
        self.assertNotContains(self.get('/'), self.two.title)

    def test_personal_link_limits_roster_and_submissions(self):
        self.enter(self.link('student', student=self.pupil))
        data = self.get('/api/students-autocomplete/').json()['students']
        self.assertEqual([r['id'] for r in data], [self.pupil.pk])
        self.assertEqual(self.get(f'/submission/{self.own_sub.pk}/').status_code, 200)
        self.assertEqual(self.get(f'/submission/{self.peer_sub.pk}/').status_code, 404)
        self.assertEqual(self.get(f'/submission/{self.other_sub.pk}/').status_code, 404)
        response = self.get('/my-submissions/')
        self.assertContains(response, self.pupil.last_name)
        self.assertNotContains(response, self.peer.last_name)

    def test_personal_link_submission_ignores_tampered_identity_and_coauthors(self):
        self.enter(self.link('student', student=self.pupil))
        response = self.client.post(reverse('submit_assignment', args=[self.one.pk]), {
            'student_id': self.outsider.pk, 'full_name': 'Підміна Імені', 'class_group': self.b.pk,
            'other_student': 'on', 'coauthor_ids': f'[{self.peer.pk}]', 'coauthors': ['Чужий Учень'],
            'comment_student': 'Відповідь учня',
        }, **PUBLIC)
        self.assertEqual(response.status_code, 302)
        saved = Submission.objects.order_by('-pk').first()
        self.assertEqual(saved.student_id, self.pupil.pk)
        self.assertEqual(saved.class_group_id, self.a.pk)
        self.assertNotIn('Чужий', saved.comment_student)
        self.assertEqual(Submission.objects.filter(assignment=self.one, last_name='Чужий').count(), 0)

    def test_class_submission_rejects_other_class(self):
        self.enter(self.link('class', class_group=self.a))
        response = self.client.post(reverse('submit_assignment', args=[self.one.pk]), {
            'student_id': self.outsider.pk, 'full_name': 'Підміна Імені', 'class_group': self.b.pk,
            'comment_student': 'Відповідь',
        }, **PUBLIC)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 3)

    def test_files_preview_ranges_and_revocation(self):
        material = AssignmentFile.objects.create(assignment=self.one,
            file=SimpleUploadedFile('allowed.txt', b'material'), original_name='allowed.txt')
        other = AssignmentFile.objects.create(assignment=self.two,
            file=SimpleUploadedFile('other.txt', b'other'), original_name='other.txt')
        link = self.link('class', class_group=self.a)
        self.enter(link)
        response = self.get(material.file.url, HTTP_RANGE='bytes=0-2')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), b'mat')
        self.assertEqual(self.get(other.file.url).status_code, 404)
        self.assertEqual(self.get(reverse('file_download', args=[other.pk])).status_code, 404)
        preview = Path(settings.MEDIA_ROOT) / 'previews' / str(material.pk) / 'slide-1.png'
        preview.parent.mkdir(parents=True, exist_ok=True); preview.write_bytes(b'png')
        response = self.get(f'/media/previews/{material.pk}/slide-1.png')
        self.assertEqual(response.status_code, 200); b''.join(response.streaming_content)
        link.revoked_at = timezone.now(); link.save()
        self.assertEqual(self.get(material.file.url).status_code, 403)
        self.assertEqual(self.get(reverse('file_download', args=[material.pk])).status_code, 403)

    def test_unknown_media_and_teacher_api_are_not_allowed_by_shared_link(self):
        self.enter(self.link())
        private = Path(settings.MEDIA_ROOT) / 'private.txt'; private.write_bytes(b'private')
        self.assertEqual(self.get('/media/private.txt').status_code, 404)
        self.assertEqual(self.get('/teacher/access-links/').status_code, 404)
        self.assertEqual(self.get('/api/server-stats/').status_code, 404)

    def test_independent_revocation(self):
        one, two = self.link(), self.link()
        other = Client(); self.enter(one); self.enter(two, other)
        one.revoked_at = timezone.now(); one.save()
        self.assertEqual(self.get('/').status_code, 403)
        self.assertEqual(other.get('/', **PUBLIC).status_code, 200)

    def test_teacher_login_remains_accessible_and_authenticated_teacher_bypasses_grant(self):
        self.assertEqual(self.get('/teacher/login/').status_code, 200)
        self.client.force_login(self.user)
        self.assertEqual(self.get('/teacher/access-links/').status_code, 200)
        self.assertEqual(self.get('/teacher/settings/tailscale/').status_code, 200)

    def test_csrf_is_required_for_grant_and_settings(self):
        client = Client(enforce_csrf_checks=True)
        link = self.link()
        self.assertEqual(client.post(reverse('access_enter', args=[link.token]), **PUBLIC).status_code, 403)
        client.get(reverse('access_enter', args=[link.token]), **PUBLIC)
        csrf = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post(reverse('access_enter', args=[link.token]), {'csrfmiddlewaretoken': csrf}, HTTP_ORIGIN='https://' + HOST, **PUBLIC)
        self.assertEqual(response.status_code, 302)
        client.force_login(self.user)
        self.assertEqual(client.post('/teacher/settings/tailscale/', {'action': 'save'}, **LAN).status_code, 403)

    def test_manage_links_create_rename_expire_revoke_preserving_statistics(self):
        self.client.force_login(self.user)
        data = {'action': 'save', 'name': '7-А — урок', 'kind': 'class', 'class_group': self.a.pk}
        self.assertEqual(self.client.post(reverse('access_links'), data, **LAN).status_code, 302)
        link = AccessLink.objects.get()
        AccessLink.objects.filter(pk=link.pk).update(visits=19)
        data.update(link_id=link.pk, name='Нова назва')
        self.assertEqual(self.client.post(reverse('access_links'), data, **LAN).status_code, 302)
        link.refresh_from_db(); self.assertEqual(link.visits, 19); self.assertEqual(link.name, 'Нова назва')
        self.assertEqual(self.client.post(reverse('access_links'), {'action': 'revoke', 'link_id': link.pk}, **LAN).status_code, 302)
        link.refresh_from_db(); self.assertFalse(link.active)
        self.assertEqual(AccessLinkEvent.objects.filter(link=link).count(), 3)
        self.assertEqual(self.client.get(reverse('access_links') + '?edit=nonsense', **LAN).status_code, 404)

    def test_other_teacher_cannot_edit_or_revoke_or_read_qr(self):
        link = self.link()
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(reverse('access_links') + '?edit=' + str(link.pk), **LAN).status_code, 404)
        self.assertEqual(self.client.post(reverse('access_links'), {'action': 'revoke', 'link_id': link.pk}, **LAN).status_code, 404)
        self.assertEqual(self.client.get(reverse('access_qr', args=[link.pk]), **LAN).status_code, 404)
        self.assertEqual(self.client.post(reverse('tailscale_settings'), {'action': 'save'}, **LAN).status_code, 403)

    def test_teacher_targets_are_scoped_and_url_validated(self):
        for data in ({'name': 'X', 'kind': 'class', 'class_group': self.b.pk},
                     {'name': 'X', 'kind': 'student', 'student': self.outsider.pk},
                     {'name': 'X', 'kind': 'assignment', 'assignment': self.two.pk},
                     {'name': 'X', 'kind': 'student'}):
            self.assertFalse(AccessLinkForm(data, teacher=self.teacher).is_valid())
        for url in ['http://' + HOST, 'https://evil.example', 'https://' + HOST + '/test',
                    'https://' + HOST + ':99999', 'https://user:secret@' + HOST]:
            self.assertFalse(ConnectionForm({'enabled': True, 'public_url': url}).is_valid(), url)
        self.assertTrue(ConnectionForm({'enabled': True, 'public_url': 'https://' + HOST + ':8443'}).is_valid())

    def test_qr_generated_locally_and_disabled_integration_still_has_management_page(self):
        link = self.link()
        TailscaleConnection.objects.update(enabled=False)
        self.client.force_login(self.user)
        response = self.client.get(reverse('access_qr', args=[link.pk]), **LAN)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<svg', response.content)
        response = self.client.get(reverse('access_links'), **LAN)
        self.assertContains(response, 'Tailscale не підключено')
        self.assertContains(response, link.name)

    def test_target_deletion_or_roster_deactivation_disables_link(self):
        link = self.link('student', student=self.pupil)
        self.enter(link)
        Student.objects.filter(pk=self.pupil.pk).update(integration_active=False)
        self.assertEqual(self.get('/').status_code, 403)
        Student.objects.filter(pk=self.pupil.pk).update(integration_active=True)
        Student.objects.filter(pk=self.pupil.pk).delete()
        link.refresh_from_db(); self.assertFalse(link.active)

    def test_scope_edits_apply_to_existing_browser_without_issuing_a_new_cookie(self):
        link = self.link('site')
        self.enter(link)
        self.assertEqual(self.get(f'/assignment/{self.two.pk}/').status_code, 200)
        AccessLink.objects.filter(pk=link.pk).update(kind='assignment', assignment=self.one)
        self.assertEqual(self.get(f'/assignment/{self.two.pk}/').status_code, 404)
        self.assertEqual(self.get(f'/assignment/{self.one.pk}/').status_code, 200)

    def test_personal_direct_submission_files_and_ai_jobs_are_scoped(self):
        from .models import AIJob
        own = Submission.objects.get(pk=self.own_sub.pk)
        own.file = SimpleUploadedFile('own.txt', b'own'); own.save()
        peer = Submission.objects.get(pk=self.peer_sub.pk)
        peer.file = SimpleUploadedFile('peer.txt', b'peer'); peer.save()
        self.enter(self.link('student', student=self.pupil))
        response = self.get(own.file.url)
        self.assertEqual(response.status_code, 200); b''.join(response.streaming_content)
        self.assertEqual(self.get(peer.file.url).status_code, 404)
        # A group file can have an earlier owner outside this personal scope.
        own.file = peer.file.name; own.save()
        response = self.get(own.file.url)
        self.assertEqual(response.status_code, 200); b''.join(response.streaming_content)
        own_job = AIJob.objects.create(kind='student_check', submission=own, status='succeeded')
        peer_job = AIJob.objects.create(kind='student_check', submission=peer, status='succeeded')
        self.assertEqual(self.get(reverse('ai_job_status', args=[own_job.pk])).status_code, 200)
        self.assertEqual(self.get(reverse('ai_job_status', args=[peer_job.pk])).status_code, 404)

    def test_scoped_assignment_understanding_can_be_polled_only_when_enabled(self):
        from .models import AIJob
        self.enter(self.link('assignment', assignment=self.one))
        own_job = AIJob.objects.create(kind='understanding', assignment=self.one, status='succeeded')
        other_job = AIJob.objects.create(kind='understanding', assignment=self.two, status='succeeded')
        Assignment.objects.filter(pk=self.one.pk).update(allow_student_ai_understanding=True)
        self.assertEqual(self.get(reverse('ai_job_status', args=[own_job.pk])).status_code, 200)
        self.assertEqual(self.get(reverse('ai_job_status', args=[other_job.pk])).status_code, 404)
        Assignment.objects.filter(pk=self.one.pk).update(allow_student_ai_understanding=False)
        self.assertEqual(self.get(reverse('ai_job_status', args=[own_job.pk])).status_code, 404)

    def test_submission_notifications_do_not_leak_other_scopes(self):
        from .submission_notifications import SubmissionNotification, SubmissionNotificationSettings
        SubmissionNotificationSettings.objects.create(teacher=self.teacher, students_enabled=True, students_all_classes=True)
        SubmissionNotificationSettings.objects.create(teacher=self.other_teacher, students_enabled=True, students_all_classes=True)
        SubmissionNotification.objects.all().delete()
        allowed = SubmissionNotification.objects.create(teacher=self.teacher, submission=self.own_sub, for_students=True)
        denied = SubmissionNotification.objects.create(teacher=self.other_teacher, submission=self.other_sub, for_students=True)
        self.enter(self.link('class', class_group=self.a))
        result = self.get('/api/submission-notifications/?since=0').json()
        self.assertEqual(result['cursor'], allowed.pk)
        self.assertEqual(len(result['events']), 1)
        self.assertNotIn(self.two.title, str(result))
        self.assertNotIn(str(self.other_teacher.pk), result['policies'])

    def test_success_page_lists_and_latest_submission_are_personal(self):
        self.enter(self.link('student', student=self.pupil))
        response = self.get(reverse('submit_success', args=[self.one.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row.pk for row in response.context['page_obj']], [self.own_sub.pk])
        self.assertEqual(response.context['latest_submission'].pk, self.own_sub.pk)
        self.assertNotContains(response, self.peer.last_name)

    def test_duplicate_warnings_and_ai_response_do_not_expose_other_student(self):
        from .models import AIJob
        self.enter(self.link('student', student=self.pupil))
        details = {'is_duplicate': True, 'type': 'student_duplicate',
            'warning_message': 'Збіг: Іваненко Іван', 'duplicate_student_name': 'Іваненко Іван'}
        with patch('feed.views.check_submission_duplicates', return_value=details):
            response = self.get(reverse('submission_detail', args=[self.own_sub.pk]))
            self.assertNotContains(response, 'Іваненко')
        job = AIJob.objects.create(kind='student_check', submission=self.own_sub,
            status='succeeded', result={'duplicate_info': details})
        response = self.get(reverse('ai_job_status', args=[job.pk]))
        self.assertNotIn('Іваненко', response.content.decode())
        self.assertTrue(response.json()['duplicate_info']['is_duplicate'])

    def test_open_mode_allows_plain_urls_but_not_teacher_or_private_resources(self):
        TailscaleConnection.objects.update(allow_without_link=True)
        self.assertEqual(self.get('/').status_code, 200)
        self.assertEqual(self.get('/api/classes/').status_code, 200)
        self.assertEqual(self.get('/api/students-autocomplete/').status_code, 200)
        self.assertEqual(self.get(reverse('assignment_detail', args=[self.one.pk])).status_code, 200)
        self.assertEqual(self.get(reverse('assignment_detail', args=[self.draft.pk])).status_code, 404)
        for url in ['/teacher/access-links/', '/api/server-stats/', '/setup/', '/media/private.txt']:
            self.assertEqual(self.get(url).status_code, 404, url)
        self.assertEqual(self.get('/teacher/login/').status_code, 200)
        material = AssignmentFile.objects.create(assignment=self.one,
            file=SimpleUploadedFile('open.txt', b'public material'), original_name='open.txt')
        response = self.get(material.file.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'public material')

    def test_turning_open_mode_off_immediately_restores_protection(self):
        link = self.link('class', class_group=self.a)
        self.enter(link)
        link.revoked_at = timezone.now(); link.save()
        TailscaleConnection.objects.update(allow_without_link=True)
        self.assertEqual(self.get('/').status_code, 200)
        self.assertEqual(self.get(reverse('assignment_detail', args=[self.two.pk])).status_code, 200)
        TailscaleConnection.objects.update(allow_without_link=False)
        self.assertEqual(self.get('/').status_code, 403)
        self.assertEqual(Client().get('/', **PUBLIC).status_code, 403)

    def test_disabled_connection_always_closes_public_access_even_in_open_mode(self):
        TailscaleConnection.objects.update(enabled=False, allow_without_link=True)
        self.assertEqual(self.get('/').status_code, 403)
        self.assertEqual(self.client.get('/', **LAN).status_code, 200)

    def test_only_admin_can_save_global_access_mode_and_connection_is_preserved(self):
        self.client.force_login(self.other_user)
        response = self.client.post(reverse('access_links'), {'action': 'access_mode', 'allow_without_link': 'on'}, **LAN)
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.user)
        response = self.client.post(reverse('access_links'), {'action': 'access_mode', 'allow_without_link': 'on', 'enabled': '', 'public_url': 'https://other.ts.net'}, **LAN)
        self.assertEqual(response.status_code, 302)
        connection = TailscaleConnection.objects.get(pk=1)
        self.assertTrue(connection.enabled); self.assertTrue(connection.allow_without_link)
        self.assertEqual(connection.public_url, 'https://' + HOST)
        self.assertContains(self.client.get(reverse('access_links'), **LAN), 'Відкритий доступ')
        self.client.post(reverse('access_links'), {'action': 'access_mode'}, **LAN)
        connection.refresh_from_db(); self.assertFalse(connection.allow_without_link)
        self.assertEqual(AccessLinkEvent.objects.filter(action__in=['public_open', 'public_protected']).count(), 2)

    def test_open_mode_requires_enabled_connection_and_csrf(self):
        self.client.force_login(self.user)
        TailscaleConnection.objects.update(enabled=False)
        response = self.client.post(reverse('access_links'), {'action': 'access_mode', 'allow_without_link': 'on'}, **LAN)
        self.assertContains(response, 'Спочатку увімкніть підключення Tailscale')
        self.assertFalse(TailscaleConnection.objects.get(pk=1).allow_without_link)
        client = Client(enforce_csrf_checks=True); client.force_login(self.user)
        self.assertEqual(client.post(reverse('access_links'), {'action': 'access_mode', 'allow_without_link': 'on'}, **LAN).status_code, 403)


class TailscaleAdapterTests(TestCase):
    def test_cli_not_configured_is_optional_and_never_invoked(self):
        with override_settings(SCHOOLNET_TAILSCALE_BINARY=''), patch('feed.tailscale_service.shutil.which', return_value=None), patch('feed.tailscale_service.subprocess.run') as run:
            with self.assertRaises(tailscale_service.TailscaleError):
                tailscale_service.status()
            run.assert_not_called()

    @override_settings(SCHOOLNET_TAILSCALE_BINARY='/usr/bin/tailscale', SCHOOLNET_TAILSCALE_TARGET_PORT=8080)
    def test_commands_are_bounded_and_do_not_reset_other_funnel_routes(self):
        connection = TailscaleConnection(enabled=True, public_url='https://' + HOST)
        config = {'Web': {HOST + ':443': {'Handlers': {'/test/': {'Proxy': 'http://127.0.0.1:8000'}}}}}
        with patch('feed.tailscale_service.status', return_value={'backend': 'Running', 'dns_name': HOST, '_config': config}), patch('feed.tailscale_service.run') as run:
            tailscale_service.set_funnel(connection, True)
            self.assertEqual(run.call_args.args, ('funnel', '--bg', '--https=443', '--set-path=/', '--yes', 'http://127.0.0.1:8080'))
            tailscale_service.set_funnel(connection, False)
            self.assertEqual(run.call_args.args[-1], 'off')
        with patch('feed.tailscale_service.subprocess.run') as run:
            run.return_value.returncode = 0; run.return_value.stdout = '{}'
            tailscale_service.run('status', '--json')
            self.assertEqual(run.call_args.kwargs['timeout'], 15)
            self.assertNotIn('shell', run.call_args.kwargs)

    @override_settings(SCHOOLNET_TAILSCALE_TARGET_PORT=8080)
    def test_refuses_overwriting_other_service_or_different_node(self):
        connection = TailscaleConnection(enabled=True, public_url='https://' + HOST)
        for state in [ {'backend': 'Running', 'dns_name': 'other.ts.net', '_config': {}},
                       {'backend': 'Running', 'dns_name': HOST, '_config': {'Web': {HOST + ':443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:8000'}}}}}}]:
            with patch('feed.tailscale_service.status', return_value=state), patch('feed.tailscale_service.run') as run:
                with self.assertRaises(tailscale_service.TailscaleError):
                    tailscale_service.set_funnel(connection, True)
                run.assert_not_called()

    @override_settings(SCHOOLNET_TAILSCALE_BINARY='')
    def test_existing_cli_is_detected_without_explicit_binary_setting(self):
        with patch('feed.tailscale_service.shutil.which', return_value='/usr/local/bin/tailscale'), patch('feed.tailscale_service.subprocess.run') as run:
            run.return_value.returncode = 0; run.return_value.stdout = '{}'
            tailscale_service.run('status', '--json')
            self.assertEqual(run.call_args.args[0], ['/usr/local/bin/tailscale', 'status', '--json'])


class AccessLoggingTests(TestCase):
    def test_backup_export_excludes_external_credentials_and_grants(self):
        from .cloud_backups import models
        labels = models()
        for label in ('feed.accesslink', 'feed.accessvisitor', 'feed.accesslinkevent', 'feed.tailscaleconnection'):
            self.assertNotIn(label, labels)

    def test_django_logs_do_not_contain_invalid_bearer_url(self):
        from django.test import RequestFactory
        from django.http import HttpResponse
        from .access_middleware import PublicAccessMiddleware
        request = RequestFactory().get('/access/synthetic-secret/')
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
        request.session = {}
        response = PublicAccessMiddleware(lambda request: HttpResponse(status=403))(request)
        self.assertEqual(request.path, '/access/***/')
        self.assertEqual(response['Referrer-Policy'], 'same-origin')

    def test_gunicorn_access_log_masks_request_and_referrer_and_custom_atoms(self):
        try:
            from .gunicorn_logging import AccessLogger
        except ImportError:
            self.skipTest('Gunicorn installed with Docker runtime dependencies')
        from gunicorn.glogging import Logger
        atoms = {'r': 'GET /access/synthetic-secret/?x=y HTTP/1.1', 'f': 'https://school/access/synthetic-secret/',
                 '{raw_uri}e': '/access/synthetic-secret/', 'U': '/access/synthetic-secret/', 'q': ''}
        with patch.object(Logger, 'atoms', return_value=atoms):
            sanitized = AccessLogger.atoms(AccessLogger.__new__(AccessLogger), None, None, {
                'PATH_INFO': '/access/synthetic-secret/', 'REQUEST_METHOD': 'GET', 'SERVER_PROTOCOL': 'HTTP/1.1'}, timedelta())
        self.assertNotIn('synthetic-secret', str(sanitized))
