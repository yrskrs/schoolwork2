"""Keep document formatting while removing executable HTML from previews."""
import html as text_html
import re
from urllib.parse import urlsplit

from lxml import html


def safe_document_html(value):
    if not value:
        return value
    try:
        root = html.fragment_fromstring(value, create_parent='div')
        forbidden = {'script', 'style', 'iframe', 'object', 'embed', 'applet', 'meta', 'link', 'svg', 'math',
                     'form', 'input', 'button', 'textarea', 'select', 'base'}
        attributes = {'class', 'style', 'href', 'src', 'alt', 'title', 'width', 'height', 'colspan', 'rowspan',
                      'align', 'valign', 'border', 'cellpadding', 'cellspacing'}
        properties = {'color', 'background-color', 'font-size', 'font-family', 'font-weight', 'font-style',
                      'text-align', 'text-decoration', 'white-space', 'line-height', 'vertical-align',
                      'border', 'border-color', 'border-width', 'border-style', 'border-collapse',
                      'width', 'height', 'max-width', 'max-height', 'min-width', 'min-height',
                      'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
                      'padding', 'padding-top', 'padding-bottom', 'padding-left', 'padding-right'}
        for element in list(root.iterdescendants()):
            if not isinstance(element.tag, str):
                continue
            if element.tag.lower() in forbidden:
                element.drop_tree()
                continue
            for key, attribute in list(element.attrib.items()):
                key_lower = key.lower()
                if key_lower not in attributes:
                    del element.attrib[key]
                elif key_lower in ('href', 'src'):
                    normalized = re.sub(r'[\x00-\x20\x7f]', '', attribute)
                    parsed = urlsplit(normalized)
                    safe = parsed.scheme.lower() in ('http', 'https', 'mailto') if key_lower == 'href' else False
                    safe = safe or (not parsed.scheme and not normalized.startswith('//'))
                    if key_lower == 'src':
                        safe = safe or bool(re.fullmatch(r'data:image/(?:png|jpeg|gif|webp|bmp);base64,[A-Za-z0-9+/=\s]+', attribute, re.I))
                    if not safe:
                        del element.attrib[key]
                elif key_lower == 'style':
                    declarations = []
                    for declaration in attribute.split(';'):
                        property_name, separator, property_value = declaration.partition(':')
                        if separator and property_name.strip().lower() in properties and not re.search(
                                r'url|expression|@|\\|behavior|javascript|vbscript', property_value, re.I):
                            declarations.append(declaration)
                    element.attrib[key] = ';'.join(declarations)
            if element.tag.lower() == 'a':
                element.set('rel', 'noopener noreferrer')
                element.set('target', '_blank')
        return html.tostring(root, encoding='unicode')
    except Exception:
        return text_html.escape(value)
