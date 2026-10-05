"""Small document previews and private, source-versioned image assets."""
import base64
import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

ASSET_MARKER = '__schoolnet_review_asset__/'
ASSET_PATTERN = re.compile(r'__schoolnet_review_asset__/([a-f0-9]{64}\.(?:png|jpeg|gif|webp|bmp))')
DATA_IMAGE = re.compile(r'(?P<prefix><img\b[^>]*?\bsrc=")data:image/(?P<type>png|jpeg|gif|webp|bmp);base64,(?P<data>[A-Za-z0-9+/=\s]+)"', re.I)


def asset_directory(file_path):
    from .ai_context import file_cache_key
    return Path(settings.REVIEW_PREVIEW_DIR) / file_cache_key(file_path, 'review-assets-v1')


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    # A reader must never see an incomplete image, including across Gunicorn workers.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = stream.name
        try:
            stream.write(data)
            stream.close()
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def externalize_images(file_path, value):
    """Preserve the original pixels, deduplicating repeated images within a document."""
    directory = asset_directory(file_path)

    def replace(match):
        kind = match['type'].lower()
        try:
            data = base64.b64decode(re.sub(r'\s+', '', match['data']), validate=True)
        except ValueError:
            return match[0]
        digest = hashlib.sha256(kind.encode() + b'\0' + data).hexdigest()
        name = f'{digest}.{kind}'
        target = directory / name
        if not target.is_file():
            _atomic_write(target, data)
        return f'{match["prefix"]}{ASSET_MARKER}{name}" loading="lazy" decoding="async"'

    return DATA_IMAGE.sub(replace, value)


def assets_available(file_path, value):
    names = set(ASSET_PATTERN.findall(value))
    if not names:
        return True
    directory = asset_directory(file_path)
    return all((directory / name).is_file() for name in names)


def render_docx_graphics(file_path, text_html):
    """Use actual pages for Word drawings that paragraph HTML would hide."""
    from .ai_context import _office_pdf, media_for_provider
    pdf = _office_pdf(file_path)
    pages = media_for_provider([{'mime_type': 'application/pdf', 'data': base64.b64encode(pdf).decode()}], 'openai')
    output = ['<div class="fv-rendered-document">']
    for number, page in enumerate(pages, 1):
        output.append(f'<div class="document-page"><img src="data:{page["mime_type"]};base64,{page["data"]}" '
                      f'alt="Сторінка {number} документа зі схемами та фігурами" '
                      'style="width:100%;height:auto;" loading="lazy"></div>')
    output.append('<details class="fv-document-text"><summary>Текст документа для читання та копіювання</summary>' + text_html + '</details></div>')
    return '\n'.join(output)


@contextmanager
def preview_conversion_lock(file_path):
    # Hover prefetch and actual opening can overlap in different web workers.
    # Recheck the converter cache inside this lock instead of doing the work twice.
    try:
        import fcntl
    except ImportError:  # Local Windows installations still retain normal caching.
        yield
        return
    try:
        directory = asset_directory(file_path)
        directory.mkdir(parents=True, exist_ok=True)
        stream = (directory / 'conversion.lock').open('a+b')
    except OSError:
        yield
        return
    with stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def convert_office(file_path, extension):
    from . import utils
    converters = {
        '.docx': utils.convert_docx_to_html, '.doc': utils.convert_docx_to_html,
        '.xlsx': utils.convert_xlsx_to_html, '.xls': utils.convert_xlsx_to_html,
        '.pptx': utils.convert_pptx_to_html, '.ppt': utils.convert_pptx_to_html,
        '.odt': utils.convert_odt_to_html, '.ods': utils.convert_ods_to_html,
        '.odp': utils.convert_odp_to_html,
    }
    converter = converters.get(extension)
    if not converter:
        return '', 'Для цього формату завантажте оригінальний файл.'
    with preview_conversion_lock(file_path):
        return converter(file_path, preview_assets=True)
