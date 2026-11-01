"""Portable SchoolWork fixtures with ownership closure and raw, atomic restore."""
import contextlib
import hashlib
import json
import os
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import User
from django.core import serializers
from django.core.management.color import no_style
from django.db import connection, transaction
from django.db.models import FileField, Q

from cloud_backup.archive import json_bytes
from cloud_backup.files import bytes_source, destination, file_source, install_files
from cloud_backup.state import State
from .models import Assignment, Submission, Teacher, SystemNotification

# Transient processes and external access credentials are reconfigured after
# recovery; do not accidentally resume a code runner or export cloud passwords.
EXCLUDE = {'teachercloudstorage', 'cloudimport', 'aijob', 'journalapikey',
           'submissionnotification', 'onlineclient', 'systemnotification', 'studentaicheckreservation'}


def state():
    root = os.environ.get('CLOUD_BACKUP_ROOT', '/var/cache/schoolnet/cloud-backups' if Path('/var/cache/schoolnet').exists() else str(settings.BASE_DIR/'private-backups'))
    return State(root, settings.SECRET_KEY)


def models():
    return {m._meta.label_lower: m for m in apps.get_app_config('feed').get_models() if m._meta.model_name not in EXCLUDE}


def schema():
    return {label: [f.name for f in [*m._meta.local_fields, *m._meta.local_many_to_many]] for label,m in models().items()} | {
        'auth.user': [f.name for f in [*User._meta.local_fields, *User._meta.local_many_to_many]]}


class Adapter:
    app = 'schoolwork'

    @staticmethod
    def owner(teacher):
        fingerprint = hashlib.sha256(f'{teacher.user.username}:{teacher.user.date_joined.isoformat()}'.encode()).hexdigest()[:32]
        return f'{teacher.pk}~{fingerprint}'

    def resolve(self, owner):
        number = int(str(owner).split('~')[0])
        if '~' in str(owner):
            teacher = Teacher.objects.select_related('user').filter(pk=number).first()
            if not teacher or self.owner(teacher) != owner:
                raise ValueError('Обліковий запис змінився. Налаштуйте резервування повторно')
        return number

    def authorize(self, owner, scope):
        from django.db import close_old_connections
        if not connection.in_atomic_block: close_old_connections()
        owner = self.resolve(owner)
        if scope not in ('personal', 'server'):
            raise ValueError('Некоректний обсяг копії')
        teacher = Teacher.objects.select_related('user').filter(pk=owner, user__is_active=True).first()
        if not teacher or (scope == 'server' and not teacher.user.is_superuser):
            raise ValueError('Недостатньо прав для цієї операції')

    @contextlib.contextmanager
    def snapshot(self, owner, config):
        self.authorize(owner, config['scope'])
        owner = self.resolve(owner)
        personal = config['scope'] == 'personal'
        allowed = models()
        if not config['students']:
            allowed = {key:m for key,m in allowed.items() if m._meta.model_name not in ('submission','submissionfile','submissioncomment','submissionactivitylog')}
        selected = {}
        def add(obj):
            if obj is not None:
                selected[(obj._meta.label_lower, str(obj.pk))] = obj
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            if not personal:
                for model in allowed.values():
                    for obj in model.objects.all().order_by('pk'):
                        add(obj)
                for obj in User.objects.all():
                    add(obj)
            else:
                teacher = Teacher.objects.get(pk=owner)
                add(teacher)
                add(teacher.user)
                for model in allowed.values():
                    fields = {f.name for f in model._meta.fields}
                    if 'teacher' in fields:
                        for obj in model.objects.filter(teacher=teacher):
                            add(obj)
                # Shared lists owned/used by this teacher, never other teachers' tasks.
                for obj in teacher.classes.all(): add(obj)
                for obj in teacher.subjects.all(): add(obj)
                for label in ('ClassGroup','Subject'):
                    for obj in apps.get_model('feed',label).objects.filter(created_by=teacher):add(obj)
                if config['students']:
                    for obj in Submission.objects.filter(Q(teacher=teacher) | Q(teacher__isnull=True, assignment__teacher=teacher)):
                        add(obj)
                changed = True
                while changed:
                    before = len(selected)
                    for obj in list(selected.values()):
                        # Expand only safe forward dependencies; another Teacher
                        # and another Assignment are never a traversal root.
                        for field in obj._meta.fields:
                            if field.is_relation and field.many_to_one or field.one_to_one:
                                related = field.remote_field.model
                                label = related._meta.label_lower
                                pk = getattr(obj, field.attname)
                                if pk is None or (label not in allowed and related is not User):
                                    continue
                                if related is Teacher and pk != int(owner):
                                    continue
                                if related is Assignment and (label, str(pk)) not in selected:
                                    continue
                                related_object=related.objects.filter(pk=pk).first()
                                if related is Submission and related_object is not None:
                                    if related_object.teacher_id != int(owner) and not (related_object.teacher_id is None and related_object.assignment_id is not None and Assignment.objects.filter(pk=related_object.assignment_id,teacher_id=owner).exists()):
                                        continue
                                add(related_object)
                        for field in obj._meta.many_to_many:
                            if field.remote_field.model._meta.label_lower in allowed:
                                for related in getattr(obj, field.name).all(): add(related)
                    # Child records belonging to chosen tasks and submissions.
                    for model in allowed.values():
                        for field in model._meta.fields:
                            if field.is_relation and field.many_to_one and field.remote_field.model in (Assignment, Submission):
                                ids = [o.pk for o in selected.values() if type(o) is field.remote_field.model]
                                if ids:
                                    for obj in model.objects.filter(**{field.name+'__in': ids}):
                                        if hasattr(obj, 'teacher_id') and obj.teacher_id not in (None, int(owner)):
                                            continue
                                        add(obj)
                    # Students in the selected class lists are part of roster data.
                    Student = apps.get_model('feed', 'Student')
                    class_ids = [o.pk for o in selected.values() if o._meta.model_name == 'classgroup']
                    for obj in Student.objects.filter(class_group_id__in=class_ids): add(obj)
                    changed = len(selected) != before
            data = json.loads(serializers.serialize('json', list(selected.values())))
            for row in data:
                fields = row['fields']
                if row['model'] == 'auth.user':
                    fields['groups'] = []
                    fields['user_permissions'] = []
                    if personal:
                        fields.update(password='!', is_superuser=False, is_staff=False, is_active=False, email='', last_login=None)
                if row['model'] == 'feed.aisettings':
                    fields['api_key'] = fields['backup_api_key'] = ''
                    fields['provider_connections'] = [{**c, 'api_key': ''} for c in fields.get('provider_connections', [])]
                if personal:
                    model = (allowed | {'auth.user':User})[row['model']]
                    for field in model._meta.fields:
                        if field.is_relation and field.many_to_one or field.one_to_one:
                            value = fields.get(field.name)
                            key = (field.remote_field.model._meta.label_lower, str(value))
                            if value is not None and key not in selected:
                                if field.null:
                                    fields[field.name] = None
                                else:
                                    raise ValueError('Не вдалося включити обов’язковий зв’язок до особистої копії')
            from .changelog import SITE_VERSION
            index = {'app':self.app, 'protocol':1, 'application_version':SITE_VERSION, 'scope':config['scope'], 'owner':int(owner),
                     'user':Teacher.objects.get(pk=owner).user_id, 'schema':schema(), 'records':len(data),
                     'options':{k:config[k] for k in ('materials','students','student_files')}}
            if not personal:
                from django.contrib.auth.models import Group
                index['groups'] = {group.name:[list(p.natural_key()) for p in group.permissions.all()] for group in Group.objects.all()}
                index['user_roles'] = {str(user.pk):{'groups':list(user.groups.values_list('name',flat=True)), 'permissions':[list(p.natural_key()) for p in user.user_permissions.all()]} for user in User.objects.all()}
            entries = [('index.json', bytes_source(json_bytes(index)))]
            files = {}
            for row in sorted(data, key=lambda r: (r['model'], str(r['pk']))):
                entries.append((f"db/{row['model']}/{row['pk']}.json", bytes_source(json_bytes(row))))
                obj = selected[(row['model'], str(row['pk']))]
                for field in obj._meta.fields:
                    if not isinstance(field, FileField): continue
                    value = getattr(obj, field.name)
                    is_student = obj._meta.model_name in ('submission','submissionfile')
                    if value and (config['student_files'] if is_student else config['materials']):
                        files[value.name] = destination(settings.MEDIA_ROOT, value.name)
            # Capture fixture consistently; file readers detect concurrent changes.
        for name, path in sorted(files.items()):
            entries.append(('files/media/'+name, file_source(path)))
        yield entries

    def load(self, owner, config, directory):
        owner = self.resolve(owner)
        index = json.loads((directory/'index.json').read_text())
        if index.get('app') != self.app or index.get('scope') != config['scope'] or index.get('schema') != schema():
            raise ValueError('Копія несумісна з цією схемою SchoolWork. Установіть сумісну версію сайту')
        rows = [json.loads(path.read_text()) for path in sorted((directory/'db').rglob('*.json'))]
        if len(rows) != index['records']:
            raise ValueError('Неповний склад БД')
        allowed = models() | {'auth.user':User}
        for row in rows:
            if row['model'] not in allowed or set(row['fields'])-set(schema()[row['model']]):
                raise ValueError('Невідома модель або поле БД')
        personal = config['scope'] == 'personal'
        if personal:
            teachers = [row for row in rows if row['model'] == 'feed.teacher']
            if len(teachers) != 1 or teachers[0]['pk'] != index['owner']:
                raise ValueError('Некоректний власник особистої копії')
            index['teacher_fields'] = teachers[0]['fields'].copy()
            target = Teacher.objects.get(pk=owner)
            for row in rows:
                if row['model'] == 'auth.user':
                    row['fields'].update(password='!', is_superuser=False, is_staff=False, is_active=False, email='', groups=[], user_permissions=[])
                for field in allowed[row['model']]._meta.fields:
                    if field.is_relation and (field.many_to_one or field.one_to_one):
                        if field.remote_field.model is Teacher and row['fields'].get(field.name) == index['owner']:
                            row['fields'][field.name] = int(owner)
                        if field.remote_field.model is User and row['fields'].get(field.name) == index['user']:
                            row['fields'][field.name] = target.user_id
                if row['model'] == 'feed.assignment' and row['fields'].get('teacher') != int(owner):
                    raise ValueError('Копія містить сторонні завдання')
            rows = [r for r in rows if not (r['model'] == 'feed.teacher' or r['model'] == 'auth.user' and r['pk'] == index['user'])]
            # Attribution-only accounts must not collide with the new installer.
            last_user = User.objects.order_by('-pk').values_list('pk',flat=True).first() or 0
            remap = {}
            for row in rows:
                if row['model'] == 'auth.user':
                    last_user += 1
                    remap[row['pk']] = last_user
                    row['pk'] = last_user
                    if User.objects.filter(username=row['fields']['username']).exists():
                        row['fields']['username'] = ('archive_'+str(last_user)+'_'+row['fields']['username'])[:150]
            # Owner FK values were mapped above; distinguish them using the
            # untouched fixture payload when applying attribution remapping.
            original = {(r['model'],r['pk']):r for r in [json.loads(path.read_text()) for path in sorted((directory/'db').rglob('*.json'))]}
            inverse_users = {new:old for old,new in remap.items()}
            for row in rows:
                original_pk = inverse_users.get(row['pk'],row['pk']) if row['model']=='auth.user' else row['pk']
                source_fields = original[(row['model'],original_pk)]['fields']
                for field in allowed[row['model']]._meta.fields:
                    if field.is_relation and (field.many_to_one or field.one_to_one) and field.remote_field.model is User:
                        value = source_fields.get(field.name)
                        if value == index['user']:
                            row['fields'][field.name] = target.user_id
                        elif value in remap:
                            row['fields'][field.name] = remap[value]
            # Other attribution-only user accounts retain their IDs but never
            # gain login privileges. A collision is rejected, never overwritten.
        pairs = []
        if (directory/'files').exists():
            for path in (directory/'files').rglob('*'):
                if path.is_file():
                    name = path.relative_to(directory/'files').as_posix()
                    if not name.startswith('media/'):
                        raise ValueError('Невідомий каталог файлів')
                    pairs.append((path, destination(settings.MEDIA_ROOT, name.removeprefix('media/'))))
        # Constructing deserialized objects validates field representations without
        # executing custom save() methods or any code from an archive.
        objects = list(serializers.deserialize('json', json.dumps(rows)))
        return index, objects, pairs

    def validate(self, owner, config, directory):
        index, objects, pairs = self.load(owner, config, directory)
        return {'records': len(objects), 'files': len(pairs), 'options':index['options']}

    def restore(self, owner, config, directory):
        self.authorize(owner, config['scope'])
        owner = self.resolve(owner)
        index, objects, pairs = self.load(owner, config, directory)
        restored_models = set()
        with contextlib.ExitStack() as file_guard:
            with transaction.atomic():
                if connection.vendor == 'postgresql':
                    with connection.cursor() as cursor:
                        cursor.execute('LOCK TABLE '+', '.join(connection.ops.quote_name(m._meta.db_table) for m in sorted(models().values(),key=lambda m:m._meta.db_table))+' IN ACCESS EXCLUSIVE MODE')
                with connection.cursor() as cursor:
                    cursor.execute('CREATE TABLE IF NOT EXISTS cloud_restore_receipts (receipt VARCHAR(200) PRIMARY KEY)')
                    receipt = config['repository']+':'+config['_snapshot']
                    cursor.execute('SELECT receipt FROM cloud_restore_receipts WHERE receipt=%s', [receipt])
                    if cursor.fetchone():
                        return {'already_restored':True}
                if Assignment.objects.exists() or Submission.objects.exists() or Teacher.objects.count() > 1:
                    raise ValueError('Відновлення дозволене лише на новому встановленні без робіт та інших учителів')
                if any(target.exists() for source,target in pairs):
                    raise ValueError('Файли вже існують. Використайте чисте встановлення')
                personal = config['scope'] == 'personal'
                file_guard.enter_context(install_files(pairs))
                if personal:
                    # On the fresh target, replace automatically seeded metadata
                    # as well. Keep the installer login and school identity.
                    for model in reversed(list(models().values())):
                        if model not in (Teacher, apps.get_model('feed','School')):
                            model.objects.all().delete()
                else:
                    for model in reversed(list(models().values())):
                        model.objects.all().delete()
                    User.objects.all().delete()
                # Django FKs are deferred; restore self references and cycles in
                # one transaction and check constraints before exposing success.
                with connection.constraint_checks_disabled():
                    for obj in objects:
                        obj.save()
                        restored_models.add(type(obj.object))
                if personal:
                    teacher = Teacher.objects.get(pk=owner)
                    teacher_fields = index['teacher_fields']
                    for field in Teacher._meta.local_fields:
                        if field.name not in ('id','user'):
                            setattr(teacher,field.attname,field.to_python(teacher_fields[field.name]))
                    teacher.save_base(raw=True)
                    teacher.classes.set(teacher_fields.get('classes',[]))
                    teacher.subjects.set(teacher_fields.get('subjects',[]))
                if not personal:
                    from django.contrib.auth.models import Group,Permission
                    restored_groups = {}
                    for name,keys in index.get('groups',{}).items():
                        group,_=Group.objects.get_or_create(name=name)
                        group.permissions.set([Permission.objects.get_by_natural_key(*key) for key in keys])
                        restored_groups[name]=group
                    for user_id,roles in index.get('user_roles',{}).items():
                        user=User.objects.get(pk=user_id)
                        user.groups.set([restored_groups[name] for name in roles['groups']])
                        user.user_permissions.set([Permission.objects.get_by_natural_key(*key) for key in roles['permissions']])
                connection.check_constraints()
                with connection.cursor() as cursor:
                    for sql in connection.ops.sequence_reset_sql(no_style(), list(restored_models)):
                        cursor.execute(sql)
                    cursor.execute('INSERT INTO cloud_restore_receipts(receipt) VALUES(%s)', [receipt])
                # The outer transaction commits just after leaving this guard;
                # deferred constraints have already been checked explicitly.
        return {'records':len(objects), 'files':len(pairs)}

    def notify(self, owner, title, body):
        # State keeps a durable copy even if full restore changes account IDs.
        owner = self.resolve(owner)
        if Teacher.objects.filter(pk=owner).exists():
            SystemNotification.objects.create(teacher_id=owner, notification_type='info', title=title, message=body,
                                              action_url='/teacher/backups/', action_label='Резервні копії')

    def notify_restored(self,title,body):
        for teacher in Teacher.objects.select_related('user').filter(user__is_superuser=True,user__is_active=True):
            state().notice(self.owner(teacher),title,body)
            self.notify(self.owner(teacher),title,body)
