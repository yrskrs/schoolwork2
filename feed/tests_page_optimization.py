"""Account navigation and cached dialog programs preserve ordinary role access."""
import re
from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from feed.models import Teacher


class PageOptimizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('optimization-admin',password='synthetic')
        Teacher.objects.create(user=self.user,full_name='Перевірка меню')
        self.client.force_login(self.user)

    def test_menu_keeps_routine_links_and_collapses_secondary_controls(self):
        response = self.client.get(reverse('teacher_dashboard'))
        self.assertEqual(response.status_code,200)
        html = response.content.decode()
        menu = html.split('<div class="user-menu-panel"',1)[1].split('</form>',1)[0]
        for name in ['teacher_dashboard','all_submissions_dashboard','gradebook']:
            self.assertIn(f'href="{reverse(name)}"',menu)
        groups = re.findall(r'<details\b[^>]*class="user-menu-group"[^>]*>',menu)
        self.assertEqual(len(groups),3)
        self.assertTrue(all(' open' not in g for g in groups))
        self.assertEqual(menu.count('onclick="applyFontScale(100)"'),1)
        self.assertIn('data-font-scale-step="-5"',menu)
        self.assertIn('data-font-scale-step="5"',menu)
        self.assertRegex(html,r'<script data-dialog-script="[^"]*guide_dialog.js[^\"]*">')
        self.assertNotIn('window.switchGuideTab = function',html)

    def test_public_help_uses_same_cached_code_without_teacher_markup(self):
        self.client.logout()
        response = self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'guide_dialog.js')
        self.assertNotContains(response,'id="user-menu"')


    def test_collected_css_serves_gzip_with_unchanged_bytes(self):
        import gzip
        import tempfile
        from django.conf import settings
        from django.core.management import call_command
        from django.test import Client, override_settings
        with tempfile.TemporaryDirectory(prefix='synthetic-compressed-static-') as directory:
            with override_settings(STATIC_ROOT=directory, WHITENOISE_USE_FINDERS=False, DEBUG=False):
                call_command('collectstatic',interactive=False,verbosity=0)
                response = Client().get('/static/css/main.css',HTTP_ACCEPT_ENCODING='gzip')
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['Content-Encoding'],'gzip')
                self.assertIn('Accept-Encoding',response.headers['Vary'])
                packed = b''.join(response.streaming_content) if response.streaming else response.content
                from pathlib import Path
                original = (settings.BASE_DIR/'static/css/main.css').read_bytes()
                self.assertEqual(gzip.decompress(packed),original)
                self.assertLess(len(packed),len(original)//2)
