import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase

from .ai_provenance import file_provenance, normalize_authorship


class AuthorshipStateTests(SimpleTestCase):
    def test_plain_text_without_provenance_is_insufficient(self):
        result = normalize_authorship({'ai_authorship_analysis': {'status': 'none'}}, False)
        self.assertEqual(result['ai_authorship_state'], 'insufficient')
        self.assertFalse(result['ai_generated_detected'])
        self.assertIn('не встановлено', result['ai_generated_details'])

    def test_checked_file_without_signals_is_none(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'none'}}, False,
            provenance=[{'source': 'photo.jpg', 'checked': True, 'signals': [], 'limitations': []}])
        self.assertEqual(result['ai_authorship_state'], 'none')
        self.assertFalse(result['ai_generated_detected'])

    def test_metadata_signal_is_suspected_not_proof(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'none'}}, False,
            provenance=[{'source': 'work.docx', 'checked': True, 'limitations': [], 'signals': [{
                'location': 'docProps/app.xml', 'basis': 'metadata', 'strength': 'medium',
                'observation': 'Застосунок документа згадує Gamma.'}]}])
        self.assertEqual(result['ai_authorship_state'], 'suspected')
        self.assertEqual(result['ai_generated_confidence'], 'medium')
        self.assertIn('Це ознаки, а не доведений факт', result['ai_generated_details'])
        self.assertEqual(result['ai_provenance_report']['signals'][0]['strength'], 'medium')

    def test_c2pa_signal_is_high_confidence(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'none'}}, False,
            provenance=[{'source': 'photo.png', 'checked': True, 'limitations': [], 'signals': [{
                'location': 'C2PA маніфест', 'basis': 'c2pa', 'strength': 'strong',
                'observation': 'Валідний маніфест вказує на генеративне створення.'}]}])
        self.assertEqual(result['ai_authorship_state'], 'suspected')
        self.assertEqual(result['ai_generated_confidence'], 'high')

    def test_verified_quote_is_kept(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'signs', 'evidence': [{
                'source': 'work.txt', 'location': 'абзац 2', 'basis': 'text_style',
                'observation': 'Фраза «Це речення справді є в роботі» виглядає шаблонно.'}]}},
            False, student_text='Тут є це речення справді є в роботі у тексті.',
            file_names=['work.txt'])
        self.assertTrue(result['ai_provenance_report'])

    def test_hallucinated_quote_is_dropped(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'signs', 'evidence': [{
                'source': 'work.txt', 'location': 'абзац 2', 'basis': 'text_style',
                'observation': 'Фраза «Цього речення немає в роботі взагалі» виглядає шаблонно.'}]}},
            False, student_text='Зовсім інший текст.', file_names=['work.txt'])
        self.assertTrue(result['ai_provenance_report']['signals'])
        self.assertFalse(any(s.get('verified') for s in result['ai_provenance_report']['signals']))
        self.assertEqual(result['ai_authorship_state'], 'insufficient')

    def test_missing_slide_number_is_rejected(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'signs', 'evidence': [{
                'source': 'deck.pptx', 'location': 'Слайд 99', 'basis': 'slide_content',
                'observation': 'Слайд містить «текст, який справді є в роботі».'}]}},
            False, student_text='текст, який справді є в роботі', file_names=['deck.pptx'],
            slide_count=3)
        self.assertFalse(any(s.get('verified') for s in result['ai_provenance_report']['signals']))

    def test_existing_slide_number_is_accepted(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'signs', 'evidence': [{
                'source': 'deck.pptx', 'location': 'Слайд 2', 'basis': 'slide_content',
                'observation': 'Слайд містить «текст, який справді є в роботі».'}]}},
            False, student_text='текст, який справді є в роботі', file_names=['deck.pptx'],
            slide_count=3)
        self.assertTrue(any(s.get('verified') for s in result['ai_provenance_report']['signals']))

    def test_percent_below_tolerance_without_evidence_does_not_accuse(self):
        result = normalize_authorship({'ai_generated_percent': 10}, False)
        self.assertFalse(result['ai_generated_detected'])
        self.assertEqual(result['ai_authorship_state'], 'insufficient')


class OfficeMetadataTests(SimpleTestCase):
    def test_gamma_application_is_detected_but_marked_editable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'work.docx'
            with zipfile.ZipFile(path, 'w') as package:
                package.writestr('docProps/app.xml',
                                 '<Properties><Application>Gamma</Application></Properties>')
                package.writestr('docProps/core.xml',
                                 '<cp:coreProperties><dc:creator>Учень</dc:creator>'
                                 '<cp:lastModifiedBy>Інший</cp:lastModifiedBy></cp:coreProperties>')
                package.writestr('word/document.xml', '<w:document></w:document>')
            report = file_provenance(path)
            self.assertTrue(report['checked'])
            self.assertTrue(any('Gamma' in s['observation'] for s in report['signals']))
            self.assertTrue(any(s.get('strength') == 'medium' for s in report['signals']))


from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Assignment, ClassGroup, School, Submission, Teacher


class TeacherAuthorshipLabelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('label_teacher', password='test-password', is_superuser=True)
        School.objects.create(name='Школа міток', admin=self.user)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Міток')
        self.group = ClassGroup.objects.create(name='9-М', grade=9)
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, title='Мітка', description='Завдання.', status='published')
        self.assignment.classes.add(self.group)
        self.submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='У', last_name='Мітка')
        self.client.force_login(self.user)

    def test_teacher_can_set_label(self):
        response = self.client.post(
            reverse('submission_authorship_label', args=[self.submission.pk]),
            {'label': 'ai', 'note': 'є слід C2PA'})
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.ai_authorship_teacher_label, 'ai')
        self.assertEqual(self.submission.ai_authorship_teacher_note, 'є слід C2PA')
        self.assertIsNotNone(self.submission.ai_authorship_labeled_at)

    def test_label_returns_to_viewer(self):
        response = self.client.post(
            reverse('submission_authorship_label', args=[self.submission.pk]),
            {'label': 'not_ai', 'next': '/teacher/view-file/%s/' % self.submission.pk})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/teacher/view-file/'))

    def test_invalid_label_is_rejected(self):
        response = self.client.post(
            reverse('submission_authorship_label', args=[self.submission.pk]), {'label': 'yes'})
        self.assertEqual(response.status_code, 400)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.ai_authorship_teacher_label, 'unknown')

    def test_other_teacher_cannot_label(self):
        other_user = get_user_model().objects.create_user('other_teacher', password='test-password')
        Teacher.objects.create(user=other_user, full_name='Інший Вчитель')
        self.client.force_login(other_user)
        response = self.client.post(
            reverse('submission_authorship_label', args=[self.submission.pk]), {'label': 'ai'})
        self.assertEqual(response.status_code, 403)
