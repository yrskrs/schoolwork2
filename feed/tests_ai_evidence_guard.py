"""Deterministic tests for the post-processing technical-evidence guard (Рівень 3).

Unit tests exercise ``apply_technical_evidence_guard`` directly with synthetic
model JSON and server technical passports. One integration test runs the real
``evaluate_submission_with_gemini`` with a mocked provider to prove that the
precedent №617 (static .xlsx praised for an absent IF formula) is fixed and that
the grade ceiling survives the final grade calculation.

Contract v2 coverage: per-file suppression (old + corrected work), function and
construct cross-check, ``only_static_output`` only for algorithmic tasks,
``no_code_blocks`` only for script/game/animation tasks, GR comment cleaning,
``unverifiable → missing`` for proven absence, ``implementation_unverified`` for
PDF/image/CSV/archive, general 9 ceiling, clause splitting ("але/проте"),
IF-логіка normalization, teacher waiver, slide-count calibration and
idempotency.
"""

import copy
import io
import json
import os
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

from .ai_evidence_guard import TECHNICAL_GRADE_CAPS, apply_technical_evidence_guard
from .ai_context import evidence_cache
from .gemini_service import evaluate_submission_with_gemini
from .models import AICriteriaPreset, AISettings, Assignment, ClassGroup, School, Submission, Teacher


def passport(kind, facts, absence=(), file_name='work', verified=True):
    """Build the shape produced by build_file_passport (+ file)."""
    return {
        'file': file_name,
        'kind': kind,
        'extension': '',
        'verified': verified,
        'facts': facts,
        'absence': list(absence),
        'warnings': [],
        'limitations': [],
    }


def unverified_stub(kind, file_name='scan', reason='Формат не підтримує детермінований аудит'):
    """Unverified passport stub produced by the integration for blind formats."""
    return {
        'file': file_name,
        'kind': kind,
        'extension': '',
        'verified': False,
        'facts': {},
        'absence': [],
        'warnings': [],
        'limitations': [reason],
    }


def excel_without_formulas(file_name='svitlofor.xlsx'):
    return passport('excel', {
        'sheets_count': 1, 'formulas_count': 0, 'cell_formulas_count': 0,
        'conditional_formulas_count': 0, 'data_validation_formulas_count': 0,
        'defined_name_formulas_count': 0, 'functions': [],
        'non_empty_cells': 12, 'has_charts': False,
    }, ['formulas_absent'], file_name)


def excel_with_if(file_name='final.xlsx'):
    return passport('excel', {
        'sheets_count': 1, 'formulas_count': 3, 'cell_formulas_count': 3,
        'conditional_formulas_count': 0, 'data_validation_formulas_count': 0,
        'defined_name_formulas_count': 0, 'functions': ['IF'],
        'non_empty_cells': 12, 'has_charts': False,
    }, [], file_name)


MARKING_REQUIREMENT = (
    'Створити таблицю «Світлофор» з використанням логічної функції IF '
    'для маркування клітинок.')

DATA_ENTRY_REQUIREMENT = 'Введіть дані у таблицю та оформіть її.'

PYTHON_REQUIREMENT = 'Написати програму мовою Python з циклом та розгалуженням.'


class TechnicalEvidenceGuardUnitTests(SimpleTestCase):
    def guard(self, result, passports, requirement, **overrides):
        kwargs = dict(
            suggested_grade=result.get('suggested_grade', ''),
            level=result.get('level', ''),
            clean_gr_results=[],
            numeric_gr_grades=[],
            avg_gr_grade=None,
            summary=result.get('summary', ''),
            strengths=result.get('strengths', []),
            weaknesses=result.get('weaknesses', []),
            feedback_comment=result.get('feedback_comment', ''),
            teacher_instructions='',
            custom_criteria='',
        )
        kwargs.update(overrides)
        return apply_technical_evidence_guard(result, passports, requirement, **kwargs)

    def precedent_result(self):
        return {
            'suggested_grade': '8',
            'level': 'Достатній (7-9)',
            'summary': 'Учень успішно застосував логічну функцію IF для маркування клітинок.',
            'strengths': ['Правильне застосування логічної функції IF',
                          'Охайне оформлення таблиці'],
            'weaknesses': ['Відсутність формул у наданому файлі (лише статичні дані).'],
            'feedback_comment': 'Ти успішно застосував функцію IF, молодчина!',
            'grade_explanation': 'Логічну функцію IF застосовано правильно.',
            'criteria_results': [
                {'criterion': 'Використання формули IF', 'status': 'completed',
                 'evidence': 'Формулу IF застосовано', 'recommendation': ''},
                {'criterion': 'Оформлення таблиці', 'status': 'completed',
                 'evidence': 'Акуратна таблиця', 'recommendation': ''},
            ],
            'tasks_evaluated': [
                {'task_num': 1, 'task_title': 'Створити таблицю з формулами',
                 'status': 'completed', 'comment': 'Виконано'},
                {'task_num': 2, 'task_title': 'Оформити таблицю',
                 'status': 'completed', 'comment': 'Оформлено'},
            ],
            'tasks_completed_count': 2,
        }

    # ── Constants and no-false-positive guarantees ─────────────────────────

    def test_grade_caps_are_synchronized_with_the_specification(self):
        self.assertEqual(TECHNICAL_GRADE_CAPS, {
            'formulas_absent': 6,
            'syntax_error': 4,
            'only_static_output': 6,
            'all_slides_empty': 4,
            'too_few_slides': 6,
            'no_code_blocks': 4,
            'empty_document': 3,
            'short_document': 6,
            'no_code_constructs': 6,
            'function_absent': 6,
            'construct_absent': 6,
            'no_queries': 6,
            'no_relationships': 6,
            'implementation_unverified': 9,
            'too_few_slides_minor': 9,
        })

    def test_real_formula_work_is_not_modified_at_all(self):
        result = self.precedent_result()
        result.update(suggested_grade='9', level='Достатній (7-9)')
        before = copy.deepcopy(result)
        evidence = excel_with_if('ok.xlsx')
        out = self.guard(result, [evidence], MARKING_REQUIREMENT)
        self.assertFalse(out['applied'])
        self.assertEqual(out['adjustments'], [])
        self.assertEqual(result, before)  # byte-for-byte, including criteria/tasks

    def test_empty_passport_list_changes_nothing(self):
        result = self.precedent_result()
        before = copy.deepcopy(result)
        out = self.guard(result, [], MARKING_REQUIREMENT)
        self.assertFalse(out['applied'])
        self.assertEqual(result, before)
        self.assertEqual(out['summary'], result['summary'])
        self.assertEqual(out['suggested_grade'], '8')

    def test_unverified_passport_is_not_treated_as_proven_absence(self):
        """An unverified stub may only cause implementation_unverified (cap 9)."""
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        stub = unverified_stub('document', 'scan.pdf')
        out = self.guard(result, [stub], MARKING_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertNotIn('formulas_absent', out['active_flags'])
        self.assertIn('implementation_unverified', out['active_flags'])
        self.assertEqual(result['criteria_results'][0]['status'], 'unverifiable')
        self.assertNotEqual(result['criteria_results'][0]['status'], 'missing')
        self.assertEqual(out['suggested_grade'], '9')
        self.assertEqual(out['level'], 'Достатній (7-9)')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        self.assertIn('неможливо перевірити', ' '.join(out['weaknesses']))
        self.assertIn('неможливо перевірити', out['summary'])

    # ── Precedent №617 ─────────────────────────────────────────────────────

    def test_precedent_617_fabricated_if_praise_is_replaced_and_capped(self):
        result = self.precedent_result()
        out = self.guard(result, [excel_without_formulas()], MARKING_REQUIREMENT)

        self.assertTrue(out['applied'])
        self.assertEqual(out['suggested_grade'], '6')
        self.assertEqual(out['level'], 'Середній (4-6)')

        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        self.assertEqual(out['strengths'], ['Охайне оформлення таблиці'])
        self.assertEqual(
            out['summary'],
            'Таблицю створено, проте формули не використано '
            '(значення маркування введено вручну статичним текстом).')
        self.assertIn('немає жодної формули', out['grade_explanation'])
        self.assertNotIn('правильно', out['grade_explanation'].lower())
        self.assertIn('немає жодної формули', out['feedback_comment'])
        self.assertNotIn('успішно', out['feedback_comment'].lower())
        self.assertNotIn('молодчина', out['feedback_comment'].lower())

        formula_remarks = [w for w in out['weaknesses'] if 'відсутність формул' in w.lower()]
        self.assertEqual(len(formula_remarks), 1)

        criteria = result['criteria_results']
        self.assertEqual(criteria[0]['status'], 'missing')  # confirmed, not unverifiable
        self.assertTrue(criteria[0]['evidence'].startswith('Фактично:'))
        self.assertTrue(criteria[0]['recommendation'])
        self.assertEqual(criteria[1]['status'], 'completed')
        self.assertEqual(result['tasks_evaluated'][0]['status'], 'not_completed')
        self.assertEqual(result['tasks_evaluated'][1]['status'], 'completed')
        self.assertEqual(result['tasks_completed_count'], 1)

        self.assertTrue(any(
            item['field'] == 'suggested_grade' and item['before'] == '8'
            and item['after'] == '6' for item in out['adjustments']))
        self.assertEqual(result['grade_explanation'], out['grade_explanation'])
        json.dumps(result, ensure_ascii=False)  # the model JSON stays serializable

    def test_precedent_617_generic_success_summary_is_replaced_when_ceiling_applies(self):
        result = self.precedent_result()
        result['summary'] = 'Завдання 1 виконано успішно з дотриманням усіх вимог.'
        out = self.guard(result, [excel_without_formulas()], MARKING_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertEqual(
            out['summary'],
            'Таблицю створено, проте формули не використано '
            '(значення маркування введено вручну статичним текстом).')

    def test_generic_success_summary_without_requirement_becomes_neutral_factual(self):
        """No formula requirement: no strict ceiling, but a neutral factual summary."""
        result = self.precedent_result()
        result['summary'] = 'Завдання 1 виконано успішно з дотриманням усіх вимог.'
        out = self.guard(result, [excel_without_formulas()], DATA_ENTRY_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        self.assertEqual(out['summary'],
                         'У наданому файлі не виявлено заявлених формул; '
                         'оцінка спирається на фактичний вміст роботи.')
        self.assertEqual(out['suggested_grade'], '8')
        self.assertNotIn('вимогу не виконано', out['summary'])

    # ── Per-file evidence: mixed old + corrected submission ───────────────

    def test_mixed_manual_and_corrected_work_is_not_punished(self):
        """Old manual workbook + corrected workbook with IF: no ceiling at all."""
        result = self.precedent_result()
        result.update(suggested_grade='10', level='Високий (10-12)')
        before = copy.deepcopy(result)
        out = self.guard(result, [excel_without_formulas('manual.xlsx'),
                                  excel_with_if('final.xlsx')], MARKING_REQUIREMENT)
        self.assertFalse(out['applied'])
        self.assertEqual(out['adjustments'], [])
        self.assertEqual(out['suggested_grade'], '10')
        self.assertEqual(out['strengths'], result['strengths'])
        self.assertEqual(result, before)

    def test_mixed_manual_and_corrected_python_work_is_not_punished(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Реалізовано цикл for.', 'strengths': ['Реалізовано цикл for'],
            'weaknesses': [], 'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        broken = passport('python', {'syntax_ok': False, 'syntax_error': 'expected ":"'},
                          ['syntax_error'], 'old.py')
        fixed = passport('python', {'syntax_ok': True, 'loops': 2, 'functions': 1,
                                    'branches': 1}, [], 'final.py')
        out = self.guard(result, [broken, fixed], PYTHON_REQUIREMENT)
        self.assertFalse(out['applied'])
        self.assertEqual(out['suggested_grade'], '10')
        self.assertEqual(result['strengths'], ['Реалізовано цикл for'])

    # ── Function / construct cross-check ──────────────────────────────────

    def test_wrong_function_available_instead_of_claimed_is_capped(self):
        """SUM exists but the model praises IF: function_absent and cap 6."""
        result = {
            'suggested_grade': '11', 'level': 'Високий (10-12)',
            'summary': 'Учень правильно застосував логічну функцію IF.',
            'strengths': ['Правильне застосування логічної функції IF',
                          'Охайне оформлення таблиці'],
            'weaknesses': [], 'feedback_comment': 'Ти застосував функцію IF.',
            'grade_explanation': 'Функцію IF застосовано правильно.',
            'criteria_results': [
                {'criterion': 'Використання функції IF', 'status': 'completed',
                 'evidence': 'IF застосовано'}],
            'tasks_evaluated': [
                {'task_num': 1, 'task_title': 'Застосувати функцію IF',
                 'status': 'completed', 'comment': 'Виконано'}],
            'tasks_completed_count': 1,
        }
        evidence = passport('excel', {
            'sheets_count': 1, 'formulas_count': 2, 'cell_formulas_count': 2,
            'functions': ['SUM'], 'non_empty_cells': 10,
        }, [], 'work.xlsx')
        out = self.guard(result, [evidence], MARKING_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertIn('function_absent', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '6')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        self.assertIn('IF', out['summary'])  # factual mention, not praise
        self.assertIn('не підтверджено', out['summary'])
        self.assertEqual(result['criteria_results'][0]['status'], 'missing')
        self.assertEqual(result['tasks_evaluated'][0]['status'], 'not_completed')

    def test_construct_absent_for_python_loop_claim_with_zero_facts(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Реалізовано цикл for для обробки даних.',
            'strengths': ['Реалізовано цикл for', 'Гарне оформлення коду'],
            'weaknesses': [], 'feedback_comment': 'Твій цикл for працює.',
            'grade_explanation': 'Цикл реалізовано правильно.',
            'criteria_results': [
                {'criterion': 'Використання циклу', 'status': 'completed',
                 'evidence': 'Цикл застосовано'}],
            'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('python', {'syntax_ok': True, 'loops': 0, 'functions': 0,
                                       'branches': 0, 'statements': 1}, [], 'main.py')
        out = self.guard(result, [evidence], PYTHON_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertIn('construct_absent', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '6')
        self.assertNotIn('цикл', ' '.join(out['strengths']).lower())
        self.assertIn('немає заявлених конструкцій', out['summary'])
        self.assertEqual(result['criteria_results'][0]['status'], 'missing')

    # ── Format-specific blind spots ───────────────────────────────────────

    def test_python_syntax_error_removes_working_code_praise_and_caps_at_four(self):
        result = {
            'suggested_grade': '10',
            'level': 'Високий (10-12)',
            'summary': 'Код працює без помилок.',
            'strengths': ['Код працює без помилок, чудовий алгоритм',
                          'Змінні названо зрозуміло'],
            'weaknesses': [],
            'feedback_comment': 'Твій код чудово працює.',
            'grade_explanation': 'Код працює коректно.',
            'criteria_results': [{'criterion': 'Працездатний код', 'status': 'completed',
                                  'evidence': 'Код запускається'}],
            'tasks_evaluated': [{'task_num': 1, 'task_title': 'Запустити програму',
                                 'status': 'completed'}],
            'tasks_completed_count': 1,
        }
        evidence = passport('python', {'syntax_ok': False, 'syntax_error': 'expected ":"'},
                            ['syntax_error'], 'main.py')
        out = self.guard(result, [evidence], PYTHON_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertEqual(out['suggested_grade'], '4')
        self.assertEqual(out['level'], 'Середній (4-6)')
        self.assertEqual(out['strengths'], ['Змінні названо зрозуміло'])
        self.assertIn('синтаксичну помилку', out['summary'])
        self.assertIn('синтаксичну помилку', ' '.join(out['weaknesses']))
        self.assertEqual(result['criteria_results'][0]['status'], 'missing')
        self.assertEqual(result['tasks_evaluated'][0]['status'], 'not_completed')

    def test_print_only_task_is_not_punished_by_only_static_output(self):
        """Typical 5-7 grade task: print the name — a correct answer, no ceiling."""
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Програма правильно виводить ім’я.', 'strengths': [
                'Правильний вивід імені', 'Охайний код'],
            'weaknesses': [], 'feedback_comment': 'Молодець, програма працює.',
            'grade_explanation': 'Вивід імені реалізовано правильно.',
            'criteria_results': [{'criterion': 'Вивід імені', 'status': 'completed'}],
            'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('python', {'syntax_ok': True, 'loops': 0, 'functions': 0,
                                       'branches': 0, 'statements': 1,
                                       'only_static_output': True},
                            ['only_static_output'], 'name.py')
        before = copy.deepcopy(result)
        out = self.guard(result, [evidence],
                         'Напиши програму, яка виводить твоє ім’я на екран')
        self.assertFalse(out['applied'])
        self.assertEqual(out['suggested_grade'], '10')
        self.assertEqual(result, before)

    def test_only_static_output_punished_for_algorithmic_requirement(self):
        result = {
            'suggested_grade': '9', 'level': 'Достатній (7-9)',
            'summary': 'Реалізовано повноцінний алгоритм.', 'strengths': [
                'Реалізовано повноцінний алгоритм', 'Гарне оформлення'],
            'weaknesses': [], 'feedback_comment': 'Код реалізує повноцінний алгоритм.',
            'grade_explanation': 'Алгоритм реалізовано повноцінно.',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('python', {'syntax_ok': True, 'loops': 0, 'functions': 0,
                                       'branches': 0}, ['only_static_output'], 'main.py')
        out = self.guard(result, [evidence],
                         'Написати програму-калькулятор з циклом')
        self.assertTrue(out['applied'])
        self.assertEqual(out['suggested_grade'], '6')
        self.assertEqual(out['strengths'], ['Гарне оформлення'])
        self.assertIn('статичний', out['summary'])

    def test_pptx_all_slides_empty_caps_at_four(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Презентація повністю розкриває тему.',
            'strengths': ['Повне розкриття теми на слайдах', 'Гарний дизайн'],
            'weaknesses': [], 'feedback_comment': 'Презентація повністю розкриває тему.',
            'grade_explanation': 'Тему розкрито повністю.',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('presentation', {'slides_count': 6, 'empty_slides': 6},
                            ['all_slides_empty'], 'work.pptx')
        out = self.guard(result, [evidence], 'Створити презентацію про безпеку')
        self.assertEqual(out['suggested_grade'], '4')
        self.assertEqual(out['strengths'], ['Гарний дизайн'])
        self.assertIn('порожні', out['summary'])

    def test_pptx_two_slides_instead_of_eight_caps_at_six(self):
        result = {
            'suggested_grade': '9', 'level': 'Достатній (7-9)',
            'summary': 'Презентація повністю розкриває тему.',
            'strengths': ['Повне розкриття теми у 8 слайдах', 'Гарний дизайн'],
            'weaknesses': [], 'feedback_comment': '', 'grade_explanation': 'Тему розкрито.',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('presentation', {'slides_count': 2, 'empty_slides': 0},
                            [], 'work.pptx')
        out = self.guard(result, [evidence], 'Створити презентацію з 8 слайдів про безпеку')
        self.assertEqual(out['suggested_grade'], '6')
        self.assertEqual(out['strengths'], ['Гарний дизайн'])
        self.assertIn('8', ' '.join(out['weaknesses']))

    def test_pptx_seven_slides_of_expected_eight_caps_at_nine(self):
        """7/8 slides is a minor gap: cap 9 (not "Високий"), not 6."""
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Презентація повністю розкриває тему.',
            'strengths': ['Повне розкриття теми у 8 слайдах'], 'weaknesses': [],
            'feedback_comment': '', 'grade_explanation': 'Тему розкрито.',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('presentation', {'slides_count': 7, 'empty_slides': 0},
                            [], 'work.pptx')
        out = self.guard(result, [evidence], 'Створити презентацію з 8 слайдів')
        self.assertTrue(out['applied'])
        self.assertIn('too_few_slides', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '9')
        self.assertEqual(out['level'], 'Достатній (7-9)')

    def test_pptx_expected_slide_count_parsed_from_words(self):
        """«вісім слайдів» must be parsed as a number (8), not ignored."""
        result = {
            'suggested_grade': '9', 'level': 'Достатній (7-9)',
            'summary': 'Презентація створена.', 'strengths': [],
            'weaknesses': [], 'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('presentation', {'slides_count': 1, 'empty_slides': 0},
                            [], 'work.pptx')
        out = self.guard(result, [evidence],
                         'Створити презентацію з восьми слайдів про безпеку')
        self.assertEqual(out['suggested_grade'], '6')
        self.assertIn('8', ' '.join(out['weaknesses']))

    def test_pptx_two_slides_without_number_requirement_is_not_punished(self):
        result = {
            'suggested_grade': '9', 'level': 'Достатній (7-9)',
            'summary': 'Презентація створена.', 'strengths': ['Повне розкриття теми'],
            'weaknesses': [], 'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('presentation', {'slides_count': 2, 'empty_slides': 0},
                            [], 'work.pptx')
        out = self.guard(result, [evidence], 'Створити презентацію про безпеку')
        self.assertFalse(out['applied'])
        self.assertEqual(out['suggested_grade'], '9')

    def test_scratch_without_code_blocks_caps_at_four(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Гра працює.', 'strengths': ['Працююча гра з анімацією'],
            'weaknesses': [], 'feedback_comment': 'Гра працює, анімація цікава.',
            'grade_explanation': 'Гру реалізовано.', 'criteria_results': [],
            'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('scratch', {'sprites_count': 3, 'total_code_blocks': 0,
                                        'scripts': 0}, ['no_code_blocks'], 'game.sb3')
        out = self.guard(result, [evidence], 'Створити гру у Scratch')
        self.assertEqual(out['suggested_grade'], '4')
        self.assertEqual(out['strengths'], [])
        self.assertIn('жодного блоку коду', out['summary'])

    def test_scratch_art_task_is_not_punished_for_no_code_blocks(self):
        """«Create costumes for the sprites» is not a scripting task."""
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Спрайти мають охайні костюми.', 'strengths': [
                'Охайні костюми спрайтів', 'Гарний дизайн'],
            'weaknesses': [], 'feedback_comment': 'Костюми спрайтів охайні.',
            'grade_explanation': 'Костюми створено охайно.',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('scratch', {'sprites_count': 3, 'total_code_blocks': 0,
                                        'scripts': 0}, ['no_code_blocks'], 'art.sb3')
        out = self.guard(result, [evidence], 'Створити костюми для спрайтів')
        # The flag is not ignored (the factual remark is added), but it must not
        # cap the grade and must not remove praise about costumes.
        self.assertEqual(out['suggested_grade'], '10')
        self.assertEqual(out['level'], 'Високий (10-12)')
        self.assertEqual(result['strengths'],
                         ['Охайні костюми спрайтів', 'Гарний дизайн'])
        self.assertFalse(any(
            item['field'] == 'suggested_grade' for item in out['adjustments']))

    def test_empty_document_caps_at_three(self):
        result = {
            'suggested_grade': '9', 'level': 'Достатній (7-9)',
            'summary': 'Глибокий твір із повним розкриттям теми.',
            'strengths': ['Глибокий твір'], 'weaknesses': [],
            'feedback_comment': 'Глибокий твір.', 'grade_explanation': 'Твір глибокий.',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('document', {'words_count': 0}, ['empty_document'], 'work.docx')
        out = self.guard(result, [evidence], 'Написати твір на тему «Осінь»')
        self.assertEqual(out['suggested_grade'], '3')
        self.assertEqual(out['level'], 'Початковий (1-3)')
        self.assertEqual(out['strengths'], [])
        self.assertIn('не містить змістовного тексту', out['summary'])

    def test_short_essay_caps_at_six(self):
        result = {
            'suggested_grade': '9', 'level': 'Достатній (7-9)',
            'summary': 'Глибокий твір.', 'strengths': ['Глибокий твір'],
            'weaknesses': [], 'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('document', {'words_count': 25}, [], 'work.docx')
        out = self.guard(result, [evidence], 'Написати есе на тему «Осінь»')
        self.assertEqual(out['suggested_grade'], '6')
        self.assertIn('менше 60', ' '.join(out['weaknesses']))

    def test_short_essay_between_40_and_59_words_is_capped_at_six(self):
        """The contract is <60 words for an essay, not only <40."""
        result = {
            'suggested_grade': '9', 'level': 'Достатній (7-9)',
            'summary': 'Глибокий твір.', 'strengths': ['Глибокий твір'],
            'weaknesses': [], 'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('document', {'words_count': 45, 'paragraphs': 2,
                                         'tables': 0, 'images': 0, 'charts': 0},
                            [], 'essay.docx')
        out = self.guard(result, [evidence], 'Написати есе на тему «Осінь»')
        self.assertTrue(out['applied'])
        self.assertIn('short_document', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '6')
        self.assertEqual(out['level'], 'Середній (4-6)')
        self.assertIn('менше 60', ' '.join(out['weaknesses']))
        self.assertEqual(out['strengths'], [])

    def test_short_data_analysis_document_is_not_treated_as_an_essay(self):
        """Regression: «Аналіз даних» (7 words) is not a short written composition."""
        result = {
            'suggested_grade': '8', 'level': 'Достатній (7-9)',
            'summary': 'Розраховано статистичні показники та наведено результати аналізу.',
            'strengths': ['Наведено результати аналізу'], 'weaknesses': ['Одна неточність.', 'Друга неточність.'],
            'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        before = copy.deepcopy(result)
        evidence = passport('document', {'words_count': 7, 'paragraphs': 1,
                                         'tables': 0, 'images': 0, 'charts': 0},
                            [], 'analysis.docx')
        out = self.guard(result, [evidence],
                         'Аналіз даних. Виконати аналіз даних за інструкцією.')
        self.assertFalse(out['applied'])
        self.assertEqual(out['adjustments'], [])
        self.assertEqual(out['suggested_grade'], '8')
        self.assertEqual(result, before)

    def test_short_document_with_tables_is_not_a_volume_violation(self):
        result = {
            'suggested_grade': '8', 'level': 'Достатній (7-9)',
            'summary': 'Роботу виконано.', 'strengths': [], 'weaknesses': [],
            'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('document', {'words_count': 15, 'paragraphs': 1,
                                         'tables': 2, 'images': 1, 'charts': 0},
                            [], 'report.docx')
        out = self.guard(result, [evidence], 'Написати есе з таблицею результатів.')
        self.assertFalse(out['applied'])
        self.assertEqual(out['suggested_grade'], '8')

    # ── Blind formats: implementation_unverified ──────────────────────────

    def test_implementation_unverified_for_pdf_image_csv_archive(self):
        """A blind format proves nothing: unverifiable criteria + cap 9, no block."""
        for kind, file_name in (
                ('document', 'export.pdf'), ('image', 'screenshot.png'),
                ('text_table', 'did.csv'), ('archive', 'work.zip')):
            result = self.precedent_result()
            result.update(suggested_grade='11', level='Високий (10-12)')
            result['criteria_results'] = [
                {'criterion': 'Використання формули IF', 'status': 'completed',
                 'evidence': 'IF застосовано'}]
            out = self.guard(result, [unverified_stub(kind, file_name)],
                             MARKING_REQUIREMENT)
            with self.subTest(kind=kind):
                self.assertTrue(out['applied'])
                self.assertEqual(out['active_flags'], ['implementation_unverified'])
                self.assertEqual(out['suggested_grade'], '9')
                self.assertEqual(out['level'], 'Достатній (7-9)')
                self.assertEqual(result['criteria_results'][0]['status'], 'unverifiable')
                self.assertIn('неможливо перевірити', result['criteria_results'][0]['evidence'])
                self.assertNotIn('IF', ' '.join(out['strengths']).upper())
                self.assertIn('неможливо перевірити', out['summary'])

    def test_verified_python_does_not_close_a_formula_gap(self):
        """P1-3: a verified .py must not prove an .xlsx formula requirement.

        Only a verified passport of the SAME family kind closes the gap; a
        helper.py next to a PDF scan (or instead of a spreadsheet) does not.
        """
        result = {
            'suggested_grade': '11', 'level': 'Високий (10-12)',
            'summary': 'Роботу виконано у Python з обчисленнями.',
            'strengths': ['Застосовано формули SUM та AVERAGE у коді'],
            'weaknesses': [], 'feedback_comment': 'Обчислення виконано.',
            'grade_explanation': 'Обчислення реалізовано.',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('python', {'syntax_ok': True, 'loops': 1, 'functions': 2,
                                       'branches': 1}, [], 'work.py')
        out = self.guard(
            result, [evidence],
            'Створіть таблицю обліку товарів з формулами SUM та AVERAGE')
        self.assertTrue(out['applied'])
        self.assertIn('implementation_unverified', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '9')
        self.assertEqual(out['level'], 'Достатній (7-9)')

    def test_verified_code_family_is_supported_by_a_python_file(self):
        """The legitimate per-family match: code requirement + verified python."""
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Реалізовано цикл for.', 'strengths': ['Реалізовано цикл for'],
            'weaknesses': [], 'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('python', {'syntax_ok': True, 'loops': 2, 'functions': 1,
                                       'branches': 1}, [], 'main.py')
        out = self.guard(result, [evidence], PYTHON_REQUIREMENT)
        self.assertFalse(out['applied'])
        self.assertEqual(out['active_flags'], [])
        self.assertEqual(out['suggested_grade'], '10')

    def test_pdf_essay_is_not_treated_as_implementation_unverified(self):
        """A visible essay/presentation in PDF is content, not hidden code."""
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Глибокий твір із повним розкриттям теми.',
            'strengths': ['Глибокий твір'], 'weaknesses': [],
            'feedback_comment': 'Глибокий твір.', 'grade_explanation': 'Твір глибокий.',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        out = self.guard(result, [unverified_stub('document', 'essay.pdf')],
                         'Написати твір на тему «Осінь»')
        self.assertFalse(out['applied'])
        self.assertEqual(out['active_flags'], [])
        self.assertEqual(out['suggested_grade'], '10')
        self.assertEqual(out['strengths'], ['Глибокий твір'])

    def test_pdf_presentation_is_not_treated_as_implementation_unverified(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Презентація повністю розкриває тему.',
            'strengths': ['Повне розкриття теми'], 'weaknesses': [],
            'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        out = self.guard(result, [unverified_stub('document', 'slides.pdf')],
                         'Створити презентацію про безпеку')
        self.assertFalse(out['applied'])
        self.assertEqual(out['active_flags'], [])
        self.assertEqual(out['suggested_grade'], '10')

    def test_unverifiable_rank_criterion_becomes_missing_when_absence_proven(self):
        """Old guard may have set 'unverifiable'; proven absence must win (2.1)."""
        result = {
            'suggested_grade': '9', 'level': 'Достатній (7-9)',
            'summary': '', 'strengths': [], 'weaknesses': [],
            'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [
                {'criterion': 'Використання функції RANK', 'status': 'unverifiable',
                 'evidence': 'RANK застосовано'}],
            'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        out = self.guard(result, [excel_without_formulas()], MARKING_REQUIREMENT)
        self.assertEqual(result['criteria_results'][0]['status'], 'missing')
        self.assertTrue(result['criteria_results'][0]['evidence'].startswith('Фактично:'))
        self.assertIn('missing', ' '.join(
            str(item.get('after')) for item in out['adjustments']))

    # ── Access (queries / relationships) ──────────────────────────────────

    def test_access_without_queries_is_capped(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Створено запити до бази даних.',
            'strengths': ['Створено запити до бази даних', 'Охайні таблиці'],
            'weaknesses': [], 'feedback_comment': 'Запити створено.',
            'grade_explanation': 'Запити реалізовано правильно.',
            'criteria_results': [
                {'criterion': 'Створення записів', 'status': 'completed'}],
            'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('access', {'tables_count': 3, 'queries_count': 0,
                                       'relationships_count': 2}, ['no_queries'],
                            'db.accdb')
        out = self.guard(result, [evidence],
                         'Створити базу даних Access із запитами та зв’язками')
        self.assertTrue(out['applied'])
        self.assertIn('no_queries', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '6')
        self.assertNotIn('запити', ' '.join(out['strengths']).lower())

    def test_access_without_relationships_is_capped(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Створено зв’язки між таблицями.', 'strengths': [
                'Створено зв’язки між таблицями'], 'weaknesses': [],
            'feedback_comment': 'Зв’язки створено.', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('access', {'tables_count': 3, 'queries_count': 1,
                                       'relationships_count': 0},
                            ['no_relationships'], 'db.accdb')
        out = self.guard(result, [evidence],
                         'Створити базу даних Access із запитами та зв’язками')
        self.assertIn('no_relationships', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '6')

    # ── GR comments and consistency ───────────────────────────────────────

    def test_gr_comments_are_cleaned_with_factual_text(self):
        result = self.precedent_result()
        gr_rows = [
            {'code': 'ГР 1', 'name': 'Зміст', 'grade': '9', 'level': 'Достатній (7-9)',
             'status': 'assessed',
             'comment': 'Учень правильно застосував логічну функцію IF'},
            {'code': 'ГР 2', 'name': 'Оформлення', 'grade': '8',
             'level': 'Достатній (7-9)', 'status': 'assessed',
             'comment': 'Оформлення охайне'},
        ]
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            clean_gr_results=copy.deepcopy(gr_rows),
            numeric_gr_grades=[9.0, 8.0], avg_gr_grade=9)
        comments = [row['comment'] for row in out['clean_gr_results']]
        self.assertNotIn('IF', comments[0].upper())
        self.assertIn('немає формул', comments[0])
        self.assertEqual(comments[1], 'Оформлення охайне')
        self.assertTrue(any(item['field'] == 'gr_comment'
                            for item in out['adjustments']))
        self.assertEqual([row['grade'] for row in out['clean_gr_results']], ['6', '6'])
        self.assertEqual(out['avg_gr_grade'], 6)

    def test_clause_after_but_is_not_treated_as_fabricated_praise(self):
        """«застосував IF, але оформлення потребує…» is a praise of IF (2.2)."""
        result = {
            'suggested_grade': '9', 'level': 'Достатній (7-9)',
            'summary': 'Учень застосував IF, але оформлення потребує покращення.',
            'strengths': ['Учень застосував IF, але оформлення потребує покращення',
                          'Оформлення потребує покращення'],
            'weaknesses': [], 'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        out = self.guard(result, [excel_without_formulas()], MARKING_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertEqual(out['strengths'], ['Оформлення потребує покращення'])
        self.assertEqual(out['suggested_grade'], '6')
        self.assertNotIn('застосував IF', out['summary'])

    def test_if_logic_wording_is_normalized(self):
        result = self.precedent_result()
        result['strengths'] = ['IF-логіка застосована правильно',
                               'Охайне оформлення таблиці']
        result['summary'] = 'IF-логіка застосована правильно.'
        out = self.guard(result, [excel_without_formulas()], MARKING_REQUIREMENT)
        self.assertEqual(out['strengths'], ['Охайне оформлення таблиці'])
        self.assertNotIn('IF-логіка', out['summary'])

    def test_ukrainian_function_name_is_normalized(self):
        result = self.precedent_result()
        result['strengths'] = ['Функція ЯКЩО застосована правильно',
                               'Охайне оформлення таблиці']
        result['summary'] = 'Функцію ЯКЩО застосовано правильно.'
        out = self.guard(result, [excel_without_formulas()], MARKING_REQUIREMENT)
        self.assertEqual(out['strengths'], ['Охайне оформлення таблиці'])
        self.assertNotIn('правильно', out['summary'].lower())

    # ── Ceiling only when the requirement demanded the element ────────────

    def test_ceiling_is_not_applied_without_requirement_but_praise_is_removed(self):
        result = self.precedent_result()
        out = self.guard(result, [excel_without_formulas()], DATA_ENTRY_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertEqual(out['suggested_grade'], '8')  # no formula requirement -> no cap
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        self.assertIn('немає жодної формули', out['feedback_comment'])

    def test_general_ceiling_9_when_fabricated_claim_removed_without_keywords(self):
        """1.8: removing a fabricated claim must not leave a "Високий" grade."""
        result = self.precedent_result()
        result.update(suggested_grade='10', level='Високий (10-12)')
        out = self.guard(result, [excel_without_formulas()], DATA_ENTRY_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertEqual(out['suggested_grade'], '9')
        self.assertEqual(out['level'], 'Достатній (7-9)')
        self.assertNotIn('вимогу не виконано', out['summary'])

    def test_corrective_weaknesses_are_preserved(self):
        result = self.precedent_result()
        result['weaknesses'] = ['Потрібно застосувати формули замість статичних значень.']
        out = self.guard(result, [excel_without_formulas()], MARKING_REQUIREMENT)
        self.assertIn('Потрібно застосувати формули замість статичних значень.',
                      out['weaknesses'])

    # ── Teacher waiver ────────────────────────────────────────────────────

    def test_teacher_waiver_disables_ceiling_but_not_praise_removal(self):
        result = self.precedent_result()
        result.update(suggested_grade='10', level='Високий (10-12)')
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            teacher_instructions='Не знижуй бал за відсутність формул, оцінюй лише зміст.')
        self.assertTrue(out['applied'])
        self.assertIn('formulas_absent', out['waived_flags'])
        self.assertEqual(out['suggested_grade'], '10')
        self.assertEqual(out['level'], 'Високий (10-12)')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        self.assertTrue(any(item.get('reason') == 'technical_evidence_waived_by_teacher'
                            for item in out['adjustments']))

    def test_custom_criteria_waiver_is_honoured(self):
        result = self.precedent_result()
        result.update(suggested_grade='10', level='Високий (10-12)')
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            custom_criteria='Достатньо лише змісту: формули не обов’язкові.')
        self.assertIn('formulas_absent', out['waived_flags'])
        self.assertEqual(out['suggested_grade'], '10')

    # ── FIX-1 regression: false-positive code family (14.11) ──────────────

    def test_encyclopedia_word_is_not_a_code_requirement(self):
        """«Інтернет-енциклопедії» contains "цикл" but is not a code task."""
        result = {
            'suggested_grade': '11', 'level': 'Високий (10-12)',
            'summary': 'Інформацію знайдено в енциклопедії.', 'strengths': [
                'Успішний пошук інформації в енциклопедії'], 'weaknesses': [],
            'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('text_document', {'words_count': 120, 'paragraphs': 4},
                            [], 'student_work.txt')
        out = self.guard(
            result, [evidence],
            'Практична робота: Пошук інформації в Інтернет-енциклопедії Вікіпедія')
        self.assertFalse(out['applied'])
        self.assertEqual(out['active_flags'], [])
        self.assertEqual(out['suggested_grade'], '11')

    def test_math_function_exercise_is_not_a_code_requirement(self):
        """«Побудувати графік функції y = 2x + 1» is maths, not programming."""
        result = {
            'suggested_grade': '11', 'level': 'Високий (10-12)',
            'summary': 'Учениця побудувала графік функції.', 'strengths': [
                'Правильно розраховані координати точок.'], 'weaknesses': [],
            'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('text_document', {'words_count': 80, 'paragraphs': 2},
                            [], 'work.txt')
        out = self.guard(
            result, [evidence],
            'Домашня робота. Виконати вправу 2. Побудувати графік функції y = 2x + 1')
        self.assertFalse(out['applied'])
        self.assertEqual(out['active_flags'], [])
        self.assertEqual(out['suggested_grade'], '11')

    # ── FIX-1 / P0-2: waiver must be clause-scoped ────────────────────────

    def test_waiver_requires_marker_and_term_in_the_same_clause(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            custom_criteria='Формули обовʼязкові. Достатньо однієї таблиці.')
        self.assertNotIn('formulas_absent', out['waived_flags'])
        self.assertEqual(out['suggested_grade'], '6')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())

    def test_waiver_does_not_cross_clauses_on_ignore_marker(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            custom_criteria='Не оцінюй оформлення. Формули обовʼязкові.')
        self.assertNotIn('formulas_absent', out['waived_flags'])
        self.assertEqual(out['suggested_grade'], '6')

    def test_bare_sufficient_marker_does_not_waive_a_ceiling(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            custom_criteria='Достатньо. Оцінюй роботу за змістом.')
        self.assertNotIn('formulas_absent', out['waived_flags'])
        # The custom scale does not demand formulas, so the ceiling is the
        # general "not Високий" 9 rather than the strict 6 (P1-4).
        self.assertEqual(out['suggested_grade'], '9')

    def test_clause_level_true_waiver_still_works(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            teacher_instructions='Не знижуй бал за відсутність формул, оцінюй лише зміст.')
        self.assertIn('formulas_absent', out['waived_flags'])
        self.assertEqual(out['suggested_grade'], '11')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())

    # ── FIX-1 / P1-3: cross-family implementation evidence ────────────────

    def test_pdf_formula_scan_with_helper_python_is_unverified(self):
        """A verified helper.py must not close a formula gap of a PDF scan."""
        result = self.precedent_result()
        result.update(suggested_grade='10', level='Високий (10-12)')
        helper = passport('python', {'syntax_ok': True, 'loops': 1, 'functions': 2,
                                     'branches': 1}, [], 'helper.py')
        out = self.guard(
            result, [unverified_stub('document', 'scan.pdf'), helper],
            MARKING_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertIn('implementation_unverified', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '9')
        self.assertEqual(out['level'], 'Достатній (7-9)')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        row = result['criteria_results'][0]
        self.assertEqual(row['status'], 'unverifiable')
        self.assertNotEqual(row['status'], 'missing')
        self.assertTrue(row.get('format_gap'))

    def test_verified_pdf_document_plus_helper_python_is_unverified(self):
        """P1-3 exact repro: verified PDF(document) + verified helper.py."""
        result = self.precedent_result()
        result.update(suggested_grade='10', level='Високий (10-12)')
        pdf = passport('document', {'words_count': 40, 'paragraphs': 1, 'tables': 1,
                                    'images': 1, 'charts': 0, 'pages': 1},
                       [], 'scan.pdf')
        helper = passport('python', {'syntax_ok': True, 'loops': 1, 'functions': 2,
                                     'branches': 1}, [], 'helper.py')
        out = self.guard(result, [pdf, helper], MARKING_REQUIREMENT)
        self.assertTrue(out['applied'])
        self.assertIn('implementation_unverified', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '9')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        row = result['criteria_results'][0]
        self.assertEqual(row['status'], 'unverifiable')
        self.assertTrue(row.get('format_gap'))

    def test_pdf_scan_without_helper_is_still_unverified(self):
        result = self.precedent_result()
        result.update(suggested_grade='10', level='Високий (10-12)')
        out = self.guard(result, [unverified_stub('document', 'scan.pdf')],
                         MARKING_REQUIREMENT)
        self.assertIn('implementation_unverified', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '9')

    # ── FIX-1 / P0-1: no-evidence bypass (comment/link only) ──────────────

    def test_comment_only_technical_task_gets_unverified_ceiling(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        profile = {'expects_artifact': True, 'has_files': False, 'has_link': False,
                   'has_comment': True, 'task_type': 'table',
                   'deliverable_type': 'table'}
        out = self.guard(result, [], MARKING_REQUIREMENT, evidence_profile=profile)
        self.assertTrue(out['applied'])
        self.assertEqual(out['active_flags'], ['implementation_unverified'])
        self.assertEqual(out['suggested_grade'], '9')
        self.assertEqual(out['level'], 'Достатній (7-9)')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        self.assertIn('не підтверджені', out['summary'])
        row = result['criteria_results'][0]
        self.assertEqual(row['status'], 'unverifiable')
        self.assertTrue(row.get('format_gap'))

    def test_link_only_scratch_task_gets_unverified_ceiling(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Гра працює.', 'strengths': ['Працююча гра з анімацією'],
            'weaknesses': [], 'feedback_comment': 'Гра працює.',
            'grade_explanation': 'Гру реалізовано.',
            'criteria_results': [{'criterion': 'Створення гри', 'status': 'completed'}],
            'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        profile = {'expects_artifact': True, 'has_files': False, 'has_link': True,
                   'has_comment': False, 'task_type': 'game',
                   'deliverable_type': 'scratch'}
        out = self.guard(result, [], 'Створити гру у Scratch',
                         evidence_profile=profile)
        self.assertIn('implementation_unverified', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '9')
        self.assertEqual(result['criteria_results'][0]['status'], 'unverifiable')

    def test_no_evidence_signal_is_disabled_for_oral_or_essay(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        before = copy.deepcopy(result)
        profile = {'expects_artifact': False, 'has_files': False, 'has_link': False,
                   'has_comment': True, 'task_type': 'oral',
                   'deliverable_type': 'oral_answer'}
        out = self.guard(result, [], 'Усна відповідь на уроці. Розкажи правило.',
                         evidence_profile=profile)
        self.assertFalse(out['applied'])
        self.assertEqual(out['suggested_grade'], '11')
        self.assertEqual(result, before)

    def test_extensionless_visible_code_without_stub_gets_unverified(self):
        """A file with no extension yields no passport but is still a technical
        artifact: expects_artifact + passports==[] must cap at 9 (P0-1)."""
        result = self.precedent_result()
        result.update(suggested_grade='10', level='Високий (10-12)')
        profile = {'expects_artifact': True, 'has_files': True, 'has_link': False,
                   'has_comment': False, 'task_type': 'table',
                   'deliverable_type': 'table'}
        out = self.guard(result, [], MARKING_REQUIREMENT, evidence_profile=profile)
        self.assertTrue(out['applied'])
        self.assertIn('implementation_unverified', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '9')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        self.assertTrue(result['criteria_results'][0].get('format_gap'))

    def test_no_evidence_profile_without_passports_is_noop(self):
        result = self.precedent_result()
        out = self.guard(result, [], MARKING_REQUIREMENT)
        self.assertFalse(out['applied'])
        self.assertEqual(out['suggested_grade'], '8')

    # ── FIX-1 / P1-4: custom_criteria scale vs technical ceiling ──────────

    def test_custom_scale_is_not_cut_to_six_by_technical_cap(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            custom_criteria='Авторська шкала 4/4/4: оцінюй зміст і оформлення.')
        self.assertEqual(out['suggested_grade'], '9')
        self.assertNotIn('IF', ' '.join(out['strengths']).upper())

    def test_custom_scale_that_requires_formulas_keeps_strict_cap(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            custom_criteria='Формули обовʼязкові. Авторська шкала 4/4/4.')
        self.assertEqual(out['suggested_grade'], '6')

    # ── FIX-3: verified text_document and partial-scan passports ──────────

    def test_verified_text_document_essay_is_not_punished(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Глибокий твір із повним розкриттям теми.',
            'strengths': ['Глибокий твір'], 'weaknesses': [],
            'feedback_comment': 'Глибокий твір.', 'grade_explanation': 'Твір глибокий.',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('text_document', {'words_count': 240, 'paragraphs': 4,
                                              'lines': 12}, [], 'essay.txt')
        out = self.guard(result, [evidence], 'Написати твір на тему «Осінь»')
        self.assertFalse(out['applied'])
        self.assertEqual(out['suggested_grade'], '10')
        self.assertEqual(out['strengths'], ['Глибокий твір'])

    def test_text_document_does_not_prove_formula_family(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        evidence = passport('text_document', {'words_count': 50, 'paragraphs': 2},
                            [], 'notes.txt')
        out = self.guard(result, [evidence], MARKING_REQUIREMENT)
        self.assertIn('implementation_unverified', out['active_flags'])
        self.assertEqual(out['suggested_grade'], '9')

    def test_partial_excel_scan_does_not_prove_formulas_absent(self):
        result = self.precedent_result()
        result.update(suggested_grade='11', level='Високий (10-12)')
        evidence = passport('excel', {
            'audit_complete': False, 'sheets_count': 1, 'formulas_count': 0,
            'functions': [], 'limitations': ['Скан обрізано'],
        }, ['formulas_absent'], 'big.xlsx')
        out = self.guard(result, [evidence], MARKING_REQUIREMENT)
        self.assertFalse(out['applied'])
        self.assertEqual(out['active_flags'], [])
        self.assertEqual(out['suggested_grade'], '11')
        self.assertNotIn('IF', '')  # no-op guard keeps the model output intact

    def test_partial_code_scan_does_not_prove_construct_absent(self):
        result = {
            'suggested_grade': '10', 'level': 'Високий (10-12)',
            'summary': 'Реалізовано цикл for.', 'strengths': ['Реалізовано цикл for'],
            'weaknesses': [], 'feedback_comment': '', 'grade_explanation': '',
            'criteria_results': [], 'tasks_evaluated': [], 'tasks_completed_count': 0,
        }
        evidence = passport('python', {
            'audit_complete': False, 'syntax_ok': True, 'loops': 0,
            'functions': 0, 'branches': 0,
        }, ['no_code_constructs'], 'big.py')
        out = self.guard(result, [evidence], PYTHON_REQUIREMENT)
        self.assertFalse(out['applied'])
        self.assertEqual(out['suggested_grade'], '10')
        self.assertEqual(out['strengths'], ['Реалізовано цикл for'])

    # ── Grade ceilings survive the final GR average ────────────────────────

    def test_gr_ceiling_survives_final_average_when_suggested_grade_is_below_cap(self):
        result = self.precedent_result()
        result['suggested_grade'] = '5'
        result['level'] = 'Середній (4-6)'
        gr_rows = [
            {'code': 'ГР 1', 'name': 'Зміст', 'grade': '8', 'level': 'Достатній (7-9)',
             'status': 'assessed', 'comment': ''},
            {'code': 'ГР 2', 'name': 'Практика', 'grade': '8', 'level': 'Достатній (7-9)',
             'status': 'assessed', 'comment': ''},
        ]
        out = self.guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            clean_gr_results=copy.deepcopy(gr_rows),
            numeric_gr_grades=[8.0, 8.0], avg_gr_grade=8)
        self.assertEqual(out['suggested_grade'], '5')  # already below the cap
        self.assertEqual([row['grade'] for row in out['clean_gr_results']], ['6', '6'])
        self.assertEqual(out['numeric_gr_grades'], [6.0, 6.0])
        self.assertEqual(out['avg_gr_grade'], 6)  # the final grade cannot exceed the cap
        self.assertTrue(any(item['field'] == 'avg_gr_grade' for item in out['adjustments']))

    # ── Real passport integration (v2, WS1) ───────────────────────────────

    def test_text_formula_is_not_counted_by_real_passport(self):
        """1.1: a textual '=IF(...)' (data_type='s') is static text, not a formula."""
        from openpyxl import Workbook, load_workbook
        from .ai_file_passport import build_file_passport

        buffer = io.BytesIO()
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Умова', 'Колір'])
        sheet['B2'] = '=IF(1<=160,"Червоне","Зелене")'
        workbook.save(buffer)
        # Re-open and force the cell to a static string, exactly like a bypass.
        tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        try:
            tmp.write(buffer.getvalue())
            tmp.close()
            loaded = load_workbook(tmp.name)
            loaded.active['B2'].data_type = 's'
            loaded.save(tmp.name)
            loaded.close()
            evidence = build_file_passport(tmp.name)
            self.assertIsNotNone(evidence)
            self.assertEqual(evidence['facts']['formulas_count'], 0)
            self.assertIn('formulas_absent', evidence['absence'])
            result = self.precedent_result()
            out = self.guard(result, [dict(evidence, file='fake_formula.xlsx')],
                             MARKING_REQUIREMENT)
            self.assertIn('formulas_absent', out['active_flags'])
            self.assertEqual(out['suggested_grade'], '6')
            self.assertNotIn('IF', ' '.join(out['strengths']).upper())
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    # ── Idempotency ────────────────────────────────────────────────────────

    def test_second_call_is_a_noop(self):
        result = self.precedent_result()
        first = self.guard(result, [excel_without_formulas()], MARKING_REQUIREMENT)
        second = apply_technical_evidence_guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            suggested_grade=first['suggested_grade'], level=first['level'],
            clean_gr_results=first['clean_gr_results'],
            numeric_gr_grades=first['numeric_gr_grades'],
            avg_gr_grade=first['avg_gr_grade'], summary=first['summary'],
            strengths=first['strengths'], weaknesses=first['weaknesses'],
            feedback_comment=first['feedback_comment'])
        self.assertFalse(second['applied'])
        self.assertEqual(second['adjustments'], [])
        self.assertEqual(second['suggested_grade'], '6')
        self.assertEqual(second['weaknesses'], first['weaknesses'])
        self.assertEqual(second['feedback_comment'], first['feedback_comment'])
        self.assertEqual(second['summary'], first['summary'])
        self.assertEqual(result['tasks_completed_count'], 1)

    def test_second_call_after_waiver_is_a_noop(self):
        result = self.precedent_result()
        result.update(suggested_grade='10', level='Високий (10-12)')
        kwargs = {'teacher_instructions': 'Не знижуй бал за відсутність формул.'}
        first = self.guard(result, [excel_without_formulas()], MARKING_REQUIREMENT,
                           **kwargs)
        second = apply_technical_evidence_guard(
            result, [excel_without_formulas()], MARKING_REQUIREMENT,
            suggested_grade=first['suggested_grade'], level=first['level'],
            clean_gr_results=first['clean_gr_results'],
            numeric_gr_grades=first['numeric_gr_grades'],
            avg_gr_grade=first['avg_gr_grade'], summary=first['summary'],
            strengths=first['strengths'], weaknesses=first['weaknesses'],
            feedback_comment=first['feedback_comment'], **kwargs)
        self.assertFalse(second['applied'])
        self.assertEqual(second['adjustments'], [])


class TechnicalEvidenceGuardIntegrationTests(TestCase):
    """End-to-end: real extraction + passport + mocked provider response."""

    def setUp(self):
        evidence_cache().clear()
        self.user = User.objects.create_superuser('guard_teacher', password='test-password')
        School.objects.create(name='Тестова школа', admin=self.user)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель')
        self.group = ClassGroup.objects.create(name='7-А', grade=7)
        self.teacher.classes.add(self.group)
        self.preset = AICriteriaPreset.objects.create(
            name='Звичайна школа', evaluation_type='traditional',
            system_prompt='Оцінюй зміст і формат.')
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, title='Таблиця «Світлофор»',
            description=('Створити таблицю «Світлофор» з використанням логічної '
                         'функції IF для маркування клітинок.'),
            status='published', default_ai_preset=self.preset)
        self.assignment.classes.add(self.group)

        from openpyxl import Workbook
        buffer = io.BytesIO()
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Умова', 'Колір'])
        sheet.append(['> 25', 'червоне'])
        sheet.append(['< 25', 'зелене'])
        workbook.save(buffer)
        self.submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='Тестовий', grade='7',
            file=SimpleUploadedFile(
                'svitlofor.xlsx', buffer.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
        config = AISettings.get_solo()
        config.is_enabled = True
        config.api_key = 'synthetic-key'
        config.save()
        self.configs = [{'provider': 'gemini', 'api_key': 'synthetic-key',
                         'model': 'synthetic-model', 'custom_url': '', 'is_backup': False}]

    def test_precedent_617_end_to_end_with_mocked_provider(self):
        model_result = {
            'suggested_grade': '8',
            'level': 'Достатній (7-9)',
            'summary': 'Учень успішно застосував логічну функцію IF для маркування клітинок.',
            'strengths': ['Правильне застосування логічної функції IF',
                          'Охайне оформлення таблиці'],
            'weaknesses': ['Відсутність формул у наданому файлі (лише статичні дані).'],
            'feedback_comment': 'Ти успішно застосував функцію IF.',
            'grade_explanation': 'Логічну функцію IF застосовано правильно.',
            'criteria_results': [
                {'criterion': 'Використання формули IF', 'status': 'completed',
                 'evidence': 'Формулу IF застосовано'},
                {'criterion': 'Оформлення таблиці', 'status': 'completed',
                 'evidence': 'Охайна таблиця'},
            ],
            'tasks_evaluated': [
                {'task_num': 1, 'task_title': 'Створити таблицю з формулами IF',
                 'status': 'completed', 'comment': 'Виконано'}],
            'tasks_completed_count': 1,
        }
        with patch.object(AISettings, 'get_request_configs', return_value=self.configs), \
             patch('feed.gemini_service.call_ai_api',
                   return_value=(200, json.dumps(model_result), None, {})):
            response = evaluate_submission_with_gemini(self.submission)

        self.assertEqual(response['status'], 'success')
        self.assertIsNotNone(self.submission._technical_passports)
        self.assertIn('formulas_absent', self.submission._technical_passports[0]['absence'])
        self.assertEqual(response['suggested_grade'], '6')  # capped from 8
        self.assertNotIn('IF', ' '.join(response['strengths']).upper())
        self.assertEqual(
            response['summary'],
            'Таблицю створено, проте формули не використано '
            '(значення маркування введено вручну статичним текстом).')
        self.assertIn('немає жодної формули', response['grade_explanation'])
        self.assertNotIn('успішно', response['feedback_comment'].lower())

        findings = response['raw_json'].get('technical_passport_findings') or {}
        self.assertIn('formulas_absent', findings.get('active_flags') or [])
        self.assertEqual(findings['passports'][0]['file'], 'svitlofor.xlsx')
        evidence_row = response['raw_json']['submission_evidence']['technical_passports'][0]
        self.assertEqual(evidence_row['absence'], ['formulas_absent'])
        self.assertIn('Формули у файлі повністю відсутні', ' '.join(evidence_row['warnings']))
        self.assertEqual(response['raw_json']['criteria_results'][0]['status'], 'missing')
        json.dumps(response['raw_json'], ensure_ascii=False)
