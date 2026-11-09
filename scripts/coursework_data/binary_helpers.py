"""Binary file generation utilities for AI Lab coursework.

Generates authentic Word (.docx), Excel (.xlsx), PowerPoint (.pptx),
and Image (.png) files used for teacher assignment materials and student submissions.
"""
import io
from pathlib import Path


def make_docx(title: str, sections: list, table_data: dict = None) -> bytes:
    """Generate a clean Microsoft Word (.docx) document."""
    import docx
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT

    doc = docx.Document()

    # Document Title
    h0 = doc.add_heading(title, level=0)
    h0.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for sec in sections:
        heading_text = sec.get('heading')
        if heading_text:
            lvl = sec.get('level', 1)
            doc.add_heading(heading_text, level=lvl)

        paragraphs = sec.get('paragraphs', [])
        for p_text in paragraphs:
            doc.add_paragraph(p_text)

        bullets = sec.get('bullets', [])
        for b_text in bullets:
            doc.add_paragraph(b_text, style='List Bullet')

        numbered = sec.get('numbered', [])
        for n_text in numbered:
            doc.add_paragraph(n_text, style='List Number')

        sec_table = sec.get('table')
        if sec_table:
            headers = sec_table.get('headers', [])
            rows = sec_table.get('rows', [])
            if headers:
                tbl = doc.add_table(rows=1 + len(rows), cols=len(headers))
                tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
                hdr_cells = tbl.rows[0].cells
                for i, h in enumerate(headers):
                    hdr_cells[i].text = str(h)
                    for p in hdr_cells[i].paragraphs:
                        for run in p.runs:
                            run.font.bold = True
                for r_idx, row in enumerate(rows):
                    row_cells = tbl.rows[r_idx + 1].cells
                    for c_idx, val in enumerate(row):
                        if c_idx < len(row_cells):
                            row_cells[c_idx].text = str(val)

    if table_data:
        headers = table_data.get('headers', [])
        rows = table_data.get('rows', [])
        if headers:
            tbl = doc.add_table(rows=1 + len(rows), cols=len(headers))
            tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
            hdr_cells = tbl.rows[0].cells
            for i, h in enumerate(headers):
                hdr_cells[i].text = str(h)
                for p in hdr_cells[i].paragraphs:
                    for run in p.runs:
                        run.font.bold = True
            for r_idx, row in enumerate(rows):
                row_cells = tbl.rows[r_idx + 1].cells
                for c_idx, val in enumerate(row):
                    if c_idx < len(row_cells):
                        row_cells[c_idx].text = str(val)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_xlsx(sheets: list) -> bytes:
    """Generate a clean Microsoft Excel (.xlsx) workbook with styled headers and formulas."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    # remove default sheet
    default_sheet = wb.active

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )
    header_fill = PatternFill(start_color='E0E7FF', end_color='E0E7FF', fill_type='solid')
    header_font = Font(name='Calibri', size=11, bold=True, color='1E1B4B')
    regular_font = Font(name='Calibri', size=11)
    bold_font = Font(name='Calibri', size=11, bold=True)

    for idx, sheet_def in enumerate(sheets):
        title = sheet_def.get('title', f'Аркуш {idx+1}')
        if idx == 0:
            ws = default_sheet
            ws.title = title
        else:
            ws = wb.create_sheet(title=title)

        ws.views.sheetView[0].showGridLines = True

        headers = sheet_def.get('headers', [])
        if headers:
            ws.append(headers)
            for col_idx, cell in enumerate(ws[1], 1):
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border = thin_border

        rows = sheet_def.get('rows', [])
        for row in rows:
            ws.append(row)
            row_idx = ws.max_row
            is_summary = any(str(val).strip().lower() in ('разом', 'всього', 'середнє', 'підсумок') for val in row)
            for c_idx in range(1, len(row) + 1):
                c = ws.cell(row=row_idx, column=c_idx)
                c.border = thin_border
                c.font = bold_font if is_summary else regular_font

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pptx(title: str, subtitle: str, slides: list) -> bytes:
    """Generate a clean Microsoft PowerPoint (.pptx) presentation."""
    import pptx
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    prs = pptx.Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(5.625) # 16:9 widescreen

    # Slide 1: Title slide
    title_layout = prs.slide_layouts[0]
    slide0 = prs.slides.add_slide(title_layout)
    if slide0.shapes.title:
        slide0.shapes.title.text = title
    if len(slide0.placeholders) > 1:
        slide0.placeholders[1].text = subtitle

    # Content slides
    content_layout = prs.slide_layouts[1]
    for s_def in slides:
        slide = prs.slides.add_slide(content_layout)
        if slide.shapes.title:
            slide.shapes.title.text = s_def.get('title', '')

        bullets = s_def.get('bullets', [])
        body_text = s_def.get('text', '')
        if len(slide.placeholders) > 1:
            body = slide.placeholders[1]
            tf = body.text_frame
            tf.word_wrap = True
            if bullets:
                tf.text = bullets[0]
                for b in bullets[1:]:
                    p = tf.add_paragraph()
                    p.text = b
                    p.level = 0
            elif body_text:
                tf.text = body_text

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def make_diagram_png(title: str, subtitle: str, blocks: list) -> bytes:
    """Generate a crisp PNG diagram / infographic for assignments and schemes."""
    from PIL import Image, ImageDraw, ImageFont

    width = 1100
    height = max(650, 180 + len(blocks) * 160)
    img = Image.new('RGB', (width, height), color=(248, 250, 252))
    draw = ImageDraw.Draw(img)

    # Outer border
    draw.rectangle([25, 25, width - 25, height - 25], outline=(203, 213, 225), width=2)

    # Header section
    draw.text((50, 45), title, fill=(15, 23, 42))
    draw.text((50, 75), subtitle, fill=(71, 85, 105))
    draw.line([50, 105, width - 50, 105], fill=(226, 232, 240), width=2)

    # Blocks
    block_y = 125
    colors = [
        ((239, 246, 255), (59, 130, 246), (30, 58, 138)),    # blue
        ((254, 243, 199), (217, 119, 6), (120, 53, 15)),     # amber
        ((236, 253, 245), (16, 185, 129), (6, 78, 59)),     # green
        ((243, 232, 255), (147, 51, 234), (88, 28, 135)),    # purple
    ]

    for idx, block in enumerate(blocks):
        bg_color, border_color, text_color = colors[idx % len(colors)]
        b_h = 135
        draw.rectangle([50, block_y, width - 50, block_y + b_h], fill=bg_color, outline=border_color, width=2)

        b_title = block.get('title', f'Блок {idx+1}')
        draw.text((70, block_y + 15), b_title, fill=text_color)

        lines = block.get('lines', [])
        line_y = block_y + 45
        for line in lines[:4]:
            draw.text((70, line_y), f"• {line}", fill=(30, 41, 59))
            line_y += 22

        block_y += b_h + 20

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()
