"""Choose the closest lesson, including one that is already in progress."""
from datetime import timedelta

from django.utils import timezone


def next_lesson(schedules, reference, subject_id=None):
    reference = timezone.localtime(reference)
    candidates = []
    for schedule in schedules:
        if subject_id and schedule.subject_id not in (None, subject_id):
            continue
        delta = (schedule.day_of_week - reference.isoweekday()) % 7
        # A lesson remains available from its start through its end, not just
        # before the bell. A finished lesson belongs to the following week.
        if delta == 0 and reference.time() >= schedule.bell_slot.end_time:
            delta = 7
        target = reference.date() + timedelta(days=delta)
        candidates.append((target, schedule.bell_slot.start_time, schedule.pk, schedule))
    if not candidates:
        return None, None
    target, _, _, schedule = min(candidates, key=lambda item: item[:3])
    return schedule, target
