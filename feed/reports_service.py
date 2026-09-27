"""
Модуль для формування звітів вчителя та експорту в PDF.
Підтримує як короткі відомості оцінок (наприклад, для передачі журналу заміни),
так і детальні звіти зі змістом робіт, відгуками та аналізом ШІ.
"""

import io
import os
from datetime import datetime, date
from decimal import Decimal
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Реєстрація шрифтів LiberationSans з підтримкою кирилиці
_FONTS_REGISTERED = False

def register_pdf_fonts():
    """Реєструє шрифти LiberationSans з кирилицею для ReportLab."""
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return

    font_dir = os.path.join(settings.BASE_DIR, 'static', 'fonts')
    regular_path = os.path.join(font_dir, 'LiberationSans-Regular.ttf')
    bold_path = os.path.join(font_dir, 'LiberationSans-Bold.ttf')
    italic_path = os.path.join(font_dir, 'LiberationSans-Italic.ttf')
    bold_italic_path = os.path.join(font_dir, 'LiberationSans-BoldItalic.ttf')

    # Якщо файлів немає в static/fonts, спробуємо знайти в системі
    fallback_sys = '/usr/share/fonts/truetype/liberation'
    if not os.path.exists(regular_path) and os.path.exists(fallback_sys):
        regular_path = os.path.join(fallback_sys, 'LiberationSans-Regular.ttf')
        bold_path = os.path.join(fallback_sys, 'LiberationSans-Bold.ttf')
        italic_path = os.path.join(fallback_sys, 'LiberationSans-Italic.ttf')
        bold_italic_path = os.path.join(fallback_sys, 'LiberationSans-BoldItalic.ttf')

    pdfmetrics.registerFont(TTFont('LiberationSans', regular_path))
    pdfmetrics.registerFont(TTFont('LiberationSans-Bold', bold_path))
    pdfmetrics.registerFont(TTFont('LiberationSans-Italic', italic_path))
    pdfmetrics.registerFont(TTFont('LiberationSans-BoldItalic', bold_italic_path))

    pdfmetrics.registerFontFamily(
        'LiberationSans',
        normal='LiberationSans',
        bold='LiberationSans-Bold',
        italic='LiberationSans-Italic',
        boldItalic='LiberationSans-BoldItalic'
    )
    _FONTS_REGISTERED = True


class NumberedCanvas(canvas.Canvas):
    """Канвас для автоматичного підрахунку та виведення номерів 'Сторінка X з Y'."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("LiberationSans", 8)
        self.setFillColor(colors.HexColor('#64748b'))

        # Нижній колонтитул
        footer_y = 18
        self.setStrokeColor(colors.HexColor('#e2e8f0'))
        self.setLineWidth(0.5)
        self.line(28, footer_y + 12, 595.27 - 28, footer_y + 12)

        # Текст колонтитула
        timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        self.drawString(28, footer_y, f"Звіт сформовано в системі «Шкільні Завдання» | {timestamp}")
        page_str = f"Сторінка {self._pageNumber} з {page_count}"
        self.drawRightString(595.27 - 28, footer_y, page_str)
        self.restoreState()


def get_report_submissions(teacher_user, filters):
    """
    Отримує відфільтровані здачі робіт (Submission) для звіту.
    
    Параметри filters:
    - class_id: ID конкретного класу або 'all'
    - student_name: пошуковий рядок для учня
    - date_from: дата від (YYYY-MM-DD)
    - date_to: дата до (YYYY-MM-DD)
    - assignment_id: ID завдання або 'all'
    - graded_only: '1'/'true' або '0'/'false'
    - selected_ids: список конкретних ID робіт
    """
    from .models import Submission

    qs = Submission.objects.filter(is_latest_attempt=True).select_related(
        'assignment', 'assignment__subject', 'assignment__teacher',
        'class_group', 'teacher', 'graded_by'
    ).prefetch_related('files')

    # Фільтрація по викладачу (якщо не суперкористувач і не вказано дивитись все)
    # Якщо вчитель замінював іншого — він міг оцінювати будь-яку роботу або свої
    scope = filters.get('scope', 'mine')
    if scope == 'mine' and not teacher_user.is_superuser:
        qs = qs.filter(
            Q(graded_by=teacher_user) |
            Q(teacher__user=teacher_user) |
            Q(assignment__teacher__user=teacher_user)
        )

    # Фільтр по класу
    class_id = filters.get('class_id')
    if class_id and class_id != 'all':
        try:
            qs = qs.filter(class_group_id=int(class_id))
        except (ValueError, TypeError):
            pass

    # Фільтр по завданню
    assignment_id = filters.get('assignment_id')
    if assignment_id and assignment_id != 'all':
        try:
            qs = qs.filter(assignment_id=int(assignment_id))
        except (ValueError, TypeError):
            pass

    # Фільтр по імені учня
    student_name = (filters.get('student_name') or '').strip()
    if student_name:
        for part in student_name.split():
            qs = qs.filter(Q(first_name__icontains=part) | Q(last_name__icontains=part))

    # Фільтр по датах (орієнтуємось на дату оцінювання, або якщо її немає — на дату здачі)
    date_from = filters.get('date_from')
    if date_from:
        try:
            df = datetime.strptime(str(date_from), '%Y-%m-%d').date()
            qs = qs.filter(Q(graded_at__date__gte=df) | (Q(graded_at__isnull=True) & Q(submitted_at__date__gte=df)))
        except (ValueError, TypeError):
            pass

    date_to = filters.get('date_to')
    if date_to:
        try:
            dt = datetime.strptime(str(date_to), '%Y-%m-%d').date()
            qs = qs.filter(Q(graded_at__date__lte=dt) | (Q(graded_at__isnull=True) & Q(submitted_at__date__lte=dt)))
        except (ValueError, TypeError):
            pass

    # Лише оцінені
    graded_only = filters.get('graded_only', '1')
    if str(graded_only).lower() in ('1', 'true', 'yes'):
        qs = qs.exclude(grade__isnull=True).exclude(grade='')

    # Вибір конкретних ID (якщо користувач позначив галочками)
    selected_ids = filters.get('selected_ids')
    if selected_ids:
        if isinstance(selected_ids, str):
            selected_ids = [int(x.strip()) for x in selected_ids.split(',') if x.strip().isdigit()]
        if selected_ids:
            qs = qs.filter(id__in=selected_ids)

    # Впорядкування: спочатку за датою (зростання), потім клас, потім прізвище
    return qs.order_by('submitted_at', 'class_group__name', 'last_name', 'first_name')


def generate_teacher_report_pdf(buffer, report_meta, submissions, mode='grades_only'):
    """
    Генерує PDF-документ зі звітом оцінок / замін уроків.
    
    buffer: файл-подібний об'єкт (io.BytesIO або HttpResponse)
    report_meta: словник з метаданими:
      - title: назва звіту
      - teacher_name: ім'я вчителя, що замінював / оцінював
      - target_teacher: ім'я основного вчителя (опціонально)
      - subject_name: назва предмету (опціонально)
      - date_from, date_to: діапазон дат
      - notes: примітки
      - school_name: назва школи
    submissions: список/QuerySet здач Submission
    mode: 'grades_only' (тільки таблиця) або 'detailed' (з роботами та рецензіями)
    """
    register_pdf_fonts()

    # Розміри: A4 Portrait, поля по 28pt (10 мм)
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=28,
        rightMargin=28,
        topMargin=26,
        bottomMargin=32
    )

    styles = getSampleStyleSheet()

    # Створюємо адаптовані стилі
    style_title = ParagraphStyle(
        'ReportTitle',
        parent=styles['Normal'],
        fontName='LiberationSans-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#0f172a'),
        alignment=1,  # Center
        spaceAfter=4
    )

    style_subtitle = ParagraphStyle(
        'ReportSubtitle',
        parent=styles['Normal'],
        fontName='LiberationSans',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor('#475569'),
        alignment=1,
        spaceAfter=12
    )

    style_meta_label = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='LiberationSans-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#334155')
    )

    style_meta_val = ParagraphStyle(
        'MetaValue',
        parent=styles['Normal'],
        fontName='LiberationSans',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#0f172a')
    )

    style_th = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='LiberationSans-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=1
    )

    style_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='LiberationSans',
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor('#1e293b')
    )

    style_cell_center = ParagraphStyle(
        'TableCellCenter',
        parent=style_cell,
        alignment=1
    )

    style_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=style_cell,
        fontName='LiberationSans-Bold'
    )

    style_grade = ParagraphStyle(
        'TableCellGrade',
        parent=styles['Normal'],
        fontName='LiberationSans-Bold',
        fontSize=9.5,
        leading=11,
        textColor=colors.HexColor('#1d4ed8'),
        alignment=1
    )

    story = []

    # 1. Заголовок школи
    school_name = report_meta.get('school_name', 'Електронний журнал завдань')
    story.append(Paragraph(school_name.upper(), style_subtitle))

    # 2. Назва звіту
    title_text = report_meta.get('title') or "ВІДОМІСТЬ ОЦІНОК / ЖУРНАЛ ЗАМІНИ УРОКІВ"
    story.append(Paragraph(title_text, style_title))

    # 3. Блок метаданих (Вчитель, Заміна, Предмет, Період)
    meta_rows = []
    
    teacher_line = report_meta.get('teacher_name') or 'Вчитель'
    if report_meta.get('target_teacher'):
        meta_rows.append([
            Paragraph("<b>Вчитель на заміні:</b>", style_meta_label),
            Paragraph(teacher_line, style_meta_val),
            Paragraph("<b>Основний вчитель:</b>", style_meta_label),
            Paragraph(report_meta.get('target_teacher'), style_meta_val)
        ])
    else:
        meta_rows.append([
            Paragraph("<b>Вчитель:</b>", style_meta_label),
            Paragraph(teacher_line, style_meta_val),
            Paragraph("<b>Дата формування:</b>", style_meta_label),
            Paragraph(datetime.now().strftime("%d.%m.%Y %H:%M"), style_meta_val)
        ])

    subj = report_meta.get('subject_name') or 'Всі предмети'
    period = report_meta.get('date_range_str') or 'Весь період'
    classes_str = report_meta.get('classes_str') or 'Всі класи'

    meta_rows.append([
        Paragraph("<b>Предмет:</b>", style_meta_label),
        Paragraph(subj, style_meta_val),
        Paragraph("<b>Період уроків:</b>", style_meta_label),
        Paragraph(period, style_meta_val)
    ])

    meta_rows.append([
        Paragraph("<b>Класи:</b>", style_meta_label),
        Paragraph(classes_str, style_meta_val),
        Paragraph("<b>Всього робіт/оцінок:</b>", style_meta_label),
        Paragraph(str(len(submissions)), style_meta_val)
    ])

    if report_meta.get('notes'):
        meta_rows.append([
            Paragraph("<b>Примітка:</b>", style_meta_label),
            Paragraph(report_meta.get('notes'), style_meta_val),
            Paragraph("", style_meta_label),
            Paragraph("", style_meta_val)
        ])

    meta_table = Table(meta_rows, colWidths=[110, 160, 110, 159])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    # 4. Статистика оцінок (середній бал, розподіл рівнів)
    grades_nums = []
    for s in submissions:
        if s.grade:
            # Спробуємо конвертувати в число
            clean = s.grade.replace(',', '.').strip()
            try:
                val = float(clean)
                grades_nums.append(val)
            except ValueError:
                pass

    if grades_nums:
        avg_grade = sum(grades_nums) / len(grades_nums)
        high_cnt = sum(1 for g in grades_nums if g >= 10)
        good_cnt = sum(1 for g in grades_nums if 7 <= g < 10)
        avg_cnt = sum(1 for g in grades_nums if 4 <= g < 7)
        low_cnt = sum(1 for g in grades_nums if g < 4)

        stats_text = (
            f"<b>Підсумок:</b> Середній бал: <b>{avg_grade:.1f}</b> з 12 | "
            f"Високий (10-12): <b>{high_cnt}</b> уч. | "
            f"Достатній (7-9): <b>{good_cnt}</b> уч. | "
            f"Середній (4-6): <b>{avg_cnt}</b> уч."
        )
        if low_cnt > 0:
            stats_text += f" | Початковий (1-3): <b>{low_cnt}</b> уч."

        style_stats = ParagraphStyle(
            'StatsBanner',
            parent=styles['Normal'],
            fontName='LiberationSans',
            fontSize=8,
            leading=10.5,
            textColor=colors.HexColor('#1e3a8a')
        )
        stats_table = Table([[Paragraph(stats_text, style_stats)]], colWidths=[539])
        stats_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eff6ff')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#bfdbfe')),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(stats_table)
        story.append(Spacer(1, 8))

    # 5. Головна таблиця оцінок
    # Колонки: № (22pt), Дата (50pt), Клас (36pt), Учень (130pt), Завдання (165pt), Оцінка (46pt), Примітки (90pt)
    # Сума: 22+50+36+130+165+46+90 = 539 pt (Рівно ширина робочої області A4)
    table_data = [
        [
            Paragraph("№", style_th),
            Paragraph("Дата", style_th),
            Paragraph("Клас", style_th),
            Paragraph("Учень (ПІБ)", style_th),
            Paragraph("Завдання / Тема", style_th),
            Paragraph("Оцінка", style_th),
            Paragraph("Примітки", style_th),
        ]
    ]

    for idx, sub in enumerate(submissions, 1):
        # Дата уроку / здачі / оцінювання
        d = sub.graded_at or sub.submitted_at
        d_str = d.strftime("%d.%m.%Y") if d else "—"

        # Клас
        cls_name = sub.class_group.name if sub.class_group else "—"

        # Учень
        student_display = sub.get_student_full_name()
        if sub.is_collective_work():
            student_display += " (група)"

        # Завдання
        task_title = sub.assignment.title if sub.assignment else "—"
        if sub.assignment and sub.assignment.subject:
            task_title = f"{sub.assignment.subject.name}: {task_title}"

        # Оцінка
        gr_val = sub.grade if sub.grade else "—"

        # Примітки (коментар, наявність ШІ, файли)
        notes_parts = []
        if sub.ai_generated_detected:
            if sub.assignment and sub.assignment.allow_ai:
                notes_parts.append("ШІ (дозв.)")
            else:
                notes_parts.append("ШІ виявлено!")
        if sub.teacher_comment:
            # Короткий відгук
            comment_short = (sub.teacher_comment[:40] + '...') if len(sub.teacher_comment) > 40 else sub.teacher_comment
            notes_parts.append(comment_short)
        elif sub.comment_student:
            c_short = (sub.comment_student[:30] + '...') if len(sub.comment_student) > 30 else sub.comment_student
            notes_parts.append(f"Уч.: {c_short}")

        notes_str = "; ".join(notes_parts) if notes_parts else "—"

        row = [
            Paragraph(str(idx), style_cell_center),
            Paragraph(d_str, style_cell_center),
            Paragraph(cls_name, style_cell_center),
            Paragraph(student_display, style_cell_bold),
            Paragraph(task_title, style_cell),
            Paragraph(f"<b>{gr_val}</b>", style_grade),
            Paragraph(notes_str, style_cell),
        ]
        table_data.append(row)

    if len(table_data) == 1:
        # Порожньо
        table_data.append([
            Paragraph("—", style_cell_center),
            Paragraph("—", style_cell_center),
            Paragraph("—", style_cell_center),
            Paragraph("Не знайдено жодної зданої роботи за обраними фільтрами", style_cell),
            Paragraph("—", style_cell_center),
            Paragraph("—", style_cell_center),
            Paragraph("—", style_cell_center),
        ])

    master_table = Table(table_data, colWidths=[22, 50, 36, 130, 165, 46, 90], repeatRows=1)
    
    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')), # насичений синій заголовок
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
    ]

    # Зебра для рядків
    for r in range(1, len(table_data)):
        if r % 2 == 0:
            t_style.append(('BACKGROUND', (0, r), (-1, r), colors.HexColor('#f8fafc')))

    master_table.setStyle(TableStyle(t_style))
    story.append(master_table)
    story.append(Spacer(1, 14))

    # 6. Якщо режим 'detailed' — додаємо розгорнуті картки робіт
    if mode == 'detailed' and len(submissions) > 0:
        story.append(PageBreak())
        
        style_h2 = ParagraphStyle(
            'DetailedH2',
            parent=styles['Normal'],
            fontName='LiberationSans-Bold',
            fontSize=12,
            leading=15,
            textColor=colors.HexColor('#1e293b'),
            spaceAfter=10
        )
        story.append(Paragraph("ДЕТАЛЬНИЙ ЗВІТ ПО ЗДАНИХ РОБОТАХ УЧНІВ", style_h2))

        for idx, sub in enumerate(submissions, 1):
            sub_elements = []

            # Заголовок картки
            card_title = f"#{idx}. {sub.get_student_full_name()} — Клас: {sub.class_group.name if sub.class_group else '—'}"
            d_sub = sub.submitted_at.strftime("%d.%m.%Y %H:%M") if sub.submitted_at else "—"
            
            sub_elements.append(Paragraph(
                f"<b>{card_title}</b> &nbsp;|&nbsp; Здано: {d_sub} &nbsp;|&nbsp; Оцінка: <b>{sub.grade or 'Без оцінки'}</b>",
                style_meta_label
            ))
            sub_elements.append(Spacer(1, 3))

            # Завдання
            task_info = f"<b>Завдання:</b> {sub.assignment.title if sub.assignment else '—'}"
            if sub.assignment and sub.assignment.subject:
                task_info += f" ({sub.assignment.subject.name})"
            sub_elements.append(Paragraph(task_info, style_cell))

            # Співавтори (якщо є)
            if sub.is_collective_work():
                members = ", ".join(sub.get_group_members_display())
                sub_elements.append(Paragraph(f"<b>Склад групи:</b> {members}", style_cell))

            # Відповідь/коментар учня
            if sub.comment_student:
                sub_elements.append(Spacer(1, 2))
                sub_elements.append(Paragraph(f"<b>Текст відповіді / коментар учня:</b>", style_cell_bold))
                sub_elements.append(Paragraph(sub.comment_student, style_cell))

            # Прикріплені файли
            attached_files = []
            if sub.file:
                attached_files.append(os.path.basename(sub.file.name))
            for f in sub.files.all():
                if f.file:
                    attached_files.append(os.path.basename(f.file.name))
            if attached_files:
                sub_elements.append(Spacer(1, 2))
                sub_elements.append(Paragraph(f"<b>Прикріплені файли:</b> {', '.join(attached_files)}", style_cell))

            if sub.link:
                sub_elements.append(Paragraph(f"<b>Посилання на роботу:</b> {sub.link}", style_cell))

            # Перевірка ШІ
            if sub.ai_generated_detected:
                ai_perm = "ШІ ДОЗВОЛЕНО вчителем" if (sub.assignment and sub.assignment.allow_ai) else "САМОСТІЙНА РОБОТА (ШІ не дозволено)"
                sub_elements.append(Spacer(1, 2))
                sub_elements.append(Paragraph(
                    f"<b>Перевірка ШІ:</b> Виявлено ознаки штучного інтелекту ({ai_perm}). "
                    f"Впевненість: {sub.ai_generated_confidence or '—'}",
                    style_cell
                ))

            # Відгук викладача
            if sub.teacher_comment:
                sub_elements.append(Spacer(1, 2))
                sub_elements.append(Paragraph(f"<b>Рецензія викладача:</b> <i>{sub.teacher_comment}</i>", style_cell))

            card_table = Table([[sub_elements]], colWidths=[539])
            card_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ]))

            story.append(KeepTogether([card_table, Spacer(1, 8)]))

    # 7. Підписи сторін (для офіційної передачі журналу заміни)
    story.append(Spacer(1, 14))
    signatures_data = [
        [
            Paragraph("<b>Вчитель, що проводив уроки / заміну:</b>", style_cell),
            Paragraph("<b>Оцінки до журналу прийняв(ла):</b>", style_cell)
        ],
        [
            Paragraph("______________________ / _____________________ /", style_cell),
            Paragraph("______________________ / _____________________ /", style_cell)
        ],
        [
            Paragraph("<font size='7' color='#64748b'>(підпис та ПІБ вчителя на заміні)</font>", style_cell),
            Paragraph("<font size='7' color='#64748b'>(підпис та ПІБ основного вчителя)</font>", style_cell)
        ],
        [
            Paragraph(f"Дата: «___» ____________ {datetime.now().year} р.", style_cell),
            Paragraph(f"Дата: «___» ____________ {datetime.now().year} р.", style_cell)
        ]
    ]

    sig_table = Table(signatures_data, colWidths=[270, 269])
    sig_table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))

    story.append(KeepTogether([sig_table]))

    # Будуємо PDF з двопрохідним Canvas
    doc.build(story, canvasmaker=NumberedCanvas)
