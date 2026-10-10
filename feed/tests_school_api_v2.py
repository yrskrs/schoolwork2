import datetime as dt
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from feed.models import Teacher, ClassGroup, Subject, Student, Assignment, Submission, JournalAPIKey
from feed.forms import SubmissionForm
from school_sync.django_backend import operate, cycle

@override_settings(ROSTER_SYNC_TOKEN='synthetic-roster',ROSTER_PEERS=[])
class SchoolAPIV2Tests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username='synthetic-admin',password='synthetic')
        self.user=get_user_model().objects.create_user(username='synthetic-v2',password='synthetic')
        self.teacher=Teacher.objects.create(user=self.user,full_name='Вигаданий Вчитель')
        self.group=ClassGroup.objects.create(name='7-А',grade=7,letter='А');self.teacher.classes.add(self.group)
        self.subject=Subject.objects.create(name='Вигаданий предмет');self.teacher.subjects.add(self.subject)
        self.student=Student.objects.create(class_group=self.group,first_name='Учень',last_name='Вигаданий')
        self.twin=Student.objects.create(class_group=self.group,first_name='Учень',last_name='Вигаданий')
        self.assignment=Assignment.objects.create(teacher=self.teacher,subject=self.subject,title='Вигадане завдання');self.assignment.classes.add(self.group)
        self.key=JournalAPIKey.objects.create(teacher=self.teacher,key='synthetic-key')
        self.client.force_login(self.user)
    def export(self, **query):
        return self.client.get('/api/v2/journal/grades/', {'class_id':self.group.pk,'subject_id':self.subject.pk,'date_from':'2000-01-01','date_to':'2099-12-31',**query},HTTP_AUTHORIZATION='Bearer synthetic-key')
    def sub(self, **extra):
        return Submission.objects.create(assignment=self.assignment,student=self.student,class_group=self.group,last_name='Вигаданий',first_name='Учень',grade='8',graded_by=self.user,graded_at=timezone.now(),**extra)
    def test_scope_final_and_repeatable_id(self):
        sub=self.sub();record=self.export().json()['grades'][0]
        self.assertEqual(record['student_id'],self.student.pk);self.assertEqual(record['value'],'8')
        sub.grade='10';sub.graded_at=timezone.now();sub.save()
        updated=self.export().json()['grades'][0];self.assertEqual(record['id'],updated['id']);self.assertEqual(updated['value'],'10')
        self.key.teacher=Teacher.objects.create(user=get_user_model().objects.create_user(username='other-v2'),full_name='Інший');self.key.save()
        self.assertEqual(self.export().json()['grades'],[])
    def test_new_attempt_suppresses_old_grade(self):
        old=self.sub();new=Submission.objects.create(assignment=self.assignment,student=self.student,class_group=self.group,last_name='Вигаданий',first_name='Учень')
        self.assertEqual(self.export().json()['grades'],[])
        new.grade='9';new.graded_by=self.user;new.graded_at=timezone.now();new.save()
        record=self.export().json()['grades'][0];self.assertEqual(record['attempt_id'],new.pk)
        old.delete();self.assertEqual(self.export().json()['grades'][0]['id'],record['id'])
    def test_twins_use_native_ids_and_assignment_context(self):
        operate(lambda engine,store:engine.share('class',self.group.pk))
        response=self.client.get('/api/students-autocomplete/',{'class_group_id':self.group.pk}).json()
        self.assertEqual({r['id'] for r in response['students']},{self.student.pk,self.twin.pk})
        data={'full_name':'Вигаданий Учень','class_group':self.group.pk,'comment_student':'Синтетична відповідь','student_id':self.twin.pk}
        form=SubmissionForm(data,assignment=self.assignment);self.assertTrue(form.is_valid(),form.errors);submission=form.save(self.assignment)
        self.assertEqual(submission.student_id,self.twin.pk)
        data.pop('student_id');form=SubmissionForm(data,assignment=self.assignment);self.assertFalse(form.is_valid())
        other=ClassGroup.objects.create(name='8-Б',grade=8,letter='Б');data['class_group']=other.pk;data['student_id']=self.student.pk
        self.assertFalse(SubmissionForm(data,assignment=self.assignment).is_valid())
    def test_roster_permissions_offline_and_no_background_grades(self):
        self.assertEqual(self.client.get('/api/v2/roster-sync/').status_code,401)
        operate(lambda engine,store:engine.share('class',self.group.pk))
        payload=self.client.get('/api/v2/roster-sync/',HTTP_AUTHORIZATION='Bearer synthetic-roster').json()
        self.assertEqual(len(payload['records']),3);cycle();self.assertFalse(Submission.objects.exists())
        self.assertEqual(self.client.get('/teacher/roster-sync/').status_code,200)
        self.assertEqual(self.client.get('/api/v2/journal/roster/',HTTP_AUTHORIZATION='Bearer synthetic-key').status_code,200)
    def test_teacher_final_only_and_cursor(self):
        sub=self.sub();self.assertEqual(self.export(cursor=sub.pk).json()['grades'],[])
        sub.graded_by=None;sub.save();self.assertEqual(self.export().json()['grades'],[])
        self.assertEqual(self.export(class_id='bad').status_code,400)
        self.assertEqual(self.client.get('/api/v2/journal/grades/').status_code,401)

    def test_inactive_assignment_classes_do_not_allow_other_classes(self):
        self.group.integration_active=False;self.group.save()
        other=ClassGroup.objects.create(name='8-Б',grade=8,letter='Б')
        data={'full_name':'Вигаданий Учень','student_id':self.student.pk,'class_group':other.pk,'comment_student':'Відповідь'}
        self.assertFalse(SubmissionForm(data,assignment=self.assignment).is_valid())
    def test_legacy_roster_import_cannot_merge_shared_ids(self):
        operate(lambda engine,store:engine.share('class',self.group.pk))
        response=self.client.post('/api/v1/journal/roster/',data={'students':[]},content_type='application/json',HTTP_AUTHORIZATION='Bearer synthetic-key')
        self.assertEqual(response.status_code,409)
        self.assertEqual(Student.objects.count(),2)
    def test_edit_active_state_preserves_native_id_and_emits_clock(self):
        operate(lambda engine,store:engine.share('class',self.group.pk))
        response=self.client.get('/teacher/roster-sync/')
        row=next(row for row in response.context['roster_rows'] if row['kind']=='student' and row['native_id']==self.student.pk)
        data={'action':'edit','ref':row['ref'],'version':row['version'],
            'first_name':row['payload']['first_name'],'last_name':row['payload']['last_name']}
        data['middle_name']='Новий запис';data['active']='1'
        self.assertEqual(self.client.post('/teacher/roster-sync/',data).status_code,302)
        pupil=Student.objects.get(pk=self.student.pk);self.assertEqual(pupil.middle_name,'Новий запис')
        record=operate(lambda engine,store:engine.native_record('student',self.student.pk))
        self.assertGreater(record['clock'][next(iter(record['clock']))],1)

    def test_native_profile_keeps_twins_separate(self):
        operate(lambda engine,store:engine.share('class',self.group.pk))
        first=self.sub()
        second=self.sub();second.student=self.twin;second.save()
        from django.urls import reverse
        page=self.client.get(reverse('student_detail',kwargs={'student_name':'Вигаданий_Учень'}),{'student_id':self.twin.pk})
        self.assertEqual(page.status_code,200)
        self.assertEqual([row.pk for row in page.context['page_obj']],[second.pk])
        self.assertEqual(self.student.get_submissions_count(),1);self.assertEqual(self.twin.get_submissions_count(),1)

    @override_settings(PUBLIC_BASE_URL='http://example.invalid', JOURNAL_API_BASE_URL='http://schoolwork-api:8000')
    def test_one_click_key_connection_code_and_key_owner_permissions(self):
        import json
        import re
        from html import unescape
        from django.urls import reverse
        url = reverse('teacher_settings')
        response = self.client.post(url, {'action': 'create_simple_journal_api_key'}, follow=True)
        self.assertEqual(response.status_code, 200)
        code = json.loads(unescape(re.search(r'<textarea[^>]*id="journal-connection-code"[^>]*>(.*?)</textarea>', response.content.decode(), re.S).group(1)))
        self.assertEqual(code['source'], 'schoolwork')
        self.assertEqual(code['site_url'], 'http://schoolwork-api:8000')
        key = JournalAPIKey.objects.get(key=code['api_key'])
        self.assertEqual(key.teacher, self.teacher)
        self.assertTrue(key.can_export_grades)
        self.assertFalse(key.can_import_roster)
        roster = self.client.get('/api/v2/journal/roster/', HTTP_AUTHORIZATION='Bearer ' + key.key).json()
        self.assertEqual(roster['classes'][0]['name'], '7-А')
        other = Teacher.objects.create(user=get_user_model().objects.create_user(username='other-private-key'), full_name='Інший')
        private = JournalAPIKey.objects.create(teacher=other, key='synthetic-other-private-key')
        self.assertNotContains(self.client.get(url, {'tab': 'api'}), private.key)
        for action in ('toggle_journal_api_key', 'delete_journal_api_key'):
            self.assertEqual(self.client.post(url, {'action': action, 'key_id': private.pk}).status_code, 404)
        private.refresh_from_db(); self.assertTrue(private.is_active)
