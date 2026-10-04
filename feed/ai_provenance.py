import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

GENERATORS = re.compile(
    r'stable[ _-]?diffusion|midjourney|dall[ ·_-]?e|comfyui|automatic1111|invokeai|adobe[ _-]?firefly|'
    r'firefly|chatgpt|openai|gpt[ _-]?[345o]|o1[ _-]?preview|o3[ _-]?mini|google[ _-]?imagen|imagen|'
    r'bing[ _-]?image[ _-]?creator|microsoft[ _-]?designer|copilot|microsoft[ _-]?copilot|'
    r'claude|anthropic|gemini|google[ _-]?ai|deepseek|perplexity|mistral|llama|grok|meta[ _-]?ai|'
    r'flux(\.1)?|ideogram|leonardo(\.ai)?|recraft(\.ai)?|civitai|fooocus|novelai|runway(ml)?|pika(\.art)?|sora|kling|seaart|'
    r'gamma(\.app)?|tome(\.app)?|canva(\s*ai)?|beautiful\.ai|decktopus|slidesai|popai|pitch\.com|slidesgo|wepik|'
    r'elevenlabs|suno|udio|heygen|synthesia|d-id|'
    r'trainedAlgorithmicMedia|compositeWithTrainedAlgorithmicMedia|c2pa|synthid',
    re.I
)
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp'}

AI_TEXT_MARKERS = re.compile(
    r'(?:'
    r'як\s+(?:штучний\s+інтелект|мовна\s+модель)|'
    r'as\s+an?\s+ai\s+language\s+model|as\s+an?\s+ai\b|'
    r'ось\s+(?:варіант\s+|текст\s+для\s+)?презентаці[їя]|'
    r'ось\s+слайди\s+(?:для|до)|here\s+(?:is\s+a\s+presentation|are\s+the\s+slides)|'
    r'звісно,?\s+ось|звісно!\s*ось|'
    r'згенеровано\s+(?:за\s+допомогою\s+)?(?:ш[іi]|нейромереж\w*|chatgpt|gamma|tome|canva)|'
    r'generated\s+by\s+(?:ai|chatgpt|openai|claude|gemini|deepseek|gamma|tome)|'
    r'created\s+with\s+(?:gamma|tome|beautiful\.ai|slidesai|decktopus|canva)|'
    r'made\s+with\s+(?:gamma|tome|canva)|'
    r'дизайн\s+створено\s+в\s+gamma'
    r')',
    re.I
)


def _image_signals(stream, location):
    from PIL import Image
    signals = []
    try:
        with Image.open(stream) as image:
            metadata = dict(image.info)
            # Read EXIF headers
            try:
                exif = image.getexif()
                for tag in (305, 270, 37510, 315, 33432):
                    value = exif.get(tag)
                    if value:
                        metadata[str(tag)] = value
            except Exception:
                pass
            # Read XMP
            try:
                xmp = None
                if hasattr(image, 'getxmp'):
                    xmp = image.getxmp()
                if not xmp:
                    xmp_raw = image.info.get('XML:com.adobe.xmp') or image.info.get('xmp')
                    if isinstance(xmp_raw, bytes):
                        xmp_raw = xmp_raw.decode('utf-8', errors='replace')
                    if isinstance(xmp_raw, str):
                        metadata['xmp'] = xmp_raw
                elif isinstance(xmp, dict):
                    metadata['xmp_dict'] = json.dumps(xmp, ensure_ascii=False)
            except Exception:
                pass

        for key, value in metadata.items():
            if isinstance(value, bytes):
                value = value[:8192].decode('utf-8', errors='replace')
            if not isinstance(value, str):
                continue
            value = value[:8192]
            match = GENERATORS.search(value)
            diffusion = key.lower() == 'parameters' and all(marker in value for marker in ('Steps:', 'Seed:', 'Sampler:'))
            if match or diffusion:
                signals.append({
                    'location': location,
                    'basis': 'metadata',
                    'observation': f'Поле метаданих {key}: {match.group(0) if match else "параметри генерації diffusion"}. Метадані можна змінити; походження не доведено.'
                })
    except Exception:
        pass
    return signals


def _openxml_signals(package, filename):
    signals = []
    names = package.namelist()
    for prop_file in ('docProps/app.xml', 'docProps/core.xml', 'docProps/custom.xml'):
        if prop_file in names:
            try:
                with package.open(prop_file) as stream:
                    content = stream.read().decode('utf-8', errors='replace')[:16384]
                    match = GENERATORS.search(content)
                    if match:
                        signals.append({
                            'location': f'{prop_file}',
                            'basis': 'metadata',
                            'observation': f'Метадані пакета {prop_file} містять назву {match.group(0)}. Це змінюване поле, а не доказ авторства.'
                        })
            except Exception:
                pass
    return signals


def _pptx_slide_signals(package):
    signals = []
    names = [n for n in package.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
    names.sort(key=lambda s: int(re.search(r'\d+', s).group(0)) if re.search(r'\d+', s) else 0)
    for idx, name in enumerate(names, 1):
        try:
            with package.open(name) as stream:
                raw_bytes = stream.read()
                text = ''
                try:
                    root = ET.fromstring(raw_bytes)
                    text = ' '.join(''.join(node.itertext()) for node in root.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}t'))
                except Exception:
                    text = raw_bytes.decode('utf-8', errors='replace')
                match = AI_TEXT_MARKERS.search(text)
                if match:
                    signals.append({
                        'location': f'Слайд {idx}',
                        'basis': 'text_analysis',
                        'observation': f'Слайд {idx} містить характерний маркер або преамбулу генеративного ШІ: «{match.group(0)}». Текст слайдів має ознаки чат-бота.'
                    })
        except Exception:
            pass
    return signals


def _pdf_signals(path):
    signals = []
    try:
        with open(path, 'rb') as f:
            header = f.read(65536).decode('latin1', errors='replace')
            try:
                f.seek(max(0, os.path.getsize(path) - 65536))
                trailer = f.read(65536).decode('latin1', errors='replace')
            except Exception:
                trailer = ''
        combined = header + '\n' + trailer
        match = GENERATORS.search(combined)
        if match:
            signals.append({
                'location': 'Метадані PDF',
                'basis': 'metadata',
                'observation': f'Метадані PDF документа містять назву генератора {match.group(0)}. Це змінюване поле, а не доказ авторства.'
            })
    except Exception:
        pass
    return signals


def file_provenance(path):
    from .ai_context import evidence_cache, file_cache_key
    key = file_cache_key(path, purpose='provenance', options='4.1.3')
    cached = evidence_cache().get(key)
    if cached is not None:
        return cached
    result = {'signals': [], 'limitations': ['Відсутність метаданих не доводить самостійне авторство. C2PA/SynthID та авторство не верифіковано.']}
    suffix = Path(path).suffix.lower()
    try:
        if suffix in IMAGE_SUFFIXES:
            result['signals'] = _image_signals(path, 'зображення')
        elif suffix in {'.docx', '.pptx', '.xlsx'}:
            with zipfile.ZipFile(path) as package:
                result['signals'].extend(_openxml_signals(package, Path(path).name))
                if suffix == '.pptx':
                    result['signals'].extend(_pptx_slide_signals(package))
                for entry in package.infolist():
                    if Path(entry.filename).suffix.lower() in IMAGE_SUFFIXES:
                        try:
                            with package.open(entry) as stream:
                                result['signals'].extend(_image_signals(stream, entry.filename))
                        except Exception:
                            result['limitations'].append(f'Не прочитано метадані зображення: {entry.filename}.')
        elif suffix in {'.odt', '.odp', '.ods'}:
            with zipfile.ZipFile(path) as package:
                if 'meta.xml' in package.namelist():
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
                if suffix == '.odp' and 'content.xml' in package.namelist():
                    try:
                        with package.open('content.xml') as stream:
                            content = stream.read().decode('utf-8', errors='replace')
                            match = AI_TEXT_MARKERS.search(content)
                            if match:
                                result['signals'].append({'location': 'Слайди презентації', 'basis': 'text_analysis',
                                    'observation': f'Вміст слайдів містить маркер генеративного ШІ: «{match.group(0)}».'})
                    except Exception:
                        pass
                for entry in package.infolist():
                    if Path(entry.filename).suffix.lower() in IMAGE_SUFFIXES:
                        try:
                            with package.open(entry) as stream:
                                result['signals'].extend(_image_signals(stream, entry.filename))
                        except Exception:
                            result['limitations'].append(f'Не прочитано метадані зображення: {entry.filename}.')
        elif suffix == '.sb3':
            with zipfile.ZipFile(path) as package:
                for entry in package.infolist():
                    if Path(entry.filename).suffix.lower() in IMAGE_SUFFIXES:
                        try:
                            with package.open(entry) as stream:
                                result['signals'].extend(_image_signals(stream, entry.filename))
                        except Exception:
                            pass
        elif suffix == '.pdf':
            result['signals'].extend(_pdf_signals(path))
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

    raw_flag = result.get('ai_generated_detected')
    legacy_detected = raw_flag is True or raw_flag == 1 or str(raw_flag).lower() == 'true'

    raw_ai_percent = result.get('ai_generated_percent')
    ai_percent = None
    if raw_ai_percent is not None:
        try:
            clean_p_str = str(raw_ai_percent).replace('%', '').strip()
            ai_percent = int(float(clean_p_str))
            ai_percent = max(0, min(100, ai_percent))
        except (ValueError, TypeError):
            ai_percent = None

    tol = 25 if tolerance_percent is None else tolerance_percent
    percent_exceeds = (ai_percent is not None and ai_percent > tol)

    if analysis.get('status') in {'none', 'unknown'} and not legacy_detected and not percent_exceeds:
        evidence = []

    for file in provenance:
        evidence.extend(dict(signal, source=file['source']) for signal in file.get('signals', []))

    details = str(result.get('ai_generated_details') or '').strip()
    if details.lower() in {'none', 'null', 'false', 'ok', 'none.', 'null.', 'лише ознаки, не доведений факт'}:
        details = ''

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

    # If details exist and legacy_detected or percent_exceeds, synthesize evidence item
    if details and not evidence and (legacy_detected or percent_exceeds):
        evidence = [{'observation': details, 'basis': 'text_analysis'}]

    # Provenance metadata signals (e.g. diffusion parameters, Gamma/Canva app tag)
    has_metadata_signal = any(item.get('basis') == 'metadata' for item in evidence)
    if has_metadata_signal and (ai_percent is None or ai_percent < 80):
        ai_percent = max(ai_percent or 0, 90)

    # Content signals from slide inspection / visual
    has_content_signal = any(item.get('basis') in {'text_analysis', 'visual'} for item in evidence)
    if has_content_signal and (ai_percent is None or ai_percent < 70) and any(file.get('signals') for file in provenance):
        ai_percent = max(ai_percent or 0, 80)

    # Determine detection against tolerance
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

