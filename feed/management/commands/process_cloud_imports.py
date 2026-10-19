import threading
from django.core.management.base import BaseCommand
from feed.cloud_imports import worker_loop


class Command(BaseCommand):
    help = 'Process cloud imports (also started automatically by process_ai_jobs).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        worker_loop(threading.Event(), once=options['once'])
