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
    if isinstance(file_path_or_file_obj, (str, os.PathLike)):
        str_path = str(file_path_or_file_obj)
        if not os.path.exists(str_path):
            return "", False, f"Файл не знайдено на диску: {str_path}"
        with open(str_path, 'rb') as f:
            file_bytes = f.read()
    elif isinstance(file_path_or_file_obj, (bytes, bytearray)):
        file_bytes = bytes(file_path_or_file_obj)
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
        elif ext in ['.pptx', '.pptm']:
            return _extract_from_pptx(file_bytes)
        elif ext == '.ppt':
            return _extract_from_ppt(file_bytes)
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
        elif ext in ['.mdb', '.accdb']:
            from .access_utils import extract_access_text_for_ai
            if isinstance(file_path_or_file_obj, str) and os.path.exists(file_path_or_file_obj):
                res = extract_access_text_for_ai(file_path_or_file_obj)
                return res, True, None
            else:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_acc:
                    tmp_acc.write(file_bytes)
                    tmp_acc_path = tmp_acc.name
                try:
                    res = extract_access_text_for_ai(tmp_acc_path)
                    return res, True, None
                finally:
                    if os.path.exists(tmp_acc_path):
                        os.remove(tmp_acc_path)
        else:
            # Спробуємо як docx, pptx, excel, потім як plain text
            try:
                text, ok, _ = _extract_from_docx(file_bytes)
                if ok and text.strip():
                    return text, True, None
            except Exception:
                pass
            try:
                text, ok, _ = _extract_from_pptx(file_bytes)
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



OPENXML_CHART_TYPES = {
    'barChart': 'Стовпчаста / лінійчата діаграма (Bar/Column chart)',
    'bar3DChart': 'Об\'ємна стовпчаста діаграма (3D Bar/Column chart)',
    'lineChart': 'Лінійний графік (Line chart)',
    'line3DChart': 'Об\'ємний графік (3D Line chart)',
    'pieChart': 'Кругова секторна діаграма (Pie chart)',
    'pie3DChart': 'Об\'ємна кругова діаграма (3D Pie chart)',
    'doughnutChart': 'Кільцева діаграма (Doughnut chart)',
    'areaChart': 'Діаграма з областями (Area chart)',
    'area3DChart': 'Об\'ємна діаграма з областями (3D Area chart)',
    'scatterChart': 'Точкова діаграма / графік розсіювання (Scatter plot)',
    'radarChart': 'Пелюсткова / радіальна діаграма (Radar chart)',
    'bubbleChart': 'Бульбашкова діаграма (Bubble chart)',
    'stockChart': 'Біржова діаграма (Stock chart)',
    'surfaceChart': 'Поверхнева діаграма (Surface chart)',
    'surface3DChart': 'Об\'ємна поверхнева діаграма (3D Surface chart)'
}


def parse_drawingml_chart_xml(xml_bytes):
    """
    Універсальний парсер DrawingML XML діаграм (.pptx, .docx, .xlsx).
    Видобуває заголовок, тип діаграми, категорії та ряди даних.
    """
    import xml.etree.ElementTree as ET
    ns = {
        'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'
    }
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None

    # Title
    title = ''
    title_elem = root.find('.//c:chart/c:title', ns)
    if title_elem is not None:
        texts = [t.text for t in title_elem.findall('.//a:t', ns) if t.text]
        if texts:
            title = ''.join(texts).strip()
        else:
            v_elem = title_elem.find('.//c:v', ns)
            if v_elem is not None and v_elem.text:
                title = v_elem.text.strip()
            else:
                f_elem = title_elem.find('.//c:f', ns)
                if f_elem is not None and f_elem.text:
                    title = f'Посилання: {f_elem.text.strip()}'

    # PlotArea
    plot_area = root.find('.//c:chart/c:plotArea', ns)
    found_types = []
    series_info = []
    categories = []

    if plot_area is not None:
        for child in plot_area:
            tag_name = child.tag.split('}')[-1]
            if tag_name in OPENXML_CHART_TYPES:
                desc = OPENXML_CHART_TYPES[tag_name]
                if tag_name == 'barChart':
                    bar_dir = child.find('./c:barDir', ns)
                    if bar_dir is not None:
                        val = bar_dir.get('val')
                        if val == 'col':
                            desc = 'Вертикальна стовпчаста діаграма / гістограма (Column chart)'
                        elif val == 'bar':
                            desc = 'Горизонтальна лінійчата діаграма (Bar chart)'
                found_types.append(desc)

                for ser in child.findall('./c:ser', ns):
                    s_name = ''
                    tx = ser.find('./c:tx', ns)
                    if tx is not None:
                        s_texts = [t.text for t in tx.findall('.//a:t', ns) if t.text]
                        if s_texts:
                            s_name = ''.join(s_texts).strip()
                        else:
                            v = tx.find('.//c:v', ns)
                            if v is not None and v.text:
                                s_name = v.text.strip()

                    cat_vals = [v.text.strip() for v in ser.findall('.//c:cat//c:v', ns) if v.text]
                    if cat_vals and not categories:
                        categories = cat_vals

                    val_items = [v.text.strip() for v in ser.findall('.//c:val//c:v', ns) if v.text]
                    s_parts = []
                    if s_name:
                        s_parts.append(f'Серія: \"{s_name}\"')
                    if val_items:
                        s_parts.append(f'Значення: [{", ".join(val_items[:15])}]')
                    elif ser.find('.//c:val//c:f', ns) is not None:
                        vf = ser.find('.//c:val//c:f', ns).text
                        if vf:
                            s_parts.append(f'Значення: {vf}')
                    if s_parts:
                        series_info.append(' | '.join(s_parts))

    chart_type_str = ', '.join(dict.fromkeys(found_types)) if found_types else 'Вбудована діаграма'
    return {
        'title': title or '(без назви)',
        'type': chart_type_str,
        'categories': categories,
        'series': series_info
    }


def format_python_pptx_chart_info(chart):
    """Витягує інформацію з об'єкта діаграми python-pptx (Chart)."""
    title = ''
    if getattr(chart, 'has_title', False) and getattr(chart, 'chart_title', None):
        try:
            title = chart.chart_title.text_frame.text.strip()
        except Exception:
            pass

    ctype_str = str(getattr(chart, 'chart_type', ''))
    ctype_desc = 'Вбудована діаграма'
    if 'COLUMN' in ctype_str:
        ctype_desc = 'Стовпчаста діаграма / гістограма (Column chart)'
    elif 'BAR' in ctype_str:
        ctype_desc = 'Горизонтальна лінійчата діаграма (Bar chart)'
    elif 'LINE' in ctype_str:
        ctype_desc = 'Лінійний графік (Line chart)'
    elif 'PIE' in ctype_str:
        ctype_desc = 'Кругова секторна діаграма (Pie chart)'
    elif 'DOUGHNUT' in ctype_str:
        ctype_desc = 'Кільцева діаграма (Doughnut chart)'
    elif 'AREA' in ctype_str:
        ctype_desc = 'Діаграма з областями (Area chart)'
    elif 'RADAR' in ctype_str:
        ctype_desc = 'Пелюсткова / радіальна діаграма (Radar chart)'
    elif 'SCATTER' in ctype_str:
        ctype_desc = 'Точкова діаграма (Scatter plot)'
    elif 'BUBBLE' in ctype_str:
        ctype_desc = 'Бульбашкова діаграма (Bubble chart)'
    elif ctype_str:
        ctype_desc = f'Діаграма ({ctype_str})'

    categories = []
    series_info = []
    if getattr(chart, 'plots', None):
        try:
            plot = chart.plots[0]
            for c in getattr(plot, 'categories', []):
                val = getattr(c, 'label', c)
                if val:
                    categories.append(str(val).strip())
            for s in getattr(plot, 'series', []):
                s_name = getattr(s, 'name', '') or ''
                try:
                    s_vals = list(getattr(s, 'values', []))
                    s_vals_str = ', '.join(str(v) for v in s_vals[:15])
                except Exception:
                    s_vals_str = ''
                if s_name and s_vals_str:
                    series_info.append(f'Серія "{s_name}": [{s_vals_str}]')
                elif s_name:
                    series_info.append(f'Серія "{s_name}"')
                elif s_vals_str:
                    series_info.append(f'Значення: [{s_vals_str}]')
        except Exception:
            pass

    return {
        'title': title or '(без назви)',
        'type': ctype_desc,
        'categories': categories,
        'series': series_info
    }


def _extract_from_docx(file_bytes):
    """Вилучення тексту з Word .docx (параграфи, таблиці та вбудовані діаграми)."""
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

        # Вбудовані діаграми у word/charts/
        try:
            import zipfile
            with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zf:
                chart_files = sorted([f for f in zf.namelist() if f.startswith('word/charts/chart') and f.endswith('.xml')])
                if chart_files:
                    charts_summary = []
                    for c_idx, cf in enumerate(chart_files, 1):
                        c_info = parse_drawingml_chart_xml(zf.read(cf))
                        if c_info:
                            c_line = f"• Діаграма #{c_idx}: {c_info['type']} «{c_info['title']}»"
                            if c_info.get('categories'):
                                c_line += f" (категорії: {', '.join(c_info['categories'][:10])})"
                            charts_summary.append(c_line)
                            for s in c_info.get('series', []):
                                charts_summary.append(f"  - {s}")
                    if charts_summary:
                        paragraphs.append("\n[ВБУДОВАНІ ДІАГРАМИ У ДОКУМЕНТІ WORD]:\n" + "\n".join(charts_summary) + "\n")
        except Exception:
            pass
                
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

                chart_files = sorted([f for f in zf.namelist() if f.startswith('word/charts/chart') and f.endswith('.xml')])
                if chart_files:
                    charts_summary = []
                    for c_idx, cf in enumerate(chart_files, 1):
                        c_info = parse_drawingml_chart_xml(zf.read(cf))
                        if c_info:
                            charts_summary.append(f"• Діаграма #{c_idx}: {c_info['type']} «{c_info['title']}»")
                    if charts_summary:
                        extracted += "\n\n[ВБУДОВАНІ ДІАГРАМИ У ДОКУМЕНТІ WORD]:\n" + "\n".join(charts_summary)

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

    # 2. Якщо це бінарний формат .xls (Excel 97-2003) — читаємо через xlrd
    try:
        import xlrd
        wb = xlrd.open_workbook(file_contents=file_bytes)
        sheet_summaries = []

        for sheetname in wb.sheet_names()[:5]:
            sheet = wb.sheet_by_name(sheetname)
            sheet_lines = [f"[АРКУШ: {sheetname}]"]
            max_r = min(sheet.nrows, 100)
            max_c = min(sheet.ncols, 25)

            for r_idx in range(max_r):
                row_vals = []
                for c_idx in range(max_c):
                    cell = sheet.cell(r_idx, c_idx)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            dt = xlrd.xldate_as_datetime(cell.value, wb.datemode)
                            val = dt.strftime('%d.%m.%Y')
                        except Exception:
                            val = str(cell.value)
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        val = str(int(cell.value)) if cell.value.is_integer() else str(cell.value)
                    elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        val = ""
                    else:
                        val = str(cell.value).strip() if cell.value is not None else ""
                    row_vals.append(val)

                if any(row_vals):
                    sheet_lines.append(" | ".join(row_vals))

            if sheet.nrows > 100:
                sheet_lines.append("... [ще рядки обрізано]")

            if len(sheet_lines) > 1:
                sheet_summaries.append("\n".join(sheet_lines))

        # Вбудовані діаграми та графіки у таблиці Excel
        try:
            import zipfile
            with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zf:
                chart_files = sorted([f for f in zf.namelist() if f.startswith('xl/charts/chart') and f.endswith('.xml')])
                if chart_files:
                    charts_summary = []
                    for c_idx, cf in enumerate(chart_files, 1):
                        c_info = parse_drawingml_chart_xml(zf.read(cf))
                        if c_info:
                            c_line = f"• Діаграма #{c_idx}: {c_info['type']} «{c_info['title']}»"
                            if c_info.get('categories'):
                                c_line += f" (категорії: {', '.join(c_info['categories'][:10])})"
                            charts_summary.append(c_line)
                            for s in c_info.get('series', []):
                                charts_summary.append(f"  - {s}")
                    if charts_summary:
                        sheet_summaries.append("\n[ВБУДОВАНІ ДІАГРАМИ ТА ГРАФІКИ У ТАБЛИЦІ EXCEL]:\n" + "\n".join(charts_summary))
        except Exception:
            pass

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
            extracted_parts = []
            if 'xl/sharedStrings.xml' in zf.namelist():
                xml_content = zf.read('xl/sharedStrings.xml')
                tree = ET.fromstring(xml_content)
                texts = [node.text for node in tree.iter() if node.tag.endswith('}t') and node.text]
                extracted = "\n".join(texts).strip()
                if extracted:
                    extracted_parts.append(extracted)

            chart_files = sorted([f for f in zf.namelist() if f.startswith('xl/charts/chart') and f.endswith('.xml')])
            if chart_files:
                charts_summary = []
                for c_idx, cf in enumerate(chart_files, 1):
                    c_info = parse_drawingml_chart_xml(zf.read(cf))
                    if c_info:
                        charts_summary.append(f"• Діаграма #{c_idx}: {c_info['type']} «{c_info['title']}»")
                if charts_summary:
                    extracted_parts.append("[ВБУДОВАНІ ДІАГРАМИ ТА ГРАФІКИ У ТАБЛИЦІ EXCEL]:\n" + "\n".join(charts_summary))

            if extracted_parts:
                return "\n\n".join(extracted_parts), True, None
    except Exception:
        pass

    return "", False, "Не вдалося витягти текст із таблиці Excel."


def _extract_from_opendocument_bytes(file_bytes):
    """Вилучення тексту з файлів OpenDocument (.odt, .ods, .odp) включно з діаграмами."""
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

            # Вилучення вбудованих об'єктів діаграм (Object */content.xml)
            chart_objects = sorted([f for f in zf.namelist() if re.match(r'^Object \d+/content\.xml$', f)])
            if chart_objects:
                for c_path in chart_objects:
                    try:
                        c_xml = zf.read(c_path)
                        c_root = ET.fromstring(c_xml)
                        c_texts = [elem.text.strip() for elem in c_root.iter() if elem.text and elem.text.strip()]
                        if c_texts:
                            obj_id = c_path.split('/')[0]
                            texts.append(f"[Вбудована діаграма OpenDocument ({obj_id}): {', '.join(c_texts[:12])}]")
                    except Exception:
                        pass

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


def _extract_from_pptx(file_bytes, max_slides=60):
    """
    Вилучення структурованого тексту зі слайдів презентації PowerPoint (.pptx).
    Враховує заголовки слайдів, текстові блоки, списки, таблиці, вбудовані діаграми
    (Bar, Column, Pie, Line тощо), SmartArt та нотатки доповідача.
    """
    try:
        from pptx import Presentation
        prs = Presentation(io.BytesIO(file_bytes))
        slides_text = []
        total_slides = len(prs.slides)

        for idx, slide in enumerate(prs.slides, 1):
            if idx > max_slides:
                slides_text.append(f"... [ще слайди обрізано, всього {total_slides}]")
                break

            title_text = ""
            try:
                if slide.shapes.title and slide.shapes.title.text.strip():
                    title_text = slide.shapes.title.text.strip().replace('\n', ' ')
            except Exception:
                pass

            slide_header = f"📽️ Слайд {idx}/{total_slides}"
            if title_text:
                slide_header += f": «{title_text}»"
            else:
                slide_header += ":"

            slide_lines = [slide_header]

            def _process_shape(shape):
                lines = []
                if hasattr(shape, "shapes"):
                    for sub_sh in shape.shapes:
                        lines.extend(_process_shape(sub_sh))
                    return lines
                if hasattr(shape, "has_table") and shape.has_table:
                    lines.append("[Таблиця на слайді:]")
                    for row in shape.table.rows:
                        row_cells = [c.text.strip().replace('\n', ' ') for c in row.cells]
                        if any(row_cells):
                            lines.append(" | ".join(row_cells))
                    return lines
                if hasattr(shape, "has_chart") and shape.has_chart:
                    c_info = format_python_pptx_chart_info(shape.chart)
                    lines.append(f"[Вбудована діаграма на слайді: {c_info['type']} «{c_info['title']}»]")
                    if c_info.get('categories'):
                        lines.append(f"  • Категорії (X): {', '.join(c_info['categories'][:15])}")
                    for s in c_info.get('series', []):
                        lines.append(f"  • {s}")
                    return lines
                if hasattr(shape, "has_text_frame") and shape.has_text_frame:
                    for p in shape.text_frame.paragraphs:
                        pt = p.text.strip()
                        if pt:
                            level = getattr(p, 'level', 0) or 0
                            indent = "  " * level
                            bullet = "• " if level > 0 else ""
                            lines.append(f"{indent}{bullet}{pt}")
                elif hasattr(shape, "text") and shape.text and shape.text.strip():
                    lines.append(shape.text.strip())
                return lines

            for shape in slide.shapes:
                try:
                    if slide.shapes.title and shape == slide.shapes.title:
                        continue
                except Exception:
                    pass
                slide_lines.extend(_process_shape(shape))

            # Нотатки до слайду (якщо є)
            try:
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    notes_txt = slide.notes_slide.notes_text_frame.text.strip()
                    if notes_txt:
                        slide_lines.append(f"[Нотатки до слайду {idx}]: {notes_txt}")
            except Exception:
                pass

            if len(slide_lines) > 1:
                slides_text.append("\n".join(slide_lines))
            elif title_text:
                slides_text.append(slide_header)

        full_text = "\n\n".join(slides_text).strip()
        if full_text:
            return full_text, True, None
    except Exception:
        pass

    # Fallback на zipfile та XML розбір слайдів + діаграм
    try:
        import zipfile
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zf:
            slide_files = [f for f in zf.namelist() if re.match(r'^ppt/slides/slide\d+\.xml$', f)]
            if slide_files:
                slide_files.sort(key=lambda x: int(re.search(r'\d+', x).group()))

                # Збираємо мапу зв'язків слайдів з діаграмами та SmartArt
                slide_charts_map = {}
                for rel_f in zf.namelist():
                    m = re.match(r'^ppt/slides/_rels/slide(\d+)\.xml\.rels$', rel_f)
                    if m:
                        s_idx = int(m.group(1))
                        try:
                            rel_tree = ET.fromstring(zf.read(rel_f))
                            for rel in rel_tree.findall('.//{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'):
                                target = rel.get('Target', '')
                                if 'charts/chart' in target:
                                    ch_name = 'ppt/charts/' + target.split('/')[-1]
                                    slide_charts_map.setdefault(s_idx, []).append(('chart', ch_name))
                                elif 'diagrams/data' in target:
                                    dgm_name = 'ppt/diagrams/' + target.split('/')[-1]
                                    slide_charts_map.setdefault(s_idx, []).append(('diagram', dgm_name))
                        except Exception:
                            pass

                slides_text = []
                for idx, s_path in enumerate(slide_files, 1):
                    xml_data = zf.read(s_path)
                    tree = ET.fromstring(xml_data)
                    texts = [n.text.strip() for n in tree.iter() if n.tag.endswith('}t') and n.text and n.text.strip()]

                    # Додаємо діаграми цього слайду
                    ch_items = slide_charts_map.get(idx, [])
                    for ch_kind, ch_file in ch_items:
                        if ch_kind == 'chart' and ch_file in zf.namelist():
                            c_info = parse_drawingml_chart_xml(zf.read(ch_file))
                            if c_info:
                                texts.append(f"[Вбудована діаграма на слайді: {c_info['type']} «{c_info['title']}»]")
                                if c_info.get('categories'):
                                    texts.append(f"  • Категорії: {', '.join(c_info['categories'][:10])}")
                                for s in c_info.get('series', []):
                                    texts.append(f"  • {s}")
                        elif ch_kind == 'diagram' and ch_file in zf.namelist():
                            try:
                                dgm_tree = ET.fromstring(zf.read(ch_file))
                                dgm_texts = [dn.text.strip() for dn in dgm_tree.iter() if dn.tag.endswith('}t') and dn.text and dn.text.strip()]
                                if dgm_texts:
                                    texts.append(f"[Діаграма SmartArt: {', '.join(dgm_texts[:10])}]")
                            except Exception:
                                pass

                    if texts:
                        slides_text.append(f"📽️ Слайд {idx}:\n" + "\n".join(texts))
                if slides_text:
                    return "\n\n".join(slides_text), True, None
    except Exception:
        pass

    return "", False, "Не вдалося витягти текст із презентації .pptx."


def _extract_from_ppt(file_bytes, max_chars=15000):
    """Вилучення тексту зі старого бінарного формату PowerPoint .ppt."""
    try:
        text_chunks = []
        ascii_matches = re.findall(b'[\x20-\x7e\t\n\r\xc0-\xff]{6,}', file_bytes)
        for m in ascii_matches:
            for enc in ['utf-8', 'cp1251', 'latin-1']:
                try:
                    s = m.decode(enc).strip()
                    if s and len(s) > 5 and any(c.isalnum() for c in s):
                        if s not in text_chunks:
                            text_chunks.append(s)
                        break
                except Exception:
                    pass
        if text_chunks:
            return "Текст презентації (видобуто з .ppt):\n" + "\n".join(text_chunks)[:max_chars], True, None
    except Exception:
        pass
    return "", False, "Формат .ppt не містить розпізнаного тексту."


