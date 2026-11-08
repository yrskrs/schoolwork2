"""Unretrieved student links must never become model-invented evidence."""
import io
from unittest.mock import patch
from django.test import TestCase
from . import tests_ai_connections as fixtures
from .gemini_service import fetch_url_content, extract_submission_content, evaluate_submission_with_gemini


class UnreadableLinkTests(TestCase):
    setUp = fixtures.AIConnectionsTests.setUp
    add_connection = fixtures.AIConnectionsTests.add_connection

    def link_only(self, comment=''):
        self.sub.file = ''
        self.sub.link = 'https://example.com/configurator/?items=123'
        self.sub.comment_student = comment
        self.sub.save()

    def test_empty_script_rendered_page_returns_no_evidence(self):
        response = io.BytesIO(b'<html><head><title>Configurator</title></head><body><script>render()</script></body></html>')
        response.headers = {'Content-Type': 'text/html'}
        with patch('feed.safe_http.validate_public_url'), patch('feed.safe_http.public_urlopen', return_value=response):
            _, content, error = fetch_url_content('https://example.com/configurator/')
        self.assertIsNone(content)
        self.assertIn('не прочитано', error)

    @patch('feed.gemini_service.fetch_url_content', return_value=('SPA', None, 'Вміст не прочитано'))
    @patch('feed.gemini_service.call_ai_api')
    def test_link_only_is_rejected_before_any_ai_request(self, ai, fetch):
        self.add_connection('gemini', 'synthetic-link')
        self.link_only()
        result = evaluate_submission_with_gemini(self.sub)
        self.assertEqual(result['status'], 'unsupported')
        self.assertNotIn('suggested_grade', result)
        ai.assert_not_called()
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.student_ai_checked)
        self.assertIsNone(self.sub.ai_suggested_grade)

    @patch('feed.gemini_service.fetch_url_content', return_value=(None, None, 'HTTP 403'))
    def test_url_in_comment_alone_is_not_substantive_work(self, fetch):
        self.link_only('https://example.com/private-document')
        self.sub.link = ''; self.sub.save()
        text, media, error = extract_submission_content(self.sub)
        self.assertEqual((text, media), ([], []))
        self.assertIn('Оцінку не сформовано', error)

    @patch('feed.gemini_service.fetch_url_content', return_value=(None, None, 'HTTP 403'))
    def test_readable_file_remains_available_with_unreadable_link(self, fetch):
        self.sub.link = 'https://example.com/private'; self.sub.save()
        text, _, error = extract_submission_content(self.sub)
        self.assertIsNone(error)
        self.assertIn('складні паролі', '\n'.join(text))
        self.assertTrue(self.sub._inaccessible_materials)

    @patch('feed.gemini_service.fetch_url_content', return_value=(None, None, 'HTTP 403'))
    def test_substantive_comment_remains_available_with_unreadable_link(self, fetch):
        self.link_only('Моя відповідь: для кожного сайту використовую унікальний пароль.')
        text, _, error = extract_submission_content(self.sub)
        self.assertIsNone(error)
        self.assertIn('унікальний пароль', '\n'.join(text))

    @patch('feed.gemini_service.fetch_url_content', return_value=('Work', 'Підтверджений вміст роботи: сума 24000 грн.', None))
    def test_readable_link_is_not_rejected(self, fetch):
        self.link_only()
        text, _, error = extract_submission_content(self.sub)
        self.assertIsNone(error)
        self.assertIn('24000', '\n'.join(text))
