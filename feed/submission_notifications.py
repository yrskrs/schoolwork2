"""Small, cursor-based submission notices. No files, AI jobs or page rendering."""
from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


class SubmissionNotificationSettings(models.Model):
    teacher = models.OneToOneField('Teacher', on_delete=models.CASCADE, related_name='submission_notification_settings')
    teacher_enabled = models.BooleanField('Сповіщення для мене', default=False)
    teacher_sound = models.BooleanField('Звук для мене', default=False)
    students_enabled = models.BooleanField('Сповіщення для учнів', default=False)
    students_sound = models.BooleanField('Звук для учнів', default=False)
    students_click_opens_task = models.BooleanField('Натискання учнем відкриває завдання', default=True)
    students_all_classes = models.BooleanField('Показувати учням без прив’язки до класу', default=True)
    students_background_active = models.BooleanField('Сповіщати у фонових вкладках', default=True)
    show_class_badge = models.BooleanField('Показувати назву класу в картці', default=False)
    duration_seconds = models.PositiveSmallIntegerField(
        'Тривалість показу (секунди)', default=5,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
    )
    until_dismissed = models.BooleanField('Показувати до натискання', default=False)


class SubmissionNotification(models.Model):
    # One event per completed form, rather than one per coauthor or file save.
    submission = models.OneToOneField('Submission', on_delete=models.CASCADE, related_name='live_notification')
    teacher = models.ForeignKey('Teacher', on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    for_teacher = models.BooleanField(default=False)
    for_students = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=['teacher', 'id'], name='submission_notice_teacher_idx')]


class SubmissionNotificationSettingsForm(forms.ModelForm):
    class Meta:
        model = SubmissionNotificationSettings
        fields = ['teacher_enabled', 'teacher_sound', 'students_enabled', 'students_sound',
                  'students_click_opens_task', 'students_all_classes', 'students_background_active',
                  'show_class_badge', 'duration_seconds', 'until_dismissed']
        widgets = {'duration_seconds': forms.NumberInput(attrs={'class': 'form-input', 'min': 1, 'max': 60})}


def record_submission_notification(submission):
    """Called inside SubmissionForm's atomic block, after all uploads succeed."""
    audience = SubmissionNotificationSettings.objects.filter(teacher_id=submission.teacher_id).values(
        'teacher_enabled', 'students_enabled').first()
    if audience and (audience['teacher_enabled'] or audience['students_enabled']):
        SubmissionNotification.objects.create(
            submission=submission, teacher_id=submission.teacher_id,
            for_teacher=audience['teacher_enabled'], for_students=audience['students_enabled'],
        )


@require_GET
@never_cache
def submission_notifications(request):
    teacher = getattr(request.user, 'teacher_profile', None) if request.user.is_authenticated else None
    # An authenticated non-teacher cannot obtain student notices by changing a role parameter.
    is_teacher = teacher is not None
    class_id = request.GET.get('class', '')
    if class_id and (not class_id.isdecimal() or len(class_id) > 9):
        return JsonResponse({'error': 'Некоректний клас.'}, status=400)
    since = request.GET.get('since')
    if since is not None and (not since.isdecimal() or len(since) > 18):
        return JsonResponse({'error': 'Некоректний курсор.'}, status=400)

    settings = SubmissionNotificationSettings.objects.all()
    if is_teacher:
        settings = settings.filter(teacher=teacher, teacher_enabled=True)
    elif request.user.is_authenticated and not is_teacher:
        settings = settings.none()
    else:
        # Учні: якщо передано class_id, шукаємо вчителів з увімкненими сповіщеннями;
        # якщо class_id немає, підходять ті вчителі, де увімкнено students_all_classes.
        if class_id:
            settings = settings.filter(students_enabled=True)
        else:
            settings = settings.filter(students_enabled=True, students_all_classes=True)

    policies = {
        str(setting.teacher_id): {
            'sound': setting.teacher_sound if is_teacher else setting.students_sound,
            'duration': setting.duration_seconds,
            'sticky': setting.until_dismissed,
            'click_opens_task': True if is_teacher else setting.students_click_opens_task,
            'all_classes': setting.students_all_classes,
            'background_active': setting.students_background_active,
            'show_class': setting.show_class_badge,
        }
        for setting in settings
    }
    high_water = SubmissionNotification.objects.order_by('-pk').values_list('pk', flat=True).first() or 0
    payload = {'cursor': high_water, 'events': [], 'policies': policies,
               'enabled': bool(policies), 'poll_after': 5 if policies else 30, 'has_more': False}
    # Establish a baseline on first visit; enabling notices never replays old submissions.
    if since is None or not policies:
        return JsonResponse(payload)
    since = int(since)
    if since > high_water:  # A restored database may have a lower sequence.
        return JsonResponse(payload)
    events = SubmissionNotification.objects.filter(
        pk__gt=since, pk__lte=high_water, teacher_id__in=policies,
        submission__assignment__teacher_id=models.F('teacher_id'),
    )
    if is_teacher:
        events = events.filter(for_teacher=True)
    else:
        events = events.filter(
            for_students=True,
            submission__assignment__status='published',
            submission__assignment__is_individual=False,
        )
        all_class_teachers = [s.teacher_id for s in settings if s.students_all_classes]
        single_class_teachers = [s.teacher_id for s in settings if not s.students_all_classes]

        class_query = models.Q(teacher_id__in=all_class_teachers)
        if class_id and single_class_teachers:
            class_query |= models.Q(teacher_id__in=single_class_teachers, submission__class_group_id=int(class_id))
        events = events.filter(class_query)

    rows = list(events.order_by('pk').values(
        'pk', 'teacher_id', 'submission_id', 'submission__last_name', 'submission__first_name',
        'submission__class_group__name', 'submission__assignment_id', 'submission__assignment__title',
        'submission__is_resubmission', 'submission__resubmission_attempt', 'submission__group_authors',
    )[:26])
    payload['has_more'] = len(rows) > 25
    for row in rows[:25]:
        policy = policies.get(str(row['teacher_id']), {})
        if is_teacher:
            target_url = reverse('view_file', args=[row['submission_id']])
        elif policy.get('click_opens_task', True):
            target_url = reverse('assignment_detail', args=[row['submission__assignment_id']])
        else:
            target_url = ''

        payload['events'].append({
            'id': row['pk'],
            'teacher_id': str(row['teacher_id']),
            'name': f"{row['submission__last_name']} {row['submission__first_name']}".strip(),
            'class_name': row['submission__class_group__name'],
            'show_class': policy.get('show_class', False),
            'assignment': row['submission__assignment__title'][:120],
            'resubmission': row['submission__is_resubmission'],
            'attempt': row['submission__resubmission_attempt'],
            'authors': row['submission__group_authors'],
            'url': target_url,
        })
    if payload['has_more']:
        payload['cursor'] = rows[24]['pk']
    return JsonResponse(payload)
