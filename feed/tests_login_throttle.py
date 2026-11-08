"""Password policy and shared failed-login throttle regressions."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import LoginThrottle, Teacher


class LoginThrottleTests(TestCase):
    def setUp(self):
        User.objects.create_superuser('site_admin', password='AdminStrong!2026')
        self.user = User.objects.create_user('throttle_teacher', password='StrongPass!2026')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Throttle Teacher')
        self.other = User.objects.create_user('other_teacher', password='OtherStrong!2026')
        Teacher.objects.create(user=self.other, full_name='Other Teacher')

    def post(self, username='throttle_teacher', password='wrong', ip='192.168.1.12'):
        return self.client.post(reverse('teacher_login'), {
            'username': username, 'password': password,
        }, REMOTE_ADDR=ip)

    def test_five_failures_lock_only_that_username(self):
        for _ in range(4):
            response = self.post()
            self.assertEqual(response.status_code, 200)
        self.post()  # fifth failure sets the lock
        self.assertEqual(LoginThrottle.objects.count(), 1)
        response = self.post(password='StrongPass!2026')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

        # The same shared school IP can still log in as another teacher.
        response = self.post(username='other_teacher', password='OtherStrong!2026')
        self.assertEqual(response.status_code, 302)

    def test_success_clears_failures_and_lock_expires(self):
        for _ in range(5):
            self.post()
        row = LoginThrottle.objects.get()
        self.assertIsNotNone(row.locked_until)
        row.locked_until = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=['locked_until'])
        response = self.post(password='StrongPass!2026')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LoginThrottle.objects.exists())

    def test_lan_http_and_funnel_sources_are_hashed_not_stored_as_passwords(self):
        self.post(ip='192.168.1.12')
        row = LoginThrottle.objects.get()
        self.assertEqual(len(row.key), 64)
        self.assertNotIn('wrong', row.key)
        self.assertNotIn('throttle_teacher', row.key)

    def test_password_validators_reject_weak_and_accept_strong_password(self):
        with self.assertRaises(ValidationError):
            validate_password('123456')
        validate_password('StrongPass!2026')
