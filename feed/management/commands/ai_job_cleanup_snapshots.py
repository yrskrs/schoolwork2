"""Remove frozen source copies of old AI checks (files only, never DB rows)."""
import shutil
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from feed.models import AIJob


class Command(BaseCommand):
    help = 'Видалити копії джерел AI-перевірок, старші за вказану кількість днів.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=14, help='Вік копій у днях (типово 14)')
        parser.add_argument('--dry-run', action='store_true', help='Лише показати, що буде видалено')
        parser.add_argument('--force', action='store_true',
                            help='Дозволити поріг менше 1 дня (небезпечно для живих даних)')

    def handle(self, *args, **options):
        root = Path(settings.MEDIA_ROOT) / 'ai_job_snapshots'
        if not root.exists():
            self.stdout.write('Копій немає.')
            return
        if options['days'] < 1 and not options['force']:
            self.stderr.write('Поріг менше 1 дня потребує --force: інакше можна видалити копії активних перевірок.')
            return
        cutoff = time.time() - max(0, options['days']) * 86400
        removed = skipped = 0
        active = set(AIJob.objects.filter(status__in=['queued', 'running']).values_list('pk', flat=True))
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            try:
                if folder.name in {str(pk) for pk in active}:
                    skipped += 1
                    continue
                if folder.stat().st_mtime >= cutoff:
                    skipped += 1
                    continue
                if not options['dry_run']:
                    shutil.rmtree(folder)
                removed += 1
            except OSError:
                skipped += 1
        self.stdout.write(f'{"Буде видалено" if options["dry_run"] else "Видалено"}: {removed}; '
                          f'пропущено (активні/нові): {skipped}.')
