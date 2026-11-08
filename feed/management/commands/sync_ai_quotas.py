import copy
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from feed.ai_quotas import apply_profile, read_provider_limits
from feed.models import AISettings


class Command(BaseCommand):
    help = 'Preview/apply quota profiles and verified API limits; never uses student work.'

    def add_arguments(self, parser):
        parser.add_argument('--profile', type=Path)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--probe-groq', action='store_true', help='Allow tiny synthetic Groq requests (may consume tokens).')
        parser.add_argument('--offline', action='store_true', help='Apply/preview a profile without any provider calls.')

    def handle(self, *args, **options):
        original = AISettings.objects.get(pk=1).provider_connections
        planned = copy.deepcopy(original)
        if options['profile']:
            try:
                planned = apply_profile(planned, json.loads(options['profile'].read_text()))
            except (OSError, ValueError, TypeError) as error:
                raise CommandError('Некоректний файл профілю квот; зміни не застосовано.') from error
        observations = []
        settings = AISettings.objects.get(pk=1)
        queue = json.loads(settings.saved_models_list or '[]')
        for connection in planned:
            if options['offline']:
                continue
            models = [m['name'] for m in queue if m.get('connection_id') == connection['id'] and m.get('enabled', True)]
            rows = read_provider_limits(connection, models, probe_groq=options['probe_groq'])
            observations.extend(rows)
            for row in rows:
                if not row['limits']:
                    continue
                if connection['provider'] == 'openrouter':
                    # This is the account's free-route pool, not a paid-route cap.
                    if not models or any(m != 'openrouter/free' and not m.endswith(':free') for m in models):
                        row['applied_to_plan'] = False
                        continue
                    target = connection.setdefault('quota_limits', {})
                else:
                    target = connection.setdefault('model_quota_limits', {}).setdefault(row['model'], {})
                for name, value in row['limits'].items():
                    target[name] = min(target.get(name) or value, value)
                connection.setdefault('quota_provenance', {}).setdefault('api_observations', {})[row['model'] or 'free_route_pool'] = {
                    'source': row['source'], 'verified_at': timezone.now().isoformat(), 'limits': row['limits'],
                }
                row['applied_to_plan'] = True
        if options['apply']:
            with transaction.atomic():
                locked = AISettings.objects.select_for_update().get(pk=1)
                if locked.provider_connections != original:
                    raise CommandError('Підключення змінились під час отримання квот; повторіть синхронізацію.')
                locked.provider_connections = planned
                locked.save(update_fields=['provider_connections', 'updated_at'])
        # Public projection only; never serialize connection dictionaries/keys.
        self.stdout.write(json.dumps({'applied': options['apply'], 'observations': observations,
            'connections': [{'provider': c['provider'], 'quota_group': c.get('quota_group', ''),
                'quota_limits': c.get('quota_limits', {}), 'model_quota_limits': c.get('model_quota_limits', {}),
                'fallback': c.get('quota_fallback_limits', {}), 'fallback_note': c.get('quota_fallback_note', '')}
                for c in planned]}, ensure_ascii=False, indent=2))
