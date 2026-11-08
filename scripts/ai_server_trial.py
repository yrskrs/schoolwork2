"""Isolated server verification. Never runs Compose or mounts production data.

Run from a separate source copy, with --output in the same trial directory.
The only removed resources are the temporary PostgreSQL container and network
created by this invocation. Candidate image and logs remain for review.
"""
import argparse
import hashlib
import json
import re
import secrets
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if not (source / 'AI_TRIAL_SOURCE_MANIFEST.json').is_file():
        raise SystemExit('An explicitly prepared trial source copy is required.')
    manifest = json.loads((source / 'AI_TRIAL_SOURCE_MANIFEST.json').read_text())
    for name, digest in manifest['files'].items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
            raise SystemExit('Trial source fingerprint mismatch: ' + name)
    output.mkdir(parents=True, exist_ok=True)
    identity = 'ai-trial-' + secrets.token_hex(5)
    image = 'schoolwork2-ai-trial:' + manifest['source_digest'][:12]
    network, pg = identity + '-net', identity + '-pg'
    production = ['schoolnet_app', 'schoolnet_ai_worker', 'schoolnet_postgres',
                  'schooljournal-web-1', 'school_test-app-1']

    def baseline():
        result = subprocess.run(['docker', 'inspect', *production], text=True, capture_output=True, check=True)
        return {row['Name'].lstrip('/'): {'id': row['Id'], 'image': row['Image'],
            'restart_count': row['RestartCount'], 'status': row['State']['Status'],
            'health': row['State'].get('Health', {}).get('Status')}
            for row in json.loads(result.stdout)}

    report = {'image': image, 'source_digest': manifest['source_digest'], 'stages': [], 'production_before': baseline()}
    (output / 'report.json').write_text(json.dumps(report, indent=2))

    def run(label, command, timeout=1200):
        print(json.dumps({'stage': label, 'status': 'starting'}), flush=True)
        started = time.monotonic()
        with (output / (label + '.log')).open('w') as log:
            result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        text = (output / (label + '.log')).read_text(errors='replace')
        summary = [line for line in text.splitlines() if re.match(r'^(Ran \d+ tests|OK(?:\b|\s)|FAILED|System check|No changes)', line)]
        item = {'stage': label, 'returncode': result.returncode, 'seconds': round(time.monotonic() - started, 3), 'summary': summary}
        report['stages'].append(item)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(item, ensure_ascii=False), flush=True)
        if result.returncode:
            raise RuntimeError(label + ' failed; inspect its log before any next stage.')

    owned_network = owned_pg = False
    try:
        run('build', ['docker', 'build', '--label', 'schoolnet.ai-trial=true',
            '--build-arg', 'VCS_REF=' + manifest['base_revision'][:12] + '-ai-trial',
            '--build-arg', 'SOURCE_DIGEST=' + manifest['source_digest'], '-t', image, '.'])
        subprocess.run(['docker', 'network', 'create', '--internal', '--label', 'schoolnet.ai-trial=true', network], check=True, capture_output=True)
        owned_network = True
        password = secrets.token_hex(18)
        subprocess.run(['docker', 'run', '-d', '--name', pg, '--network', network,
            '--label', 'schoolnet.ai-trial=true', '--memory', '512m', '--cpus', '1',
            '-e', 'POSTGRES_DB=ai_trial', '-e', 'POSTGRES_USER=trial', '-e', 'POSTGRES_PASSWORD=' + password,
            'postgres:16'], check=True, capture_output=True)
        owned_pg = True
        for attempt in range(60):
            ready = subprocess.run(['docker', 'exec', pg, 'pg_isready', '-U', 'trial', '-d', 'ai_trial'], capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Isolated PostgreSQL did not become ready.')
        common = ['docker', 'run', '--rm', '--network', network, '--memory', '2g', '--cpus', '2',
            '--label', 'schoolnet.ai-trial=true', '-e', 'PYTHON_DOTENV_DISABLED=1',
            '-e', 'PYTHONUNBUFFERED=1', '-e', 'ROSTER_PEERS=[]', '-e', 'ROSTER_SYNC_TOKEN=',
            '-e', 'JOURNAL_API_BASE_URL=']
        run('sqlite-suite', common + [image, 'python', 'manage.py', 'test', '--settings=schoolnet.test_settings', '--noinput'])
        run('postgres-suite', common + ['-e', f'DATABASE_URL=postgres://trial:{password}@{pg}:5432/ai_trial',
            '-e', 'SCHOOLNET_TEST_POSTGRES=1', image, 'python', 'manage.py', 'test', '--settings=schoolnet.test_settings', '--noinput'])
        run('worker-http', common + [image, 'python', 'scripts/test_ai_synthetic_runtime.py'])
        run('updater-suite', common + [image, 'python', '-m', 'unittest', 'discover', '-s', 'scripts', '-p', 'test_update*.py'])
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        raise
    finally:
        if owned_pg:
            subprocess.run(['docker', 'rm', '-f', pg], capture_output=True)
        if owned_network:
            subprocess.run(['docker', 'network', 'rm', network], capture_output=True)
        report['production_after'] = baseline()
        report['production_unchanged'] = report['production_before'] == report['production_after']
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'status': report.get('status'), 'production_unchanged': report['production_unchanged'],
                          'report': str(output / 'report.json')}, ensure_ascii=False), flush=True)
    if not report['production_unchanged']:
        raise RuntimeError('Production baseline changed during trial; investigate before proceeding.')


if __name__ == '__main__':
    main()
