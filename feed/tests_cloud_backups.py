"""Synthetic end-to-end backups, ownership boundaries and fresh-install recovery."""
import json
import shutil
import tempfile
from pathlib import Path
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TransactionTestCase, override_settings
from .models import Teacher, Assignment, ClassGroup, Subject, Student, Submission, SubmissionFile, AssignmentFile, SubmissionComment
from .cloud_backups import Adapter, models
from cloud_backup.archive import Repository
from cloud_backup.staging import recovery


class Remote:
    def __init__(self): self.objects={}
    def put(self,name,data): self.objects[name]=data
    def get(self,name,limit=10**12): return self.objects[name]
    def exists(self,name): return name in self.objects
    def list(self,name): return [key.split('/',1)[1] for key in self.objects if key.startswith(name+'/')]
    def delete(self,name): self.objects.pop(name,None)


class CloudBackupGraphTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='schoolwork-cloud-graph-');self.addCleanup(self.work.cleanup)
        self.root=Path(self.work.name)
        self.override=override_settings(MEDIA_ROOT=self.root/'source-media');self.override.enable();self.addCleanup(self.override.disable)
        with connection.cursor() as c: c.execute('DROP TABLE IF EXISTS cloud_restore_receipts')
        self.admin_user=User.objects.create_superuser(username='admin',password='admin-pass')
        self.admin=Teacher.objects.create(user=self.admin_user,full_name='Admin')
        self.user=User.objects.create_user(username='teacher',password='private-login')
        self.teacher=Teacher.objects.create(user=self.user,full_name='Учитель')
        self.group=ClassGroup.objects.create(name='9А',created_by=self.teacher)
        self.subject=Subject.objects.create(name='Інформатика',created_by=self.teacher)
        self.teacher.classes.add(self.group);self.teacher.subjects.add(self.subject)
        self.student=Student.objects.create(class_group=self.group,first_name='Іван',last_name='Учень')
        self.task=Assignment.objects.create(teacher=self.teacher,title='Own',subject=self.subject)
        self.task.classes.add(self.group)
        self.other=Assignment.objects.create(teacher=self.admin,title='Private other')
        self.material=AssignmentFile.objects.create(assignment=self.task,file=ContentFile(b'material',name='material.txt'))
        self.submission=Submission.objects.create(teacher=self.teacher,assignment=self.task,student=self.student,class_group=self.group,first_name='Іван',last_name='Учень',grade='11',file=ContentFile(b'answer',name='answer.txt'))
        self.comment=SubmissionComment.objects.create(submission=self.submission,author=self.user,text='Добре')
        self.config={'repository':'a'*32,'scope':'personal','materials':True,'students':True,'student_files':True,'format':'incremental'}
        self.adapter=Adapter()

    def capture(self,owner=None):
        repo=Repository(Remote(),'schoolwork','a'*32,'пароль')
        with self.adapter.snapshot(owner or self.teacher.pk,self.config) as entries:
            result=repo.backup(entries,scope=self.config['scope'])
        return repo,result['snapshot']

    def target(self):
        for model in reversed(list(models().values())): model.objects.all().delete()
        User.objects.all().delete()
        user=User.objects.create_superuser(id=1,username='new-admin',password='new-login')
        teacher=Teacher.objects.create(id=1,user=user,full_name='New admin')
        target_settings=override_settings(MEDIA_ROOT=self.root/'target-media');target_settings.enable();self.addCleanup(target_settings.disable)
        return teacher

    def test_personal_owns_only_own_work_and_excludes_credentials(self):
        repo,snapshot=self.capture();contents=dict(repo.read(snapshot))
        tasks=[json.loads(v) for n,v in contents.items() if n.startswith('db/feed.assignment/')]
        self.assertEqual([r['fields']['title'] for r in tasks],['Own'])
        auth=[json.loads(v) for n,v in contents.items() if n.startswith('db/auth.user/')]
        self.assertTrue(all(r['fields']['password']=='!' and not r['fields']['is_superuser'] for r in auth))
        self.assertTrue(any(n.startswith('files/media/submissions/') for n in contents))

    def test_personal_recovers_files_grades_comments_lists_and_idempotence(self):
        original_task=self.task.pk;original_submission=self.submission.pk;original_comment=self.comment.pk
        source_file=self.submission.file.name
        repo,snapshot=self.capture();target=self.target();self.config['_snapshot']=snapshot
        with recovery(self.root,repo,snapshot,'personal') as directory:
            self.adapter.validate(target.pk,self.config,directory)
            self.adapter.restore(target.pk,self.config,directory)
            self.assertTrue(self.adapter.restore(target.pk,self.config,directory)['already_restored'])
        self.assertEqual(Assignment.objects.get(pk=original_task).teacher_id,target.pk)
        self.assertEqual(Submission.objects.get(pk=original_submission).grade,'11')
        self.assertEqual(SubmissionComment.objects.get(pk=original_comment).author_id,target.user_id)
        self.assertEqual(target.classes.count(),1)
        self.assertEqual((self.root/'target-media'/source_file).read_bytes(),b'answer')
        target.user.refresh_from_db();self.assertTrue(target.user.check_password('new-login'))

    def test_server_recovers_all_accounts_and_work(self):
        self.config['scope']='server'
        from django.contrib.auth.models import Group,Permission
        group=Group.objects.create(name='Reviewers');group.permissions.add(Permission.objects.get(codename='view_assignment'))
        self.user.groups.add(group);self.user.user_permissions.add(Permission.objects.get(codename='change_submission'))
        repo,snapshot=self.capture(self.admin.pk);target=self.target();self.config['_snapshot']=snapshot
        with recovery(self.root,repo,snapshot,'server') as directory:
            self.adapter.restore(target.pk,self.config,directory)
        self.assertEqual(Assignment.objects.count(),2)
        self.assertTrue(User.objects.get(username='teacher').check_password('private-login'))
        self.assertTrue(User.objects.get(username='admin').is_superuser)
        recovered=User.objects.get(username='teacher')
        self.assertTrue(recovered.groups.filter(name='Reviewers').exists())
        self.assertTrue(recovered.user_permissions.filter(codename='change_submission').exists())

    def test_refuses_restore_over_existing_work(self):
        repo,snapshot=self.capture();self.config['_snapshot']=snapshot
        with recovery(self.root,repo,snapshot,'personal') as directory:
            with self.assertRaises(ValueError): self.adapter.restore(self.teacher.pk,self.config,directory)
        self.assertEqual(Assignment.objects.count(),2)

    def test_data_only_and_teacher_file_selection(self):
        self.config.update(materials=False,students=False,student_files=False)
        repo,snapshot=self.capture();contents=dict(repo.read(snapshot))
        self.assertFalse(any(n.startswith('files/') or n.startswith('db/feed.submission/') for n in contents))

    def test_teacher_cannot_read_full_backup(self):
        self.config['scope']='server'
        with self.assertRaises(ValueError): self.capture()

    def test_reused_numeric_account_id_cannot_access_previous_control_store(self):
        principal=self.adapter.owner(self.teacher)
        new=self.target()
        self.assertNotEqual(self.adapter.owner(new),principal)
        with self.assertRaises(ValueError): self.adapter.resolve(principal)

from django.test import TestCase, Client
from unittest.mock import patch
from cloud_backup.state import State


class CloudBackupRoutesTests(TestCase):
    def setUp(self):
        self.work=tempfile.TemporaryDirectory(prefix='schoolwork-cloud-routes-');self.addCleanup(self.work.cleanup)
        self.store=State(self.work.name,'test-secret')
        self.mock=patch('feed.views_backups.state',return_value=self.store);self.mock.start();self.addCleanup(self.mock.stop)
        User.objects.create_superuser(username='setup-admin',password='setup-only')
        self.user=User.objects.create_user(username='teacher',password='p')
        self.teacher=Teacher.objects.create(user=self.user,full_name='Учитель')
        self.client=Client(enforce_csrf_checks=True);self.client.force_login(self.user)

    def test_settings_render_csrf_and_secrets_stay_private(self):
        import re
        page=self.client.get('/teacher/backups')
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'Інкрементальне сховище')
        self.assertEqual(self.client.post('/teacher/backups',{'action':'save'}).status_code,403)
        csrf=re.search(r'name="csrfmiddlewaretoken" value="([a-zA-Z0-9]+)"',page.content.decode())[1]
        saved=self.client.post('/teacher/backups',{'csrfmiddlewaretoken':csrf,'action':'save','provider':'nextcloud','url':'https://cloud.example','username':'teacher','app_password':'private-cloud-pass','password':'private-archive-pass','materials':'on','students':'on'})
        self.assertEqual(saved.status_code,302)
        status=self.client.get('/teacher/backups/status')
        self.assertEqual(status.status_code,200)
        self.assertNotIn('private-cloud-pass',status.content.decode())
        self.assertNotIn('private-archive-pass',status.content.decode())
        self.assertEqual(self.client.get('/teacher/backups?scope=server').status_code,403)
        self.assertEqual(self.client.get('/teacher/backups/download/'+'a'*32).status_code,404)

    def test_callback_rejects_unsolicited_oauth_state(self):
        response=self.client.get('/teacher/backups/google/callback?state=forged&code=fake')
        self.assertEqual(response.status_code,302)
        self.assertFalse(self.store.config(str(self.teacher.pk)+'/personal').get('refresh_token'))
