"""
Глобальні контекстні процесори для шаблонів SchoolNet.
"""
from .models import Submission, School, BellSchedule, AssignmentRescheduleLog, Teacher

def teacher_stats_context(request):
    """
    Додає у всі шаблони:
    - pending_submissions_count: кількість зданих робіт, що очікують перевірки та оцінки вчителем.
    - current_school: дані поточного навчального закладу.
    - is_superadmin_mode: чи увімкнено активний режим супер-адміністратора для поточного сеансу.
    - all_bell_slots: всі налаштовані дзвінки школи.
    - reschedule_reason_choices: типи причин перенесення занять.
    """
    is_super_mode = False
    if request.user.is_authenticated and request.user.is_superuser:
        is_super_mode = bool(request.session.get('superadmin_mode', False))

    context = {
        'pending_submissions_count': 0,
        'current_school': None,
        'is_superadmin_mode': is_super_mode,
        'all_bell_slots': [],
        'reschedule_reason_choices': AssignmentRescheduleLog.REASON_CHOICES,
        'teacher_conducted_lessons': None,
    }

    try:
        context['current_school'] = School.objects.first()
        context['all_bell_slots'] = list(BellSchedule.objects.all().order_by('lesson_number'))
    except Exception:
        pass

    if request.user.is_authenticated:
        try:
            teacher = getattr(request.user, 'teacher_profile', None)
            if not teacher and (request.user.is_superuser or is_super_mode):
                teacher = Teacher.objects.first()

            if teacher:
                if is_super_mode:
                    count = Submission.objects.filter(grade__isnull=True).count() + Submission.objects.filter(grade='').count()
                else:
                    count = Submission.objects.filter(
                        assignment__teacher=teacher,
                        grade__isnull=True
                    ).count() + Submission.objects.filter(
                        assignment__teacher=teacher,
                        grade=''
                    ).count()
                context['pending_submissions_count'] = count

                # Інформер поточного уроку вчителя
                from .utils import get_teacher_live_lesson_status, get_teacher_upcoming_notifications
                context['live_lesson_status'] = get_teacher_live_lesson_status(teacher)

                # Центр сповіщень
                notifs = get_teacher_upcoming_notifications(teacher)
                context['upcoming_notifications'] = notifs
                context['unread_notifications_count'] = len([n for n in notifs if n.get('type') == 'missing_task'])

                # Лічильник проведених уроків вчителя
                context['teacher_conducted_lessons'] = teacher.get_conducted_lessons_info()
        except Exception:
            pass

    return context
