"""Server-only launcher for the synthetic real-provider trial.

Run with explicit --trial and --image. Reads only production AISettings;
credentials stay in a private server directory and are removed in finally.
No production DB or media are mounted into the candidate container.
"""
import argparse
import json
import secrets
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--trial', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    trial = args.trial.resolve()
    manifest = json.loads((trial / 'source/AI_TRIAL_SOURCE_MANIFEST.json').read_text())
    if args.image != 'schoolwork2-ai-trial:' + manifest['source_digest'][:12]:
        raise SystemExit('Only the previously verified candidate image is accepted.')
    report = json.loads((trial / 'results/report.json').read_text())
    if report.get('status') != 'passed' or not report.get('production_unchanged'):
        raise SystemExit('Isolated server tests must pass first.')
    harness = trial / 'ai_live_provider_trial.py'
    if not harness.is_file():
        raise SystemExit('Copy the reviewed synthetic harness into the trial directory first.')
    export_code = '''
import os,json
os.environ.setdefault('DJANGO_SETTINGS_MODULE','schoolnet.settings')
import django;django.setup()
from feed.models import AISettings
print(json.dumps(AISettings.objects.filter(pk=1).values().first() or {},default=str))
'''
    export = subprocess.run(['docker', 'exec', '-i', 'schoolnet_app', 'python', '-'],
        input=export_code, text=True, capture_output=True)
    if export.returncode:
        raise SystemExit('Read-only AI configuration export failed; output withheld.')
    try:
        config = json.loads(export.stdout)
    except ValueError:
        raise SystemExit('Configuration export was not JSON; output withheld.')
    secret_values = [c.get('api_key', '') for c in config.get('provider_connections', [])]
    secret_values += [config.get('api_key', ''), config.get('backup_api_key', '')]

    def sanitize(value):
        for secret in secret_values:
            if secret:
                value = value.replace(secret, '[KEY]')
        return value

    results = trial / 'live-results'
    results.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='private-api-', dir=trial) as private:
        private_path = Path(private)
        config_path = private_path / 'config.json'
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        container = 'ai-live-trial-' + secrets.token_hex(5)
        command = ['docker', 'run', '--rm', '--name', container, '--network', 'bridge', '--memory', '2g', '--cpus', '2',
            '--label', 'schoolnet.ai-trial=true', '-e', 'PYTHON_DOTENV_DISABLED=1',
            '-e', 'PYTHONPATH=/app', '-e', 'PYTHONUNBUFFERED=1',
            '-v', str(config_path) + ':/trial-config.json:ro',
            '-v', str(harness) + ':/trial-harness.py:ro',
            '-v', str(results) + ':/trial-results', args.image, 'python', '/trial-harness.py',
            '--config', '/trial-config.json', '--output', '/trial-results/report.json']
        # Keep unsanitized subprocess output private until it is filtered.
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=420)
            text = sanitize(result.stdout + result.stderr)
            (results / 'run.log').write_text(text)
            print(text, end='')
            print(json.dumps({'returncode': result.returncode, 'private_config_removed_on_exit': True}))
            if result.returncode:
                raise SystemExit(result.returncode)
        except subprocess.TimeoutExpired:
            raise SystemExit('Synthetic API trial exceeded its 420-second budget.')
        finally:
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True)


if __name__ == '__main__':
    main()
