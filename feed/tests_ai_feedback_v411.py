"""AI feedback boundaries and persisted choices, using only synthetic work."""
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .ai_context import complete_result_groups, feedback_evidence_sections, strip_teacher_criteria
from .ai_jobs import job_response
from .ai_provenance import file_provenance, normalize_authorship
from .models import AIJob, AISettings
from . import tests_ai_assessment_v4 as fixtures
from .tests_release_v41 import image_bytes


class FeedbackV411Tests(TestCase):
    setUp = fixtures.AssessmentV4Tests.setUp
    evaluate = fixtures.AssessmentV4Tests.evaluate
    result = fixtures.AssessmentV4Tests.result

    def test_unknown_origin_is_explained_without_a_false_human_verdict(self):
        data = normalize_authorship({'ai_authorship_analysis': {'status': 'none'}}, False)
        self.assertFalse(data['ai_generated_detected'])
        self.assertIn('не встановлено', data['ai_generated_details'])
        self.assertIn('не підтверджує самостійність', data['ai_generated_details'])
        self.assertIn('ШІ не дозволено', data['ai_generated_details'])

    def test_open_document_metadata_and_embedded_images(self):
        root = Path(settings.MEDIA_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        for suffix in ('.odt', '.odp', '.ods'):
            path = root / ('provenance' + suffix)
            with zipfile.ZipFile(path, 'w') as package:
                package.writestr('meta.xml', '<meta><generator>OpenAI ChatGPT</generator></meta>')
                package.writestr('Pictures/generated.png', image_bytes('ComfyUI'))
            signals = file_provenance(path)['signals']
            self.assertEqual(len(signals), 2)
            self.assertEqual(signals[0]['location'], 'meta.xml/generator')
            self.assertEqual(signals[1]['location'], 'Pictures/generated.png')

    def test_libreoffice_is_not_evidence_of_ai_generation(self):
        path = Path(settings.MEDIA_ROOT) / 'ordinary.odt'
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as package:
            package.writestr('meta.xml', '<meta><generator>Collabora Office LibreOffice</generator></meta>')
        self.assertFalse(file_provenance(path)['signals'])

    def test_legacy_criteria_removed_but_reason_and_advice_preserved(self):
        text = '🎯 **Чому така оцінка:**\nЗміст правильний.\n\n📋 **Перевірка критеріїв:**\n• Службовий критерій — виконано.\n\n🛠️ **Як покращити роботу:**\n1. Додай приклад.'
        public = strip_teacher_criteria(text)
        self.assertNotIn('Службовий критерій', public)
        self.assertIn('Зміст правильний', public)
        self.assertIn('Додай приклад', public)
        self.sub.student_ai_feedback = text
        self.sub.ai_feedback = text
        self.assertEqual(self.sub.get_student_ai_evidence_sections(), [])
        self.assertNotIn('критерій', self.sub.get_clean_ai_feedback_for_student())
        self.assertIn('Службовий критерій', self.sub.get_formatted_ai_feedback())

    def test_student_check_response_and_saved_comment_exclude_criteria(self):
        from .views import _perform_student_ai_check
        response, _ = self.evaluate(self.result())
        with patch('feed.views.check_submission_duplicates', return_value={}):
            student = _perform_student_ai_check(self.sub, response)
        data = json.loads(student.content)
        self.assertNotIn('Перевірка критеріїв', data['feedback'])
        self.assertNotIn('criteria_results', data)
        self.sub.refresh_from_db()
        self.assertNotIn('Перевірка критеріїв', self.sub.student_ai_feedback)
        self.assertIn('Перевірка критеріїв', self.sub.ai_feedback)
        self.assertNotIn('Перевірка критеріїв', self.sub.get_clean_ai_feedback_for_student())
        self.assertEqual(self.sub.grade, '11')

    def test_historical_student_job_poll_is_filtered(self):
        job = AIJob.objects.create(kind='student_check', status='succeeded', submission=self.sub,
            result={'feedback': '📋 **Перевірка критеріїв:**\nПриватний розбір.\n\n🛠️ **Як покращити роботу:**\nДодай приклад.', 'criteria_results': [{'criterion': 'Приватний'}]})
        data = json.loads(job_response(job).content)
        self.assertNotIn('Приватний', data['feedback'])
        self.assertIn('Додай приклад', data['feedback'])
        self.assertNotIn('criteria_results', data)

    def test_missing_selected_groups_visible_and_unscored(self):
        self.assignment.default_ai_grs = '[]'
        self.assignment.save()
        response, _ = self.evaluate(self.result(gr_results=[{'code': 'ГР 1', 'grade': 8}]))
        self.assertEqual([g['code'] for g in response['gr_results']], ['ГР 1', 'ГР 10'])
        self.assertIsNone(response['gr_results'][1]['grade'])
        self.assertEqual(response['gr_results'][1]['status'], 'unverifiable')
        self.assertTrue(response['needs_teacher_review'])
        self.assertEqual(response['suggested_grade'], '8')

    def test_group_normalization_excludes_unselected_and_duplicate_marks(self):
        output = complete_result_groups([{'code': 'ГР 1', 'grade': 8}, {'code': 'ГР1', 'grade': 1}, {'code': 'ГР 10', 'grade': 12}], [{'code': 'ГР 1', 'name': 'Зміст'}])
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['grade'], '8')

    def test_viewer_remembers_last_successful_choice_and_class_defaults(self):
        self.assignment.default_ai_grs = '[]'
        self.assignment.save()
        AIJob.objects.create(kind='teacher_check', status='succeeded', submission=self.sub,
            requested_by=self.user, finished_at=timezone.now(),
            parameters={'preset_id': str(self.preset.pk), 'selected_gr_codes': ['ГР 1']})
        self.client.force_login(self.user)
        page = self.client.get(reverse('view_file', args=[self.sub.pk]))
        self.assertEqual(page.context['viewer_gr_codes'], ['ГР 1'])
        self.assertEqual(page.context['default_gr_codes'], [])
        self.assertContains(page, 'viewer-selected-grs')
        self.assertContains(page, 'Повернути ГР завдання')

    def test_invalid_or_empty_selection_does_not_enqueue_a_check(self):
        self.client.force_login(self.user)
        with patch('feed.ai_jobs.enqueue_submission_job') as enqueue:
            for codes in ([], ['ГР 100'], {'ГР 1': True}):
                response = self.client.post(reverse('ai_check_single_submission', args=[self.sub.pk]),
                    {'preset_id': self.preset.pk, 'selected_gr_codes': json.dumps(codes)})
                self.assertEqual(response.status_code, 400)
            enqueue.assert_not_called()

    def test_zero_temperature_is_sent_as_zero(self):
        config = AISettings.get_solo()
        config.temperature = 0
        config.save()
        _, call = self.evaluate(self.result())
        self.assertEqual(call.call_args.kwargs['temperature'], 0.0)

    def test_viewer_preserves_explicit_preset_override(self):
        AIJob.objects.create(kind='teacher_check', status='succeeded', submission=self.sub,
            requested_by=self.user, finished_at=timezone.now(),
            parameters={'preset_id': str(self.traditional.pk), 'selected_gr_codes': None})
        self.client.force_login(self.user)
        page = self.client.get(reverse('view_file', args=[self.sub.pk]))
        self.assertEqual(page.context['viewer_preset'], self.traditional)
        self.assertEqual(page.context['default_preset'], self.preset)

    def test_thinking_uses_global_default_but_preserves_explicit_false(self):
        config = AISettings.get_solo()
        config.default_thinking_mode = True
        config.save()
        self.client.force_login(self.user)
        url = reverse('view_file', args=[self.sub.pk])
        self.assertTrue(self.client.get(url).context['viewer_thinking_mode'])
        AIJob.objects.create(kind='teacher_check', status='succeeded', submission=self.sub,
            requested_by=self.user, finished_at=timezone.now(), parameters={'force_thinking': False})
        self.assertFalse(self.client.get(url).context['viewer_thinking_mode'])

    def test_group_without_evidence_does_not_gain_a_model_mark(self):
        output = complete_result_groups([{'code': 'ГР 1', 'grade': 12, 'status': 'unverifiable',
            'comment': 'Процес не спостерігався.'}], [{'code': 'ГР 1', 'name': 'Зміст'}])
        self.assertIsNone(output[0]['grade'])
        self.assertEqual(output[0]['comment'], 'Процес не спостерігався.')

    def test_model_cannot_praise_unverified_authorship(self):
        response, call = self.evaluate(self.result(strengths=['Самостійність та відповідальний підхід.', 'Правильні відомості.']))
        self.assertEqual(response['strengths'], ['Правильні відомості.'])
        self.assertIn('загальний алгоритм', call.call_args.kwargs['system_prompt'])
        self.assertNotIn('Перевірка критеріїв', response['clean_feedback'])
        self.assertIn('Перевірка критеріїв', response['feedback'])
