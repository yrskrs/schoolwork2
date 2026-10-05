"""Static DOCX evidence for objects omitted by paragraph-only extraction.

SmartArt's data model is stored separately from document.xml. Reading its
nodes and edges complements rendered pages without claiming visual fidelity.
"""
import base64
import io
import json
import re
import zipfile
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
DGM = 'http://schemas.openxmlformats.org/drawingml/2006/diagram'
M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'


def _labels(root):
    return ' '.join(node.text for node in root.iter()
                    if node.tag in {f'{{{W}}}t', f'{{{A}}}t', f'{{{M}}}t'} and node.text)


def ooxml_object_evidence(path, prefix='word'):
    result = {'text': [], 'limitations': [], 'image_count': 0}
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            result['image_count'] = sum(name.startswith(f'{prefix}/media/') and not name.endswith('/') for name in names)
            for name in names:
                story = bool(re.fullmatch(r'word/(document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml', name))
                diagram = name.startswith(f'{prefix}/diagrams/') and name.endswith('.xml')
                drawing = name.startswith(f'{prefix}/drawings/') and name.endswith('.xml')
                if not story and not diagram and not drawing:
                    continue
                try:
                    root = ET.fromstring(archive.read(name))
                except ET.ParseError:
                    result['limitations'].append(f'Не прочитано XML об’єктів: {name}.')
                    continue
                if diagram:
                    nodes = [{'id': point.get('modelId'), 'type': point.get('type'), 'text': _labels(point)}
                             for point in root.iter(f'{{{DGM}}}pt')]
                    edges = [{key: edge.get(key) for key in ('srcId', 'destId', 'type', 'srcOrd', 'destOrd')}
                             for edge in root.iter(f'{{{DGM}}}cxn')]
                    if nodes or edges:
                        result['text'].append('SmartArt / карта знань (' + name + '), вузли та зв’язки: ' +
                                              json.dumps({'nodes': nodes, 'connections': edges}, ensure_ascii=False))
                    elif _labels(root):
                        result['text'].append(f'Підписи схеми ({name}): {_labels(root)}')
                elif name != 'word/document.xml':
                    # Include tables/text boxes in headers, notes and comments too.
                    if _labels(root):
                        result['text'].append(f'Додаткова частина документа ({name}): {_labels(root)}')
                else:
                    for node in root.iter():
                        local = node.tag.rsplit('}', 1)[-1]
                        if local in {'drawing', 'pict', 'oMath'}:
                            labels = _labels(node)
                            descriptions = [child.get('descr') or child.get('title') for child in node.iter()
                                            if child.get('descr') or child.get('title')]
                            textpaths = [child.get('string') for child in node.iter()
                                         if child.tag.rsplit('}', 1)[-1] == 'textpath' and child.get('string')]
                            result['text'].append('Графічний об’єкт / формула: ' +
                                                  json.dumps({'text': labels, 'descriptions': descriptions,
                                                              'wordart': textpaths}, ensure_ascii=False))
                if any(node.tag.rsplit('}', 1)[-1] in {'OLEObject', 'control', 'altChunk'} for node in root.iter()):
                    result['limitations'].append(f'{name}: вбудовані OLE-об’єкти, елементи керування або '
                                                  'вкладені документи повністю не прочитані. Їх наявність '
                                                  'не означає відсутності роботи; потрібна ручна перевірка.')
            if any(name.startswith(f'{prefix}/embeddings/') for name in names):
                result['limitations'].append('Office-документ містить вкладені файли: їх внутрішній вміст не перевірено, '
                                              'доступний лише можливий вигляд на сторінці.')
    except (OSError, zipfile.BadZipFile):
        result['limitations'].append('Не вдалося прочитати пакет об’єктів Office-документа.')
    return result


def docx_object_evidence(path):
    return ooxml_object_evidence(path, 'word')


def docx_has_complex_graphics(path):
    """Raster pictures survive HTML conversion; shapes/SmartArt/charts do not."""
    try:
        with zipfile.ZipFile(path) as archive:
            if any(name.startswith(('word/diagrams/', 'word/charts/')) for name in archive.namelist()):
                return True
            for name in archive.namelist():
                if re.fullmatch(r'word/(document|header\d+|footer\d+)\.xml', name):
                    root = ET.fromstring(archive.read(name))
                    if any(node.tag.rsplit('}', 1)[-1] in {'wsp', 'wgp', 'shape', 'group', 'object'} for node in root.iter()):
                        return True
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        pass
    return False


def embedded_raster_evidence(path, prefix):
    """No silent image count/8 MB cutoff; normalize images for vision APIs.

    Vector formats depend on the page renderer, which is explicitly reported.
    This does not execute embedded files or external links.
    """
    from PIL import Image, ImageOps
    media, limitations = [], []
    try:
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if not name.startswith(prefix) or name.endswith('/'):
                    continue
                try:
                    raw = archive.read(name)
                    with Image.open(io.BytesIO(raw)) as original:
                        raster = ImageOps.exif_transpose(original)
                        raster.thumbnail((1800, 1800))
                        output = io.BytesIO()
                        if raster.mode not in ('RGB', 'RGBA', 'L', 'LA'):
                            raster = raster.convert('RGBA')
                        raster.save(output, format='PNG')
                    media.append({'mime_type': 'image/png', 'data': base64.b64encode(output.getvalue()).decode(),
                                  'source': f'Оригінальне вбудоване зображення: {name}'})
                except (OSError, ValueError, Image.DecompressionBombError):
                    limitations.append(f'{name}: зображення не прочитане окремо; перевір його на візуальних '
                                       'сторінках, а за недоступності познач неперевіреним.')
    except (OSError, zipfile.BadZipFile):
        limitations.append('Вбудовані зображення не прочитані: пакет файла недоступний.')
    return media, limitations
