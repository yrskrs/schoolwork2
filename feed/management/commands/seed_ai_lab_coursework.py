"""Seed the local AI lab with realistic Ukrainian Informatics coursework."""
import json
import os
import runpy
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from feed.models import (AICriteriaPreset, Assignment, AssignmentFile, ClassGroup, Student,
                         Submission, SubmissionFile, Subject, Teacher)

SOURCE_BY_GRADE = {
    5: 'https://mon.gov.ua/static-objects/mon/sites/1/zagalna%20serednya/Navchalni.prohramy/2021/14.07/Model.navch.prohr.5-9.klas.NUSH-poetap.z.2022/Inform.osv.haluz.5-6-kl/Inform.5-6-kl.Ryvkind.ta.in.14.07.pdf',
    6: 'https://mon.gov.ua/static-objects/mon/sites/1/zagalna%20serednya/Navchalni.prohramy/2021/14.07/Model.navch.prohr.5-9.klas.NUSH-poetap.z.2022/Inform.osv.haluz.5-6-kl/Inform.5-6-kl.Ryvkind.ta.in.14.07.pdf',
    7: 'https://mon.gov.ua/static-objects/mon/sites/1/zagalna%20serednya/Navchalni.prohramy/2023/Model.navch.prohr.5-9.klas/Inform.osv.haluz.2023/Informatyka.7-9.kl.Hromko.ta.in.21.12.2023.pdf',
    8: 'https://mon.gov.ua/static-objects/mon/sites/1/zagalna%20serednya/Navchalni.prohramy/2023/Model.navch.prohr.5-9.klas/Inform.osv.haluz.2023/Informatyka.7-9.kl.Hromko.ta.in.21.12.2023.pdf',
    9: 'https://mon.gov.ua/static-objects/mon/sites/1/zagalna%20serednya/Navchalni.prohramy/2023/Model.navch.prohr.5-9.klas/Inform.osv.haluz.2023/Informatyka.7-9.kl.Hromko.ta.in.21.12.2023.pdf',
    10: 'https://mon.gov.ua/osvita-2/zagalna-serednya-osvita/osvitni-programi/navchalni-programi-dlya-10-11-klasiv',
    11: 'https://mon.gov.ua/osvita-2/zagalna-serednya-osvita/osvitni-programi/navchalni-programi-dlya-10-11-klasiv',
}

SCALE = (12, 10, 7, 5, 4)


def kind_label(kind):
    return {
        'docx': 'документ Word (.docx)',
        'xlsx': 'електронна таблиця Excel (.xlsx)',
        'pptx': 'презентація PowerPoint (.pptx)',
        'png': 'графічна схема / зображення (.png)',
        'html': 'HTML-документ (.html)',
        'txt': 'текстовий звіт (.txt)',
        'py': 'програма Python (.py)'
    }.get(kind, f'файл {kind}')


def level_label(mark):
    if mark <= 4:
        return 'початковий'
    if mark <= 6:
        return 'середній'
    if mark <= 9:
        return 'достатній'
    return 'високий'


class Command(BaseCommand):
    help = 'Створити 35 реалістичних навчальних завдань та 175 неоцінених учнівських робіт у локальній AI-лабораторії.'

    @transaction.atomic
    def handle(self, *args, **options):
        if os.environ.get('SCHOOLNET_LOCAL_LAB') != '1':
            raise CommandError('Команда дозволена лише в середовищі SCHOOLNET_LOCAL_LAB=1.')

        module = runpy.run_path(str(Path(settings.BASE_DIR, 'scripts', 'ai_lab_coursework_content.py')))
        lessons = module['LESSONS']
        if len(lessons) != 35 or {item['grade'] for item in lessons} != set(range(5, 12)):
            raise CommandError('Набір має містити рівно 5 уроків для кожного класу 5–11.')

        teacher = Teacher.objects.get(user__username='teacher_lab')
        subject = Subject.objects.get(name='Інформатика')

        # Видалення старих тестових завдань та здач teacher_lab перед перезаповненням
        old_assignments = Assignment.objects.filter(teacher=teacher)
        old_count = old_assignments.count()
        if old_count > 0:
            Submission.objects.filter(teacher=teacher).delete()
            old_assignments.delete()
            self.stdout.write(f'Видалено {old_count} попередніх тестових завдань та їх здач.')

        nus, _ = AICriteriaPreset.objects.get_or_create(
            name='AI-Lab НУШ: інформатика 5–9',
            defaults={
                'evaluation_type': 'nus_gr',
                'is_system': False,
                'description': 'Навчальний набір: НУШ, чотири групи результатів інформатичної галузі.',
                'gr_definitions': (
                    '[{"code":"ГР 1","name":"Інформація, дані, моделі"},'
                    '{"code":"ГР 2","name":"Створення інформаційних продуктів"},'
                    '{"code":"ГР 3","name":"Алгоритми та програмування"},'
                    '{"code":"ГР 4","name":"Цифрове середовище, безпека і відповідальність"}]'
                )
            }
        )

        general, _ = AICriteriaPreset.objects.get_or_create(
            name='AI-Lab: загальні критерії 10–11',
            defaults={
                'evaluation_type': 'traditional',
                'is_system': False,
                'description': 'Навчальний набір: загальні критерії 12-бальної шкали.'
            }
        )

        now = timezone.now()
        report_rows = []

        for lesson in lessons:
            group = ClassGroup.objects.get(name=f'{lesson["grade"]}-А')
            preset = nus if lesson['grade'] <= 9 else general

            assignment = Assignment.objects.create(
                teacher=teacher,
                title=f'Інформатика {lesson["grade"]}-А. Урок {lesson["lesson"]}: {lesson["title"]}',
                subject=subject,
                description=lesson['description'],
                status=Assignment.STATUS_PUBLISHED,
                published_at=now - timedelta(days=lesson['lesson']),
                due_date=(now + timedelta(days=14)).date(),
                default_ai_preset=preset,
                default_ai_grs='[]',
                allow_student_ai_check=False,
                allow_student_ai_understanding=False,
                allow_ai_usage=False,
                custom_criteria=lesson['custom_criteria'],
                link_url='https://mon.gov.ua/osvita-2/zagalna-serednya-osvita/osvitni-programi',
                link_label='Навчальні програми МОН України',
                teacher_ai_instructions='Тестовий набір даних. Автоматичну перевірку запускати вчителем за потреби.'
            )
            assignment.classes.add(group)

            teacher_att = lesson.get('teacher_attachment')
            if teacher_att:
                af_name = teacher_att['filename']
                af_bytes = teacher_att['content_bytes']
                af_source = teacher_att.get('is_task_source_for_ai', True)
                AssignmentFile.objects.create(
                    assignment=assignment,
                    file=ContentFile(af_bytes, name=af_name),
                    original_name=af_name,
                    is_task_source_for_ai=af_source
                )

            students = list(group.students.order_by('last_name', 'first_name'))
            for student_index, student in enumerate(students):
                # Ротація оцінок для кожного учня та уроку
                mark = SCALE[(student_index + lesson['lesson'] - 1) % len(SCALE)]
                sub_info = lesson['submissions'][mark]

                content = sub_info['content']
                comment_student = sub_info.get('comment_student', '')
                rationale = sub_info.get('rationale', '')
                gaps = sub_info.get('gaps', [])

                clean_last = student.last_name.lower()
                ext = lesson['kind'].lstrip('.')
                filename = f'{lesson["grade"]}_{lesson["lesson"]}_{clean_last}_robota.{ext}'

                submission = Submission(
                    assignment=assignment,
                    student=student,
                    first_name=student.first_name,
                    last_name=student.last_name,
                    class_group=group,
                    teacher=teacher,
                    comment_student=comment_student,
                    submitted_at=now - timedelta(days=lesson['lesson'], hours=student_index + 1)
                )

                if sub_info.get('content_bytes'):
                    raw_bytes = sub_info['content_bytes']
                else:
                    raw_bytes = content.encode('utf-8')

                upload = ContentFile(raw_bytes, name=filename)
                submission.file.save(filename, upload, save=False)
                submission.save()

                SubmissionFile.objects.create(
                    submission=submission,
                    file=submission.file,
                    original_name=filename,
                    file_size=len(raw_bytes)
                )

                report_rows.append({
                    'class_name': group.name,
                    'student': student.get_full_name(),
                    'lesson': lesson['lesson'],
                    'topic': lesson['title'],
                    'work_type': kind_label(ext),
                    'filename': filename,
                    'teacher_attachment': teacher_att['filename'] if teacher_att else '',
                    'assigned_task': lesson.get('assigned_task', 'Вся практична робота'),
                    'expected_mark': mark,
                    'level': level_label(mark),
                    'rationale': rationale,
                    'gaps': gaps,
                    'source': SOURCE_BY_GRADE[lesson['grade']],
                    'submission_id': submission.pk,
                    'assignment_id': assignment.pk
                })

        # Запис рядків звіту в JSON у спільний каталог та у /tmp
        json_rows = json.dumps(report_rows, ensure_ascii=False)
        shared_path = Path(settings.BASE_DIR, 'docs', 'audits', 'artifacts', 'ai_lab_coursework_import.json')
        try:
            shared_path.parent.mkdir(parents=True, exist_ok=True)
            shared_path.write_text(json_rows, encoding='utf-8')
        except Exception:
            pass

        log_path = Path('/tmp/ai_lab_coursework_import.log')
        try:
            log_path.write_text('AI_LAB_COURSEWORK_ROWS=' + json_rows + '\n', encoding='utf-8')
        except Exception:
            pass

        self.stdout.write('AI_LAB_COURSEWORK_ROWS=' + json_rows)
        self.stdout.write(self.style.SUCCESS(
            f'Успішно створено {len(lessons)} завдань та {len(report_rows)} реалістичних робіт учнів!'
        ))
