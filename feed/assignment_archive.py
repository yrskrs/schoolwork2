"""Portable assignment bundles, shared by single and bulk export."""
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import PurePosixPath

from django import forms
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime, parse_time

from .assignment_ai_policy import export_ai_policy, import_ai_policy
from .models import (
    Assignment, AssignmentFile, AssignmentLink, AssignmentYouTubeLink,
    AssignmentScheduleTarget, BellSchedule, ClassGroup, Subject,
)


def archive_assignments(teacher):
    return Assignment.objects.filter(teacher=teacher).select_related(
        'subject', 'default_ai_preset'
    ).prefetch_related('classes', 'files', 'additional_links', 'youtube_links',
                       'schedule_targets__class_group', 'schedule_targets__bell_slot')


def _iso(value):
    return value.isoformat() if value is not None else None


def assignment_occurrences(assignment):
    """Lesson dates take precedence over publication/creation dates."""
    targets = list(assignment.schedule_targets.all())
    if targets:
        return [{
            'date': target.target_date,
            'weekday': target.target_date.isoweekday() if target.target_date else target.target_day_of_week,
            'lesson': target.bell_slot.lesson_number if target.bell_slot else None,
            'class_name': target.class_group.name,
        } for target in targets]
    stamp = (assignment.scheduled_at if assignment.status == Assignment.STATUS_SCHEDULED else None)
    stamp = stamp or assignment.published_at or assignment.created_at
    day = timezone.localtime(stamp).date()
    return [{'date': day, 'weekday': day.isoweekday(), 'lesson': None, 'class_name': ''}]


def export_bundle(assignments, teacher):
    buffer = tempfile.TemporaryFile(mode='w+b')
    try:
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            rows = []
            for assignment in assignments:
                files = []
                for index, attachment in enumerate(assignment.files.all(), 1):
                    if not attachment.file or not attachment.file.storage.exists(attachment.file.name):
                        raise ValueError(f'Не знайдено файл «{attachment.original_name}» завдання «{assignment.title}».')
                    filename = os.path.basename((attachment.original_name or attachment.file.name).replace('\\', '/'))
                    path = f'files/{assignment.pk}/{index}_{filename}'
                    with attachment.file.open('rb') as source, archive.open(path, 'w') as destination:
                        shutil.copyfileobj(source, destination)
                    files.append({'original_name': attachment.original_name or filename, 'zip_path': path,
                                  'is_task_source_for_ai': attachment.is_task_source_for_ai,
                                  'uploaded_at': _iso(attachment.uploaded_at)})
                targets = []
                for target in assignment.schedule_targets.all():
                    slot = target.bell_slot
                    targets.append({
                        'class_name': target.class_group.name, 'target_date': _iso(target.target_date),
                        'target_day_of_week': target.target_day_of_week,
                        'lesson_number': slot.lesson_number if slot else None,
                        'bell_schedule': {'start_time': _iso(slot.start_time), 'end_time': _iso(slot.end_time),
                                          'order': slot.order} if slot else None,
                    })
                scalar_fields = ('title', 'description', 'is_individual', 'student_name', 'link_url',
                                 'link_label', 'youtube_url', 'status', 'allow_student_ai_check',
                                 'allow_ai_usage', 'custom_criteria', 'no_submission_required')
                row = {name: getattr(assignment, name) for name in scalar_fields}
                row.update({name: _iso(getattr(assignment, name)) for name in
                            ('due_date', 'scheduled_at', 'published_at', 'created_at', 'updated_at', 'unarchived_at')})
                row.update({
                    'subject_name': assignment.subject.name if assignment.subject else None,
                    'subject_color': assignment.subject.color if assignment.subject else '#2563eb',
                    'subject_icon': assignment.subject.icon if assignment.subject else '📚',
                    'class_names': [group.name for group in assignment.classes.all()],
                    'classes': [{'name': group.name, 'grade': group.grade, 'letter': group.letter}
                                for group in assignment.classes.all()],
                    'files': files, 'schedule_targets': targets,
                    'additional_links': [{'url': link.url, 'label': link.label}
                                         for link in assignment.additional_links.all()],
                    'youtube_links': [{'url': link.url, 'title': link.title}
                                      for link in assignment.youtube_links.all()],
                    'ai_policy': export_ai_policy(assignment, archive),
                })
                rows.append(row)
            archive.writestr('manifest.json', json.dumps({
                'version': '1.1', 'exported_at': timezone.now().isoformat(),
                'teacher_name': teacher.full_name, 'assignments': rows,
            }, ensure_ascii=False, indent=2))
        buffer.seek(0)
        return buffer
    except Exception:
        buffer.close()
        raise


def _date(value, field, parser=parse_date):
    if value in (None, ''):
        return None
    try:
        parsed = parser(value) if isinstance(value, str) else None
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        raise ValueError(f'Некоректне значення «{field}» в архіві.')
    if parser is parse_datetime and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _validate_manifest(archive):
    entries = archive.infolist()
    max_size = getattr(settings, 'ASSIGNMENT_ARCHIVE_MAX_BYTES', 1024 * 1024 * 1024)
    if len(entries) > 10000 or sum(entry.file_size for entry in entries) > max_size:
        raise ValueError('Архів завдань перевищує допустимий розмір.')
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError('В архіві є файли з однаковими шляхами.')
    if 'manifest.json' not in names:
        raise ValueError('Некоректний ZIP-архів: відсутній файл опису manifest.json.')
    if archive.getinfo('manifest.json').file_size > 16 * 1024 * 1024:
        raise ValueError('Опис архіву завдань завеликий.')
    manifest = json.loads(archive.read('manifest.json').decode('utf-8'))
    if not isinstance(manifest, dict) or manifest.get('version', '1.0') not in ('1.0', '1.1'):
        raise ValueError('Непідтримуваний формат архіву завдань.')
    rows = manifest.get('assignments')
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise ValueError('В архіві немає коректного списку завдань.')
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Некоректний опис завдання в архіві.')
        for name in ('class_names', 'classes', 'files', 'additional_links', 'youtube_links', 'schedule_targets'):
            values = row.get(name, [])
            if not isinstance(values, list) or any(not isinstance(item, str if name == 'class_names' else dict) for item in values):
                raise ValueError(f'Некоректний список «{name}» в архіві.')
        policy = row.get('ai_policy')
        if policy is not None and not isinstance(policy, dict):
            raise ValueError('Некоректні критерії оцінювання в архіві.')
        if policy:
            presets = policy.get('presets') or {}
            classes = policy.get('classes') or {}
            if (not isinstance(presets, dict) or any(not isinstance(item, dict) for item in presets.values())
                    or not isinstance(classes, dict) or any(not isinstance(item, dict) for item in classes.values())
                    or not isinstance(policy.get('default_grs') or [], list)):
                raise ValueError('Некоректні шаблони оцінювання в архіві.')
        target_classes = [item.get('class_name') for item in row.get('schedule_targets', [])]
        if any(not isinstance(name, str) or not name for name in target_classes) or len(target_classes) != len(set(target_classes)):
            raise ValueError('Некоректні або повторні прив’язки класів до уроків.')
        if any(item.get('bell_schedule') is not None and not isinstance(item['bell_schedule'], dict)
               for item in row.get('schedule_targets', [])):
            raise ValueError('Некоректний розклад дзвінків в архіві.')
        paths = [item.get('zip_path') for item in row.get('files', [])]
        paths += [item.get('document_zip_path') for item in ((policy or {}).get('presets') or {}).values()
                  if isinstance(item, dict) and item.get('document_zip_path')]
        for path in paths:
            if (not isinstance(path, str) or '\\' in path or PurePosixPath(path).is_absolute()
                    or '..' in PurePosixPath(path).parts or path not in names or archive.getinfo(path).is_dir()):
                raise ValueError(f'В архіві відсутній або некоректний файл: {path}.')
    # Check CRCs before creating records or writing uploaded files.
    broken = archive.testzip()
    if broken:
        raise ValueError(f'Пошкоджений файл в архіві: {broken}.')
    return rows


def import_bundle(upload, teacher):
    saved_files = []
    try:
        with zipfile.ZipFile(upload) as archive, transaction.atomic():
            rows = _validate_manifest(archive)
            file_count = 0
            for row in rows:
                subject = None
                if row.get('subject_name'):
                    subject, _ = Subject.objects.get_or_create(name=row['subject_name'], defaults={
                        'color': row.get('subject_color', '#2563eb'), 'icon': row.get('subject_icon', '📚'),
                        'created_by': teacher,
                    })
                    teacher.subjects.add(subject)
                defaults = {'title': 'Імпортоване завдання', 'status': Assignment.STATUS_PUBLISHED,
                            'allow_student_ai_check': False}
                scalar_fields = ('title', 'description', 'is_individual', 'student_name', 'link_url',
                                 'link_label', 'youtube_url', 'status', 'allow_student_ai_check',
                                 'allow_ai_usage', 'custom_criteria', 'no_submission_required')
                values = {name: row.get(name, defaults.get(name, Assignment._meta.get_field(name).get_default()))
                          for name in scalar_fields}
                assignment = Assignment(teacher=teacher, subject=subject, **values)
                for name in scalar_fields:
                    values[name] = Assignment._meta.get_field(name).clean(values[name], assignment)
                    setattr(assignment, name, values[name])
                assignment.due_date = _date(row.get('due_date'), 'due_date')
                assignment.scheduled_at = _date(row.get('scheduled_at'), 'scheduled_at', parse_datetime)
                assignment.published_at = _date(row.get('published_at'), 'published_at', parse_datetime)
                if assignment.status == Assignment.STATUS_SCHEDULED and not assignment.scheduled_at:
                    raise ValueError('Відкладене завдання не має часу публікації. Вкажіть його у вихідному завданні та повторіть експорт.')
                # Preserve blank labels without fetching external sites during import.
                super(Assignment, assignment).save()
                groups = {}
                class_data = {item['name']: item for item in row.get('classes', []) if item.get('name')}
                class_names = set(row.get('class_names', [])) | set(class_data)
                class_names.update(item['class_name'] for item in row.get('schedule_targets', []) if item.get('class_name'))
                for name in sorted(class_names):
                    if not name or not isinstance(name, str) or len(name) > 20:
                        raise ValueError('Некоректна назва класу в архіві.')
                    info = class_data.get(name, {})
                    group, _ = ClassGroup.objects.get_or_create(name=name, defaults={
                        'grade': info.get('grade', 9), 'letter': info.get('letter', 'А'), 'created_by': teacher,
                    })
                    groups[name] = group
                    assignment.classes.add(group)
                    teacher.classes.add(group)
                import_ai_policy(assignment, row.get('ai_policy'), archive, saved_files=saved_files)
                for item in row.get('additional_links', []):
                    link = AssignmentLink(assignment=assignment, url=item.get('url', ''), label=item.get('label', ''))
                    super(AssignmentLink, link).save()
                for item in row.get('youtube_links', []):
                    AssignmentYouTubeLink.objects.create(assignment=assignment, url=item.get('url', ''), title=item.get('title', ''))
                for item in row.get('files', []):
                    original = item.get('original_name') or os.path.basename(item['zip_path'])
                    name = os.path.basename(original.replace('\\', '/'))
                    attachment = AssignmentFile(assignment=assignment, original_name=original,
                                                is_task_source_for_ai=bool(item.get('is_task_source_for_ai')))
                    attachment.file.save(name, ContentFile(archive.read(item['zip_path'])), save=False)
                    saved_files.append((attachment.file.storage, attachment.file.name))
                    attachment.save()
                    uploaded_at = _date(item.get('uploaded_at'), 'uploaded_at', parse_datetime)
                    if uploaded_at:
                        AssignmentFile.objects.filter(pk=attachment.pk).update(uploaded_at=uploaded_at)
                    file_count += 1
                for item in row.get('schedule_targets', []):
                    group = groups.get(item.get('class_name'))
                    if group is None:
                        raise ValueError('Прив’язка до уроку не має класу.')
                    target_date = _date(item.get('target_date'), 'target_date')
                    weekday = item.get('target_day_of_week')
                    if 'target_day_of_week' not in item and target_date:
                        weekday = target_date.isoweekday()
                    if weekday is not None and (type(weekday) is not int or not 1 <= weekday <= 7):
                        raise ValueError('Некоректний день тижня в архіві.')
                    slot = None
                    number = item.get('lesson_number')
                    if number is not None:
                        if type(number) is not int or not 1 <= number <= 32767:
                            raise ValueError('Некоректний номер уроку в архіві.')
                        slot = BellSchedule.objects.filter(lesson_number=number).first()
                        if slot is None:
                            times = item.get('bell_schedule') or {}
                            start = _date(times.get('start_time'), 'start_time', parse_time)
                            end = _date(times.get('end_time'), 'end_time', parse_time)
                            if start is None or end is None:
                                raise ValueError(f'Додайте урок №{number} у розклад дзвінків і повторіть імпорт старого архіву.')
                            slot = BellSchedule.objects.create(lesson_number=number, start_time=start, end_time=end,
                                                               order=times.get('order', number))
                    AssignmentScheduleTarget.objects.create(assignment=assignment, class_group=group,
                                                            target_date=target_date, target_day_of_week=weekday, bell_slot=slot)
                # Restore timestamps set automatically when creating the new records.
                preserved = {'status': values['status']}
                for name in ('scheduled_at', 'published_at', 'unarchived_at', 'created_at', 'updated_at'):
                    if name in row:
                        stamp = _date(row[name], name, parse_datetime)
                        if stamp is not None or name not in ('created_at', 'updated_at'):
                            preserved[name] = stamp
                if 'published_at' not in row and values['status'] == Assignment.STATUS_PUBLISHED:
                    preserved['published_at'] = timezone.now()
                Assignment.objects.filter(pk=assignment.pk).update(**preserved)
            return len(rows), file_count
    except Exception:
        for storage, name in reversed(saved_files):
            storage.delete(name)
        raise


WEEKDAYS = [(1, 'Понеділок'), (2, 'Вівторок'), (3, 'Середа'), (4, 'Четвер'),
            (5, 'П’ятниця'), (6, 'Субота'), (7, 'Неділя')]


class ExportSelectionForm(forms.Form):
    date = forms.DateField(label='Дата уроку / від', required=False,
                           widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}, format='%Y-%m-%d'))
    date_to = forms.DateField(label='По дату (для періоду)', required=False,
                              widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}, format='%Y-%m-%d'))
    weekdays = forms.MultipleChoiceField(label='Дні тижня', choices=WEEKDAYS, required=False,
                                         widget=forms.CheckboxSelectMultiple)
    lesson = forms.ChoiceField(label='Номер уроку', required=False,
                               widget=forms.Select(attrs={'class': 'form-input'}))
    status = forms.ChoiceField(label='Статус завдання', required=False,
                               choices=[('', 'Усі статуси')] + Assignment.STATUS_CHOICES,
                               widget=forms.Select(attrs={'class': 'form-input'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lesson'].choices = [('', 'Усі уроки')] + [
            (str(number), f'{number} урок') for number in BellSchedule.objects.values_list('lesson_number', flat=True)]

    def clean(self):
        values = super().clean()
        start, end = values.get('date'), values.get('date_to')
        if end and not start:
            self.add_error('date', 'Для періоду вкажіть початкову дату.')
        if start and end and end < start:
            self.add_error('date_to', 'Кінцева дата має бути не раніше початкової.')
        return values


def export_selection_groups(assignments, filters):
    groups = {}
    weekdays = {int(day) for day in filters.get('weekdays', [])}
    start = filters.get('date')
    end = filters.get('date_to') or start
    lesson = int(filters['lesson']) if filters.get('lesson') else None
    labels = dict(WEEKDAYS)
    for assignment in assignments:
        if filters.get('status') and assignment.status != filters['status']:
            continue
        occurrences = assignment_occurrences(assignment)
        matches = [item for item in occurrences if
                   (not start or (item['date'] is not None and start <= item['date'] <= end))
                   and (not weekdays or item['weekday'] in weekdays)
                   and (lesson is None or item['lesson'] == lesson)]
        if not matches:
            continue
        def sort_key(item):
            return (0 if item['date'] else 1, -item['date'].toordinal() if item['date'] else item['weekday'] or 8,
                    item['lesson'] or 999)
        for item in occurrences:
            day_name = labels.get(item['weekday'], 'Без дати')
            item['label'] = f"{day_name}, {item['date']:%d.%m.%Y}" if item['date'] else day_name
        added = set()
        for occurrence in sorted(matches, key=sort_key):
            key = occurrence['date'].isoformat() if occurrence['date'] else f"weekday-{occurrence['weekday']}"
            if key in added:
                continue
            added.add(key)
            if key not in groups:
                day_name = labels.get(occurrence['weekday'], 'Без дати уроку')
                groups[key] = {'key': key,
                               'title': f"{day_name}, {occurrence['date']:%d.%m.%Y}" if occurrence['date'] else day_name,
                               'sort_key': sort_key(occurrence)[:2], 'rows': []}
            groups[key]['rows'].append({'assignment': assignment, 'occurrences': occurrences,
                                        'lesson': occurrence['lesson'] or 999})
    result = sorted(groups.values(), key=lambda group: group['sort_key'])
    for group in result:
        group['rows'].sort(key=lambda row: (row['lesson'], row['assignment'].title, row['assignment'].pk))
    return result
