"""Deterministic assessment policy: statuses, partial grading and seed/temperature."""
import json
from unittest.mock import patch

from django.test import TestCase

from .models import AICriteriaPreset
from . import tests_ai_assessment_v4 as fixtures


class AssessmentStabilityPolicyTests(TestCase):
    setUp = fixtures.AssessmentV4Tests.setUp
    evaluate = fixtures.AssessmentV4Tests.evaluate
    result = fixtures.AssessmentV4Tests.result

    def four_gr_preset(self):
        preset = AICriteriaPreset.objects.create(
            name='Чотири ГР', evaluation_type='nus_gr',
            gr_definitions=json.dumps([
                {'code': 'ГР 1', 'name': 'Зміст'},
                {'code': 'ГР 2', 'name': 'Практика'},
                {'code': 'ГР 3', 'name': 'Формат'},
                {'code': 'ГР 4', 'name': 'Висновок'},
            ]),
        )
        self.assignment.default_ai_preset = preset
        self.assignment.default_ai_grs = json.dumps(['ГР 1', 'ГР 2', 'ГР 3', 'ГР 4'])
        self.assignment.save()
        return preset

    def test_three_of_four_verified_grs_produce_partial_grade(self):
        self.four_gr_preset()
        result = self.result(
            assessment_blocked=True, suggested_grade=None,
            gr_results=[
                {'code': 'ГР 1', 'name': 'Зміст', 'grade': 8, 'status': 'assessed'},
                {'code': 'ГР 2', 'name': 'Практика', 'grade': 8, 'status': 'assessed'},
                {'code': 'ГР 3', 'name': 'Формат', 'grade': 8, 'status': 'assessed'},
                {'code': 'ГР 4', 'name': 'Висновок', 'grade': None, 'status': 'unverifiable',
                 'comment': 'Скріншот не читається.'},
            ])
        response, _ = self.evaluate(result)
        self.assertEqual(response['status'], 'success')
        self.assertFalse(response['assessment_blocked'])
        self.assertTrue(response['partial_assessment'])
        self.assertEqual(response['suggested_grade'], '8')
        self.assertTrue(response['needs_teacher_review'])
        self.assertIn('Оцінено частково', response['format_warning'])
        self.assertIn('ГР 4', ' '.join(response['unverified_reasons']))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.ai_suggested_grade, '8')

    def test_majority_unverifiable_grs_block_numeric_grade(self):
        self.four_gr_preset()
        result = self.result(
            assessment_blocked=True, suggested_grade=None,
            gr_results=[
                {'code': 'ГР 1', 'name': 'Зміст', 'grade': 8, 'status': 'assessed'},
                {'code': 'ГР 2', 'name': 'Практика', 'grade': None, 'status': 'unverifiable',
                 'comment': 'Файл пошкоджено.'},
                {'code': 'ГР 3', 'name': 'Формат', 'grade': None, 'status': 'unverifiable',
                 'comment': 'Скріншот розмитий.'},
                {'code': 'ГР 4', 'name': 'Висновок', 'grade': None, 'status': 'unverifiable',
                 'comment': 'Сторінку не прочитано.'},
            ])
        response, _ = self.evaluate(result)
        self.assertEqual(response['status'], 'failed')
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.ai_suggested_grade)

    def test_completed_without_evidence_is_not_full_credit(self):
        result = self.result()
        result['criteria_results'] = [
            {'criterion': 'Зміст', 'status': 'completed', 'evidence': '', 'recommendation': ''},
            {'criterion': 'Формат', 'status': 'partial', 'evidence': 'Подано слайди.', 'recommendation': ''},
        ]
        response, _ = self.evaluate(result)
        self.assertEqual(response['status'], 'success')
        content = next(row for row in response['criteria_results'] if row['criterion'] == 'Зміст')
        self.assertEqual(content['status'], 'partial')
        self.assertIn('не навела конкретного доказу', content['evidence'])

    def test_evaluation_uses_zero_temperature_and_fixed_seed(self):
        self.sub.assignment.default_ai_preset = self.traditional
        self.sub.assignment.save()
        config = self.sub.assignment.default_ai_preset
        self.assertIsNotNone(config)
        from .models import AISettings
        settings = AISettings.get_solo()
        settings.temperature = 0.2
        settings.save()
        _, call = self.evaluate(self.result())
        self.assertEqual(call.call_args.kwargs['temperature'], 0.0)
        self.assertEqual(call.call_args.kwargs['seed'], 42)
        system = call.call_args.kwargs['system_prompt']
        self.assertIn('not_completed', system)
        self.assertIn('evidence', system)
        self.assertIn('ЛИШЕ', system)
        self.assertIn('половини', system)

    def test_model_blocked_but_verified_criteria_can_be_partial(self):
        result = self.result(
            assessment_blocked=True,
            criteria_results=[
                {'criterion': 'Зміст', 'status': 'completed', 'evidence': 'Цитата з роботи.', 'recommendation': ''},
                {'criterion': 'Формат', 'status': 'partial', 'evidence': 'Слайд 2.', 'recommendation': ''},
            ],
        )
        response, _ = self.evaluate(result)
        self.assertEqual(response['status'], 'success')
        self.assertFalse(response['assessment_blocked'])
