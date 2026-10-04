"""Inspectable AI-origin signals; never an authorship verdict or a style-based penalty."""
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

GENERATORS = re.compile(r'stable[ _-]?diffusion|midjourney|dall[ ·_-]?e|comfyui|automatic1111|invokeai|adobe firefly|chatgpt|openai|gpt[ _-]?image|google[ _-]?imagen', re.I)
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff'}


def _image_signals(stream, location):
    from PIL import Image
    with Image.open(stream) as image:
        metadata = dict(image.info)
        # Read headers, not pixels. EXIF Software / Description / UserComment only.
        for tag in (305, 270, 37510):
            value = image.getexif().get(tag)
            if value:
                metadata[str(tag)] = value
    signals = []
    for key, value in metadata.items():
        if isinstance(value, bytes):
            value = value[:8192].decode('utf-8', errors='replace')
        if not isinstance(value, str):
            continue
        value = value[:8192]
        match = GENERATORS.search(value)
        diffusion = key.lower() == 'parameters' and all(marker in value for marker in ('Steps:', 'Seed:', 'Sampler:'))
        if match or diffusion:
            signals.append({'location': location, 'basis': 'metadata',
                            'observation': f'Поле метаданих {key}: {match.group(0) if match else "параметри генерації diffusion"}. Метадані можна змінити; походження не доведено.'})
    return signals


def file_provenance(path):
    from .ai_context import evidence_cache, file_cache_key
    key = file_cache_key(path, purpose='provenance', options='4.1.1')
    cached = evidence_cache().get(key)
    if cached is not None:
        return cached
    result = {'signals': [], 'limitations': ['Відсутність метаданих не доводить самостійне авторство. C2PA/SynthID та авторство не верифіковано.']}
    suffix = Path(path).suffix.lower()
    try:
        if suffix in IMAGE_SUFFIXES:
            result['signals'] = _image_signals(path, 'зображення')
        elif suffix in {'.docx', '.pptx', '.xlsx', '.sb3', '.odt', '.odp', '.ods'}:
            with zipfile.ZipFile(path) as package:
                if suffix in {'.odt', '.odp', '.ods'} and 'meta.xml' in package.namelist():
                    with package.open('meta.xml') as stream:
                        root = ET.parse(stream).getroot()
                    for element in root.iter():
                        field = element.tag.rsplit('}', 1)[-1]
                        if field in {'generator', 'description', 'keyword', 'user-defined'}:
                            value = ''.join(element.itertext())[:8192]
                            match = GENERATORS.search(value)
                            if match:
                                result['signals'].append({'location': 'meta.xml/' + field, 'basis': 'metadata',
                                    'observation': f'Метадані містять назву {match.group(0)}. Це змінюване поле, а не доказ авторства.'})
                for entry in package.infolist():
                    if Path(entry.filename).suffix.lower() in IMAGE_SUFFIXES:
                        try:
                            with package.open(entry) as stream:
                                result['signals'].extend(_image_signals(stream, entry.filename))
                        except Exception:
                            result['limitations'].append(f'Не прочитано метадані зображення: {entry.filename}.')
        else:
            result['limitations'].append('Доступні текстові/візуальні ознаки в прочитаному вмісті; походження цього формату не встановлено.')
    except Exception:
        result['limitations'].append('Метадані файлу недоступні для перевірки.')
    evidence_cache().set(key, result, 86400 * 7)
    return result


def submission_provenance(submission):
    files = list(submission.files.all()) or ([submission] if submission.file else [])
    output = []
    for file in files:
        if file.file and Path(file.file.path).is_file():
            output.append({'source': getattr(file, 'original_name', '') or Path(file.file.name).name, **file_provenance(file.file.path)})
    return output


def normalize_authorship(result, allowed, provenance=(), tolerance_percent=25):
    """Calibrate AI detection percentage, tolerance threshold, and inspectable evidence."""
    analysis = result.get('ai_authorship_analysis') or {}
    analysis = analysis if isinstance(analysis, dict) else {}
    evidence = analysis.get('evidence') or []
    evidence = [item for item in evidence if isinstance(item, dict) and item.get('observation')] if isinstance(evidence, list) else []
    if analysis.get('status') in {'none', 'unknown'}:
        evidence = []
    for file in provenance:
        evidence.extend(dict(signal, source=file['source']) for signal in file.get('signals', []))
    details = str(result.get('ai_generated_details') or '').strip()
    if details.lower() in {'none', 'null', 'false', 'ok', 'none.', 'null.', 'лише ознаки, не доведений факт'}:
        details = ''

    raw_ai_percent = result.get('ai_generated_percent')
    ai_percent = None
    if raw_ai_percent is not None:
        try:
            clean_p_str = str(raw_ai_percent).replace('%', '').strip()
            ai_percent = int(float(clean_p_str))
            ai_percent = max(0, min(100, ai_percent))
        except (ValueError, TypeError):
            ai_percent = None

    note = ('ШІ дозволено вчителем: використання інструмента саме по собі не знижує бал.' if allowed else
            'ШІ не дозволено вчителем. Підозра потребує уточнення; самостійність оцінюється за критеріями завдання, без автоматичного штрафу за стиль чи метадані.')

    # Empty claim without details or evidence is uncalibrated and not proof
    if not details and not evidence and not any(file.get('signals') for file in provenance):
        lines = ['Походження роботи не встановлено. Виразних ознак ШІ не знайдено, але це не підтверджує самостійність. ' + note]
        if not allowed:
            lines.append('Бал оцінює наданий результат. Перед остаточним рішенням учитель може попросити пояснити кроки або показати чернетку.')
        return {
            'ai_generated_detected': False,
            'ai_generated_percent': None,
            'ai_generated_confidence': 'none',
            'ai_generated_details': '\n'.join(lines)
        }

    raw_flag = result.get('ai_generated_detected')
    legacy_detected = raw_flag is True or raw_flag == 1 or str(raw_flag).lower() == 'true'
    if details and not evidence and legacy_detected and analysis.get('status') not in {'none', 'unknown'}:
        evidence = [{'observation': details, 'basis': 'text_analysis'}]

    # Provenance metadata signals (e.g. diffusion parameters)
    has_metadata_signal = any(item.get('basis') == 'metadata' for item in evidence)
    if has_metadata_signal and (ai_percent is None or ai_percent < 80):
        ai_percent = max(ai_percent or 0, 90)

    # Determine detection against tolerance
    tol = 25 if tolerance_percent is None else tolerance_percent
    if ai_percent is not None:
        if ai_percent > tol:
            detected = True
        else:
            detected = False
    else:
        detected = legacy_detected or bool(evidence)

    lines = []
    for item in evidence:
        source = ' — '.join(str(item.get(k) or '') for k in ('source', 'location') if item.get(k))
        line = (source + ': ' if source else '') + str(item['observation'])
        if line not in lines:
            lines.append(line)
    if details and details not in lines and not any(details in l for l in lines):
        lines.insert(0, details)

    basis = {item.get('basis') for item in evidence}
    raw_conf = str(result.get('ai_generated_confidence') or '').lower().strip()
    if raw_conf in {'high', 'medium', 'low'}:
        confidence = raw_conf if detected else 'none'
    elif basis & {'metadata', 'self_disclosure'}:
        confidence = 'high' if detected else 'none'
    elif ai_percent is not None and ai_percent >= 70:
        confidence = 'high' if detected else 'none'
    elif ai_percent is not None and ai_percent >= 40:
        confidence = 'medium' if detected else 'none'
    elif detected:
        confidence = 'low'
    else:
        confidence = 'none'

    note = ('ШІ дозволено вчителем: використання інструмента саме по собі не знижує бал.' if allowed else
            'ШІ не дозволено вчителем. Підозра потребує уточнення; самостійність оцінюється за критеріями завдання, без автоматичного штрафу за стиль чи метадані.')
    if detected:
        lines.extend(['Це ознаки, а не доведений факт авторства. ' + note,
                      'Для уточнення поясни свої кроки, покажи проміжні результати та вкажи використані інструменти.'])
    else:
        lines = ['Походження роботи не встановлено. Виразних ознак ШІ не знайдено, але це не підтверджує самостійність. ' + note]
        if not allowed:
            lines.append('Бал оцінює наданий результат. Перед остаточним рішенням учитель може попросити пояснити кроки або показати чернетку.')
    return {'ai_generated_detected': detected, 'ai_generated_percent': ai_percent,
            'ai_generated_confidence': confidence, 'ai_generated_details': '\n'.join(lines)}
