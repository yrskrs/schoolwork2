"""Framework-neutral input validation and safe page data."""
import json
import os
import re
import secrets
import time
import urllib.parse
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

SECRET_FIELDS = ('password', 'app_password', 'client_secret', 'refresh_token')


def profile(owner, scope):
    if scope not in ('personal', 'server'):
        raise ValueError('Некоректний обсяг')
    return str(owner)+'/'+scope


def configuration(state, owner, scope):
    return {'provider':'nextcloud', 'repository':'', 'scope':scope, 'format':'incremental',
            'materials':True, 'students':True, 'student_files':True, 'automatic':False,
            'hour':2, 'frequency':'daily', **state.config(profile(owner, scope))}


def save_config(state, owner, scope, form):
    old = configuration(state, owner, scope)
    config = {**old}
    for key in ('provider','format','url','username','client_id','public_url'):
        config[key] = str(form.get(key, old.get(key, ''))).strip()
    repository = str(form.get('repository', '')).strip() or old['repository'] or uuid.uuid4().hex
    if not re.fullmatch('[a-f0-9]{32}', repository):
        raise ValueError('Ідентифікатор сховища: 32 символи 0–9, a–f')
    for key in SECRET_FIELDS:
        if form.get(key): config[key] = str(form[key])
    if form.get('clear_password'):
        config['password'] = ''
    # Password rotation starts a new independent repository; old snapshots keep
    # their original password and are never mixed with new encrypted blocks.
    config['recovery_only'] = form.get('recovery_only') in ('1','on','true',True)
    if not config['recovery_only'] and old.get('repository') == repository and config.get('password','') != old.get('password',''):
        repository = uuid.uuid4().hex
    if form.get('new_repository'):
        repository = uuid.uuid4().hex
    config['repository'] = repository
    if config.get('public_url'):
        public=urllib.parse.urlsplit(config['public_url'])
        if (public.scheme!='https' and not (public.scheme=='http' and public.hostname in ('localhost','127.0.0.1'))) or not public.hostname or public.username or public.query or public.fragment or public.path not in ('','/'):
            raise ValueError('Публічна адреса: HTTPS-адреса сайту без шляху, логіна та параметрів')
        config['public_url']=config['public_url'].rstrip('/')
    if config['provider'] not in ('nextcloud','google') or config['format'] not in ('incremental','zip'):
        raise ValueError('Оберіть сховище та формат зі списку')
    config['scope'] = scope
    for key in ('materials','students','student_files','automatic'):
        config[key] = form.get(key) in ('1','on','true',True)
    config['student_files'] = config['students'] and config['student_files']
    config['frequency'] = str(form.get('frequency', 'daily'))
    if config['frequency'] not in ('daily','weekly'):
        raise ValueError('Некоректний розклад')
    try:
        config['hour'] = int(form.get('hour', 2))
    except (ValueError,TypeError):
        raise ValueError('Некоректна година') from None
    if not 0 <= config['hour'] <= 23:
        raise ValueError('Година має бути від 0 до 23')
    if any(len(str(v)) > 8000 for v in config.values()):
        raise ValueError('Завелике значення налаштування')
    if config.get('recovery_only'):
        config['automatic'] = False
    if config['automatic']:
        check_connection(config)
    if (config.get('client_id'), config.get('client_secret')) != (old.get('client_id'), old.get('client_secret')):
        config.pop('refresh_token', None)
        config['automatic'] = False
    state.save(profile(owner, scope), config)
    return config


def check_connection(config):
    required = ('url','username','app_password') if config['provider'] == 'nextcloud' else ('client_id','client_secret','refresh_token')
    if not all(config.get(k) for k in required):
        raise ValueError('Спочатку збережіть підключення Nextcloud або підключіть Google через OAuth')


def begin_oauth(state, owner, scope, redirect_uri):
    from .remote import GoogleDrive
    config = configuration(state, owner, scope)
    if not config.get('client_id') or not config.get('client_secret'):
        raise ValueError('Спочатку збережіть Google Client ID і Client Secret')
    public_url=config.get('public_url','')
    if not public_url and not redirect_uri.startswith('https://'):
        candidate=os.environ.get('PUBLIC_BASE_URL','').rstrip('/')
        if candidate.startswith('https://'):public_url=candidate
    if public_url:
        original=urllib.parse.urlsplit(redirect_uri)
        redirect_uri=public_url.rstrip('/')+original.path+('?' + original.query if original.query else '')
    parsed = urllib.parse.urlsplit(redirect_uri)
    if parsed.scheme != 'https' and parsed.hostname not in ('localhost','127.0.0.1'):
        raise ValueError('Google OAuth потребує HTTPS-домену сайту. Відкрийте сайт за HTTPS-адресою та додайте callback до Google Cloud Console')
    token = secrets.token_urlsafe(32)
    config['oauth'] = {'state':token, 'expires':time.time()+600, 'redirect':redirect_uri}
    state.save(profile(owner, scope), config)
    return 'https://accounts.google.com/o/oauth2/v2/auth?'+urllib.parse.urlencode({
        'client_id':config['client_id'], 'redirect_uri':redirect_uri, 'response_type':'code',
        'scope':GoogleDrive.SCOPE, 'access_type':'offline', 'prompt':'consent', 'state':token})


def finish_oauth(state, owner, scope, token, code):
    from .remote import request
    config = configuration(state, owner, scope)
    pending = config.pop('oauth', {})
    if not token or not secrets.compare_digest(str(token), str(pending.get('state',''))) or pending.get('expires',0) < time.time():
        raise ValueError('Спроба OAuth застаріла. Підключіть Google ще раз')
    # Consume before making external requests, preventing callback replay.
    state.save(profile(owner, scope), config)
    data,_ = request('https://oauth2.googleapis.com/token', 'POST', urllib.parse.urlencode({
        'code':code, 'client_id':config['client_id'], 'client_secret':config['client_secret'],
        'redirect_uri':pending['redirect'], 'grant_type':'authorization_code'}).encode(),
        {'Content-Type':'application/x-www-form-urlencoded'})
    token_data = json.loads(data)
    if not token_data.get('refresh_token'):
        raise ValueError('Google не надав offline-доступ. Повторіть підключення з підтвердженням дозволу')
    config['refresh_token'] = token_data['refresh_token']
    state.save(profile(owner, scope), config)


def page_data(state, owner, scope):
    config = configuration(state, owner, scope)
    public = {key:value for key,value in config.items() if key not in SECRET_FIELDS and key != 'oauth'}
    public['has_password'] = bool(config.get('password'))
    public['has_nextcloud_password'] = bool(config.get('app_password'))
    public['google_connected'] = bool(config.get('refresh_token'))
    public['has_client_secret'] = bool(config.get('client_secret'))
    history = [row for row in state.history(owner) if row.get('scope','personal') == scope]
    snapshots = []
    for row in history:
        row['result'] = json.loads(row['result'])
        kind_labels = {'backup':'Резервування','list':'Пошук копій','verify':'Перевірка','restore':'Відновлення','download':'Підготовка ZIP'}
        status_labels = {'queued':'У черзі','running':'Виконується','completed':'Завершено','failed':'Помилка'}
        row['kind_label'] = kind_labels.get(row['kind'],row['kind'])
        row['status_label'] = status_labels.get(row['status'],row['status'])
        row['created_label'] = datetime.fromtimestamp(row['created'],ZoneInfo('Europe/Kyiv')).strftime('%d.%m.%Y %H:%M')
        result = row['result']
        row['summary'] = f"Записів даних: {result.get('records','—')}; об’єктів: {result.get('objects',result.get('records','—'))}; файлів: {result.get('files',result.get('files_count','—'))}; даних: {result.get('original_bytes',0)/1048576:.2f} МіБ; передано: {result.get('uploaded_bytes',0)/1048576:.2f} МіБ; повторно використано: {result.get('reused_bytes',0)/1048576:.2f} МіБ."
        if not snapshots and row['kind'] == 'list' and row['status'] == 'completed' and row['result'].get('repository') == config['repository']:
            snapshots = row['result'].get('snapshots', [])
    choices = [{'value':snapshot,'label':datetime.fromtimestamp(int(snapshot.split('-')[0])/1000,ZoneInfo('Europe/Kyiv')).strftime('%d.%m.%Y %H:%M:%S · Київ')} for snapshot in snapshots]
    return {'backup':public, 'jobs':history, 'snapshots':snapshots, 'snapshot_choices':choices, 'backup_notices':state.notices(owner)}


def enqueue(state, owner, scope, action, form):
    config = configuration(state, owner, scope)
    check_connection(config)
    if action not in ('backup','list','verify','restore','download'):
        raise ValueError('Невідома дія')
    if action == 'backup' and config.get('recovery_only'):
        raise ValueError('Це сховище підключене лише для відновлення. Для нових копій створіть нове сховище')
    payload = {'config':config}
    if action in ('verify','restore','download'):
        snapshot = str(form.get('snapshot',''))
        if not re.fullmatch(r'[0-9]{13}-[a-f0-9]{32}\.zip', snapshot):
            raise ValueError('Спочатку знайдіть та оберіть копію')
        payload['snapshot'] = snapshot
        payload['config']['_snapshot'] = snapshot
    if action == 'restore' and str(form.get('confirmation','')).strip() != 'ВІДНОВИТИ':
        raise ValueError('Введіть ВІДНОВИТИ. Відновлення призначене для нового встановлення без робіт')
    return state.enqueue(owner, action, payload)
