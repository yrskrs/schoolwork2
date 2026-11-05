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


class AccessLinkForm(forms.ModelForm):
    expires_at = forms.DateTimeField(label='Діє до (час Києва)', required=False,
        input_formats=['%Y-%m-%dT%H:%M'], widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}))

    class Meta:
        model = AccessLink
        fields = ['name', 'kind', 'class_group', 'student', 'assignment', 'expires_at']
        labels = {'class_group': 'Клас', 'student': 'Учень', 'assignment': 'Завдання'}

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['class_group'].queryset = teacher.classes.filter(integration_active=True)
        self.fields['student'].queryset = Student.objects.filter(class_group__in=teacher.classes.all(), integration_active=True, class_group__integration_active=True)
        self.fields['assignment'].queryset = Assignment.objects.filter(teacher=teacher)
        for field in self.fields.values():
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
        if data.get('expires_at') and data['expires_at'] <= timezone.now():
            self.add_error('expires_at', 'Час завершення має бути в майбутньому.')
        return data
