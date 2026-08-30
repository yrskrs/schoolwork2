"""
Глобальні контекстні процесори для шаблонів SchoolNet.
"""
from .models import Submission, School

def teacher_stats_context(request):
    """
    Додає у всі шаблони:
    - pending_submissions_count: кількість зданих робіт, що очікують перевірки та оцінки вчителем.
    - current_school: дані поточного навчального закладу.
    - is_superadmin_mode: чи увімкнено активний режим супер-адміністратора для поточного сеансу.
    """
    is_super_mode = False
    if request.user.is_authenticated and request.user.is_superuser:
        is_super_mode = bool(request.session.get('superadmin_mode', False))

    context = {
        'pending_submissions_count': 0,
        'current_school': None,
        'is_superadmin_mode': is_super_mode,
    }

    try:
        context['current_school'] = School.objects.first()
    except Exception:
        pass

    if request.user.is_authenticated and hasattr(request.user, 'teacher_profile'):
        try:
            teacher = request.user.teacher_profile
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
        except Exception:
            pass

    return context
