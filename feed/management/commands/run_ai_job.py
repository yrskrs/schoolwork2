from django.core.management.base import BaseCommand

from feed.ai_jobs import execute_job


class Command(BaseCommand):
    help = 'Execute one already claimed AI job in an isolated worker process.'

    def add_arguments(self, parser):
        parser.add_argument('job_id')
        parser.add_argument('--spawned-at', type=float)

    def handle(self, *args, **options):
        if options.get('spawned_at'):
            import time
            from feed.ai_progress import track_job
            from feed.ai_metrics import record
            with track_job(options['job_id']):
                record('process_startup_ms', (time.time() - options['spawned_at']) * 1000)
        execute_job(options['job_id'])
