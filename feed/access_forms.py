from urllib.parse import urlsplit
from django import forms
from django.utils import timezone
from .models import AccessLink, TailscaleConnection, Student, Assignment


class ConnectionForm(forms.ModelForm):
    class Meta:
        model = TailscaleConnection
        fields = ['enabled', 'public_url']
        labels = {'enabled': 'Підключити Tailscale', 'public_url': 'Публічна HTTPS-адреса сайту'}

    def clean_public_url(self):
        value = self.cleaned_data['public_url'].rstrip('/')
        if value:
            parts = urlsplit(value)
            try:
                port = parts.port or 443
            except ValueError:
                raise forms.ValidationError('Некоректний порт.')
            if (parts.scheme != 'https' or not parts.hostname or not parts.hostname.endswith('.ts.net')
                    or parts.username or parts.password or parts.path or parts.query or parts.fragment
                    or port not in (443, 8443, 10000)):
                raise forms.ValidationError('Укажіть адресу Funnel: https://назва.tail….ts.net з портом 443, 8443 або 10000, без шляху.')
        return value

    def clean(self):
        data = super().clean()
        if data.get('enabled') and not data.get('public_url'):
            self.add_error('public_url', 'Укажіть публічну адресу Funnel.')
        return data


class StudentSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if value and hasattr(value, 'instance') and value.instance:
            cg_id = getattr(value.instance, 'class_group_id', None)
            if cg_id:
                option['attrs']['data-class-id'] = str(cg_id)
        return option


class AssignmentSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if value and hasattr(value, 'instance') and value.instance:
            inst = value.instance
            class_ids = [str(c.pk) for c in inst.classes.all()]
            option['attrs']['data-class-ids'] = ','.join(class_ids)
        return option


class AssignmentChoiceField(forms.ModelChoiceField):
    widget = AssignmentSelect

    def label_from_instance(self, obj):
        classes = list(obj.classes.all())
        classes_str = ", ".join(c.name for c in classes) if classes else "Всі класи"

        # Localized and explicit date formatting with Europe/Kyiv timezone
        date_parts = []
        if obj.created_at:
            local_created = timezone.localtime(obj.created_at)
            date_parts.append(f"створено {local_created.strftime('%d.%m.%Y')}")
        if obj.due_date:
            date_parts.append(f"дедлайн {obj.due_date.strftime('%d.%m.%Y')}")
        elif obj.scheduled_at:
            local_sched = timezone.localtime(obj.scheduled_at)
            date_parts.append(f"публікація {local_sched.strftime('%d.%m.%Y')}")
        date_str = f" · {', '.join(date_parts)}" if date_parts else ""

        subject_str = f" · {obj.subject.name}" if obj.subject else ""

        status_names = {
            Assignment.STATUS_PUBLISHED: 'Опубліковано',
            Assignment.STATUS_DRAFT: 'Чернетка',
            Assignment.STATUS_SCHEDULED: 'Відкладено',
            Assignment.STATUS_ARCHIVED: 'Архів',
        }
        status_label = status_names.get(obj.status, obj.get_status_display() or 'Завдання')
        status_icon = {
            Assignment.STATUS_PUBLISHED: '✅',
            Assignment.STATUS_DRAFT: '📝',
            Assignment.STATUS_SCHEDULED: '⏰',
            Assignment.STATUS_ARCHIVED: '📦',
        }.get(obj.status, '📄')
        status_text = f"[{status_icon} {status_label}]"

        return f"{status_text} [{classes_str}] {obj.title}{subject_str}{date_str}"


class StudentChoiceField(forms.ModelChoiceField):
    widget = StudentSelect


class AccessLinkForm(forms.ModelForm):
    student = StudentChoiceField(queryset=Student.objects.none(), label='Учень', required=False)
    assignment = AssignmentChoiceField(queryset=Assignment.objects.none(), label='Завдання', required=False)
    expires_at = forms.DateTimeField(label='Діє до (час Києва)', required=False,
        input_formats=['%Y-%m-%dT%H:%M'], widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}))

    class Meta:
        model = AccessLink
        fields = ['name', 'kind', 'class_group', 'student', 'assignment', 'restrict_access', 'expires_at']
        labels = {
            'class_group': 'Клас',
            'student': 'Учень',
            'assignment': 'Завдання',
            'restrict_access': 'Обмежувати доступ лише вибраним (клас / учень / завдання)',
        }
        help_texts = {
            'restrict_access': 'Якщо прапорець встановлено, для учня відкриється лише вибраний клас, учень чи завдання, навіть коли загальний доступ до сайту відкритий. Для персонального посилання особистість учня фіксується завжди.',
        }

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['class_group'].queryset = teacher.classes.filter(integration_active=True).order_by('grade', 'letter', 'name')
        self.fields['student'].queryset = Student.objects.filter(
            class_group__in=teacher.classes.all(),
            integration_active=True,
            class_group__integration_active=True
        ).select_related('class_group').order_by('class_group__grade', 'class_group__letter', 'class_group__name', 'last_name', 'first_name')
        asgn_qs = Assignment.objects.filter(teacher=teacher)
        # Exclude archived tasks from link creation unless currently editing an archived link
        if not (self.instance and self.instance.assignment and self.instance.assignment.status == Assignment.STATUS_ARCHIVED):
            asgn_qs = asgn_qs.exclude(status=Assignment.STATUS_ARCHIVED)
        self.fields['assignment'].queryset = asgn_qs.prefetch_related('classes').select_related('subject').order_by('-created_at', '-pk')
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-checkbox'
            else:
                field.widget.attrs.setdefault('class', 'form-input')
        self.fields['expires_at'].help_text = 'Порожнє поле — до скасування.'

    def clean(self):
        data = super().clean()
        target = {'class': 'class_group', 'student': 'student', 'assignment': 'assignment'}.get(data.get('kind'))
        for key in ('class_group', 'student', 'assignment'):
            if key == target:
                if not data.get(key):
                    self.add_error(key, 'Оберіть значення для цього типу доступу.')
            else:
                data[key] = None
        if data.get('kind') == 'site':
            data['restrict_access'] = False
        if data.get('expires_at') and data['expires_at'] <= timezone.now():
            self.add_error('expires_at', 'Час завершення має бути в майбутньому.')
        return data


class PublicAccessModeForm(forms.ModelForm):
    class Meta:
        model = TailscaleConnection
        fields = ['allow_without_link']
        help_texts = {'allow_without_link': 'Учні зможуть відкривати звичайну адресу сайту без секретного посилання. Вхід учителя залишається обов’язковим.'}

    def clean_allow_without_link(self):
        value = self.cleaned_data['allow_without_link']
        if value and not self.instance.enabled:
            raise forms.ValidationError('Спочатку увімкніть підключення Tailscale в налаштуваннях.')
        return value
