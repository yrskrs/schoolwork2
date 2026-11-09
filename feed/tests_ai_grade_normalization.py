from django.test import SimpleTestCase

from .gemini_service import normalize_model_grade


class GradeNormalizationTests(SimpleTestCase):
    def test_rework_synonyms_are_not_validation_errors(self):
        for value in ('Потребує доопрацювання', 'потрібно доопрацювати',
                      'роботу не зараховано', 'не зараховано'):
            self.assertEqual(normalize_model_grade(value), 'Доопрацювати')

    def test_zero_is_rework_not_a_crash(self):
        self.assertEqual(normalize_model_grade('0'), 'Доопрацювати')
        self.assertEqual(normalize_model_grade(0), 'Доопрацювати')

    def test_numeric_variants_are_extracted(self):
        self.assertEqual(normalize_model_grade('10 з 12'), '10')
        self.assertEqual(normalize_model_grade('3,5'), '3.5')
        self.assertEqual(normalize_model_grade('9'), '9')

    def test_plain_rework_and_empty_are_preserved(self):
        self.assertEqual(normalize_model_grade('Доопрацювати'), 'Доопрацювати')
        self.assertEqual(normalize_model_grade(''), '')
