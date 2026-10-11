import uuid
from django.test import TestCase
from feed import tests_school_api_v2 as fixtures
from feed.models import Student, ClassGroup, Submission


class JournalRosterReceiveTests(TestCase):
    setUp = fixtures.SchoolAPIV2Tests.setUp

    def payload(self, **changes):
        data = {'schema_version': 2, 'journal_instance_id': '11111111-1111-4111-8111-111111111111',
            'class': {'id': '22222222-2222-4222-8222-222222222222', 'name': self.group.name, 'grade_level': 7, 'letter': 'А'},
            'source_class_id': self.group.pk, 'subject': {'name': self.subject.name},
            'students': [{'id': '33333333-3333-4333-8333-333333333333', 'last_name': 'Коваленко', 'first_name': 'Ірина'}]}
        data.update(changes)
        return data

    def send(self, data):
        return self.client.post('/api/v2/journal/roster/', data=data, content_type='application/json', HTTP_AUTHORIZATION='Bearer synthetic-key')

    def test_copy_and_repeat_keep_class_and_submission_history(self):
        sub = fixtures.SchoolAPIV2Tests.sub(self)
        first = self.send(self.payload()); second = self.send(self.payload())
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()['mappings'], second.json()['mappings'])
        self.assertEqual(Student.objects.count(), 3)
        sub.refresh_from_db(); self.assertEqual(sub.student_id, self.student.pk)
        self.assertEqual(ClassGroup.objects.count(), 1)
        self.send(self.payload(students=[]))
        self.assertEqual(Student.objects.count(), 3)

    def test_twins_and_typos_do_not_merge_by_name(self):
        data = self.payload(students=[{'id': str(uuid.uuid4()), 'last_name': 'Вигаданий', 'first_name': 'Учень'}])
        report = self.send(data).json()
        self.assertEqual(report['counts']['needs_review'], 1)
        self.assertEqual(len(report['needs_review'][0]['candidates']), 2)
        self.assertEqual(Student.objects.count(), 2)
        data['students'][0]['local_id'] = self.twin.pk
        response = self.send(data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['mappings'][0]['local_id'], self.twin.pk)

    def test_malformed_later_row_rolls_back_everything(self):
        data = self.payload()
        data['students'].append({'id': 'bad', 'last_name': 'Другий', 'first_name': 'Учень'})
        self.assertEqual(self.send(data).status_code, 400)
        self.assertEqual(Student.objects.count(), 2)

    def test_new_class_and_subject_belong_to_key_teacher(self):
        data = self.payload(source_class_id=None)
        data['class']['name'] = '8-Б'; data['class']['grade_level'] = 8
        data['subject']['name'] = 'Синтетичний новий предмет'
        response = self.send(data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(self.teacher.classes.filter(pk=response.json()['class_id']).exists())
        self.assertTrue(self.teacher.subjects.filter(pk=response.json()['subject_id']).exists())
        self.assertEqual(self.send(data).status_code, 200)

    def test_import_permission_and_foreign_class(self):
        self.key.can_import_roster = False; self.key.save()
        self.assertEqual(self.send(self.payload()).status_code, 403)
        self.key.can_import_roster = True; self.key.save()
        group = ClassGroup.objects.create(name='8-Б', grade=8, letter='Б')
        self.assertEqual(self.send(self.payload(source_class_id=group.pk)).status_code, 400)
        self.assertEqual(Student.objects.count(), 2)

    def test_name_is_in_grade_export_and_request_stats_increase(self):
        sub = fixtures.SchoolAPIV2Tests.sub(self)
        row = fixtures.SchoolAPIV2Tests.export(self).json()['grades'][0]
        self.assertEqual(row['student_name'], self.student.get_full_name())
        self.assertEqual(row['student_key'], str(self.student.pk))
        self.key.refresh_from_db(); self.assertGreater(self.key.requests_count, 0)

    def test_inactive_key_owner_cannot_read_or_change_roster(self):
        user = self.teacher.user
        user.is_active = False; user.save(update_fields=['is_active'])
        self.assertEqual(self.send(self.payload()).status_code, 401)
        self.assertEqual(self.client.get('/api/v2/journal/roster/', HTTP_AUTHORIZATION='Bearer synthetic-key').status_code, 401)
        self.assertEqual(Student.objects.count(), 2)

    def test_legacy_name_import_only_works_before_optional_journal_connection(self):
        from feed.journal_api import sync_roster_data
        from feed.models import RosterReplica
        payload = {'classes': [{'name': self.group.name, 'students': [
            {'last_name': 'Самостійний', 'first_name': 'Учень'}]}]}
        self.assertNotIn('blocked', sync_roster_data(payload, teacher=self.teacher))
        self.assertEqual(self.send(self.payload()).status_code, 200)
        self.assertFalse(RosterReplica.objects.filter(data__kind='class').exists())
        before = list(Student.objects.order_by('pk').values())
        self.assertTrue(sync_roster_data(payload, teacher=self.teacher)['blocked'])
        self.assertEqual(list(Student.objects.order_by('pk').values()), before)
