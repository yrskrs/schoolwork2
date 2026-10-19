"""Validate bundles and discover pip requirements without importing student code."""
import ast
import base64
import re
import sys
from pathlib import PurePosixPath

MAX_FILES = 50
MAX_BYTES = 10 * 1024 * 1024
ALIASES = {
    'PIL': 'Pillow', 'cv2': 'opencv-python', 'sklearn': 'scikit-learn',
    'yaml': 'PyYAML', 'bs4': 'beautifulsoup4', 'Crypto': 'pycryptodome',
    'dateutil': 'python-dateutil', 'serial': 'pyserial', 'dotenv': 'python-dotenv',
    'pygame': 'pygame', 'OpenGL': 'PyOpenGL', 'skimage': 'scikit-image',
    'docx': 'python-docx', 'pptx': 'python-pptx', 'fitz': 'PyMuPDF',
}
# Intentionally excludes pip options, URLs, editable installs and environment markers.
REQUIREMENT = re.compile(
    r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?'
    r'(?:\s*(?:===|==|!=|~=|>=|<=|>|<)\s*[A-Za-z0-9.*+!-]+'
    r'(?:\s*,\s*(?:===|==|!=|~=|>=|<=|>|<)\s*[A-Za-z0-9.*+!-]+)*)?\Z')


def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def validate_bundle(payload):
    files = payload.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError(f'Дозволено від 1 до {MAX_FILES} файлів.')
    decoded = {}
    total = 0
    for item in files:
        name = item.get('name', '') if isinstance(item, dict) else ''
        path = PurePosixPath(name)
        if (not name or len(name) > 250 or '\\' in name or '\x00' in name
                or path.is_absolute() or '..' in path.parts or str(path) != name
                or name in decoded or any(p.startswith('.') for p in path.parts)):
            raise ValueError('Некоректна або повторна назва файлу.')
        try:
            content = base64.b64decode(item['content'], validate=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('Некоректний вміст файлу.') from exc
        total += len(content)
        if total > MAX_BYTES:
            raise ValueError('Сумарний розмір файлів перевищує 10 МБ.')
        decoded[name] = content
    entry = payload.get('entry')
    if entry not in decoded or PurePosixPath(entry).suffix.lower() not in ('.py', '.pyw'):
        raise ValueError('Оберіть файл Python (.py або .pyw).')
    return decoded, entry


def discover_requirements(files):
    explicit = {}
    for name, content in files.items():
        if PurePosixPath(name).name.lower() != 'requirements.txt':
            continue
        for line in content.decode('utf-8-sig').splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if len(line) > 200 or not REQUIREMENT.fullmatch(line):
                raise ValueError('requirements.txt: підтримуються назви пакетів і версії; URL та опції pip заборонені.')
            explicit[normalize(re.split(r'[\[<>=!~\s]', line, 1)[0])] = line
    local = {PurePosixPath(name).parts[0].split('.')[0] for name in files}
    local.update(PurePosixPath(name).stem for name in files if PurePosixPath(name).suffix.lower() in ('.py', '.pyw'))
    imports = set()
    for name, content in files.items():
        if PurePosixPath(name).suffix.lower() not in ('.py', '.pyw'):
            continue
        try:
            # compile(..., PyCF_ONLY_AST) honors Python encoding cookies.
            tree = compile(content, name, 'exec', ast.PyCF_ONLY_AST)
        except (SyntaxError, ValueError):
            continue  # The interpreter will show the original syntax error.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                imports.add(node.module.split('.')[0])
    for module in sorted(imports - local - sys.stdlib_module_names - {'__future__'}):
        package = ALIASES.get(module, module)
        if not REQUIREMENT.fullmatch(package):
            raise ValueError(f'Не вдалося визначити пакет для модуля {module}.')
        explicit.setdefault(normalize(package), package)
    if len(explicit) > 30:
        raise ValueError('Дозволено не більше 30 залежностей.')
    return list(explicit.values())
