from django.core.management.base import BaseCommand

from feed.ai_jobs import execute_job


class Command(BaseCommand):
    help = 'Execute one already claimed AI job in an isolated worker process.'

    def add_arguments(self, parser):
        parser.add_argument('job_id')

    def handle(self, *args, **options):
        execute_job(options['job_id'])
