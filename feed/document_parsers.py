"""
Модуль для вилучення тексту з документів критеріїв оцінювання (Word .docx, PDF, TXT, RTF тощо).
Використовується для аналізу офіційних матеріалів МОН та критеріїв оцінювання.
"""

import os
import io
import re

def extract_text_from_document(file_path_or_file_obj, original_filename=None):
    """
    Витягує чистий текст із документа Word (.docx), PDF (.pdf), TXT (.txt) або RTF.
    Повертає tuple: (extracted_text: str, success: bool, error_message: str | None).
    """
    if not file_path_or_file_obj:
        return "", False, "Файл не передано."

    filename = original_filename or ""
    if hasattr(file_path_or_file_obj, 'name') and not filename:
        filename = file_path_or_file_obj.name
    elif isinstance(file_path_or_file_obj, str) and not filename:
        filename = os.path.basename(file_path_or_file_obj)

    ext = os.path.splitext(filename)[1].lower() if filename else ""

    # 1. Читання байтів або використання шляху
    if isinstance(file_path_or_file_obj, str):
        if not os.path.exists(file_path_or_file_obj):
            return "", False, f"Файл не знайдено на диску: {file_path_or_file_obj}"
        with open(file_path_or_file_obj, 'rb') as f:
            file_bytes = f.read()
    elif hasattr(file_path_or_file_obj, 'read'):
        if hasattr(file_path_or_file_obj, 'seek'):
            file_path_or_file_obj.seek(0)
        file_bytes = file_path_or_file_obj.read()
        if hasattr(file_path_or_file_obj, 'seek'):
            file_path_or_file_obj.seek(0)
    else:
        return "", False, "Невідомий формат джерела файлу."

    if not file_bytes:
        return "", False, "Файл порожній."

    # 2. Обробка за розширенням
    try:
        if ext in ['.docx', '.docm']:
            return _extract_from_docx(file_bytes)
        elif ext == '.pdf':
            return _extract_from_pdf(file_bytes)
        elif ext in ['.xlsx', '.xls']:
            return _extract_from_excel(file_bytes)
        elif ext in ['.odt', '.ods', '.odp']:
            return _extract_from_opendocument_bytes(file_bytes)
        elif ext in ['.rtf']:
            return _extract_from_rtf_bytes(file_bytes)
        elif ext in ['.txt', '.csv', '.tsv', '.md', '.markdown', '.log', '.json', '.yaml', '.yml', '.rst']:
            return _extract_from_plain_text(file_bytes)
        elif ext == '.doc':
            return _extract_from_doc_fallback(file_bytes)
        else:
            # Спробуємо як docx, потім як excel, потім як plain text
            try:
                text, ok, _ = _extract_from_docx(file_bytes)
                if ok and text.strip():
                    return text, True, None
            except Exception:
                pass
            try:
                text, ok, _ = _extract_from_excel(file_bytes)
                if ok and text.strip():
                    return text, True, None
            except Exception:
                pass
            return _extract_from_plain_text(file_bytes)
    except Exception as e:
        return "", False, f"Помилка обробки файлу: {str(e)}"


def _extract_from_docx(file_bytes):
    """Вилучення тексту з Word .docx (параграфи та таблиці)."""
    try:
        import docx
        doc_stream = io.BytesIO(file_bytes)
        doc = docx.Document(doc_stream)
        
        paragraphs = []
        for p in doc.paragraphs:
            p_text = p.text.strip()
            if p_text:
                paragraphs.append(p_text)
                
        # Також витягуємо дані з таблиць (часто критерії МОН оформлені у таблицях)
        for table in doc.tables:
            table_lines = []
            for row in table.rows:
                row_cells = [c.text.strip().replace('\n', ' ') for c in row.cells]
                # видалення дублікатів об'єднаних комірок
                deduped = []
                for cell in row_cells:
                    if not deduped or cell != deduped[-1]:
                        deduped.append(cell)
                if any(deduped):
                    table_lines.append(" | ".join(deduped))
            if table_lines:
                paragraphs.append("\n[ТАБЛИЦЯ КРИТЕРІЇВ]:\n" + "\n".join(table_lines) + "\n")
                
        full_text = "\n\n".join(paragraphs).strip()
        if not full_text:
            return "", False, "Word документ не містить розпізнаного тексту."
        return full_text, True, None
    except Exception as e:
        # Fallback на вилучення XML з zip
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                xml_content = zf.read('word/document.xml')
                tree = ET.fromstring(xml_content)
                texts = [node.text for node in tree.iter() if node.tag.endswith('}t') and node.text]
                extracted = " ".join(texts).strip()
                if extracted:
                    return extracted, True, None
        except Exception:
            pass
        return "", False, f"Не вдалося прочитати .docx: {str(e)}"


def _extract_from_pdf(file_bytes):
    """Вилучення тексту з PDF документа."""
    text_chunks = []
    # Спроба 1: pypdf / pdfplumber якщо є
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_chunks.append(t.strip())
        if text_chunks:
            return "\n\n".join(text_chunks).strip(), True, None
    except Exception:
        pass

    # Спроба 2: базовий regex вилучення тексту з PDF потоків
    try:
        raw = file_bytes.decode('latin-1', errors='ignore')
        # пошук текстових блоків між BT та ET
        bt_blocks = re.findall(r'BT\s*(.*?)\s*ET', raw, re.DOTALL)
        found_words = []
        for block in bt_blocks:
            matches = re.findall(r'\((.*?)\)\s*T[jd]', block)
            for m in matches:
                if len(m) > 1:
                    found_words.append(m)
        if found_words:
            return " ".join(found_words).strip(), True, None
    except Exception:
        pass

    return "", False, "Не вдалося витягти текст із PDF (можливо документ містить скановані зображення без OCR)."


def _extract_from_plain_text(file_bytes):
    """Вилучення тексту з текстових файлів із детекцією кодування."""
    encodings = ['utf-8', 'utf-8-sig', 'windows-1251', 'cp1251', 'latin-1', 'cp866']
    for enc in encodings:
        try:
            text = file_bytes.decode(enc)
            cleaned = text.strip()
            if cleaned:
                return cleaned, True, None
        except UnicodeDecodeError:
            continue
    return file_bytes.decode('utf-8', errors='replace').strip(), True, None


def _extract_from_excel(file_bytes):
    """Вилучення тексту та таблиць критеріїв із файлу Excel (.xlsx, .xls)."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        sheet_summaries = []

        for sheetname in wb.sheetnames[:5]:
            sheet = wb[sheetname]
            sheet_lines = [f"[АРКУШ: {sheetname}]"]
            rows_count = 0

            for row in sheet.iter_rows(values_only=True):
                rows_count += 1
                if rows_count > 100:
                    sheet_lines.append("... [ще рядки обрізано]")
                    break
                row_vals = [str(v).strip() if v is not None else "" for v in row[:25]]
                if any(row_vals):
                    sheet_lines.append(" | ".join(row_vals))

            if len(sheet_lines) > 1:
                sheet_summaries.append("\n".join(sheet_lines))

        wb.close()
        full_text = "\n\n".join(sheet_summaries).strip()
        if full_text:
            return full_text, True, None
    except Exception:
        pass

    # Fallback на вилучення sharedStrings з zip
    try:
        import zipfile
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            if 'xl/sharedStrings.xml' in zf.namelist():
                xml_content = zf.read('xl/sharedStrings.xml')
                tree = ET.fromstring(xml_content)
                texts = [node.text for node in tree.iter() if node.tag.endswith('}t') and node.text]
                extracted = "\n".join(texts).strip()
                if extracted:
                    return extracted, True, None
    except Exception:
        pass

    return "", False, "Не вдалося витягти текст із таблиці Excel."


def _extract_from_opendocument_bytes(file_bytes):
    """Вилучення тексту з файлів OpenDocument (.odt, .ods, .odp)."""
    try:
        import zipfile
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zf:
            if 'content.xml' not in zf.namelist():
                return "", False, "OpenDocument архів не містить content.xml."
            xml_bytes = zf.read('content.xml')
            root = ET.fromstring(xml_bytes)
            texts = []
            for elem in root.iter():
                if elem.tag.endswith(('}p', '}h', '}span', '}a', '}table-cell')):
                    if elem.text and elem.text.strip():
                        texts.append(elem.text.strip())
            full_text = "\n".join(texts).strip()
            if full_text:
                return full_text, True, None
    except Exception as e:
        return "", False, f"Помилка читання OpenDocument: {e}"
    return "", False, "OpenDocument файл порожній або не містить тексту."


def _extract_from_rtf_bytes(file_bytes):
    """Вилучення тексту з RTF формату."""
    try:
        raw = file_bytes.decode('latin-1', errors='ignore')
        if raw.startswith('{\\rtf'):
            text = re.sub(r'\\[a-zA-Z0-9\-]+ ?', ' ', raw)
            text = re.sub(r'[{}]', '', text)
            cleaned = "\n".join([line.strip() for line in text.splitlines() if line.strip()])
            if cleaned:
                return cleaned, True, None
    except Exception:
        pass
    return _extract_from_plain_text(file_bytes)


def _extract_from_doc_fallback(file_bytes):
    """Вилучення тексту зі старого бінарного Word .doc."""
    try:
        text_utf16 = file_bytes.decode('utf-16-le', errors='ignore')
        printable_utf16 = re.findall(r'[\w\s.,!?:;()\-«»"\'/]{5,}', text_utf16)
        if printable_utf16 and len(" ".join(printable_utf16)) > 50:
            return "\n".join(printable_utf16).strip(), True, None

        text_1251 = file_bytes.decode('windows-1251', errors='ignore')
        printable_1251 = re.findall(r'[\w\s.,!?:;()\-«»"\'/]{5,}', text_1251)
        if printable_1251 and len(" ".join(printable_1251)) > 50:
            return "\n".join(printable_1251).strip(), True, None
    except Exception:
        pass
    return "", False, "Формат старого Word .doc не підтримується повністю. Рекомендуємо зберегти файл у сучасному форматі .docx або .txt."

