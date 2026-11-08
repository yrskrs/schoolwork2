"""Regressions for task-number parsing, completion caps and false format warnings."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from .ai_context import clear_false_format_warning, downgrade_unsupported_task_claims
from .gemini_service import parse_teacher_specific_task_numbers


class TaskNumberParsingTests(SimpleTestCase):
    def test_points_are_not_task_numbers(self):
        text = ('Виконання завдання. Кожне виконане завдання 4 бали. '
                'Виконані завдання здавати один файлом.')
        self.assertEqual(parse_teacher_specific_task_numbers(text), [])

    def test_real_task_number_and_points(self):
        self.assertEqual(parse_teacher_specific_task_numbers('Виконати завдання 1 (4 бали).'), [1])
        self.assertEqual(parse_teacher_specific_task_numbers('Виконати завдання 4.'), [4])
        self.assertEqual(parse_teacher_specific_task_numbers('Завдання 1 та 2.'), [1, 2])


class ClaimedEvidenceTests(SimpleTestCase):
    def test_rank_claim_without_rank_formula_is_downgraded(self):
        result = {
            'tasks_evaluated': [{'task_num': 4, 'task_title': 'Результати олімпіади',
                                 'status': 'completed', 'comment': 'Застосовано формулу ранжування.'}],
            'criteria_results': [{'criterion': 'Правильність використання функції RANK',
                                  'status': 'completed', 'evidence': 'Аркуш1'}],
            'tasks_completed_count': 1,
        }
        text = ('Аркуш1: A1 Номер партії, E2 =C2-D2. Аркуш2 порожній. Аркуш3 порожній.')
        downgrade_unsupported_task_claims(result, text)
        self.assertEqual(result['tasks_evaluated'][0]['status'], 'not_completed')
        self.assertEqual(result['criteria_results'][0]['status'], 'unverifiable')
        self.assertEqual(result['tasks_completed_count'], 0)

    def test_rank_formula_is_accepted(self):
        result = {
            'tasks_evaluated': [{'task_num': 4, 'task_title': 'Результати олімпіади',
                                 'status': 'completed', 'comment': 'Застосовано RANK.'}],
            'tasks_completed_count': 1,
        }
        downgrade_unsupported_task_claims(result, 'Аркуш1: F2 =RANK(B2,$B$2:$B$10,0)')
        self.assertEqual(result['tasks_evaluated'][0]['status'], 'completed')


class FormatWarningTests(SimpleTestCase):
    def submission(self, name):
        return SimpleNamespace(get_files=lambda: [SimpleNamespace(
            original_name=name, file=SimpleNamespace(name=name))])

    def test_xlsx_warning_is_cleared(self):
        result = {'format_warning': 'Файл здано без належного розширення або не у тому форматі.'}
        clear_false_format_warning(result, self.submission('книга.xlsx'))
        self.assertEqual(result['format_warning'], '')

    def test_missing_extension_warning_is_kept(self):
        result = {'format_warning': 'Файл здано без розширення (має бути .xlsx).'}
        clear_false_format_warning(result, self.submission('книга'))
        self.assertNotEqual(result['format_warning'], '')
