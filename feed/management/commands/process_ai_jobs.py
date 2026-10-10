import subprocess
import sys
import time
import threading
import signal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from feed.ai_jobs import claim_next_job, fail_job


class Command(BaseCommand):
    help = 'Process the durable AI queue without blocking web requests.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        child = None
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True
            if child and child.poll() is None:
                child.terminate()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        if not options['once']:
            from school_sync.django_backend import start_worker
            start_worker()
            def heartbeat():
                while True:
                    Path('/tmp/schoolnet-ai-worker-heartbeat').touch()
                    time.sleep(10)
            threading.Thread(target=heartbeat, daemon=True).start()
        while not stopping:
            close_old_connections()
            job = claim_next_job()
            if job:
                try:
                    child = subprocess.Popen([
                        sys.executable, str(settings.BASE_DIR / 'manage.py'), 'run_ai_job', str(job.pk)])
                    if stopping:
                        child.terminate()
                    child.wait(timeout=settings.AI_JOB_TIMEOUT)
                    if child.returncode:
                        raise subprocess.CalledProcessError(child.returncode, child.args)
                    # A process killed by OOM/signals or an early exit must release the claim.
                    job.refresh_from_db()
                    if job.status == 'running':
                        fail_job(job.pk, 'Перевірку перервано. Спробуйте ще раз.')
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                    if child and child.poll() is None:
                        child.kill()
                        child.wait()
                    fail_job(job.pk, 'Перевірку перервано. Спробуйте ще раз; спробу самоперевірки не використано.')
                finally:
                    child = None
            if options['once']:
                return
            if not job:
                time.sleep(1)
