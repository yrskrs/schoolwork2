"""Real-works grade-stability comparison on a COPY of the production database.

Runs inside a throwaway container against an isolated PostgreSQL restored from a
pg_dump. Media is mounted read-only from a copy of the production media volume.
Production containers, database and media are never written to.

For each agreed submission id it calls evaluate_submission_with_gemini several
times with identical materials and records the suggested grade, status, privacy
outcome and timing so grade stability can be compared across repeats.

API credentials are read from a private config file and never printed.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

WORK_IDS = [602, 450, 436, 435, 433, 427, 418, 410, 372, 347]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--ids', default=','.join(str(i) for i in WORK_IDS))
    parser.add_argument('--retry-deferred', type=int, default=0)
    parser.add_argument('--pause', type=float, default=0.0)
    args = parser.parse_args()

    exported = json.loads(args.config.read_text())
    secrets = [c.get('api_key', '') for c in exported.get('provider_connections', [])]
    secrets += [exported.get('api_key', ''), exported.get('backup_api_key', '')]

    def sanitized(text):
        for secret in secrets:
            if secret:
                text = text.replace(secret, '[KEY]')
        return text

    ids = [int(x) for x in args.ids.split(',') if x.strip()]

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'schoolnet.settings')
    import django
    django.setup()
    from django.db import close_old_connections, connections
    from feed.models import Submission, AISettings
    from feed.gemini_service import evaluate_submission_with_gemini

    settings = AISettings.objects.first()
    if settings is None:
        raise SystemExit('No AISettings row in the restored database copy.')
    # Force the redact default behaviour under test; do not mutate the source row.
    settings.privacy_mode = 'redact'

    report = {
        'scope': 'real works on restored DB copy; media read-only; production untouched',
        'repeats': args.repeats,
        'ids': ids,
        'privacy_mode': 'redact',
        'works': [],
    }

    for sid in ids:
        close_old_connections()
        entry = {'id': sid, 'runs': [], 'grades': [], 'statuses': [], 'privacy': []}
        try:
            sub = Submission.objects.select_related('assignment', 'teacher', 'class_group').get(pk=sid)
        except Submission.DoesNotExist:
            entry['error'] = 'missing'
            report['works'].append(entry)
            continue
        entry['assignment'] = (sub.assignment.title if sub.assignment else '')[:80]
        entry['files'] = [f.original_name for f in sub.files.all()]
        entry['legacy_file'] = bool(sub.file)
        entry['grade_before'] = sub.grade
        entry['ai_grade_before'] = sub.ai_suggested_grade
        for run in range(args.repeats):
            if run and args.pause:
                time.sleep(args.pause)
            close_old_connections()
            deferred_retries = 0
            while True:
                started = time.monotonic()
                try:
                    result = evaluate_submission_with_gemini(sub, ai_settings=settings)
                    grade = result.get('suggested_grade')
                    status = result.get('status')
                    blocked = bool(result.get('assessment_blocked'))
                    reasons = result.get('blocked_reasons') or []
                    err = sanitized(str(result.get('error') or ''))[:400]
                except Exception as exc:  # noqa: BLE001 - record and continue
                    grade, status, blocked, reasons = None, 'exception', False, []
                    err = sanitized(f'{type(exc).__name__}: {exc}')[:400]
                if status == 'deferred' and deferred_retries < args.retry_deferred:
                    deferred_retries += 1
                    time.sleep(max(1, min(120, int(result.get('retry_after') or 5))))
                    continue
                break
            seconds = round(time.monotonic() - started, 3)
            entry['runs'].append({
                'run': run + 1, 'grade': grade, 'status': status,
                'assessment_blocked': blocked, 'blocked_reasons': reasons,
                'seconds': seconds, 'error': err,
                'deferred_retries': deferred_retries,
            })
            entry['grades'].append(grade)
            entry['statuses'].append(status)
            entry['privacy'].append('blocked' if blocked else 'allowed')
            print(sanitized(json.dumps({'id': sid, 'run': run + 1, 'grade': grade,
                                        'status': status, 'blocked': blocked,
                                        'seconds': seconds}, ensure_ascii=False)), flush=True)
            args.output.write_text(sanitized(json.dumps(report, ensure_ascii=False, indent=2)) + '\n')
        numeric = [g for g in entry['grades'] if g not in (None, '', 'Доопрацювати')]
        entry['numeric_grades'] = numeric
        entry['stable'] = len(set(str(g) for g in entry['grades'])) <= 1
        entry['numeric_stable'] = len(set(str(g) for g in numeric)) <= 1
        report['works'].append(entry)
        args.output.write_text(sanitized(json.dumps(report, ensure_ascii=False, indent=2)) + '\n')

    summary = {
        'works': len(report['works']),
        'all_stable': all(w.get('stable') for w in report['works']),
        'numeric_stable': all(w.get('numeric_stable') for w in report['works']),
        'per_work': {w['id']: {'grades': w['grades'], 'stable': w.get('stable'),
                               'numeric_stable': w.get('numeric_stable')} for w in report['works']},
    }
    report['summary'] = summary
    args.output.write_text(sanitized(json.dumps(report, ensure_ascii=False, indent=2)) + '\n')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
