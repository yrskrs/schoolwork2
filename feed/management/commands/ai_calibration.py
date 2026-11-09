"""Compare produced AI marks with reference marks (calibration, no API calls)."""
import csv
import json
import statistics
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from feed.models import AIJob, Submission

DEFAULT_LABELS = 'docs/audits/AI_LAB_COURSEWORK_EXPECTED_RESULTS_2026-11-09.csv'


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class Command(BaseCommand):
    help = 'Порахувати калібрування оцінок ШІ проти еталонних балів (без викликів API).'

    def add_arguments(self, parser):
        parser.add_argument('--labels', default=DEFAULT_LABELS, help='CSV з колонками submission_id, expected_mark')
        parser.add_argument('--limit', type=int, default=0, help='Обмежити кількість робіт')
        parser.add_argument('--class-name', default='', help='Лише один клас, наприклад 8-А')
        parser.add_argument('--json-out', default='', help='Записати результат у JSON-файл')
        parser.add_argument('--include-failed', action='store_true', help='Врахувати невдалі перевірки')
        parser.add_argument('--include-ungraded', action='store_true', help='Додати роботи без жодної перевірки')
        parser.add_argument('--submission-ids', default='', help='Лише вказані submission_id через кому')

    def handle(self, *args, **options):
        path = Path(options['labels'])
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if not path.exists():
            raise CommandError(f'Немає файлу еталонів: {path}')
        rows = list(csv.DictReader(path.open(encoding='utf-8-sig')))
        if options['class_name']:
            rows = [row for row in rows if row.get('class_name') == options['class_name']]
        if options['submission_ids']:
            wanted = {_as_int(part) for part in options['submission_ids'].split(',') if part.strip()}
            rows = [row for row in rows if _as_int(row.get('submission_id')) in wanted]
        if options['limit']:
            rows = rows[:options['limit']]

        report = []
        for row in rows:
            submission = Submission.objects.filter(pk=_as_int(row.get('submission_id'))).first()
            if not submission:
                continue
            job = AIJob.objects.filter(
                submission=submission, kind__in=('teacher_check', 'batch_check'),
                status__in=['succeeded'] + (['failed'] if options['include_failed'] else [])
            ).order_by('-created_at').first()
            result = (job.result or {}) if job else {}
            ai_raw = result.get('suggested_grade')
            if ai_raw in (None, '') and not job:
                ai_raw = submission.ai_suggested_grade
            if not job and ai_raw in (None, '') and not options['include_ungraded']:
                continue
            expected = _as_int(row.get('expected_mark'))
            ai_int = _as_int(ai_raw)
            report.append({
                'class': row.get('class_name', ''),
                'lesson': row.get('lesson', ''),
                'student': row.get('student', ''),
                'work_type': row.get('work_type', ''),
                'submission': submission.pk,
                'job': str(job.pk) if job else '',
                'job_status': job.status if job else 'none',
                'expected': expected,
                'ai_raw': ai_raw,
                'ai': ai_int,
                'rework': str(ai_raw).strip() == 'Доопрацювати',
                'blocked': ai_raw in (None, ''),
                'model': result.get('model_used', ''),
                'needs_review': result.get('needs_teacher_review'),
                'partial': result.get('partial_assessment'),
            })

        numeric = [item for item in report if item['ai'] is not None and item['expected'] is not None]
        errors = [item['ai'] - item['expected'] for item in numeric]
        exact = [item for item in numeric if item['ai'] == item['expected']]
        within1 = [item for item in numeric if 0 < abs(item['ai'] - item['expected']) <= 1]
        within2 = [item for item in numeric if abs(item['ai'] - item['expected']) == 2]
        far = [item for item in numeric if abs(item['ai'] - item['expected']) >= 3]
        rework = [item for item in report if item['rework']]
        blocked = [item for item in report if item['blocked']]
        failed = [item for item in report if item['job_status'] == 'failed']

        def by(key):
            groups = {}
            for item in numeric:
                groups.setdefault(item[key] or '—', []).append(item['ai'] - item['expected'])
            return {name: {'count': len(values), 'mae': round(statistics.mean(abs(v) for v in values), 2),
                           'bias': round(statistics.mean(values), 2)}
                    for name, values in sorted(groups.items())}

        payload = {
            'labels': str(path),
            'total_rows': len(report),
            'numeric': len(numeric),
            'exact': len(exact),
            'within_1': len(within1),
            'within_2': len(within2),
            'far_3_plus': len(far),
            'rework': len(rework),
            'blocked': len(blocked),
            'failed': len(failed),
            'mae': round(statistics.mean(abs(e) for e in errors), 2) if errors else None,
            'bias': round(statistics.mean(errors), 2) if errors else None,
            'by_class': by('class'),
            'by_model': by('model'),
            'by_work_type': by('work_type'),
            'items': report,
        }
        self.stdout.write('CALIBRATION ' + json.dumps({k: payload[k] for k in (
            'total_rows', 'numeric', 'exact', 'within_1', 'within_2', 'far_3_plus',
            'rework', 'blocked', 'failed', 'mae', 'bias')}, ensure_ascii=False))
        self.stdout.write('BY_CLASS ' + json.dumps(payload['by_class'], ensure_ascii=False))
        self.stdout.write('BY_MODEL ' + json.dumps(payload['by_model'], ensure_ascii=False))
        if options['json_out']:
            Path(options['json_out']).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                                 encoding='utf-8')
            self.stdout.write(f'JSON: {options["json_out"]}')
