"""Synthetic provenance, student privacy and lightweight-response regression checks."""
import gzip
import io
import zipfile
from unittest.mock import patch

from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from PIL import Image, PngImagePlugin

from .ai_provenance import file_provenance, normalize_authorship
from .middleware import PageCompressionMiddleware
from . import tests_ai_assessment_v4 as fixtures


def image_bytes(generator=None):
    stream = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    if generator:
        metadata.add_text('Software', generator)
    Image.new('RGB', (20, 20), 'green').save(stream, 'PNG', pnginfo=metadata)
    return stream.getvalue()


class ProvenanceTests(TestCase):
    setUp = fixtures.AssessmentV4Tests.setUp
    evaluate = fixtures.AssessmentV4Tests.evaluate
    result = fixtures.AssessmentV4Tests.result
    def test_uncalibrated_percent_and_empty_claim_are_not_proof(self):
        parsed = normalize_authorship({'ai_generated_detected': True, 'ai_generated_percent': 100}, False)
        self.assertFalse(parsed['ai_generated_detected'])
        self.assertIsNone(parsed['ai_generated_percent'])
        negative = normalize_authorship({'ai_generated_detected': False, 'ai_generated_details': 'Ознак ШІ не знайдено.'}, False)
        self.assertFalse(negative['ai_generated_detected'])

    def test_style_is_low_confidence_and_does_not_invent_penalty(self):
        result = {'ai_authorship_analysis': {'evidence': [{'basis': 'text_style', 'observation': 'Типовий вступ.'}]}}
        for allowed in (False, True):
            parsed = normalize_authorship(result, allowed)
            self.assertEqual(parsed['ai_generated_confidence'], 'low')
            self.assertIn('не доведений факт', parsed['ai_generated_details'])
            self.assertIsNone(parsed['ai_generated_percent'])

    def test_metadata_found_in_image_and_office_image_before_optimization(self):
        from pathlib import Path
        from django.conf import settings
        image = Path(settings.MEDIA_ROOT) / 'synthetic-generated.png'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(image_bytes('ComfyUI'))
        signals = file_provenance(image)
        self.assertEqual(signals['signals'][0]['basis'], 'metadata')
        office = image.with_suffix('.pptx')
        with zipfile.ZipFile(office, 'w') as package:
            package.writestr('ppt/media/image1.png', image.read_bytes())
        self.assertEqual(file_provenance(office)['signals'][0]['location'], 'ppt/media/image1.png')
        scratch = image.with_suffix('.sb3')
        with zipfile.ZipFile(scratch, 'w') as package:
            package.writestr('costume.png', image.read_bytes())
        self.assertTrue(file_provenance(scratch)['signals'])

    def test_no_metadata_does_not_prove_human_authorship(self):
        from pathlib import Path
        from django.conf import settings
        image = Path(settings.MEDIA_ROOT) / 'ordinary.png'; image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(image_bytes())
        result = file_provenance(image)
        self.assertEqual(result['signals'], [])
        self.assertIn('не доводить', result['limitations'][0])

    def test_allowed_and_banned_policy_sent_to_model_with_concrete_evidence(self):
        for allowed in (False, True):
            self.assignment.allow_ai_usage = allowed; self.assignment.save()
            result, call = self.evaluate(self.result(ai_authorship_analysis={'evidence': [
                {'source': 'Робота', 'basis': 'self_disclosure', 'observation': 'Учень заявив про використання ШІ.'}]}))
            self.assertTrue(result['ai_generated_detected'])
            self.assertEqual(result['suggested_grade'], '8')
            self.assertIn('ai_usage_allowed', call.call_args.kwargs['prompt_text'])
            self.assertIn('Не вигадуй автоматичний штраф', call.call_args.kwargs['system_prompt'])

    def test_student_receives_details_but_not_teacher_grade(self):
        from .views import _perform_student_ai_check
        self.sub.grade = '12'; self.sub.save()
        result = self.result(status='success', ai_generated_detected=True, ai_generated_details='Файл: ознаки генерації.', ai_generated_confidence='low')
        with patch('feed.views.check_submission_duplicates', return_value={}):
            response = _perform_student_ai_check(self.sub, result)
        import json
        data = json.loads(response.content)
        self.assertEqual(data['ai_generated_details'], 'Файл: ознаки генерації.')
        self.assertNotEqual(data['grade'], '12')
        self.assertNotIn('teacher_grade', data)
        self.sub.refresh_from_db(); self.assertEqual(self.sub.grade, '12')


class PageCompressionTests(SimpleTestCase):
    def test_only_html_compressed_and_streamed_files_untouched(self):
        request = RequestFactory().get('/', HTTP_ACCEPT_ENCODING='gzip')
        middleware = PageCompressionMiddleware(lambda r: None)
        html = b'<p>SchoolNet page content</p>' * 300
        response = middleware.process_response(request, HttpResponse(html))
        self.assertEqual(response['Content-Encoding'], 'gzip')
        self.assertEqual(gzip.decompress(response.content), html)
        archive = HttpResponse(html, content_type='application/octet-stream')
        self.assertNotIn('Content-Encoding', middleware.process_response(request, archive))
        stream = StreamingHttpResponse(iter([html]), content_type='text/html')
        self.assertNotIn('Content-Encoding', middleware.process_response(request, stream))
