"""Render stored teacher criteria without changing their text or grading semantics."""
from markdown_it import MarkdownIt


def render_criteria(text):
    """Support plain text and Markdown; never execute embedded HTML or images."""
    source = (text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not source:
        return ''
    parser = MarkdownIt('js-default', {'html': False, 'breaks': True}).disable('image')

    def table_open(renderer, tokens, index, options, env):
        return ('<p class="criteria-table-hint">Таблицю можна гортати вбік →</p>'
                '<div class="criteria-table-scroll" role="region" '
                'aria-label="Таблиця критеріїв оцінювання" tabindex="0"><table>\n')

    def table_close(renderer, tokens, index, options, env):
        return '</table></div>\n'

    parser.add_render_rule('table_open', table_open)
    parser.add_render_rule('table_close', table_close)
    return parser.render(source)
