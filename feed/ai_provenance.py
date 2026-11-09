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
    r'gamma(\.app)?|tome(\.app)?|canva(\s*magic\s*design|\s*ai|\s*presentation)?|beautiful\.ai|'
    r'decktopus(\.com)?|slidesai(\.io)?|popai|pitch(\.com)?|slidesgo|wepik|presentations\.ai|plus\s*ai|magic\s*slides|'
    r'elevenlabs|suno|udio|heygen|synthesia|d-id|'
    r'чатбот\w*|chatbot\w*|'
    r'trainedAlgorithmicMedia|compositeWithTrainedAlgorithmicMedia|c2pa|synthid',
    re.I
)
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp'}

AI_TEXT_MARKERS = re.compile(
    r'(?:'
    r'як\s+(?:штучний\s+інтелект|мовна\s+модель)|'
    r'як\s+ai\b|as\s+an?\s+ai\s+language\s+model|as\s+an?\s+ai\b|'
    r'ось\s+(?:варіант\s+|текст\s+для\s+|план\s+|макет\s+|структура\s+|конспект\s+|чернетка\s+)?(?:презентаці[їя]|слайдів|доповіді)|'
    r'ось\s+(?:готові\s+)?слайди\s+(?:для|до)|'
    r'ось\s+(?:готова\s+)?презентація|'
    r'here\s+(?:is\s+a\s+presentation|are\s+the\s+slides|is\s+the\s+outline)|'
    r'звісно[!,.]?\s*(?:ось|я\s+можу|нижче|наведено)|'
    r'безумовно,?\s+ось|радий\s+допомогти|'
    r'сподіваюсь,?\s+це\s+допоможе|надіюсь,?\s+це\s+допоможе|hope\s+this\s+helps|'
    r'я\s+(?:створив|підготував|згенерував)\s+(?:для\s+вас\s+)?(?:презентацію|слайди)|'
    r'(?:підготовлено|створено|згенеровано|зроблено)\s+(?:за\s+допомогою\s+|завдяки\s+|в\s+)?(?:ш[іi]|штучн\w*\s+інтелект\w*|нейромереж\w*|chatgpt|gamma|tome|canva|slidesai|decktopus|beautiful\.ai)|'
    r'(?:дизайн|оформлення)\s+(?:створено|згенеровано)\s+(?:в|за\s+допомогою)\s+(?:gamma|tome|canva)|'
    r'generated\s+by\s+(?:ai|chatgpt|openai|claude|gemini|deepseek|gamma|tome|canva)|'
    r'created\s+with\s+(?:gamma|tome|beautiful\.ai|slidesai|decktopus|canva|ai)|'
    r'made\s+with\s+(?:gamma|tome|canva|slidesai)|'
    r'designed\s+with\s+(?:gamma|canva|tome|slidesai)|'
    r'powered\s+by\s+(?:ai|gamma|tome|chatgpt|openai)|'
    r'\[\s*(?:зображення|ілюстрація|фото|картинка|опис\s+зображення|image|photo|illustration|prompt)\s*:[^\]\r\n]+\]|'
    r'(?:\*\*)?(?:слайд|slide)\s+\d+\s*(?:\:|\-|\—|\.)|'
    r'\[(?:вставте|додайте|замініть)\s+[^\]\r\n]+\]|'
    r'замініть\s+(?:цей\s+текст|плейсхолдер)|'
    r'вставте\s+(?:сюди\s+)?(?:зображення|фото|ілюстрацію)|'
    r'дайте\s+відповідь\s+на\s+запитання|'
    r'порівняйте\s+(?:їхню|вашу)\s+поведінку|'
    r'виконайте\s+(?:наступні|такі)\s+(?:дії|кроки)|'
    r'ось\s+(?:алгоритм|інструкція|покроковий\s+план)|'
    r'розглянемо\s+детальніше|'
    r'підсумовуючи\s+(?:вищезазначене|викладене)|'
    r'у\s+підсумку\s+варто\s+зазначити'
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
    signals = _office_metadata_signals(package, filename)
    names = package.namelist()
    prop_files = [
        f for f in names
        if f in ('docProps/app.xml', 'docProps/core.xml', 'docProps/custom.xml', 'ppt/presentation.xml')
        or ('commentAuthors' in f or 'comment' in f or 'authors.xml' in f)
    ]
    for prop_file in prop_files:
        try:
            with package.open(prop_file) as stream:
                content = stream.read().decode('utf-8', errors='replace')[:65536]
                clean_text = re.sub(r'<[^>]+>', ' ', content)
                match = GENERATORS.search(clean_text) or GENERATORS.search(content)
                if match:
                    signals.append({
                        'location': f'{prop_file}',
                        'basis': 'metadata',
                        'observation': f'Метадані пакета {prop_file} містять назву {match.group(0)}. Це змінюване поле, а не доказ авторства.'
                    })
                match_txt = AI_TEXT_MARKERS.search(clean_text)
                if match_txt:
                    signals.append({
                        'location': f'{prop_file}',
                        'basis': 'text_analysis',
                        'observation': f'Службовий розділ {prop_file} містить маркер ШІ: «{match_txt.group(0)}».'
                    })
        except Exception:
            pass

    # Word document body check
    if 'word/document.xml' in names:
        try:
            with package.open('word/document.xml') as stream:
                raw_bytes = stream.read()
            try:
                root = ET.fromstring(raw_bytes)
                text = ' '.join(''.join(node.itertext()) for node in root.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
            except Exception:
                clean = re.sub(r'<[^>]+>', ' ', raw_bytes.decode('utf-8', errors='replace'))
                text = ' '.join(clean.split())
            match_marker = AI_TEXT_MARKERS.search(text)
            if match_marker:
                signals.append({
                    'location': 'Вміст документа Word (.docx)',
                    'basis': 'text_analysis',
                    'observation': f'Текст документа Word містить характерний маркер або директиву чат-бота: «{match_marker.group(0)}». Текст має ознаки генеративного ШІ.'
                })
            else:
                match_gen = GENERATORS.search(text)
                if match_gen:
                    signals.append({
                        'location': 'Вміст документа Word (.docx)',
                        'basis': 'text_analysis',
                        'observation': f'Текст документа Word містить пряму згадку генератора ШІ: «{match_gen.group(0)}».'
                    })
        except Exception:
            pass

    # Word TotalTime editing duration check
    if 'docProps/app.xml' in names:
        try:
            with package.open('docProps/app.xml') as stream:
                app_content = stream.read().decode('utf-8', errors='replace')
            m_time = re.search(r'<TotalTime>(\d+)</TotalTime>', app_content)
            m_words = re.search(r'<Words>(\d+)</Words>', app_content)
            if m_time and m_words:
                total_time = int(m_time.group(1))
                words = int(m_words.group(1))
                if total_time <= 1 and words >= 100:
                    signals.append({
                        'location': 'docProps/app.xml/TotalTime',
                        'basis': 'metadata',
                        'observation': f'Загальний час редагування документа Word становить лише {total_time} хв для {words} слів, що свідчить про швидку вставку стороннього тексту.'
                    })
        except Exception:
            pass

    return signals


def _pptx_slide_signals(package):
    signals = []
    # 1. Slide contents
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
                    clean = re.sub(r'<[^>]+>', ' ', raw_bytes.decode('utf-8', errors='replace'))
                    text = ' '.join(clean.split())

                match_marker = AI_TEXT_MARKERS.search(text)
                if match_marker:
                    signals.append({
                        'location': f'Слайд {idx}',
                        'basis': 'text_analysis',
                        'observation': f'Слайд {idx} містить характерний маркер або преамбулу генеративного ШІ: «{match_marker.group(0)}». Текст слайдів має ознаки чат-бота.'
                    })
                else:
                    match_gen = GENERATORS.search(text)
                    if match_gen:
                        signals.append({
                            'location': f'Слайд {idx}',
                            'basis': 'text_analysis',
                            'observation': f'Слайд {idx} містить назву інструмента генеративного ШІ: «{match_gen.group(0)}». Текст слайдів містить згадки або водяні знаки ШІ-сервісу.'
                        })
        except Exception:
            pass

    # 2. Speaker notes (ppt/notesSlides/notesSlide*.xml)
    notes_names = [n for n in package.namelist() if n.startswith('ppt/notesSlides/notesSlide') and n.endswith('.xml')]
    notes_names.sort(key=lambda s: int(re.search(r'\d+', s).group(0)) if re.search(r'\d+', s) else 0)
    for idx, name in enumerate(notes_names, 1):
        try:
            with package.open(name) as stream:
                raw_bytes = stream.read()
                text = ''
                try:
                    root = ET.fromstring(raw_bytes)
                    text = ' '.join(''.join(node.itertext()) for node in root.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}t'))
                except Exception:
                    clean = re.sub(r'<[^>]+>', ' ', raw_bytes.decode('utf-8', errors='replace'))
                    text = ' '.join(clean.split())

                match_marker = AI_TEXT_MARKERS.search(text)
                if match_marker:
                    signals.append({
                        'location': f'Нотатки доповідача (Слайд {idx})',
                        'basis': 'text_analysis',
                        'observation': f'Нотатки доповідача до слайду {idx} містять маркер генеративного ШІ: «{match_marker.group(0)}». Залишено підказки або запити чат-бота.'
                    })
                else:
                    match_gen = GENERATORS.search(text)
                    if match_gen:
                        signals.append({
                            'location': f'Нотатки доповідача (Слайд {idx})',
                            'basis': 'text_analysis',
                            'observation': f'Нотатки доповідача до слайду {idx} містять назву генератора ШІ: «{match_gen.group(0)}».'
                        })
        except Exception:
            pass

    return signals


def _odp_signals(package):
    signals = []
    # 1. Check meta.xml
    if 'meta.xml' in package.namelist():
        try:
            with package.open('meta.xml') as stream:
                root = ET.parse(stream).getroot()
            for element in root.iter():
                field = element.tag.rsplit('}', 1)[-1]
                if field in {'generator', 'description', 'keyword', 'user-defined', 'title', 'creator'}:
                    value = ''.join(element.itertext())[:8192]
                    match = GENERATORS.search(value)
                    if match:
                        signals.append({
                            'location': 'meta.xml/' + field,
                            'basis': 'metadata',
                            'observation': f'Метадані містять назву {match.group(0)}. Це змінюване поле, а не доказ авторства.'
                        })
        except Exception:
            pass

    # 2. Check content.xml per-slide
    if 'content.xml' in package.namelist():
        try:
            with package.open('content.xml') as stream:
                xml_bytes = stream.read()
            try:
                root = ET.fromstring(xml_bytes)
                pages = [elem for elem in root.iter() if elem.tag.endswith('page')]
            except Exception:
                pages = []

            if pages:
                for idx, page in enumerate(pages, 1):
                    p_texts = []
                    notes_texts = []
                    for elem in page.iter():
                        tag = elem.tag.rsplit('}', 1)[-1]
                        if tag in ('p', 'h', 'span'):
                            t = ''.join(elem.itertext()).strip()
                            if t:
                                p_texts.append(t)
                        elif tag == 'notes':
                            nt = ' '.join(''.join(c.itertext()).strip() for c in elem.iter() if c.tag.rsplit('}', 1)[-1] in ('p', 'h', 'span'))
                            if nt:
                                notes_texts.append(nt)

                    slide_body = ' '.join(p_texts)
                    match_marker = AI_TEXT_MARKERS.search(slide_body)
                    if match_marker:
                        signals.append({
                            'location': f'Слайд {idx}',
                            'basis': 'text_analysis',
                            'observation': f'Слайд {idx} презентації ODP містить маркер генеративного ШІ: «{match_marker.group(0)}». Текст слайдів має ознаки чат-бота.'
                        })
                    else:
                        match_gen = GENERATORS.search(slide_body)
                        if match_gen:
                            signals.append({
                                'location': f'Слайд {idx}',
                                'basis': 'text_analysis',
                                'observation': f'Слайд {idx} презентації ODP містить назву генератора ШІ: «{match_gen.group(0)}».'
                            })

                    if notes_texts:
                        notes_body = ' '.join(notes_texts)
                        match_nm = AI_TEXT_MARKERS.search(notes_body) or GENERATORS.search(notes_body)
                        if match_nm:
                            signals.append({
                                'location': f'Нотатки доповідача (Слайд {idx})',
                                'basis': 'text_analysis',
                                'observation': f'Нотатки доповідача ODP містять маркер або генератор ШІ: «{match_nm.group(0)}».'
                            })
            else:
                clean_content = re.sub(r'<[^>]+>', ' ', xml_bytes.decode('utf-8', errors='replace'))
                match = AI_TEXT_MARKERS.search(clean_content) or GENERATORS.search(clean_content)
                if match:
                    signals.append({
                        'location': 'Слайди презентації',
                        'basis': 'text_analysis',
                        'observation': f'Вміст слайдів містить маркер генеративного ШІ: «{match.group(0)}».'
                    })
        except Exception:
            pass

    return signals


def _parse_iso_duration(val):
    """Parse ISO 8601 duration like PT6S, PT1M20S, PT1H2M3S into seconds."""
    if not val or not isinstance(val, str):
        return None
    m = re.match(r'^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$', val.strip())
    if not m:
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = float(m.group(4) or 0)
    return int(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def _odt_signals(package):
    signals = []
    # 1. Check meta.xml for generators and editing duration
    if 'meta.xml' in package.namelist():
        try:
            with package.open('meta.xml') as stream:
                meta_bytes = stream.read()
            try:
                root = ET.fromstring(meta_bytes)
                for element in root.iter():
                    field = element.tag.rsplit('}', 1)[-1]
                    if field in {'generator', 'description', 'keyword', 'user-defined', 'title', 'creator'}:
                        value = ''.join(element.itertext())[:8192]
                        match = GENERATORS.search(value)
                        if match:
                            signals.append({
                                'location': 'meta.xml/' + field,
                                'basis': 'metadata',
                                'observation': f'Метадані містять назву {match.group(0)}. Це змінюване поле, а не доказ авторства.'
                            })
                # Check editing duration and word count in ODT
                dur_elem = None
                stat_elem = None
                for elem in root.iter():
                    t = elem.tag.rsplit('}', 1)[-1]
                    if t == 'editing-duration':
                        dur_elem = elem
                    elif t == 'document-statistic':
                        stat_elem = elem

                dur_str = ''.join(dur_elem.itertext()).strip() if dur_elem is not None else ''
                words = 0
                if stat_elem is not None:
                    for k, v in stat_elem.attrib.items():
                        if k.rsplit('}', 1)[-1] == 'word-count':
                            try:
                                words = int(v)
                            except (ValueError, TypeError):
                                pass

                if dur_str:
                    dur_sec = _parse_iso_duration(dur_str)
                    if dur_sec is not None and dur_sec <= 90 and words >= 80:
                        signals.append({
                            'location': 'meta.xml/editing-duration',
                            'basis': 'metadata',
                            'observation': f'Загальний час редагування документа ODT становить лише {dur_sec} с для {words} слів, що свідчить про швидку вставку згенерованого або стороннього тексту.'
                        })
            except Exception:
                pass
        except Exception:
            pass

    # 2. Check content.xml for AI text markers and generator mentions
    if 'content.xml' in package.namelist():
        try:
            with package.open('content.xml') as stream:
                content_bytes = stream.read()
            try:
                root = ET.fromstring(content_bytes)
                p_texts = [''.join(elem.itertext()).strip() for elem in root.iter() if elem.tag.rsplit('}', 1)[-1] in ('p', 'h', 'span')]
                text = ' '.join(t for t in p_texts if t)
            except Exception:
                clean = re.sub(r'<[^>]+>', ' ', content_bytes.decode('utf-8', errors='replace'))
                text = ' '.join(clean.split())

            match_marker = AI_TEXT_MARKERS.search(text)
            if match_marker:
                signals.append({
                    'location': 'Вміст документа ODT',
                    'basis': 'text_analysis',
                    'observation': f'Текст документа ODT містить характерний маркер або директиву чат-бота: «{match_marker.group(0)}». Текст має ознаки генеративного ШІ.'
                })
            else:
                match_gen = GENERATORS.search(text)
                if match_gen:
                    signals.append({
                        'location': 'Вміст документа ODT',
                        'basis': 'text_analysis',
                        'observation': f'Текст документа ODT містить пряму згадку генератора ШІ: «{match_gen.group(0)}».'
                    })
        except Exception:
            pass

    return signals


def _binary_ppt_signals(path):
    signals = []
    # 1. Try LibreOffice conversion to PPTX for deep inspection
    try:
        from .document_parsers import convert_ppt_to_pptx
        converted = convert_ppt_to_pptx(str(path))
        if converted and os.path.exists(converted) and zipfile.is_zipfile(converted):
            with zipfile.ZipFile(converted) as package:
                signals.extend(_openxml_signals(package, Path(path).name))
                signals.extend(_pptx_slide_signals(package))
                for entry in package.infolist():
                    if Path(entry.filename).suffix.lower() in IMAGE_SUFFIXES:
                        try:
                            with package.open(entry) as stream:
                                signals.extend(_image_signals(stream, entry.filename))
                        except Exception:
                            pass
            return signals
    except Exception:
        pass

    # 2. Binary text scanning for UTF-16LE, CP1251, UTF-8 markers
    try:
        with open(path, 'rb') as f:
            raw_bytes = f.read(1024 * 1024 * 8)

        for enc in ('utf-16le', 'cp1251', 'utf-8', 'latin1'):
            try:
                decoded = raw_bytes.decode(enc, errors='ignore')
                m_gen = GENERATORS.search(decoded)
                if m_gen:
                    signals.append({
                        'location': 'Метадані / вміст .ppt',
                        'basis': 'text_analysis',
                        'observation': f'Бінарний файл презентації .ppt містить згадку генератора {m_gen.group(0)}.'
                    })
                    break
                m_txt = AI_TEXT_MARKERS.search(decoded)
                if m_txt:
                    signals.append({
                        'location': 'Вміст слайдів .ppt',
                        'basis': 'text_analysis',
                        'observation': f'Бінарний файл презентації .ppt містить характерний маркер ШІ: «{m_txt.group(0)}».'
                    })
                    break
            except Exception:
                pass
    except Exception:
        pass

    return signals


def _c2pa_signals(path):
    """Verify a Content Credentials manifest; a mere 'c2pa' string is not proof."""
    try:
        import c2pa
    except Exception:
        return [], ['C2PA-перевірка недоступна: бібліотеку не встановлено.']
    try:
        reader = c2pa.Reader.from_file(str(path))
        store = json.loads(reader.json())
    except Exception:
        return [], ['C2PA-маніфест не знайдено або підпис не валідний.']
    manifests = store.get('manifests') if isinstance(store, dict) else None
    active_id = store.get('active_manifest') if isinstance(store, dict) else None
    manifest = (manifests or {}).get(active_id) if isinstance(manifests, dict) else None
    if not isinstance(manifest, dict):
        return [], ['C2PA-маніфест не знайдено.']
    generator = manifest.get('claim_generator') or manifest.get('claim_generator_info') or ''
    if isinstance(generator, (dict, list)):
        generator = json.dumps(generator, ensure_ascii=False)[:200]
    ai_action = False
    for assertion in manifest.get('assertions') or []:
        data = assertion.get('data') if isinstance(assertion, dict) else None
        if not isinstance(data, dict):
            continue
        for action in data.get('actions') or []:
            if not isinstance(action, dict):
                continue
            source_type = str(action.get('digitalSourceType') or '')
            if 'trainedAlgorithmicMedia' in source_type:
                ai_action = True
    if ai_action:
        return [{
            'location': 'C2PA маніфест',
            'basis': 'c2pa',
            'strength': 'strong',
            'observation': f'Валідний підписаний C2PA-маніфест вказує на генеративне створення (claim_generator: {generator or "—"}).',
        }], []
    return [], ['C2PA-маніфест валідний, але не вказує на генеративне створення.']


def _office_metadata_signals(package, filename):
    """Inspect core/app properties beyond a raw generator-name search."""
    signals = []
    props = {}
    for name in ('docProps/core.xml', 'docProps/app.xml'):
        if name not in package.namelist():
            continue
        try:
            with package.open(name) as stream:
                text = stream.read().decode('utf-8', errors='replace')
            for field in ('creator', 'lastModifiedBy', 'revision', 'created', 'modified',
                          'Application', 'Company', 'Template', 'TotalTime', 'Words', 'Pages', 'Slides'):
                match = re.search(rf'<[^>]*{field}[^>]*>([^<]*)</', text)
                if match:
                    props[field] = match.group(1).strip()
        except Exception:
            continue
    application = ' '.join(str(props.get(k) or '') for k in ('Application', 'Company', 'Template'))
    match = GENERATORS.search(application)
    if match:
        signals.append({
            'location': 'docProps/app.xml',
            'basis': 'metadata',
            'strength': 'medium',
            'observation': f'Застосунок/шаблон документа згадує {match.group(0)}. Це змінюване поле, але разом з іншими ознаками підсилює підозру.',
        })
    creator = props.get('creator') or ''
    editor = props.get('lastModifiedBy') or ''
    if creator and editor and creator.strip().casefold() != editor.strip().casefold():
        signals.append({
            'location': 'docProps/core.xml',
            'basis': 'metadata',
            'strength': 'weak',
            'observation': f'Автор створення ({creator}) і останній редактор ({editor}) різні. Це може бути спільна робота або сторонній текст, але не доказ.',
        })
    return signals


def _pdf_signals(path):
    signals = []
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        metadata = reader.metadata or {}
        for key in ('/Producer', '/Creator', '/Author', '/Title'):
            value = str(metadata.get(key) or '')
            match = GENERATORS.search(value)
            if match:
                signals.append({
                    'location': f'PDF {key}',
                    'basis': 'metadata',
                    'strength': 'medium',
                    'observation': f'Поле {key} PDF згадує {match.group(0)}. Це змінюване поле, а не доказ авторства.',
                })
        xmp = ''
        try:
            xmp = str(reader.xmp_metadata) if reader.xmp_metadata else ''
        except Exception:
            pass
        match = GENERATORS.search(xmp)
        if match:
            signals.append({
                'location': 'PDF XMP',
                'basis': 'metadata',
                'strength': 'medium',
                'observation': f'XMP-метадані PDF згадують {match.group(0)}. Це змінюване поле.',
            })
    except Exception:
        pass
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
    key = file_cache_key(path, purpose='provenance', options='2026-11-09-states')
    cached = evidence_cache().get(key)
    if cached is not None:
        return cached
    result = {'signals': [], 'checked': False, 'limitations': ['Відсутність метаданих не доводить самостійне авторство. C2PA/SynthID та авторство не верифіковано.']}
    suffix = Path(path).suffix.lower()
    try:
        if suffix in IMAGE_SUFFIXES:
            result['checked'] = True
            result['signals'] = _image_signals(path, 'зображення')
            c2pa_signals, c2pa_limits = _c2pa_signals(path)
            result['signals'].extend(c2pa_signals)
            result['limitations'].extend(c2pa_limits)
        elif suffix in {'.docx', '.pptx', '.xlsx', '.pptm', '.ppsx', '.potx'}:
            result['checked'] = True
            with zipfile.ZipFile(path) as package:
                result['signals'].extend(_openxml_signals(package, Path(path).name))
                if suffix in {'.pptx', '.pptm', '.ppsx', '.potx'}:
                    result['signals'].extend(_pptx_slide_signals(package))
                for entry in package.infolist():
                    if Path(entry.filename).suffix.lower() in IMAGE_SUFFIXES:
                        try:
                            with package.open(entry) as stream:
                                result['signals'].extend(_image_signals(stream, entry.filename))
                        except Exception:
                            result['limitations'].append(f'Не прочитано метадані зображення: {entry.filename}.')
        elif suffix in {'.odt', '.odp', '.ods'}:
            result['checked'] = True
            with zipfile.ZipFile(path) as package:
                if suffix == '.odp':
                    result['signals'].extend(_odp_signals(package))
                elif suffix == '.odt':
                    result['signals'].extend(_odt_signals(package))
                elif 'meta.xml' in package.namelist():
                    with package.open('meta.xml') as stream:
                        root = ET.parse(stream).getroot()
                    for element in root.iter():
                        field = element.tag.rsplit('}', 1)[-1]
                        if field in {'generator', 'description', 'keyword', 'user-defined', 'title', 'creator'}:
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
        elif suffix in {'.ppt', '.pps'}:
            result['checked'] = True
            result['signals'].extend(_binary_ppt_signals(path))
        elif suffix == '.sb3':
            result['checked'] = True
            with zipfile.ZipFile(path) as package:
                for entry in package.infolist():
                    if Path(entry.filename).suffix.lower() in IMAGE_SUFFIXES:
                        try:
                            with package.open(entry) as stream:
                                result['signals'].extend(_image_signals(stream, entry.filename))
                        except Exception:
                            pass
        elif suffix == '.pdf':
            result['checked'] = True
            result['signals'].extend(_pdf_signals(path))
            c2pa_signals, c2pa_limits = _c2pa_signals(path)
            result['signals'].extend(c2pa_signals)
            result['limitations'].extend(c2pa_limits)
        else:
            result['limitations'].append('Доступні текстові/візуальні ознаки в прочитаному вмісті; походження цього формату не встановлено.')
    except Exception:
        result['limitations'].append('Метадані файлу недоступні для перевірки.')
    evidence_cache().set(key, result, 86400 * 7)
    return result


def submission_provenance(submission):
    files = list(submission.files.all())
    file_paths = {f.file.path for f in files if getattr(f, 'file', None) and hasattr(f.file, 'path')}
    if submission.file and hasattr(submission.file, 'path') and submission.file.path not in file_paths:
        files.append(submission)
    output = []
    for file in files:
        if file.file and Path(file.file.path).is_file():
            output.append({'source': getattr(file, 'original_name', '') or Path(file.file.name).name, **file_provenance(file.file.path)})
    return output


def normalize_authorship(result, allowed, provenance=(), tolerance_percent=25):
    """Turn uncalibrated model claims and file metadata into an evidence report.

    The output never lowers the grade. States are:
    - suspected: at least one verifiable/medium signal, or the model threshold fired;
    - none: files were inspected and no signals were found (not proof of authorship);
    - insufficient: no inspectable provenance (e.g. plain text) or no evidence at all.
    """
    analysis = result.get('ai_authorship_analysis') or {}
    analysis = analysis if isinstance(analysis, dict) else {}
    model_evidence = analysis.get('evidence') or []
    model_evidence = [item for item in model_evidence if isinstance(item, dict) and item.get('observation')] if isinstance(model_evidence, list) else []

    raw_flag = result.get('ai_generated_detected')
    legacy_detected = raw_flag is True or raw_flag == 1 or str(raw_flag).lower() == 'true'

    raw_ai_percent = result.get('ai_generated_percent')
    ai_percent = None
    if raw_ai_percent is not None:
        try:
            clean_p_str = str(raw_ai_percent).replace('%', '').strip()
            ai_percent = max(0, min(100, int(float(clean_p_str))))
        except (ValueError, TypeError):
            ai_percent = None

    tol = 25 if tolerance_percent is None else tolerance_percent
    percent_exceeds = (ai_percent is not None and ai_percent > tol)

    details = str(result.get('ai_generated_details') or '').strip()
    if details.lower() in {'none', 'null', 'false', 'ok', 'none.', 'null.', 'лише ознаки, не доведений факт'}:
        details = ''

    signals = []
    for item in model_evidence:
        basis = str(item.get('basis') or 'model_style')
        if basis not in {'self_disclosure', 'text_analysis'}:
            basis = 'model_style'
        signals.append({
            'source': str(item.get('source') or ''),
            'location': str(item.get('location') or ''),
            'basis': basis,
            'strength': 'medium' if basis == 'self_disclosure' else 'weak',
            'observation': str(item.get('observation')),
        })
    limitations = []
    checked_files = 0
    for file in provenance:
        source = file.get('source') or ''
        if file.get('checked'):
            checked_files += 1
        limitations.extend(file.get('limitations') or [])
        for signal in file.get('signals', []):
            basis = str(signal.get('basis') or 'metadata')
            strength = str(signal.get('strength') or ('strong' if basis == 'c2pa' else
                                                      'medium' if basis == 'metadata' else 'weak'))
            signals.append({
                'source': source,
                'location': str(signal.get('location') or ''),
                'basis': basis,
                'strength': strength,
                'observation': str(signal.get('observation') or ''),
            })

    strongest = 'weak'
    if any(s['strength'] == 'strong' for s in signals):
        strongest = 'strong'
    elif any(s['strength'] == 'medium' for s in signals):
        strongest = 'medium'

    detected = bool(percent_exceeds or legacy_detected or strongest in {'strong', 'medium'})
    if detected:
        state = 'suspected'
    elif checked_files:
        state = 'none'
    else:
        state = 'insufficient'

    if detected and strongest == 'strong' and any(s['basis'] == 'c2pa' for s in signals):
        confidence = 'high'
    elif detected and (strongest in {'strong', 'medium'} or 'self_disclosure' in {s['basis'] for s in signals}):
        confidence = 'medium'
    elif detected:
        confidence = 'low'
    else:
        confidence = 'none'

    note = ('ШІ дозволено вчителем: використання інструмента саме по собі не знижує бал.' if allowed else
            'ШІ не дозволено вчителем. Підозра потребує уточнення; самостійність оцінюється за критеріями завдання, без автоматичного штрафу за стиль чи метадані.')

    lines = []
    for item in signals:
        if not item['observation']:
            continue
        where = ' — '.join(part for part in (item['source'], item['location']) if part)
        line = (where + ': ' if where else '') + item['observation']
        if line not in lines:
            lines.append(line)
    if details and details not in lines and not any(details in line for line in lines):
        lines.insert(0, details)

    if state == 'suspected':
        lines.extend(['Це ознаки, а не доведений факт авторства. ' + note,
                      'Для уточнення поясни свої кроки, покажи проміжні результати та вкажи використані інструменти.'])
    elif state == 'none':
        lines = ['Ознак використання ШІ не знайдено. Це не підтверджує самостійність: метадані могли бути видалені. ' + note]
    else:
        lines = ['Походження роботи не встановлено. Недостатньо даних: у файлі немає перевірюваних метаданих. Це не підтверджує самостійність. ' + note]
        if not allowed:
            lines.append('Бал оцінює наданий результат. Перед остаточним рішенням учитель може попросити пояснити кроки або показати чернетку.')

    report = {
        'version': 'provenance-2026-11-09',
        'state': state,
        'confidence': confidence,
        'checked_files': checked_files,
        'signals': signals,
        'limitations': list(dict.fromkeys(limitations))[:12],
    }
    return {
        'ai_generated_detected': detected,
        'ai_generated_percent': ai_percent,
        'ai_generated_confidence': confidence,
        'ai_generated_details': '\n'.join(lines),
        'ai_authorship_state': state,
        'ai_provenance_report': report,
    }
