"""
Утиліти для конвертації та обробки файлів і посилань для попереднього перегляду в SchoolNet+
"""
import os
import re
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs


def convert_docx_to_html(file_path: str) -> Tuple[str, Optional[str]]:
    """
    Конвертує .docx файл у HTML для відображення в браузері.
    """
    try:
        from docx import Document
        
        doc = Document(file_path)
        html_parts = []
        html_parts.append('<div class="document-page">')
        
        for para in doc.paragraphs:
            if not para.text.strip():
                html_parts.append('<p>&nbsp;</p>')
                continue
                
            if para.style.name.startswith('Heading'):
                level = para.style.name.replace('Heading ', '')
                if level.isdigit():
                    html_parts.append(f'<h{level}>{para.text}</h{level}>')
                else:
                    html_parts.append(f'<h3>{para.text}</h3>')
            else:
                html_parts.append(f'<p>{para.text}</p>')
        
        for table in doc.tables:
            html_parts.append('<table class="table table-bordered table-striped mt-3 excel-table">')
            for i, row in enumerate(table.rows):
                html_parts.append('<tr>')
                for cell in row.cells:
                    tag = 'th' if i == 0 else 'td'
                    cell_text = cell.text
                    if tag == 'td':
                        html_parts.append(f'<{tag} data-formula="{cell_text}" onclick="showFormula(this)">{cell_text}</{tag}>')
                    else:
                        html_parts.append(f'<{tag}>{cell_text}</{tag}>')
                html_parts.append('</tr>')
            html_parts.append('</table>')
            
        html_parts.append('</div>')
        html_content = '\n'.join(html_parts)
        
        if not html_content.strip() or html_content == '<div class="document-page"></div>':
            html_content = '<div class="document-page"><p class="text-muted">Документ порожній або не містить текстового контенту.</p></div>'
        
        return html_content, None
        
    except Exception as e:
        error_msg = f"Помилка при читанні документа: {str(e)}"
        return "", error_msg


def convert_xlsx_to_html(file_path: str, max_rows: int = 100) -> Tuple[str, Optional[str]]:
    """
    Конвертує .xlsx файл у HTML таблиці з підтримкою формул та стилів.
    """
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import PatternFill
        from openpyxl.utils import get_column_letter
        
        wb_values = load_workbook(file_path, data_only=True)
        wb_formulas = load_workbook(file_path, data_only=False)
        
        html_parts = []
        
        for sheet_name in wb_values.sheetnames:
            sheet_values = wb_values[sheet_name]
            sheet_formulas = wb_formulas[sheet_name]
            
            html_parts.append('<div class="document-page excel-sheet mb-4">')
            html_parts.append(f'<h4 class="sheet-title mb-3" style="color:var(--color-primary, #6366f1);font-weight:700;">📊 Аркуш: {sheet_name}</h4>')
            
            max_row = min(sheet_values.max_row or 1, max_rows)
            max_col = sheet_values.max_column or 1
            
            if max_row == 0 or max_col == 0:
                html_parts.append('<p class="text-muted">Аркуш порожній</p>')
                html_parts.append('</div>')
                continue
            
            html_parts.append('<div class="table-responsive" style="overflow-x:auto;">')
            html_parts.append('<table class="excel-table table table-bordered" style="width:100%;border-collapse:collapse;font-size:13px;">')
            
            # Column headers (A, B, C...)
            html_parts.append('<thead><tr style="background:var(--color-bg-secondary,#f8fafc);"><th class="row-header" style="width:40px;text-align:center;">#</th>')
            for col in range(1, max_col + 1):
                col_letter = get_column_letter(col)
                html_parts.append(f'<th style="text-align:center;padding:6px 10px;font-weight:600;">{col_letter}</th>')
            html_parts.append('</tr></thead>')
            
            html_parts.append('<tbody>')
            
            for row_idx in range(1, max_row + 1):
                html_parts.append('<tr>')
                html_parts.append(f'<td class="row-header" style="background:var(--color-bg-secondary,#f8fafc);text-align:center;font-weight:600;width:40px;">{row_idx}</td>')
                
                for col_idx in range(1, max_col + 1):
                    cell_val = sheet_values.cell(row=row_idx, column=col_idx)
                    cell_formula = sheet_formulas.cell(row=row_idx, column=col_idx)
                    
                    value = cell_val.value
                    if value is None:
                        value = ''
                    
                    formula = ""
                    if cell_formula.data_type == 'f':
                        formula = f"={cell_formula.value}"
                    elif value != "":
                        formula = str(value)
                        
                    style_parts = ['padding:6px 10px;', 'border:1px solid var(--color-border, #e2e8f0);']
                    
                    if cell_val.font:
                        if cell_val.font.bold:
                            style_parts.append('font-weight: bold;')
                        if cell_val.font.italic:
                            style_parts.append('font-style: italic;')
                        if cell_val.font.color and hasattr(cell_val.font.color, 'rgb') and cell_val.font.color.rgb:
                            color = str(cell_val.font.color.rgb)
                            if len(color) == 8:
                                color = '#' + color[2:]
                            elif len(color) == 6:
                                color = '#' + color
                            if color.startswith('#'):
                                style_parts.append(f'color: {color};')

                    if cell_val.fill and isinstance(cell_val.fill, PatternFill) and cell_val.fill.start_color:
                        if hasattr(cell_val.fill.start_color, 'rgb') and cell_val.fill.start_color.rgb:
                            bg_color = str(cell_val.fill.start_color.rgb)
                            if len(bg_color) == 8:
                                bg_color = '#' + bg_color[2:]
                            elif len(bg_color) == 6:
                                bg_color = '#' + bg_color
                            if bg_color.startswith('#') and bg_color != '#000000':
                                style_parts.append(f'background-color: {bg_color};')
                    
                    style_attr = f'style="{ " ".join(style_parts) }"'
                    escaped_val = str(value).replace('<', '&lt;').replace('>', '&gt;')
                    escaped_formula = str(formula).replace('"', '&quot;')
                    
                    html_parts.append(f'<td {style_attr} data-formula="{escaped_formula}" onclick="showFormula(this)" style="cursor:pointer;">{escaped_val}</td>')
                
                html_parts.append('</tr>')
            
            html_parts.append('</tbody>')
            html_parts.append('</table>')
            html_parts.append('</div>')
            
            if (sheet_values.max_row or 0) > max_rows:
                html_parts.append(f'<p class="text-muted small mt-2">Показано перші {max_rows} рядків з {sheet_values.max_row}</p>')
                
            html_parts.append('</div>')
        
        html_content = '\n'.join(html_parts)
        return html_content, None

    except Exception as e:
        error_msg = f"Помилка при читанні таблиці: {str(e)}"
        return "", error_msg


def convert_pptx_to_html(file_path: str) -> Tuple[str, Optional[str]]:
    """
    Конвертує .pptx файл у HTML слайди.
    """
    try:
        from pptx import Presentation
        
        prs = Presentation(file_path)
        html_parts = []
        html_parts.append('<div class="pptx-viewer" style="display:flex;flex-direction:column;gap:20px;">')
        
        for slide_idx, slide in enumerate(prs.slides, 1):
            html_parts.append(f'<div class="slide-container card p-4 shadow-sm" style="border:1px solid var(--color-border,#e2e8f0);border-radius:12px;background:var(--color-surface,#fff);">')
            html_parts.append(f'<div class="d-flex align-items-center justify-content-between mb-3 pb-2 border-bottom">')
            html_parts.append(f'<h5 style="margin:0;color:var(--color-primary,#6366f1);font-weight:700;">Слайд {slide_idx}</h5>')
            html_parts.append('</div>')
            html_parts.append('<div class="slide-body" style="font-size:15px;line-height:1.6;">')
            
            slide_texts = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_texts.append(shape.text)
            
            if slide_texts:
                for text in slide_texts:
                    if text == slide_texts[0] and len(text) < 100:
                        html_parts.append(f'<h4 style="font-weight:700;margin-bottom:12px;">{text}</h4>')
                    else:
                        paragraphs = text.split('\n')
                        for para in paragraphs:
                            if para.strip():
                                html_parts.append(f'<p style="margin-bottom:8px;">{para}</p>')
            else:
                html_parts.append('<p class="text-muted">Слайд не містить текстового контенту</p>')
            
            html_parts.append('</div>')
            html_parts.append('</div>')
        
        html_parts.append('</div>')
        html_content = '\n'.join(html_parts)
        
        if not prs.slides:
            html_content = '<p class="text-muted">Презентація не містить слайдів.</p>'
        
        return html_content, None
        
    except Exception as e:
        error_msg = f"Помилка при читанні презентації: {str(e)}"
        return "", error_msg


def convert_odt_to_html(file_path: str) -> Tuple[str, Optional[str]]:
    """Конвертує .odt файл у HTML."""
    try:
        from odf.opendocument import load
        from odf.text import P, H
        from odf import teletype

        doc = load(file_path)
        html_parts = ['<div class="document-page">']

        for h in doc.getElementsByType(H):
            text = teletype.extractText(h)
            level = h.getAttribute('outlinelevel') or 3
            html_parts.append(f'<h{level}>{text}</h{level}>')

        for p in doc.getElementsByType(P):
            text = teletype.extractText(p)
            if text.strip():
                html_parts.append(f'<p>{text}</p>')

        html_parts.append('</div>')
        html_content = '\n'.join(html_parts)
        
        if not html_content.strip() or html_content == '<div class="document-page"></div>':
            html_content = '<p class="text-muted">Документ порожній або не містить текстового контенту.</p>'
            
        return html_content, None

    except Exception as e:
        error_msg = f"Помилка при читанні ODT документа: {str(e)}"
        return "", error_msg


def convert_ods_to_html(file_path: str, max_rows: int = 100) -> Tuple[str, Optional[str]]:
    """Конвертує .ods файл у HTML таблиці."""
    try:
        from odf.opendocument import load
        from odf.table import Table, TableRow, TableCell
        from odf import teletype

        doc = load(file_path)
        html_parts = ['<div class="document-page">']

        for sheet in doc.getElementsByType(Table):
            sheet_name = sheet.getAttribute('name')
            html_parts.append(f'<h4 class="mb-3" style="color:var(--color-primary,#6366f1);font-weight:700;">Аркуш: {sheet_name}</h4>')
            html_parts.append('<div class="excel-sheet mb-4 table-responsive">')
            html_parts.append('<table class="excel-table table table-bordered" style="font-size:13px;">')
            
            rows = sheet.getElementsByType(TableRow)
            for i, row in enumerate(rows):
                if i >= max_rows:
                    break
                    
                html_parts.append('<tr>')
                html_parts.append(f'<td class="row-header" style="background:var(--color-bg-secondary,#f8fafc);text-align:center;font-weight:600;width:40px;">{i+1}</td>')
                
                cells = row.getElementsByType(TableCell)
                for cell in cells:
                    repeat = int(cell.getAttribute('numbercolumnsrepeated') or 1)
                    text = teletype.extractText(cell)
                    formula = cell.getAttribute('formula') or ""
                    
                    if formula.startswith('of:='):
                        formula = '=' + formula[4:]
                    elif not formula and text:
                        formula = text
                        
                    for _ in range(repeat):
                        escaped_val = str(text).replace('<', '&lt;').replace('>', '&gt;')
                        escaped_form = str(formula).replace('"', '&quot;')
                        html_parts.append(f'<td data-formula="{escaped_form}" onclick="showFormula(this)" style="cursor:pointer;padding:6px 10px;">{escaped_val}</td>')
                        
                html_parts.append('</tr>')
            
            html_parts.append('</table>')
            html_parts.append('</div>')
            
            if len(rows) > max_rows:
                html_parts.append(f'<p class="text-muted small">Показано перші {max_rows} рядків з {len(rows)}</p>')

        html_parts.append('</div>')
        html_content = '\n'.join(html_parts)
        return html_content, None

    except Exception as e:
        error_msg = f"Помилка при читанні ODS таблиці: {str(e)}"
        return "", error_msg


def convert_odp_to_html(file_path: str) -> Tuple[str, Optional[str]]:
    """Конвертує .odp файл у HTML."""
    try:
        from odf.opendocument import load
        from odf.draw import Page, Frame, TextBox
        from odf import teletype

        doc = load(file_path)
        html_parts = ['<div class="pptx-viewer" style="display:flex;flex-direction:column;gap:20px;">']

        slides = doc.getElementsByType(Page)
        for i, slide in enumerate(slides, 1):
            html_parts.append(f'<div class="slide-container card p-4 shadow-sm" style="border:1px solid var(--color-border,#e2e8f0);border-radius:12px;background:var(--color-surface,#fff);">')
            html_parts.append(f'<div class="d-flex align-items-center justify-content-between mb-3 pb-2 border-bottom">')
            html_parts.append(f'<h5 style="margin:0;color:var(--color-primary,#6366f1);font-weight:700;">Слайд {i}</h5>')
            html_parts.append('</div>')
            html_parts.append('<div class="slide-body" style="font-size:15px;line-height:1.6;">')
            
            slide_text_found = False
            for frame in slide.getElementsByType(Frame):
                for textbox in frame.getElementsByType(TextBox):
                    text = teletype.extractText(textbox)
                    if text.strip():
                        html_parts.append(f'<p style="margin-bottom:8px;">{text}</p>')
                        slide_text_found = True
            
            if not slide_text_found:
                html_parts.append('<p class="text-muted">Слайд не містить текстового контенту</p>')
                
            html_parts.append('</div>')
            html_parts.append('</div>')

        html_parts.append('</div>')
        html_content = '\n'.join(html_parts)
        
        if not slides:
            html_content = '<p class="text-muted">Презентація не містить слайдів.</p>'

        return html_content, None

    except Exception as e:
        error_msg = f"Помилка при читанні ODP презентації: {str(e)}"
        return "", error_msg


def get_archive_content(file_path: str, file_ext: str) -> Tuple[list, Optional[str]]:
    """Повертає список файлів в архіві (.zip, .rar, .7z, .tar, .gz)."""
    files_list = []
    try:
        if file_ext == '.zip':
            import zipfile
            with zipfile.ZipFile(file_path, 'r') as zf:
                for info in zf.infolist():
                    files_list.append({
                        'name': info.filename,
                        'size': info.file_size,
                        'is_dir': info.is_dir()
                    })
                    
        elif file_ext == '.rar':
            try:
                import rarfile
                with rarfile.RarFile(file_path) as rf:
                    for info in rf.infolist():
                        files_list.append({
                            'name': info.filename,
                            'size': info.file_size,
                            'is_dir': info.isdir()
                        })
            except Exception:
                pass
                
        elif file_ext == '.7z':
            try:
                import py7zr
                with py7zr.SevenZipFile(file_path, 'r') as zf:
                    for info in zf.list():
                        files_list.append({
                            'name': info.filename,
                            'size': info.uncompressed,
                            'is_dir': info.is_directory
                        })
            except Exception:
                pass

        elif file_ext in ('.tar', '.gz', '.tgz'):
            import tarfile
            with tarfile.open(file_path, 'r:*') as tf:
                for member in tf.getmembers():
                    files_list.append({
                        'name': member.name,
                        'size': member.size,
                        'is_dir': member.isdir()
                    })
                    
        return files_list, None
        
    except Exception as e:
        error_msg = f"Помилка при читанні архіву: {str(e)}"
        return [], error_msg


def get_file_type_info(file_ext: str) -> dict:
    """Повертає інформацію про тип файлу (назва, іконка, прев'ю)."""
    file_types = {
        '.doc': {'name': 'Word документ', 'icon': '📝', 'color': 'primary', 'preview': False},
        '.docx': {'name': 'Word документ', 'icon': '📝', 'color': 'primary', 'preview': True},
        '.odt': {'name': 'OpenDocument Текст', 'icon': '📝', 'color': 'primary', 'preview': True},
        
        '.xls': {'name': 'Excel таблиця', 'icon': '📊', 'color': 'success', 'preview': False},
        '.xlsx': {'name': 'Excel таблиця', 'icon': '📊', 'color': 'success', 'preview': True},
        '.ods': {'name': 'OpenDocument Таблиця', 'icon': '📊', 'color': 'success', 'preview': True},
        
        '.ppt': {'name': 'PowerPoint', 'icon': '📽️', 'color': 'warning', 'preview': False},
        '.pptx': {'name': 'PowerPoint', 'icon': '📽️', 'color': 'warning', 'preview': True},
        '.odp': {'name': 'OpenDocument Презентація', 'icon': '📽️', 'color': 'warning', 'preview': True},
        
        '.pdf': {'name': 'PDF документ', 'icon': '📄', 'color': 'danger', 'preview': True},
        
        '.jpg': {'name': 'Зображення', 'icon': '🖼️', 'color': 'info', 'preview': True},
        '.jpeg': {'name': 'Зображення', 'icon': '🖼️', 'color': 'info', 'preview': True},
        '.png': {'name': 'Зображення', 'icon': '🖼️', 'color': 'info', 'preview': True},
        '.gif': {'name': 'Зображення', 'icon': '🖼️', 'color': 'info', 'preview': True},
        '.bmp': {'name': 'Зображення', 'icon': '🖼️', 'color': 'info', 'preview': True},
        '.webp': {'name': 'Зображення', 'icon': '🖼️', 'color': 'info', 'preview': True},
        
        '.txt': {'name': 'Текстовий файл', 'icon': '📃', 'color': 'secondary', 'preview': True},
        '.md': {'name': 'Markdown документ', 'icon': '📃', 'color': 'secondary', 'preview': True},
        
        '.py': {'name': 'Python', 'icon': '🐍', 'color': 'teal', 'preview': True},
        '.html': {'name': 'HTML', 'icon': '💻', 'color': 'teal', 'preview': True},
        '.css': {'name': 'CSS', 'icon': '💻', 'color': 'teal', 'preview': True},
        '.js': {'name': 'JavaScript', 'icon': '💻', 'color': 'teal', 'preview': True},
        '.json': {'name': 'JSON', 'icon': '💻', 'color': 'teal', 'preview': True},
        '.xml': {'name': 'XML', 'icon': '💻', 'color': 'teal', 'preview': True},
        
        '.zip': {'name': 'ZIP архів', 'icon': '📦', 'color': 'secondary', 'preview': True},
        '.rar': {'name': 'RAR архів', 'icon': '📦', 'color': 'secondary', 'preview': True},
        '.7z': {'name': '7Z архів', 'icon': '📦', 'color': 'secondary', 'preview': True},
        '.tar': {'name': 'TAR архів', 'icon': '📦', 'color': 'secondary', 'preview': True},
        '.gz': {'name': 'GZ архів', 'icon': '📦', 'color': 'secondary', 'preview': True},

        # Microsoft Access Databases
        '.mdb': {'name': 'Microsoft Access 97-2003', 'icon': '🗄️', 'color': 'success', 'preview': True},
        '.accdb': {'name': 'Microsoft Access 2007+', 'icon': '🗄️', 'color': 'success', 'preview': True},
    }
    
    return file_types.get(file_ext.lower() if file_ext else '', {
        'name': f'{(file_ext or "").upper().replace(".", "")} файл',
        'icon': '📎',
        'color': 'secondary',
        'preview': False
    })


def parse_embed_url(url: str) -> dict:
    """
    Аналізує посилання та перетворює його на валідну embed-URL для <iframe>.
    """
    if not url:
        return {
            'is_embeddable': False,
            'embed_type': None,
            'embed_url': None,
            'original_url': '',
            'platform': None,
            'icon': '🔗',
            'video_id': None
        }

    clean_url = url.strip()

    # 1. YouTube
    yt_regex = (
        r'(?:https?:\/\/)?(?:www\.|m\.)?'
        r'(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|v\/|shorts\/|live\/)|youtu\.be\/)'
        r'([a-zA-Z0-9_-]{11})'
    )
    yt_match = re.search(yt_regex, clean_url)
    if yt_match:
        video_id = yt_match.group(1)
        parsed = urlparse(clean_url)
        params = parse_qs(parsed.query)
        start_param = ''
        if 't' in params:
            t_str = params['t'][0].replace('s', '')
            if t_str.isdigit():
                start_param = f"?start={t_str}"
        elif 'start' in params:
            if params['start'][0].isdigit():
                start_param = f"?start={params['start'][0]}"

        embed_url = f"https://www.youtube.com/embed/{video_id}{start_param}"
        return {
            'is_embeddable': True,
            'embed_type': 'youtube',
            'embed_url': embed_url,
            'original_url': clean_url,
            'platform': 'YouTube',
            'icon': '▶️',
            'video_id': video_id
        }

    # 2. Vimeo
    vimeo_regex = r'(?:https?:\/\/)?(?:www\.)?vimeo\.com\/(?:channels\/(?:\w+\/)?|groups\/([^\/]*)\/videos\/|album\/(\d+)\/video\/|)(\d+)'
    vimeo_match = re.search(vimeo_regex, clean_url)
    if vimeo_match:
        video_id = vimeo_match.group(3)
        return {
            'is_embeddable': True,
            'embed_type': 'vimeo',
            'embed_url': f"https://player.vimeo.com/video/{video_id}",
            'original_url': clean_url,
            'platform': 'Vimeo',
            'icon': '▶️',
            'video_id': video_id
        }

    # 3. Google Drive
    if 'drive.google.com' in clean_url and '/file/d/' in clean_url:
        gdrive_match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', clean_url)
        if gdrive_match:
            gdrive_id = gdrive_match.group(1)
            return {
                'is_embeddable': True,
                'embed_type': 'google_drive',
                'embed_url': f"https://drive.google.com/file/d/{gdrive_id}/preview",
                'original_url': clean_url,
                'platform': 'Google Диск',
                'icon': '📁',
                'video_id': gdrive_id
            }

    # 4. Google Docs / Sheets / Slides
    if 'docs.google.com' in clean_url:
        doc_preview = re.sub(r'/(edit|view|copy|export).*$', '/preview', clean_url)
        if not doc_preview.endswith('/preview'):
            doc_preview = doc_preview.rstrip('/') + '/preview'
        return {
            'is_embeddable': True,
            'embed_type': 'google_docs',
            'embed_url': doc_preview,
            'original_url': clean_url,
            'platform': 'Google Документи',
            'icon': '📑',
            'video_id': None
        }

    # Generic Website / Link
    return {
        'is_embeddable': True,
        'embed_type': 'generic',
        'embed_url': clean_url,
        'original_url': clean_url,
        'platform': 'Веб-посилання',
        'icon': '🔗',
        'video_id': None
    }


def optimize_uploaded_file(uploaded_file):
    """
    Оптимізує завантажений файл: якщо це зображення (JPG, PNG тощо),
    виправляє EXIF-орієнтацію, зменшує роздільну здатність до макс. 1920px
    та зберігає у легкому сучасному форматі WebP для економії пам'яті сервера.
    """
    if not uploaded_file:
        return uploaded_file

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}:
        return uploaded_file

    try:
        from io import BytesIO
        from PIL import Image, ImageOps
        from django.core.files.uploadedfile import InMemoryUploadedFile

        img = Image.open(uploaded_file)
        # Виправлення орієнтації за EXIF
        img = ImageOps.exif_transpose(img)

        # Конвертація кольорової моделі
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGBA')
        else:
            img = img.convert('RGB')

        # Зменшення якщо роздільна здатність перевищує 1920px
        max_dim = 1920
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        output = BytesIO()
        img.save(output, format='WEBP', quality=85, optimize=True)
        output.seek(0)

        base_name = os.path.splitext(uploaded_file.name)[0]
        new_filename = f"{base_name}.webp"

        optimized_file = InMemoryUploadedFile(
            file=output,
            field_name=getattr(uploaded_file, 'field_name', 'file'),
            name=new_filename,
            content_type='image/webp',
            size=output.getbuffer().nbytes,
            charset=None
        )
        return optimized_file
    except Exception:
        return uploaded_file

