"""Матричні тести ШІ-помічника опису завдання (контракт 4.9.5).

Покриває:
1. Динамічний промпт для різних типів завдання (усна/no_submission, файл,
   вибір 1 з N, 2 з 3, обов'язкові «обидва», матеріали-посилання,
   англомовний предмет, звичайне завдання) — через endpoint із замоканим
   ``call_ai_api`` та перевіркою фактичного ``prompt_text``.
2. Fail-open guardrail ``_clean_hallucinated_choice_block`` — 25 кейсів:
   легітимний вибір не видаляється, галюцинований блок видаляється лише
   за явної обов'язковості, межі/порожній результат -> оригінал.
3. ``strip_html_tags`` зберігає математичні порівняння і видаляє лише
   відомі HTML-теги.

Зовнішні ШІ-API не викликаються — лише ``unittest.mock``.
"""
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from feed.gemini_service import (
    _clean_hallucinated_choice_block,
    get_ai_settings,
    strip_html_tags,
)
from feed.models import Teacher


GENERIC_MARKDOWN = (
    "📌 **Що зробити:**\n"
    "1. Відкрийте підручник.\n"
    "2. Виконайте завдання."
)

CHOICE_BLOCK = (
    "⚠️ **Увага (вибір завдання та оцінювання):**\n"
    "• Оберіть та виконайте одне завдання на вибір:\n"
    "- Завдання 1 (максимум 8 балів)\n"
    "- Завдання 2 (максимум 12 балів)"
)

CHOICE_MARKDOWN = GENERIC_MARKDOWN + "\n\n" + CHOICE_BLOCK

MANDATORY_MARKDOWN = (
    GENERIC_MARKDOWN + "\n\n" + CHOICE_BLOCK + "\n\n"
    "**Критерії:**\n"
    "- Повнота виконання.\n"
    "- Точність обчислень."
)


def _criteria_block():
    """Текст після блоку вибору: критерії та дедлайн мають зберегтись."""
    return (
        "**Критерії:**\n"
        "- Повнота виконання.\n"
        "- Точність обчислень.\n\n"
        "**Дедлайн:** п'ятниця."
    )


class _DescriptionEndpointMatrixBase(TestCase):
    """Спільне налаштування для матриці типів завдань через endpoint."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            'teacher_matrix', password='pass', is_superuser=True, is_staff=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Учитель')
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse('teacher_refine_assignment_description')
        settings = get_ai_settings()
        settings.is_enabled = True
        settings.api_key = 'AIzaValidKey'
        settings.temperature = 0.2
        settings.default_thinking_mode = False
        settings.save()

    def tearDown(self):
        cache.clear()

    def post_helper(self, payload):
        return self.client.post(
            self.url, data=json.dumps(payload), content_type='application/json')

    def _prompt_and_response(self, mock_call, payload, output=GENERIC_MARKDOWN):
        mock_call.reset_mock()
        mock_call.return_value = (200, output, None, {})
        response = self.post_helper(payload)
        self.assertEqual(
            response.status_code, 200, response.content.decode()[:500])
        return mock_call.call_args.kwargs['prompt_text'], response.json()

    def assert_phrase_only_in_prohibition(self, prompt, phrase):
        """Фраза може згадуватись лише як заборонена («Не згадуй …»), не як вимога."""
        for line in prompt.splitlines():
            if phrase in line:
                self.assertIn(
                    'Не згадуй', line,
                    f'Фраза «{phrase}» стоїть у позитивній вимозі: {line!r}')


class AssignmentTypePromptMatrixTests(_DescriptionEndpointMatrixBase):
    """Матриця типів завдань: перевіряємо фактичний prompt_text, надісланий у ШІ."""

    @patch('feed.gemini_service.call_ai_api')
    def test_oral_no_submission_task_has_no_file_branch(self, mock_call):
        """Усна відповідь: гілка «не вимагає завантаження», без файлового шаблону."""
        prompt, _ = self._prompt_and_response(mock_call, {
            'teacher_notes': "Розкажіть вірш напам'ять усно перед класом",
            'no_submission_required': True,
        })
        self.assertIn('НЕ вимагає завантаження', prompt)
        self.assertNotIn('Прикріпіть', prompt)
        self.assertNotIn('📁', prompt)
        self.assertNotIn('Збережіть під прізвищем', prompt)
        self.assert_phrase_only_in_prohibition(prompt, 'збережіть під прізвищем')
        self.assertNotIn(
            'Якщо з контексту випливає, що учні здають файл', prompt)

    @patch('feed.gemini_service.call_ai_api')
    def test_file_task_keeps_submission_branch(self, mock_call):
        """Завдання зі здачею файлу: присутня гілка здачі, немає «не вимагає»."""
        prompt, _ = self._prompt_and_response(mock_call, {
            'teacher_notes': 'Підготуйте звіт і здайте його PDF-файлом',
        })
        self.assertIn('Якщо з контексту випливає, що учні здають файл', prompt)
        self.assertNotIn('НЕ вимагає завантаження', prompt)
        self.assertNotIn('📁', prompt)

    @patch('feed.gemini_service.call_ai_api')
    def test_choice_one_of_n_prompt_and_block_preserved(self, mock_call):
        """Вибір 1 з N: явна гілка вибору, вивід із блоком не очищується."""
        prompt, data = self._prompt_and_response(mock_call, {
            'teacher_notes': 'Оберіть один варіант із трьох запропонованих',
        }, output=CHOICE_MARKDOWN)
        self.assertIn('Учитель прямо вказав вибір', prompt)
        self.assertNotIn("завдання обов'язкові", prompt)
        self.assertNotIn('виконувати обидва не потрібно', prompt)
        self.assertIn('Увага (вибір', data['markdown'])
        self.assertIn('Завдання 1 (максимум 8 балів)', data['markdown'])

    @patch('feed.gemini_service.call_ai_api')
    def test_choice_two_of_three_prompt_and_block_preserved(self, mock_call):
        """Вибір 2 з 3: промпт фіксує точну кількість, блок вибору лишається."""
        prompt, data = self._prompt_and_response(mock_call, {
            'teacher_notes': 'Оберіть 2 з 3 завдань і виконайте їх',
        }, output=CHOICE_MARKDOWN)
        self.assertIn('Учитель прямо вказав вибір', prompt)
        self.assertIn('скільки варіантів і скільки потрібно обрати', prompt)
        self.assertNotIn("завдання обов'язкові", prompt)
        self.assertIn('Увага (вибір', data['markdown'])

    @patch('feed.gemini_service.call_ai_api')
    def test_mandatory_both_prompt_and_hallucinated_block_removed(self, mock_call):
        """Обов'язкові «обидва»: гілка обов'язковості, блок вибору видалено, критерії збережено."""
        prompt, data = self._prompt_and_response(mock_call, {
            'teacher_notes': 'Виконати обидва завдання та здати зошит',
        }, output=MANDATORY_MARKDOWN)
        self.assertIn("завдання обов'язкові", prompt)
        self.assertNotIn('Учитель прямо вказав вибір', prompt)
        markdown = data['markdown']
        self.assertNotIn('Увага (вибір', markdown)
        self.assertNotIn('не потрібно', markdown)
        self.assertIn('**Критерії:**', markdown)
        self.assertIn('Повнота виконання', markdown)
        self.assertIn('Точність обчислень', markdown)

    @patch('feed.gemini_service.call_ai_api')
    def test_materials_link_prompt(self, mock_call):
        """Матеріали-посилання: гілка матеріалів без заборони згадувати їх."""
        prompt, _ = self._prompt_and_response(mock_call, {
            'teacher_notes': 'Опрацюйте матеріали за посиланням на YouTube',
            'has_materials': True,
        })
        self.assertIn('прикріплені матеріали', prompt)
        self.assertIn('НЕ переказуй', prompt)
        self.assertNotIn('НЕ прикріплено файлів', prompt)

    @patch('feed.gemini_service.call_ai_api')
    def test_english_subject_and_notes_stay_ukrainian(self, mock_call):
        """Англомовний предмет/notes: промпт вимагає української, вивід не змінюється."""
        output = (
            "📌 **Що зробити:**\n"
            "1. Прочитайте текст.\n"
            "2. Дайте відповіді на запитання."
        )
        prompt, data = self._prompt_and_response(mock_call, {
            'subject_name': 'English',
            'teacher_notes': 'Read the text and answer the questions in English',
        }, output=output)
        self.assertIn('ВИКЛЮЧНО українською', prompt)
        self.assertIn('Українська — єдина мова відповіді', prompt)
        self.assertEqual(data['markdown'], output)

    @patch('feed.gemini_service.call_ai_api')
    def test_plain_task_uses_neutral_prompt(self, mock_call):
        """Звичайне завдання: нейтральний промпт без вибору, балів і файлового нав'язування."""
        prompt, _ = self._prompt_and_response(mock_call, {
            'teacher_notes': 'Підготуйте повідомлення про рослини рідного краю',
        })
        self.assertIn('Якщо в контексті немає явного вибору', prompt)
        self.assertNotIn('Учитель прямо вказав вибір', prompt)
        self.assertNotIn("завдання обов'язкові", prompt)
        self.assertIn('НЕ прикріплено файлів', prompt)
        self.assertIn('Якщо з контексту випливає, що учні здають файл', prompt)
        self.assertNotIn('максимум', prompt)

    @patch('feed.gemini_service.call_ai_api')
    def test_negated_choice_is_treated_as_mandatory(self, mock_call):
        """«Не на вибір» -> гілка обов'язковості, блок вибору видаляється."""
        prompt, data = self._prompt_and_response(mock_call, {
            'teacher_notes': 'Не на вибір: виконайте обидва завдання',
        }, output=MANDATORY_MARKDOWN)
        self.assertIn("завдання обов'язкові", prompt)
        self.assertNotIn('Увага (вибір', data['markdown'])

    @patch('feed.gemini_service.call_ai_api')
    def test_materials_and_no_submission_combined(self, mock_call):
        """Матеріали + усна робота: обидві динамічні гілки присутні одночасно."""
        prompt, _ = self._prompt_and_response(mock_call, {
            'teacher_notes': 'Усно опрацюйте матеріали за посиланням',
            'has_materials': True,
            'no_submission_required': True,
        })
        self.assertIn('прикріплені матеріали', prompt)
        self.assertIn('НЕ вимагає завантаження', prompt)
        self.assertNotIn('НЕ прикріплено файлів', prompt)


class ChoiceBlockGuardrailMatrixTests(SimpleTestCase):
    """Fail-open матриця пост-фільтра галюцинованого блоку вибору (25 кейсів)."""

    def _text_with_block(self, tail=''):
        return (
            "📌 **Вказівки:**\n"
            "1. Відкрийте підручник.\n"
            "2. Виконайте завдання.\n\n"
            + CHOICE_BLOCK + tail
        )

    def _text_with_block_and_criteria(self):
        return self._text_with_block("\n\n" + _criteria_block())

    def _assert_unchanged(self, output, notes, description=''):
        self.assertEqual(
            _clean_hallucinated_choice_block(output, notes, description), output)

    # ── Легітимний вибір/нейтральні вказівки не видаляються ─────────────────
    def test_choice_one_variant_preserved(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(), 'Оберіть один варіант')

    def test_choice_two_of_three_preserved(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(), 'Оберіть 2 з 3 завдань')

    def test_choice_at_discretion_preserved(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(), 'Зробіть на ваш розсуд')

    def test_choice_any_option_preserved(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(), 'Оберіть будь-яке завдання')

    def test_negated_both_means_choice_preserved(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(),
            'Не потрібно виконувати обидва завдання')

    def test_english_choose_one_preserved(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(), 'Choose one of the tasks')

    def test_not_all_tasks_is_not_mandatory(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(),
            'Не всі завдання потрібно робити')

    def test_explain_to_all_pupils_is_not_mandatory(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(),
            'Поясніть усім учням матеріал уроку')

    def test_every_lesson_is_not_mandatory(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(),
            'На кожен урок готуйте коротке повідомлення')

    def test_conflicting_choice_and_both_fails_open(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(),
            'Оберіть одне завдання або виконайте обидва')

    def test_choice_notes_beat_mandatory_description(self):
        self._assert_unchanged(
            self._text_with_block_and_criteria(),
            'Оберіть одне завдання', 'виконати обидва завдання')

    def test_generic_warning_header_is_untouched(self):
        output = (
            "📌 **Вказівки:**\n"
            "1. Крок.\n\n"
            "⚠️ **Увага:**\n"
            "Оберіть одне завдання на вибір.\n\n"
            "**Критерії:**\n- Повнота."
        )
        self._assert_unchanged(output, 'виконати обидва завдання')

    def test_no_block_returns_original(self):
        self._assert_unchanged(
            "📌 **Вказівки:**\n1. Крок.\n2. Ще крок.",
            'виконати обидва завдання')

    def test_undefined_block_boundary_fails_open(self):
        output = (
            "📌 **Вказівки:**\n1. Крок.\n\n"
            + CHOICE_BLOCK + "\n\n- Окремий пункт без заголовка"
        )
        self._assert_unchanged(output, 'виконати обидва завдання')

    def test_block_only_returns_original(self):
        self._assert_unchanged(CHOICE_BLOCK, 'виконати обидва завдання')

    def test_empty_result_is_not_modified(self):
        self.assertEqual(
            _clean_hallucinated_choice_block('', 'виконати обидва', ''), '')

    # ── Галюцинований блок видаляється лише за обов'язковості ───────────────
    def test_mandatory_both_removes_block_keeps_criteria_and_deadline(self):
        cleaned = _clean_hallucinated_choice_block(
            self._text_with_block_and_criteria(),
            'Виконати обидва завдання', '')
        self.assertNotIn('Увага (вибір', cleaned)
        self.assertNotIn('виконуйте обидва', cleaned.lower())
        self.assertIn('**Критерії:**', cleaned)
        self.assertIn('Повнота виконання', cleaned)
        self.assertIn("**Дедлайн:** п'ятниця", cleaned)

    def test_mandatory_tasks_one_and_two_removes_block(self):
        cleaned = _clean_hallucinated_choice_block(
            self._text_with_block_and_criteria(),
            'Виконайте завдання 1 і 2', '')
        self.assertNotIn('Увага (вибір', cleaned)
        self.assertIn('**Критерії:**', cleaned)

    def test_negation_not_on_choice_removes_block(self):
        cleaned = _clean_hallucinated_choice_block(
            self._text_with_block_and_criteria(),
            'Не на вибір: виконайте обидва завдання', '')
        self.assertNotIn('Увага (вибір', cleaned)

    def test_mandatory_from_description_when_notes_empty(self):
        cleaned = _clean_hallucinated_choice_block(
            self._text_with_block_and_criteria(), '',
            'виконати обидва завдання')
        self.assertNotIn('Увага (вибір', cleaned)
        self.assertIn('**Критерії:**', cleaned)

    def test_mandatory_all_tasks_removes_block(self):
        cleaned = _clean_hallucinated_choice_block(
            self._text_with_block_and_criteria(),
            'Виконайте всі завдання', '')
        self.assertNotIn('Увага (вибір', cleaned)

    def test_mandatory_each_task_removes_block(self):
        cleaned = _clean_hallucinated_choice_block(
            self._text_with_block_and_criteria(),
            'Виконайте кожне завдання', '')
        self.assertNotIn('Увага (вибір', cleaned)

    def test_multiple_blocks_all_removed(self):
        output = (
            self._text_with_block("\n\n" + _criteria_block())
            + "\n\n" + CHOICE_BLOCK
            + "\n\n**Дедлайн:** середа."
        )
        cleaned = _clean_hallucinated_choice_block(
            output, 'виконати обидва завдання', '')
        self.assertNotIn('Увага (вибір', cleaned)
        self.assertIn('**Критерії:**', cleaned)
        self.assertIn('**Дедлайн:** середа', cleaned)

    def test_block_at_end_removed_and_output_not_empty(self):
        cleaned = _clean_hallucinated_choice_block(
            self._text_with_block(), 'виконати обидва завдання', '')
        self.assertNotIn('Увага (вибір', cleaned)
        self.assertIn('Відкрийте підручник', cleaned)

    def test_forbidden_both_line_outside_block_removed(self):
        output = (
            "📌 **Вказівки:**\n"
            "1. Відкрийте підручник.\n\n"
            "Виконувати обидва завдання не потрібно.\n\n"
            "**Критерії:**\n- Повнота."
        )
        cleaned = _clean_hallucinated_choice_block(
            output, 'виконати обидва завдання', '')
        self.assertNotIn('не потрібно', cleaned)
        self.assertIn('**Критерії:**', cleaned)


class StripHtmlTagsSafetyTests(SimpleTestCase):
    """strip_html_tags: видаляються лише відомі HTML-теги, математика зберігається."""

    def test_preserves_math_comparisons(self):
        self.assertEqual(strip_html_tags('2 < 3 і 5 > 4'), '2 < 3 і 5 > 4')

    def test_removes_known_tags_and_script_content(self):
        self.assertEqual(
            strip_html_tags(
                '<p>Текст <b>жирний</b></p><script>alert(1)</script>'),
            'Текст жирний')

    def test_decodes_html_entities(self):
        self.assertEqual(strip_html_tags('A &amp; B&nbsp;C'), 'A & B C')

    def test_preserves_comparison_inside_known_tag(self):
        self.assertEqual(strip_html_tags('<span>2 &lt; 3</span>'), '2 < 3')

    def test_keeps_unknown_angle_brackets(self):
        self.assertEqual(
            strip_html_tags('a < b та c > d'), 'a < b та c > d')
