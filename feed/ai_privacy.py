"""One outbound privacy boundary. Originals and alias maps stay local.

Text redaction is deterministic minimization, not general-purpose anonymous NLP.
Strict mode refuses unreviewed visual evidence rather than pretending that regex
or stripped EXIF can anonymize pixels, faces, handwriting or arbitrary PDFs.
"""
import base64
import functools
import io
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote, quote_plus

_current = ContextVar('ai_privacy', default=None)
EMAIL = re.compile(r'(?i)\b[\w.+-]+@[\w.-]+\.[a-zа-яіїєґ]{2,}\b')
PHONE = re.compile(r'(?<!\w)(?:\+380|00380|0)[\s().-]*\d{2}(?:[\s().-]*\d){7}(?!\w)')
URL = re.compile(r'https?://[^\s<>"»]+')


class PrivacyBlocked(ValueError):
    """Raised when outbound evidence must not be sent.

    ``reason`` distinguishes a real personal-data detection from a mere
    format restriction so the UI can explain the actual cause instead of
    implying that every visual file is unsafe.
    """

    def __init__(self, message, reason='mode_restriction', detected=None):
        super().__init__(message)
        self.reason = reason
        self.detected = list(detected or [])


@dataclass
class Redactor:
    mode: str = 'strict'
    aliases: dict = field(default_factory=dict)

    def add(self, text, alias):
        value = str(text or '').strip()
        if len(value) >= 3:
            self.aliases[value] = alias
            self.aliases[quote(value, safe='')] = alias
            self.aliases[quote_plus(value)] = alias

    def text(self, value):
        value = str(value or '')
        for original, alias in sorted(self.aliases.items(), key=lambda item: -len(item[0])):
            value = re.sub(r'(?<!\w)' + re.escape(original) + r'(?!\w)', lambda match: alias, value, flags=re.I)
        value = EMAIL.sub('[EMAIL]', value)
        value = PHONE.sub('[ТЕЛЕФОН]', value)

        def clean_url(match):
            try:
                parts = urlsplit(match[0])
                # Share identifiers and URL parameters may grant private access.
                if parts.fragment or re.search(r'/(?:s|d|folders|file)/', parts.path):
                    return '[ПРИВАТНЕ ПОСИЛАННЯ]'
                if parts.username or parts.password:
                    return '[ПРИВАТНЕ ПОСИЛАННЯ]'
                pairs = parse_qsl(parts.query, keep_blank_values=True)
                sensitive = re.compile(r'(?i)token|auth|password|secret|session|signature|api.?key|access.?key|^code$|email|phone|student|user|login|contact|address|birth|^name$')
                cleaned = [(key, '[ПРИХОВАНО]' if sensitive.search(key) else
                            ('[ПОСИЛАННЯ]' if value.startswith(('http://', 'https://')) else self.text(value)))
                           for key, value in pairs]
                if cleaned != pairs:
                    query = urlencode(cleaned)
                else:
                    query = parts.query
                return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ''))
            except ValueError:
                return '[ПОСИЛАННЯ]'
        return URL.sub(clean_url, value)

    def _detect_embedded_data(self, entry):
        """Return a list of real personal-data findings in image metadata.

        This inspects only embedded metadata (EXIF, PNG text chunks, comments),
        never the decoded pixels. Absence of findings does not mean the pixels
        are anonymous; the UI states that explicitly.
        """
        findings = []
        mime = str(entry.get('mime_type', ''))
        if not mime.startswith('image/'):
            return findings
        from PIL import Image
        from PIL.ExifTags import TAGS
        try:
            encoded = entry['data'].split(',', 1)[-1] if entry['data'].startswith('data:') else entry['data']
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
                exif = getattr(image, 'getexif', lambda: {})() or {}
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, str(tag_id))
                    text = str(value or '')
                    if not text:
                        continue
                    if tag in ('Artist', 'Copyright', 'XPAuthor', 'ImageDescription') and text.strip():
                        findings.append(f'EXIF {tag}')
                    elif EMAIL.search(text) or PHONE.search(text):
                        findings.append(f'EXIF {tag}: контактні дані')
                    elif tag == 'GPSInfo':
                        findings.append('EXIF GPS-координати')
                info = getattr(image, 'info', {}) or {}
                for key in ('Author', 'Comment', 'Description', 'Title', 'Software', 'XML:com.adobe.xmp'):
                    text = str(info.get(key) or '')
                    if text and (EMAIL.search(text) or PHONE.search(text) or key in ('Author', 'XML:com.adobe.xmp')):
                        findings.append(f'Метадані {key}')
        except Exception:
            # Unreadable metadata is not proof of personal data; the pixel
            # content is still handled below.
            return findings
        return sorted(set(findings))

    def media(self, media):
        output = []
        for item in media or []:
            entry = dict(item)
            entry['source'] = self.text(entry.get('source', ''))
            # Strip image metadata without altering decoded pixels. This does
            # not anonymize visible content; the UI explicitly explains that.
            if str(entry.get('mime_type', '')).startswith('image/'):
                from PIL import Image
                detected = self._detect_embedded_data(entry)
                if detected and self.mode == 'strict':
                    raise PrivacyBlocked(
                        'Суворе знеособлення: у метаданих зображення виявлено персональні дані ('
                        + ', '.join(detected) + '). Їх не надіслано. Усуньте ці дані або '
                        'дозвольте візуальну перевірку для цієї роботи.',
                        reason='detected_data', detected=detected)
                try:
                    encoded = entry['data'].split(',', 1)[-1] if entry['data'].startswith('data:') else entry['data']
                    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
                        if getattr(image, 'is_animated', False):
                            raise PrivacyBlocked('Анімовані зображення потребують попереднього знеособлення.',
                                                 reason='mode_restriction')
                        clean = Image.new('RGBA', image.size)
                        clean.paste(image.convert('RGBA'))
                        buffer = io.BytesIO()
                        clean.save(buffer, format='PNG')
                        entry.update(mime_type='image/png', data=base64.b64encode(buffer.getvalue()).decode())
                except PrivacyBlocked:
                    raise
                except Exception:
                    raise PrivacyBlocked('Не вдалося безпечно прочитати зображення; оригінал не надіслано.',
                                         reason='mode_restriction')
            output.append(entry)
        return output


def for_submission(submission, mode='strict'):
    redactor = Redactor(mode)
    redactor.add(submission.get_student_full_name(), '[УЧЕНЬ]')
    for name in ('last_name', 'first_name'):
        redactor.add(getattr(submission, name, ''), '[УЧЕНЬ]')
    authors = getattr(submission, 'group_authors', '') or ''
    for index, name in enumerate(re.split(r'[,;\n]+', authors), 1):
        redactor.add(name, f'[СПІВАВТОР_{index}]')
        for part in name.split():
            redactor.add(part, f'[СПІВАВТОР_{index}]')
    teacher = getattr(submission.assignment, 'teacher', None)
    if teacher:
        redactor.add(getattr(teacher, 'full_name', ''), '[УЧИТЕЛЬ]')
    files = list(submission.get_files()) + list(submission.assignment.files.all())
    previous = getattr(submission, 'previous_submission', None)
    if previous:
        files += list(previous.get_files())
    for index, file in enumerate(files, 1):
        name = getattr(file, 'original_name', '') or Path(str(getattr(getattr(file, 'file', None), 'name', ''))).name
        if name:
            # Alias the full filename before aliasing its name components.
            redactor.add(name, f'Матеріал_{index}{Path(name).suffix}')
    return redactor


def private_assessment(function):
    import inspect
    signature = inspect.signature(function)
    @functools.wraps(function)
    def wrapped(submission, *args, **kwargs):
        from .models import AISettings
        from django.conf import settings as django_settings
        from .ai_transport import evaluation_deadline
        from .ai_metrics import measure
        import time
        values = signature.bind_partial(submission, *args, **kwargs).arguments
        settings = values.get('ai_settings') or AISettings.get_solo()
        mode = settings.privacy_mode
        if values.get('ignore_strict_privacy') is True:
            from django.core.exceptions import PermissionDenied
            from types import SimpleNamespace
            from .permissions import can_manage_submission
            from .ai_progress import emit_event
            teacher = values.get('teacher')
            user = getattr(teacher, 'user', None)
            if not user or not user.is_active or not can_manage_submission(SimpleNamespace(user=user), submission):
                raise PermissionDenied('Разовий обхід суворого режиму доступний лише вчителю цієї роботи.')
            mode = 'redact'
            emit_event('privacy_override', 'Учитель дозволив візуальну перевірку без суворого режиму лише для цього запиту. Очищення тексту збережено.')
        token = _current.set(for_submission(submission, mode))
        deadline = evaluation_deadline.set(time.monotonic() + django_settings.AI_REQUEST_DEADLINE)
        try:
            with measure('evaluation'):
                return function(submission, *args, **kwargs)
        finally:
            evaluation_deadline.reset(deadline)
            _current.reset(token)
    return wrapped


def outbound(prompt, system, media):
    redactor = _current.get() or Redactor('redact')
    return redactor.text(prompt), redactor.text(system), redactor.media(media)


def diagnostic(text):
    return (_current.get() or Redactor('redact')).text(text)
