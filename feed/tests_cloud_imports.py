"""Cloud identity, corruption, durability and authorization, with synthetic data only."""
from datetime import timedelta
import hashlib
import io
import json
import ssl
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import zipfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import cloud_imports
from .cloud_files import CloudStorageError, fingerprint, stream_response, response_filename
from .cloud_storage import GoogleDriveClient, NextcloudClient
from .models import Assignment, AssignmentFile, ClassGroup, CloudImport, Subject, Teacher


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, content_type='application/octet-stream', filename='дані.bin', **headers):
        super().__init__(body)
        self.headers = {'Content-Type': content_type, 'Content-Length': str(len(body)),
                        'Content-Disposition': "attachment; filename*=UTF-8''" + __import__('urllib.parse', fromlist=['quote']).quote(filename), **headers}
        self.read_sizes = []

    def read(self, size=-1):
        if size < 0:
            raise AssertionError('Unbounded provider read')
        self.read_sizes.append(size)
        return super().read(size)


def api_response(data):
    return Response(json.dumps(data).encode(), 'application/json', 'metadata.json')


def office_file(kind='docx'):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr({'docx':'word/document.xml', 'xlsx':'xl/workbook.xml', 'pptx':'ppt/presentation.xml'}[kind], '<document>synthetic</document>')
        archive.writestr('[Content_Types].xml', '<Types/>')
    return output.getvalue()


class CloudTransferTests(SimpleTestCase):
    def test_unicode_extended_filename_takes_precedence_over_ascii_fallback(self):
        headers={'Content-Disposition':"attachment; filename=ascii.txt; filename*=UTF-8''%D0%A3%D1%80%D0%BE%D0%BA.txt"}
        self.assertEqual(response_filename(headers,'fallback'),'Урок.txt')

    def test_binary_link_never_tries_google_document_export(self):
        body = b'original binary\x00\x01'
        with patch.object(GoogleDriveClient, '_open', return_value=Response(body, filename='Оригінал.bin')) as network:
            download = GoogleDriveClient().download_file('https://drive.google.com/file/d/SyntheticBinary123456/view')
            self.assertEqual(download.filename, 'Оригінал.bin')
            self.assertEqual(download.file.read(), body)
            self.assertFalse(download.exported)
            download.close()
        self.assertIn('/uc?', network.call_args.args[0].full_url)
        self.assertNotIn('/document/', network.call_args.args[0].full_url)

    def test_direct_link_resolves_true_filename(self):
        with patch.object(GoogleDriveClient, '_open', return_value=Response(b'hello', 'text/plain', 'Мій файл.txt')):
            item = GoogleDriveClient().resolve_file('https://drive.google.com/file/d/SyntheticText123456/view')
        self.assertEqual(item['name'], 'Мій файл.txt')
        self.assertFalse(item['exported'])

    def test_google_native_document_uses_its_own_export_format(self):
        with patch.object(GoogleDriveClient, '_open', return_value=Response(office_file('xlsx'), filename='Таблиця.xlsx')) as network:
            result = GoogleDriveClient().download_file('https://docs.google.com/spreadsheets/d/SyntheticSheet12345/edit')
            self.assertTrue(result.exported)
            self.assertEqual(result.filename, 'Таблиця.xlsx')
            result.close()
        self.assertIn('/spreadsheets/', network.call_args.args[0].full_url)
        self.assertIn('format=xlsx', network.call_args.args[0].full_url)

    def test_login_html_is_never_attached_as_document(self):
        response = Response(b'<html>Sign in</html>', 'text/html')
        response.headers.pop('Content-Disposition')
        with patch.object(GoogleDriveClient, '_open', return_value=response):
            with self.assertRaises(CloudStorageError):
                GoogleDriveClient().download_file('https://drive.google.com/file/d/SyntheticText123456/view')

    def test_response_reads_are_bounded_and_sha256_matches_original(self):
        body = b'x' * (1024 * 1024 + 17)
        response = Response(body)
        download = stream_response(response, filename='дані.bin')
        self.assertEqual(download.sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(download.size, len(body))
        self.assertGreater(len(response.read_sizes), 4)
        self.assertLessEqual(max(response.read_sizes), 256 * 1024)
        download.close()

    def test_truncated_response_rejected(self):
        with self.assertRaisesRegex(CloudStorageError, 'не повністю'):
            stream_response(Response(b'partial', **{'Content-Length':'100'}), filename='дані.bin')

    def test_provider_checksum_rejects_wrong_file_of_same_size(self):
        with self.assertRaisesRegex(CloudStorageError, 'сума'):
            stream_response(Response(b'wrong'), filename='дані.bin', expected_size=5,
                            algorithm='md5', checksum=hashlib.md5(b'right').hexdigest())

    def test_provider_checksum_confirms_exact_file(self):
        result = stream_response(Response(b'right'), filename='дані.bin', expected_size=5,
                                 algorithm='md5', checksum=hashlib.md5(b'right').hexdigest())
        self.assertTrue(result.source_verified)
        result.close()

    def test_corrupt_office_file_rejected(self):
        with self.assertRaisesRegex(CloudStorageError, 'пошкоджений'):
            stream_response(Response(b'not a document', filename='Завдання.docx'), filename='Завдання.docx')

    @override_settings(CLOUD_IMPORT_MAX_BYTES=4)
    def test_cloud_size_limit_applies_before_copy(self):
        with self.assertRaises(CloudStorageError):
            stream_response(Response(b'large file'), filename='дані.bin')

    def test_google_api_pagination_reads_every_page(self):
        responses = [api_response({'files':[{'id':str(i),'name':str(i),'mimeType':'text/plain'} for i in range(100)],'nextPageToken':'page2'}),
                     api_response({'files':[{'id':'last','name':'last.txt','mimeType':'text/plain'}]})]
        with patch.object(GoogleDriveClient, '_open', side_effect=responses) as network:
            result = GoogleDriveClient(access_token='synthetic-only').list_directory()
        self.assertEqual(len(result['items']), 101)
        self.assertEqual(parse_qs(urlsplit(network.call_args.args[0].full_url).query)['pageToken'], ['page2'])

    def test_drive_sizes_distinguish_empty_unknown_and_native_export(self):
        data = {'files': [
            {'id': 'empty', 'name': 'empty.txt', 'mimeType': 'text/plain', 'size': '0'},
            {'id': 'unknown', 'name': 'unknown.txt', 'mimeType': 'text/plain'},
            {'id': 'native', 'name': 'Native', 'mimeType': 'application/vnd.google-apps.document'},
            {'id': 'known', 'name': 'photo.png', 'mimeType': 'image/png', 'size': '2048'},
        ]}
        with patch.object(GoogleDriveClient, '_open', return_value=api_response(data)):
            items = GoogleDriveClient(access_token='synthetic-only').list_directory()['items']
        self.assertTrue(items[0]['size_known'])
        self.assertFalse(items[1]['size_known'])
        self.assertIn('недоступний', items[1]['size_display'])
        self.assertEqual(items[2]['size_display'], 'Після експорту')
        self.assertTrue(items[2]['exported'])
        self.assertEqual(items[3]['size'], 2048)

    def test_public_size_resolution_reads_headers_without_file_body(self):
        response = Response(b'synthetic file', filename='original.txt')
        with patch.object(GoogleDriveClient, '_open', return_value=response):
            item = GoogleDriveClient().resolve_file('https://drive.google.com/file/d/SyntheticBinary123456/view')
        self.assertEqual(item['size'], 14)
        self.assertTrue(item['size_known'])
        self.assertEqual(response.read_sizes, [])
        self.assertTrue(response.closed)

    def test_public_missing_content_length_is_unknown_not_empty(self):
        response = Response(b'synthetic file', filename='original.txt')
        del response.headers['Content-Length']
        with patch.object(GoogleDriveClient, '_open', return_value=response):
            item = GoogleDriveClient().resolve_file('https://drive.google.com/file/d/SyntheticBinary123456/view')
        self.assertFalse(item['size_known'])
        self.assertEqual(item['size_display'], 'Розмір недоступний')

    def test_public_folder_marks_native_export_and_missing_sizes(self):
        data = {'items': [
            {'id': 'binary', 'name': 'file.txt', 'is_dir': False, 'mime_type': 'text/plain'},
            {'id': 'native', 'name': 'Native.docx', 'is_dir': False, 'mime_type': 'application/vnd.google-apps.document'},
        ]}
        with patch('feed.cloud_storage.fetch_embedded_google_folder', return_value=data):
            items = GoogleDriveClient().list_directory('SyntheticFolder123456')['items']
        self.assertEqual(items[0]['size_display'], 'Уточнення…')
        self.assertFalse(items[0]['size_known'])
        self.assertTrue(items[1]['exported'])
        self.assertEqual(items[1]['size_display'], 'Після експорту')

    def test_api_outage_is_reported_without_html_fallback(self):
        with patch.object(GoogleDriveClient, '_open', side_effect=HTTPError('https://www.googleapis.com/',503,'synthetic',{},None)), patch('feed.cloud_storage.time.sleep'), patch('feed.cloud_storage.fetch_embedded_google_folder') as fallback:
            with self.assertRaises(CloudStorageError):
                GoogleDriveClient(access_token='synthetic-only').list_directory()
            fallback.assert_not_called()

    def test_gmail_data_does_not_claim_drive_authorization(self):
        ok, _ = GoogleDriveClient(email='synthetic@example.invalid', password='synthetic-only').test_connection()
        self.assertFalse(ok)

    def test_folder_check_requires_actual_network_read(self):
        with patch('feed.cloud_storage.fetch_embedded_google_folder', side_effect=CloudStorageError('offline')) as network:
            ok, _ = GoogleDriveClient(folder_link='https://drive.google.com/drive/folders/SyntheticFolder12345').test_connection()
        self.assertFalse(ok)
        network.assert_called_once()

    def test_api_wrong_identity_rejected(self):
        with patch.object(GoogleDriveClient, '_open', return_value=api_response({'id':'other','name':'wrong.bin','mimeType':'application/octet-stream'})):
            with self.assertRaisesRegex(CloudStorageError, 'інший файл'):
                GoogleDriveClient(access_token='synthetic-only').resolve_file('https://drive.google.com/file/d/SyntheticBinary123456/view')

    def test_api_key_download_uses_official_binary_endpoint(self):
        body = b'original'
        responses = [api_response({'id':'SyntheticBinary123456','name':'exact.bin','mimeType':'application/octet-stream','size':str(len(body)),'md5Checksum':hashlib.md5(body).hexdigest()}), Response(body, filename='exact.bin')]
        with patch.object(GoogleDriveClient, '_open', side_effect=responses) as network:
            result = GoogleDriveClient(api_key='synthetic-only').download_file('https://drive.google.com/file/d/SyntheticBinary123456/view')
            self.assertTrue(result.source_verified); result.close()
        query = parse_qs(urlsplit(network.call_args.args[0].full_url).query)
        self.assertEqual(query['alt'], ['media']); self.assertEqual(query['key'], ['synthetic-only'])

    def test_resource_key_is_preserved_for_public_binary(self):
        with patch.object(GoogleDriveClient, '_open', return_value=Response(b'original')) as network:
            result = GoogleDriveClient().download_file('https://drive.google.com/file/d/SyntheticBinary123456/view?resourcekey=0-synthetic')
            result.close()
        self.assertEqual(parse_qs(urlsplit(network.call_args.args[0].full_url).query)['resourcekey'], ['0-synthetic'])

    def test_tls_requires_explicit_opt_out(self):
        for url in ('https://localhost:8090','https://cloud.example.invalid/localhost'):
            self.assertEqual(NextcloudClient(url,'synthetic','synthetic')._get_ssl_context().verify_mode,ssl.CERT_REQUIRED)
        self.assertEqual(NextcloudClient('https://localhost','synthetic','synthetic',ignore_ssl=True)._get_ssl_context().verify_mode,ssl.CERT_NONE)

    def test_non_google_link_and_nextcloud_traversal_rejected(self):
        with self.assertRaises(CloudStorageError): GoogleDriveClient._reference('https://example.invalid/file/d/SyntheticFile123456')
        with self.assertRaises(CloudStorageError): NextcloudClient._clean_path('../other/file')


class CloudQueueTests(TestCase):
    def setUp(self):
        from .middleware import set_has_admin
        set_has_admin(True)
        self.user = User.objects.create_superuser('synthetic-cloud',password='synthetic-only')
        self.teacher = Teacher.objects.create(user=self.user,full_name='Synthetic teacher')
        self.group = ClassGroup.objects.create(grade=9,letter='A')
        self.subject = Subject.objects.create(name='Synthetic subject')
        self.teacher.classes.add(self.group); self.teacher.subjects.add(self.subject)
        self.storage = self.teacher.get_cloud_storage()
        self.storage.nextcloud_enabled=True; self.storage.nextcloud_url='https://cloud.example.invalid'
        self.storage.nextcloud_username='synthetic';self.storage.nextcloud_password='synthetic-only'
        self.storage.google_drive_enabled=True;self.storage.save()
        self.client.force_login(self.user)
        self.body = b'exact original source contents'

    def ready(self, key='synthetic-request'):
        job=cloud_imports.enqueue(self.teacher,'nextcloud','original.txt',key,'original.txt')
        def download(client,path,progress=None,destination=None):
            return stream_response(Response(self.body,'text/plain','Оригінал.txt'),filename='Оригінал.txt',progress=progress,destination=destination)
        with patch.object(NextcloudClient,'download_file',download): cloud_imports.run_import(cloud_imports.claim())
        job.refresh_from_db();self.assertEqual(job.status,'ready');return job

    def form_data(self,job):
        return {'title':'Synthetic task','subject':self.subject.pk,'classes':[self.group.pk],
                'publish_choice':'draft','cloud_imports':json.dumps([str(job.pk)])}

    def test_ready_import_is_private_and_exact(self):
        job=self.ready()
        self.assertEqual(job.sha256,hashlib.sha256(self.body).hexdigest())
        self.assertEqual(cloud_imports.staging_path(job).read_bytes(),self.body)
        self.assertNotIn('/media/',str(cloud_imports.staging_path(job)))

    def test_assignment_creation_attaches_exact_verified_bytes(self):
        job=self.ready()
        response=self.client.post(reverse('assignment_create'),self.form_data(job))
        self.assertEqual(response.status_code,302)
        material=AssignmentFile.objects.get()
        self.assertEqual(material.original_name,'Оригінал.txt')
        self.assertEqual(material.content_sha256,job.sha256)
        with material.file.open('rb') as source:self.assertEqual(source.read(),self.body)
        job.refresh_from_db();self.assertEqual(job.status,'attached')

    def test_storage_corruption_is_rejected_and_assignment_rolled_back(self):
        job=self.ready()
        storage=AssignmentFile._meta.get_field('file').storage
        original=storage._save
        def corrupt(name,content):
            saved=original(name,content)
            with storage.open(saved,'wb') as output:output.write(b'changed in storage')
            return saved
        with patch.object(storage,'_save',side_effect=corrupt):
            response=self.client.post(reverse('assignment_create'),self.form_data(job))
        self.assertEqual(response.status_code,422)
        self.assertFalse(Assignment.objects.exists());self.assertFalse(AssignmentFile.objects.exists())
        job.refresh_from_db();self.assertEqual(job.status,'ready')

    def test_repeated_edit_is_idempotent_and_preserves_ai_source(self):
        job=self.ready()
        assignment=Assignment.objects.create(teacher=self.teacher,subject=self.subject,title='Draft',description='Synthetic',status='draft')
        assignment.classes.add(self.group)
        data=self.form_data(job);data['ai_task_file']='cloud:'+str(job.pk)
        for _ in range(2):
            self.assertEqual(self.client.post(reverse('assignment_edit',args=[assignment.pk]),data).status_code,302)
        self.assertEqual(assignment.files.count(),1)
        self.assertTrue(assignment.files.get().is_task_source_for_ai)

    def test_tampering_before_submit_blocks_attachment(self):
        job=self.ready();cloud_imports.staging_path(job).write_bytes(b'corrupted')
        response=self.client.post(reverse('assignment_create'),self.form_data(job))
        self.assertEqual(response.status_code,200)
        self.assertFalse(Assignment.objects.exists());self.assertFalse(AssignmentFile.objects.exists())

    def test_other_teacher_cannot_poll_or_attach(self):
        job=self.ready()
        other=User.objects.create_user('synthetic-other',password='synthetic-only')
        other_teacher=Teacher.objects.create(user=other,full_name='Other')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('teacher_cloud_imports'),{'ids':str(job.pk)}).status_code,404)
        with self.assertRaises(CloudStorageError):cloud_imports.prepare([str(job.pk)],other_teacher)

    def test_cancelled_import_is_not_processed(self):
        job=cloud_imports.enqueue(self.teacher,'nextcloud','original.txt','cancel')
        cloud_imports.cancel(job)
        self.assertIsNone(cloud_imports.claim())

    def test_enqueue_replay_does_not_duplicate_transfer(self):
        first=cloud_imports.enqueue(self.teacher,'nextcloud','original.txt','same')
        second=cloud_imports.enqueue(self.teacher,'nextcloud','original.txt','same')
        self.assertEqual(first.pk,second.pk);self.assertEqual(CloudImport.objects.count(),1)

    def test_stale_worker_claim_recovers_after_restart(self):
        job=cloud_imports.enqueue(self.teacher,'nextcloud','original.txt','restart')
        old=cloud_imports.claim()
        CloudImport.objects.filter(pk=job.pk).update(updated_at=timezone.now()-timedelta(hours=1))
        recovered=cloud_imports.claim()
        self.assertEqual(recovered.pk,old.pk);self.assertNotEqual(recovered.lease,old.lease)
        self.assertEqual(recovered.attempts,2)

    def test_expired_file_cannot_be_submitted(self):
        job=self.ready();CloudImport.objects.filter(pk=job.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(CloudStorageError):cloud_imports.prepare([str(job.pk)],self.teacher)

    def test_import_post_requires_csrf_and_authenticated_owner(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(reverse('teacher_cloud_imports'),json.dumps({'items':[]}),content_type='application/json').status_code,403)
        self.assertEqual(Client().get(reverse('teacher_cloud_imports')).status_code,302)

    def test_regular_upload_also_gets_persisted_integrity_evidence(self):
        assignment=Assignment.objects.create(teacher=self.teacher,title='Uploaded')
        material=AssignmentFile.objects.create(assignment=assignment,file=SimpleUploadedFile('original.txt',self.body))
        self.assertEqual(material.content_sha256,hashlib.sha256(self.body).hexdigest())
        self.assertEqual(material.verified_size,len(self.body))

    def test_previous_release_can_insert_files_after_additive_migration(self):
        from django.db import connection
        assignment=Assignment.objects.create(teacher=self.teacher,title='Rollback compatibility')
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO feed_assignmentfile (assignment_id, file, original_name, is_task_source_for_ai, uploaded_at) VALUES (%s,%s,%s,%s,%s)',
                           [assignment.pk,'legacy-reference.txt','legacy-reference.txt',False,timezone.now()])
        legacy=AssignmentFile.objects.get()
        self.assertEqual(legacy.content_sha256,'')
        self.assertEqual(legacy.source_provider,'')

    @patch('feed.views_cloud.GoogleDriveClient')
    def test_connection_check_preserves_token_expiry(self,client_class):
        self.storage.google_token_expiry=timezone.now()-timedelta(minutes=5);self.storage.save()
        client_class.return_value.test_connection.return_value=(True,'OK')
        self.client.post(reverse('teacher_cloud_test_google'))
        self.assertEqual(client_class.call_args.kwargs['token_expiry'],self.storage.google_token_expiry)
