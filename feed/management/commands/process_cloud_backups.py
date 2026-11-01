import signal
import threading
import time
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from feed.cloud_backups import Adapter, state


class Command(BaseCommand):
    help = 'Process private durable cloud backup schedules and queued operations.'

    def add_arguments(self, parser):
        parser.add_argument('--once',action='store_true')

    def handle(self,*args,**options):
        stop = threading.Event()
        signal.signal(signal.SIGTERM,lambda *args:stop.set())
        signal.signal(signal.SIGINT,lambda *args:stop.set())
        def heartbeat():
            while not stop.is_set():
                Path('/tmp/schoolnet-backup-worker-heartbeat').touch()
                stop.wait(10)
        threading.Thread(target=heartbeat,daemon=True).start()
        store, adapter = state(), Adapter()
        while not stop.is_set():
            close_old_connections()
            store.tick(adapter)
            if options['once']: break
            stop.wait(5)
