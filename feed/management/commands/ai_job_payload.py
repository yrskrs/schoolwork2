"""Show what a finished AI job actually sent, without calling any provider."""
import json

from django.core.management.base import BaseCommand, CommandError

from feed.models import AIJob


class Command(BaseCommand):
    help = 'Показати маніфест payload для завершеної перевірки ШІ (без виклику API).'

    def add_arguments(self, parser):
        parser.add_argument('job_id', help='UUID завдання AIJob')
        parser.add_argument('--events', action='store_true', help='Додати журнал подій')

    def handle(self, *args, **options):
        job = AIJob.objects.filter(pk=options['job_id']).first()
        if not job:
            raise CommandError('Завдання не знайдено')
        manifest = (job.result or {}).get('payload_manifest')
        if not manifest:
            raise CommandError('У цього завдання немає маніфесту (перевірку не завершено або це стара версія)')
        self.stdout.write(json.dumps(manifest, ensure_ascii=False, indent=2))
        if options['events']:
            self.stdout.write(json.dumps(job.events, ensure_ascii=False, indent=2))
