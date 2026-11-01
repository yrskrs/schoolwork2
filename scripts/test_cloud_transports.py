"""Provider protocol tests use synthetic HTTP responses; no provider accounts."""
import io
import json
import unittest
from unittest.mock import patch, Mock
from urllib.parse import urlsplit,parse_qs
from urllib.error import URLError
from cloud_backup.remote import GoogleDrive,Nextcloud,NoRedirect,request


class TransportTests(unittest.TestCase):
    def test_nextcloud_stages_then_moves_and_uses_private_auth(self):
        calls=[];objects={}
        def call(url,method='GET',data=None,headers=None,limit=None):
            calls.append((url,method,headers))
            self.assertTrue(headers['Authorization'].startswith('Basic '))
            self.assertNotIn('app-secret',url)
            if method=='MKCOL': return b'',{}
            if method=='PUT': objects[url]=data;return b'',{}
            if method=='MOVE': objects[headers['Destination']]=objects.pop(url);return b'',{}
            if method=='HEAD':
                if url not in objects: raise FileNotFoundError()
                return b'',{}
            if method=='GET': return objects[url],{}
            if method=='DELETE': objects.pop(url);return b'',{}
            if method=='PROPFIND':
                return b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/snapshots/123.zip</d:href></d:response></d:multistatus>',{}
        remote=Nextcloud({'url':'https://cloud.example/nextcloud','username':'test user','app_password':'app-secret'},'test','a'*32)
        with patch('cloud_backup.remote.request',side_effect=call):
            remote.prepare();self.assertFalse(remote.exists('snapshots/123.zip'))
            remote.put('snapshots/123.zip',b'archive')
            self.assertTrue(remote.exists('snapshots/123.zip'))
            self.assertEqual(remote.get('snapshots/123.zip'),b'archive')
            self.assertEqual(remote.list('snapshots'),['123.zip'])
            remote.delete('snapshots/123.zip')
        self.assertTrue(any(method=='MOVE' and headers['Destination'].endswith('/snapshots/123.zip') for url,method,headers in calls))

    def test_drive_refresh_folder_and_resumable_upload_then_update(self):
        calls=[]
        def call(url,method='GET',data=None,headers=None,limit=None):
            calls.append((url,method,data,headers))
            self.assertNotIn('refresh-secret',url)
            if 'oauth2.googleapis.com' in url: return json.dumps({'access_token':'token'}).encode(),{}
            if 'uploadType=resumable' in url: return b'',{'Location':'https://www.googleapis.com/upload/session/123'}
            if '/upload/session/' in url: return json.dumps({'id':'file-id'}).encode(),{}
            if 'alt=media' in url: return b'archive',{}
            if method=='DELETE': return b'',{}
            q=parse_qs(urlsplit(url).query).get('q',[''])[0]
            if "mimeType='application/vnd.google-apps.folder'" in q: return json.dumps({'files':[{'id':'folder','name':'folder'}]}).encode(),{}
            return json.dumps({'files':[]}).encode(),{}
        remote=GoogleDrive({'client_id':'client','client_secret':'client-secret','refresh_token':'refresh-secret'},'test','a'*32)
        with patch('cloud_backup.remote.request',side_effect=call):
            remote.prepare();remote.put('snapshots/test.zip',b'archive')
            remote.put('snapshots/test.zip',b'archive2')
            self.assertEqual(remote.get('snapshots/test.zip'),b'archive')
            remote.delete('snapshots/test.zip')
        self.assertTrue(any(method=='POST' and 'uploadType=resumable' in url for url,method,data,headers in calls))
        self.assertTrue(any(method=='PATCH' and '/file-id?' in url for url,method,data,headers in calls))
        self.assertEqual(GoogleDrive.SCOPE,'https://www.googleapis.com/auth/drive.file')

    def test_external_redirect_and_upload_url_rejected(self):
        with self.assertRaises(ValueError): NoRedirect().redirect_request(None,None,302,'',{},'https://attacker.example')
        remote=GoogleDrive({},'test','a'*32);remote.token='secret';remote.folder='folder'
        with patch.object(remote,'lookup',return_value=None),patch.object(remote,'call',return_value=(b'',{'Location':'https://attacker.example/upload'})):
            with self.assertRaises(ValueError):remote.put('blocks/test.zip',b'archive')

    def test_retry_transient_reads_and_bound_response_size(self):
        response=io.BytesIO(b'ok');response.headers={}
        opener=Mock();opener.open.side_effect=[URLError('offline'),URLError('offline'),response]
        with patch('urllib.request.build_opener',return_value=opener),patch('time.sleep'):
            self.assertEqual(request('https://cloud.example')[0],b'ok')
        self.assertEqual(opener.open.call_count,3)
        opener.open.side_effect=None;opener.open.return_value=io.BytesIO(b'too large')
        with patch('urllib.request.build_opener',return_value=opener):
            with self.assertRaises(ValueError):request('https://cloud.example',limit=2)

if __name__=='__main__':unittest.main()
