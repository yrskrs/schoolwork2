import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from feed.ai_jobs import claim_next_job, fail_job


class Command(BaseCommand):
    help = 'Process AI work in bounded isolated processes, reserving student capacity.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        children = {}
        stopping = threading.Event()

        def stop(signum, frame):
            stopping.set()
            for child, job, started in list(children.values()):
                if child.poll() is None:
                    child.terminate()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        if not options['once']:
            from feed.cloud_backups import Adapter, state
            from cloud_backup.state import start_worker as start_backup_worker
            from feed.cloud_imports import start_worker as start_cloud_worker
            from school_sync.django_backend import start_worker
            start_backup_worker(state(), Adapter(), stopping)
            start_cloud_worker(stopping)
            start_worker()

        launched = False
        while True:
            close_old_connections()
            if not options['once']:
                Path('/tmp/schoolnet-ai-worker-heartbeat').touch()
            for identity, (child, job, started) in list(children.items()):
                timed_out = time.monotonic() - started >= settings.AI_JOB_TIMEOUT
                if timed_out or stopping.is_set():
                    if child.poll() is None:
                        child.kill()
                        child.wait()
                if child.poll() is not None:
                    job.refresh_from_db()
                    if job.status == 'running':
                        fail_job(job.pk, 'Перевірку перервано. Спробуйте ще раз; спробу самоперевірки не використано.')
                    children.pop(identity)
            if stopping.is_set() or options['once'] and launched and not children:
                return
            for kinds, maximum in ((None, settings.AI_WORKER_CONCURRENCY), (('prepare',), settings.AI_PREP_CONCURRENCY)):
                while not stopping.is_set():
                    count = sum((job.kind == 'prepare') == (kinds == ('prepare',)) for child, job, start in children.values())
                    if count >= maximum or options['once'] and launched:
                        break
                    job = claim_next_job(kinds=kinds)
                    if not job:
                        break
                    try:
                        spawned_at = time.time()
                        child = subprocess.Popen([sys.executable, str(settings.BASE_DIR / 'manage.py'),
                            'run_ai_job', str(job.pk), '--spawned-at', str(spawned_at)])
                        children[str(job.pk)] = (child, job, time.monotonic())
                        launched = True
                    except OSError:
                        fail_job(job.pk, 'Не вдалося запустити перевірку; спробу не використано.')
            if options['once'] and not children:
                return
            stopping.wait(.2 if children else .5)
