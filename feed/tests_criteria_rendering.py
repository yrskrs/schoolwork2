from django.template.loader import render_to_string
from django.test import SimpleTestCase
from lxml import html

from .criteria_rendering import render_criteria
from .models import Assignment


SAMPLE = '''### Критерії практичної роботи

#### Розподіл балів (максимум — 12)

| Рівень | Бали | Вимоги |
| :--- | :---: | :--- |
| **Початковий** | 1–3 | Створено частину папок. |
| **Високий** | 10–12 | Усі файли збережені у відповідних папках. |

#### Деталізація вимог

* **4 бали:** Головна папка `Petrenko_5A`.
* **4 бали:** Три підпапки.
* **4 бали:** Збережений файл.
'''


class CriteriaRenderingTests(SimpleTestCase):
    def test_ai_markdown_table_headings_and_lists_are_structured(self):
        tree = html.fromstring('<div>' + render_criteria(SAMPLE) + '</div>')
        self.assertEqual(tree.xpath('.//h3/text()'), ['Критерії практичної роботи'])
        self.assertEqual(len(tree.xpath('.//table/thead/tr/th')), 3)
        self.assertEqual(len(tree.xpath('.//table/tbody/tr')), 2)
        self.assertEqual(tree.xpath('.//td/strong/text()'), ['Початковий', 'Високий'])
        self.assertEqual(len(tree.xpath('.//ul/li')), 3)
        self.assertEqual(tree.xpath('.//code/text()'), ['Petrenko_5A'])
        self.assertTrue(tree.xpath('.//*[@role="region" and @tabindex="0"]/table'))

    def test_plain_text_and_real_windows_line_endings_remain_readable(self):
        tree = html.fromstring(render_criteria('Повнота — 6 балів.\r\nОформлення — 6 балів.'))
        self.assertEqual(len(tree.xpath('.//br')), 1)
        self.assertIn('Оформлення — 6 балів.', tree.text_content())

    def test_empty_content_has_no_empty_boxes(self):
        for source in [None, '', ' \r\n ']:
            self.assertEqual(render_criteria(source), '')

    def test_raw_html_and_scripts_are_displayed_as_text(self):
        payload = '<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n<iframe src="https://example.org"></iframe>'
        tree = html.fromstring('<div>' + render_criteria(payload) + '</div>')
        self.assertFalse(tree.xpath('.//script | .//img | .//iframe | .//*[@onerror]'))
        self.assertIn('<script>alert(1)</script>', tree.text_content())

    def test_unsafe_links_and_remote_images_cannot_execute(self):
        payload = '[a](javascript:alert(1)) [b](data:text/html,script) [c](vbscript:evil) [d](file:///secret) ![tracking](https://example.org/pixel.png)'
        tree = html.fromstring('<div>' + render_criteria(payload) + '</div>')
        self.assertFalse(tree.xpath('.//img'))
        for href in tree.xpath('.//a/@href'):
            self.assertFalse(href.lower().startswith(('javascript:', 'vbscript:', 'data:', 'file:')))

    def test_web_links_and_code_remain_available(self):
        tree = html.fromstring(render_criteria('[Матеріал](https://example.org/task?q=1&x=2) та `<назва>`'))
        self.assertEqual(tree.xpath('.//a/@href'), ['https://example.org/task?q=1&x=2'])
        self.assertEqual(tree.xpath('.//code/text()'), ['<назва>'])

    def test_model_and_modal_render_without_mutating_saved_source(self):
        assignment = Assignment(title='Назва', custom_criteria=SAMPLE)
        rendered = render_to_string('feed/components/criteria_modal.html', {'assignment': assignment})
        tree = html.fromstring(rendered)
        content = tree.xpath('//*[contains(@class,"teacher-criteria-content")]')[0]
        self.assertEqual(len(content.xpath('.//table')), 1)
        self.assertNotIn('###', content.text_content())
        self.assertEqual(assignment.custom_criteria, SAMPLE)
        self.assertEqual(assignment.get_formatted_custom_criteria(), render_criteria(SAMPLE))

    def test_pipe_inside_code_and_escaped_html_stays_safe(self):
        result = render_criteria('| Опис | Бали |\n| --- | --- |\n| `x` **важливо** | 12 |\n\n&lt;script&gt;alert(1)&lt;/script&gt;')
        tree = html.fromstring('<div>' + result + '</div>')
        self.assertFalse(tree.xpath('.//script'))
        self.assertEqual(len(tree.xpath('.//table')), 1)
