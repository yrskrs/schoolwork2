"""Private WebDAV / Drive transports. No tokens in errors, URLs or logs."""
import base64
import json
import time
from pathlib import Path
import contextlib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

MAX_RESPONSE = 32 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Хмара перенаправляє запит. Укажіть кінцеву адресу сховища')


def request(url, method='GET', data=None, headers=None, limit=MAX_RESPONSE):
    if isinstance(data, Path):
        with data.open('rb') as stream:
            return request(url, method, stream, {**(headers or {}), 'Content-Length':str(data.stat().st_size)}, limit)
    import time
    start = data.tell() if hasattr(data,'tell') else None
    retries = 3 if method in ('GET','HEAD','PUT','PROPFIND') else 1
    for attempt in range(retries):
        if start is not None: data.seek(start)
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=90) as response:
                value = response.read(limit+1)
                if len(value)>limit: raise ValueError('Відповідь хмари перевищує ліміт')
                return value,response.headers
        except urllib.error.HTTPError as error:
            code = error.code
            if code in (408,429,500,502,503,504) and attempt+1<retries:
                time.sleep(0.5*(2**attempt));continue
            if code==404: raise FileNotFoundError('Об’єкт відсутній у хмарі') from None
            raise ValueError(f'Хмарне сховище відхилило запит (HTTP {code}). Перевірте доступ, вільне місце та підключення') from None
        except (urllib.error.URLError,TimeoutError,OSError):
            if attempt+1<retries:
                time.sleep(0.5*(2**attempt));continue
            raise ValueError('Хмарне сховище недоступне. Перевірте адресу та мережу') from None


def download(url, target, headers, limit):
    import shutil
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=90) as response, Path(target).open('wb') as out:
            size = 0
            while True:
                block = response.read(1024*1024)
                if not block: break
                size += len(block)
                if size > limit: raise ValueError('Відповідь хмари перевищує ліміт')
                out.write(block)
    except urllib.error.HTTPError as error:
        raise ValueError(f'Не вдалося завантажити копію (HTTP {error.code})') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError('Не вдалося завантажити копію. Перевірте мережу та вільне місце') from None


class Nextcloud:
    def __init__(self, config, app, repository_id):
        parsed = urllib.parse.urlsplit(config['url'].strip().rstrip('/'))
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Некоректна адреса Nextcloud')
        if parsed.hostname in ('localhost', '127.0.0.1', '::1', '169.254.169.254'):
            raise ValueError('Недопустима адреса Nextcloud')
        base = urllib.parse.urlunsplit(parsed)
        user = urllib.parse.quote(config['username'], safe='')
        self.root = base + '/remote.php/dav/files/' + user + '/SchoolBackup-' + app + '/' + repository_id
        self.auth = 'Basic ' + base64.b64encode((config['username']+':'+config['app_password']).encode()).decode()

    def url(self, path):
        from .archive import safe_path
        return self.root + ('/' + urllib.parse.quote(safe_path(path), safe='/') if path else '')

    def call(self, path='', method='GET', data=None, headers=None, limit=MAX_RESPONSE):
        return request(self.url(path), method, data, {'Authorization': self.auth, **(headers or {})}, limit)

    def mkdir(self, url):
        try:
            request(url, 'MKCOL', headers={'Authorization': self.auth})
        except ValueError as error:
            if 'HTTP 405' not in str(error):
                raise

    def prepare(self):
        parent = self.root.rsplit('/', 1)[0]
        self.mkdir(parent)
        self.mkdir(self.root)
        for name in ('blocks', 'snapshots'):
            self.mkdir(self.url(name))

    def exists(self, path):
        try:
            self.call(path, 'HEAD')
            return True
        except FileNotFoundError:
            return False

    def put(self, path, data):
        # MOVE publishes only a completely uploaded temporary object.
        import uuid
        temporary = path + '.' + uuid.uuid4().hex + '.partial'
        self.call(temporary, 'PUT', data, {'Content-Type': 'application/zip'})
        self.call(temporary, 'MOVE', headers={'Destination': self.url(path), 'Overwrite': 'T'})

    def rename(self, source, target):
        self.call(source,'MOVE',headers={'Destination':self.url(target),'Overwrite':'F'})

    def get(self, path, limit=MAX_RESPONSE):
        return self.call(path, limit=limit)[0]

    def download(self, path, target, limit):
        download(self.url(path), target, {'Authorization':self.auth}, limit)

    def delete(self, path):
        self.call(path, 'DELETE')

    def list(self, directory):
        try:
            body = self.call(directory, 'PROPFIND', headers={'Depth': '1'})[0]
        except FileNotFoundError:
            return []
        # defusedxml prevents entity expansion by an untrusted WebDAV server.
        from defusedxml.ElementTree import fromstring
        names = []
        for response in fromstring(body).findall('{DAV:}response'):
            href = response.findtext('{DAV:}href') or ''
            value = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
            name = value.rstrip('/').rsplit('/', 1)[-1]
            if name and name != directory:
                names.append(name)
        return names


class GoogleDrive:
    API = 'https://www.googleapis.com/drive/v3/files'
    SCOPE = 'https://www.googleapis.com/auth/drive.file'

    def __init__(self, config, app, repository_id, save=lambda config: None):
        self.config, self.save = config, save
        self.folder_name = 'SchoolBackup-' + app + '-' + repository_id
        self.ids = {}
        self.folder = None
        self.token = ''
        self.token_expires = 0

    def authenticate(self):
        form = {key: self.config[key] for key in ('client_id', 'client_secret', 'refresh_token')}
        form['grant_type'] = 'refresh_token'
        body, _ = request('https://oauth2.googleapis.com/token', 'POST', urllib.parse.urlencode(form).encode(),
                          {'Content-Type': 'application/x-www-form-urlencoded'})
        value=json.loads(body)
        self.token=value['access_token']
        self.token_expires=time.monotonic()+max(0,int(value.get('expires_in',3600))-60)

    def ensure_token(self):
        if not self.token or (self.token_expires and time.monotonic()>=self.token_expires):
            self.authenticate()

    def call(self, url, method='GET', data=None, headers=None, limit=MAX_RESPONSE):
        self.ensure_token()
        try:
            return request(url,method,data,{'Authorization':'Bearer '+self.token,**(headers or {})},limit)
        except ValueError as error:
            if 'HTTP 401' not in str(error):raise
            self.authenticate()
            return request(url,method,data,{'Authorization':'Bearer '+self.token,**(headers or {})},limit)

    def query(self, q):
        items, token = [], ''
        while True:
            url = self.API + '?' + urllib.parse.urlencode({'q': q+' and trashed=false', 'fields': 'nextPageToken,files(id,name)', 'pageSize': 1000, 'pageToken': token})
            value = json.loads(self.call(url)[0])
            items.extend(value.get('files', []))
            token = value.get('nextPageToken')
            if not token:
                return items

    def find_folder(self, create=False):
        if self.folder:
            return self.folder
        found = self.query("name='"+self.folder_name+"' and mimeType='application/vnd.google-apps.folder'")
        if len(found) > 1:
            raise ValueError('Знайдено дублікати папки резервування. Потрібна перевірка сховища')
        if found:
            self.folder = found[0]['id']
        elif create:
            self.folder = json.loads(self.call(self.API, 'POST', json.dumps({'name': self.folder_name, 'mimeType': 'application/vnd.google-apps.folder'}).encode(), {'Content-Type': 'application/json'})[0])['id']
        else:
            raise FileNotFoundError('Папку резервування не знайдено')
        return self.folder

    def prepare(self):
        self.find_folder(True)

    def lookup(self, path):
        from .archive import safe_path
        safe_path(path)
        if path not in self.ids:
            try:
                folder = self.find_folder()
            except FileNotFoundError:
                return None
            found = self.query("'"+folder+"' in parents and name='"+path.replace('/', '__')+"'")
            if len(found) > 1:
                raise ValueError('Дублікати об’єктів у сховищі')
            if found:
                self.ids[path] = found[0]['id']
        return self.ids.get(path)

    def exists(self, path):
        return bool(self.lookup(path))

    def put(self, path, data):
        object_id = self.lookup(path)
        url = 'https://www.googleapis.com/upload/drive/v3/files' + ('/'+object_id if object_id else '') + '?uploadType=resumable'
        metadata = {} if object_id else {'name': path.replace('/', '__'), 'parents': [self.find_folder(True)]}
        _, headers = self.call(url, 'PATCH' if object_id else 'POST', json.dumps(metadata).encode(),
                               {'Content-Type': 'application/json', 'X-Upload-Content-Type': 'application/zip', 'X-Upload-Content-Length': str(data.stat().st_size if isinstance(data, Path) else len(data))})
        location = headers.get('Location', '')
        parsed = urllib.parse.urlsplit(location)
        if parsed.scheme != 'https' or parsed.hostname != 'www.googleapis.com':
            raise ValueError('Некоректна адреса завантаження Drive')
        body = self.call(location, 'PUT', data, {'Content-Type': 'application/zip'}, MAX_RESPONSE)[0]
        self.ids[path] = json.loads(body)['id']

    def rename(self, source, target):
        from .archive import safe_path
        safe_path(target)
        object_id=self.lookup(source)
        if not object_id:raise FileNotFoundError('Об’єкт не знайдено')
        self.call(self.API+'/'+object_id,'PATCH',json.dumps({'name':target.replace('/','__')}).encode(),{'Content-Type':'application/json'})
        self.ids[target]=object_id;self.ids.pop(source,None)

    def get(self, path, limit=MAX_RESPONSE):
        object_id = self.lookup(path)
        if not object_id:
            raise FileNotFoundError('Копію не знайдено')
        return self.call(self.API+'/'+object_id+'?alt=media', limit=limit)[0]

    def download(self, path, target, limit):
        object_id = self.lookup(path)
        if not object_id: raise FileNotFoundError('Копію не знайдено')
        self.ensure_token()
        try:
            download(self.API+'/'+object_id+'?alt=media',target,{'Authorization':'Bearer '+self.token},limit)
        except ValueError as error:
            if 'HTTP 401' not in str(error):raise
            self.authenticate()
            download(self.API+'/'+object_id+'?alt=media',target,{'Authorization':'Bearer '+self.token},limit)

    def delete(self, path):
        object_id = self.lookup(path)
        if object_id:
            self.call(self.API+'/'+object_id, 'DELETE')
            self.ids.pop(path, None)

    def list(self, directory):
        try:
            folder = self.find_folder()
        except FileNotFoundError:
            return []
        prefix = directory+'__'
        found = self.query("'"+folder+"' in parents")
        result = []
        for row in found:
            if row['name'].startswith(prefix):
                name = row['name'][len(prefix):]
                self.ids[directory+'/'+name] = row['id']
                result.append(name)
        return result


def transport(config, app, repository_id):
    return (Nextcloud if config['provider'] == 'nextcloud' else GoogleDrive)(config, app, repository_id)
