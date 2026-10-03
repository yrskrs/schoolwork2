"""Aggregate usage logs into bounded, timezone-aware chart data."""
from datetime import timedelta

from django.db.models import Count, Min, Sum
from django.db.models.functions import TruncDate, TruncHour, TruncMonth
from django.utils import timezone


def build_usage_chart(logs, now, start_date=None):
    local_now = timezone.localtime(now)
    if start_date is not None:
        logs = logs.filter(created_at__gte=start_date)
        first = timezone.localtime(start_date)
    else:
        earliest = logs.aggregate(first=Min('created_at'))['first']
        if earliest is None:
            return {'labels': [], 'rows': [], 'models': [], 'providers': [], 'actions': [], 'resolution': 'day'}
        first = timezone.localtime(earliest)

    tz = timezone.get_current_timezone()
    span = local_now - first
    if start_date is not None and span <= timedelta(days=1):
        resolution = 'hour'
        truncate = TruncHour('created_at', tzinfo=tz)
        cursor = first.replace(minute=0, second=0, microsecond=0)
        last = local_now.replace(minute=0, second=0, microsecond=0)
        key = lambda value: timezone.localtime(value).strftime('%Y-%m-%dT%H')
        label = lambda value: value.strftime('%d.%m %H:00')
    elif start_date is None and span > timedelta(days=90):
        resolution = 'month'
        truncate = TruncMonth('created_at', tzinfo=tz)
        cursor = first.date().replace(day=1)
        last = local_now.date().replace(day=1)
        key = lambda value: value.strftime('%Y-%m')
        label = lambda value: value.strftime('%m.%Y')
    else:
        resolution = 'day'
        truncate = TruncDate('created_at', tzinfo=tz)
        cursor, last = first.date(), local_now.date()
        key = lambda value: value.isoformat()
        label = lambda value: value.strftime('%d.%m.%Y')

    keys, labels = [], []
    while cursor <= last:
        keys.append(key(cursor))
        labels.append(label(cursor))
        if resolution == 'month':
            cursor = cursor.replace(year=cursor.year + (cursor.month == 12), month=cursor.month % 12 + 1)
        else:
            cursor += timedelta(hours=1) if resolution == 'hour' else timedelta(days=1)
    positions = {value: index for index, value in enumerate(keys)}
    rows = []
    grouped = logs.filter(created_at__lte=now).annotate(bucket=truncate).values(
        'bucket', 'model_name', 'provider', 'action', 'is_success',
    ).annotate(requests=Count('pk'), total_tokens=Sum('total_tokens'),
               prompt_tokens=Sum('prompt_tokens'), completion_tokens=Sum('completion_tokens')).order_by('bucket')
    for row in grouped:
        bucket = row.pop('bucket')
        if resolution == 'month':
            bucket = timezone.localtime(bucket).date()
        position = positions.get(key(bucket))
        if position is not None:
            rows.append({'index': position, **row})
    return {
        'labels': labels, 'rows': rows, 'resolution': resolution,
        'models': sorted({row['model_name'] for row in rows}),
        'providers': sorted({row['provider'] for row in rows}),
        'actions': sorted({row['action'] for row in rows}),
    }
