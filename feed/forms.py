"""
Форми Django для SchoolNet.

Включає:
    - AssignmentForm      — створення та редагування завдань
    - TeacherLoginForm    — вхід вчителя
    - ClassSelectForm     — вибір класу для учня
    - TeacherProfileForm  — редагування профілю вчителя
"""

from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from .models import Assignment, Teacher, ClassGroup, Subject, Student


# ─── Кастомний widget для множинного завантаження файлів (Django 6+) ───────────
class MultipleFileInput(forms.FileInput):
    """HTML5 FileInput з атрибутом multiple для вибору кількох файлів."""
    allow_multiple_selected = True

    def __init__(self, attrs=None):
        default_attrs = {'multiple': 'multiple'}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)

    def value_from_datadict(self, data, files, name):
        """Повертає список завантажених файлів."""
        if hasattr(files, 'getlist'):
            return files.getlist(name)
        return files.get(name)


class MultipleFileField(forms.FileField):
    """Поле форми що приймає кілька файлів одночасно."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={
            'class': 'form-file-input',
            'accept': '*/*',
            'id': 'id_files',
        }))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        # Якщо порожньо — повертаємо пустий список
        if not data:
            return []
        if not isinstance(data, list):
            data = [data]
        result = []
        for item in data:
            result.append(super().clean(item, initial))
        return result


# ─── Форма авторизації вчителя ─────────────────────────────────────────────────

class TeacherLoginForm(forms.Form):
    """Форма входу вчителя (username + password)."""

    username = forms.CharField(
        label='Логін',
        max_length=150,
        widget=forms.TextInput(attrs={
            'placeholder': 'Введіть логін',
            'autocomplete': 'username',
            'class': 'form-input',
        })
    )
    password = forms.CharField(
        label='Пароль',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Введіть пароль',
            'autocomplete': 'current-password',
            'class': 'form-input',
        })
    )


# ─── Форма вибору класу учнем ─────────────────────────────────────────────────
class ClassSelectForm(forms.Form):
    """Форма вибору класу учнем при вході до стрічки."""

    class_group = forms.ModelChoiceField(
        queryset=ClassGroup.objects.all().order_by('grade', 'letter'),
        label='Ваш клас',
        empty_label='— Всі класи —',
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )


# ─── Форма профілю вчителя ────────────────────────────────────────────────────
class TeacherProfileForm(forms.ModelForm):
    """Форма редагування профілю вчителя."""

    class Meta:
        model = Teacher
        fields = ['full_name', 'avatar_color', 'avatar_image']
        labels = {
            'full_name': 'ПІБ вчителя',
            'avatar_color': 'Колір фону аватара',
            'avatar_image': 'Фото аватара',
        }
        widgets = {
            'full_name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Шевченко Тарас Григорович',
            }),
            'avatar_color': forms.TextInput(attrs={
                'type': 'color',
                'class': 'form-color-input',
            }),
            'avatar_image': forms.FileInput(attrs={
                'class': 'form-input',
                'accept': 'image/*',
                'id': 'avatar-file-input',
            }),
        }


# ─── Форми створення Предметів та Класів ────────────────────────────────────────
class SubjectForm(forms.ModelForm):
    """Форма створення новий предмету."""

    class Meta:
        model = Subject
        fields = ['name', 'icon', 'color']
        labels = {
            'name': 'Назва предмету',
            'icon': 'Іконка (емодзі)',
            'color': 'Колір (HEX)',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Наприклад: Астрономія'}),
            'icon': forms.TextInput(attrs={'class': 'form-input', 'placeholder': '🌌'}),
            'color': forms.TextInput(attrs={'type': 'color', 'class': 'form-color-input'}),
        }


class ClassGroupForm(forms.ModelForm):
    """Форма створення нового класу."""

    class Meta:
        model = ClassGroup
        fields = ['name', 'grade', 'letter']
        labels = {
            'name': 'Назва класу',
            'grade': 'Паралель (номер)',
            'letter': 'Літера',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Наприклад: 9А'}),
            'grade': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '9'}),
            'letter': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'А'}),
        }


# ─── Форми супер-адміністратора ───────────────────────────────────────────────
class TeacherCreateForm(forms.Form):
    """Форма створення нового облікового запису вчителя (для Супер-Адміна)."""

    username = forms.CharField(
        label='Логін вчителя',
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'teacher_ivanov'})
    )
    password = forms.CharField(
        label='Початковий пароль',
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': '••••••••'})
    )
    full_name = forms.CharField(
        label='ПІБ вчителя',
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Іванов Іван Іванович'})
    )
    is_superuser = forms.BooleanField(
        label='Надати права Супер-Адміністратора',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'})
    )

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if User.objects.filter(username=username).exists():
            raise ValidationError('Користувач з таким логіном вже існує.')
        return username


class PasswordResetForm(forms.Form):
    """Форма скидання пароля вчителя."""

    user_id = forms.IntegerField(widget=forms.HiddenInput())
    new_password = forms.CharField(
        label='Новий пароль',
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Введіть новий пароль'})
    )




# ─── Форма завдання ────────────────────────────────────────────────────────────
class AssignmentForm(forms.ModelForm):
    """
    Форма створення та редагування завдання.

    Валідація: обов'язкова наявність хоча б одного з:
        - опис (description)
        - файл (файли додаються через AssignmentFileFormSet)
        - посилання (link_url)
    """

    # Поле для вибору типу публікації (замінює поле status напряму)
    PUBLISH_CHOICE_NOW = 'now'
    PUBLISH_CHOICE_SCHEDULED = 'scheduled'
    PUBLISH_CHOICE_DRAFT = 'draft'

    PUBLISH_CHOICES = [
        (PUBLISH_CHOICE_NOW, '✅ Опублікувати зараз'),
        (PUBLISH_CHOICE_SCHEDULED, '⏰ Відкласти публікацію'),
        (PUBLISH_CHOICE_DRAFT, '📝 Зберегти як чернетку'),
    ]

    publish_choice = forms.ChoiceField(
        choices=PUBLISH_CHOICES,
        initial=PUBLISH_CHOICE_NOW,
        label='Публікація',
        widget=forms.RadioSelect(attrs={'class': 'publish-radio'}),
    )

    scheduled_at = forms.DateTimeField(
        required=False,
        label='Дата та час публікації',
        input_formats=['%Y-%m-%dT%H:%M', '%d.%m.%Y %H:%M'],
        widget=forms.DateTimeInput(
            attrs={
                'type': 'datetime-local',
                'class': 'form-input',
            },
            format='%Y-%m-%dT%H:%M'
        )
    )

    due_date = forms.DateField(
        required=False,
        label='Термін виконання',
        input_formats=['%Y-%m-%d', '%d.%m.%Y'],
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'form-input',
            },
            format='%Y-%m-%d'
        )
    )

    # Файли завантажуються через MultipleFileField (підтримує кілька файлів)
    files = MultipleFileField(
        required=False,
        label='Прикріпити файли',
    )


    class Meta:
        model = Assignment
        fields = [
            'subject', 'title', 'description',
            'classes', 'is_individual', 'student_name',
            'link_url', 'link_label', 'youtube_url',
            'due_date', 'scheduled_at',
            'allow_student_ai_check', 'allow_ai_usage',
        ]
        labels = {
            'subject': 'Предмет',
            'title': 'Тема / Заголовок',
            'description': 'Опис завдання',
            'classes': 'Призначити класам',
            'is_individual': 'Індивідуальне завдання (для конкретного учня / учениці)',
            'student_name': "Прізвище та ім'я учня / учениці",
            'link_url': 'Посилання (URL)',
            'link_label': 'Текст посилання',
            'youtube_url': 'Посилання на YouTube',
            'allow_student_ai_check': 'Дозволити учням 1 самоперевірку через ШІ',
            'allow_ai_usage': 'Дозволити учням використання ШІ при виконанні завдання',
        }
        widgets = {
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Наприклад: Параграф 12, вправа 5',
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-textarea',
                'rows': 5,
                'placeholder': 'Детальний опис завдання, умова задачі...',
            }),
            'classes': forms.CheckboxSelectMultiple(attrs={'class': 'class-checkbox-group'}),
            'is_individual': forms.CheckboxInput(attrs={
                'class': 'form-checkbox',
                'id': 'id_is_individual',
            }),
            'student_name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': "Прізвище та ім'я учня або учениці",
            }),
            'link_url': forms.URLInput(attrs={
                'class': 'form-input',
                'placeholder': 'https://...',
            }),
            'link_label': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Відкрити підручник, Переглянути відео...',
            }),
            'youtube_url': forms.URLInput(attrs={
                'class': 'form-input',
                'placeholder': 'https://www.youtube.com/watch?v=... або https://youtu.be/...',
            }),
            'allow_student_ai_check': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'allow_ai_usage': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def __init__(self, teacher=None, *args, **kwargs):
        """Ініціалізація: відображає класи та предмети даного вчителя (або всі, якщо не вказані)."""
        super().__init__(*args, **kwargs)

        if teacher:
            if teacher.subjects.exists():
                self.fields['subject'].queryset = teacher.subjects.all()
            else:
                self.fields['subject'].queryset = Subject.objects.all()

            if teacher.classes.exists():
                self.fields['classes'].queryset = teacher.classes.all().order_by('grade', 'letter')
            else:
                self.fields['classes'].queryset = ClassGroup.objects.all().order_by('grade', 'letter')

            default_subject = teacher.get_default_subject()
            if default_subject and not self.initial.get('subject'):
                self.initial['subject'] = default_subject.pk
        else:
            self.fields['subject'].queryset = Subject.objects.all()
            self.fields['classes'].queryset = ClassGroup.objects.all().order_by('grade', 'letter')



        # Заповнення publish_choice з існуючого об'єкта (при редагуванні)
        if self.instance and self.instance.pk:
            if self.instance.status == Assignment.STATUS_DRAFT:
                self.initial['publish_choice'] = self.PUBLISH_CHOICE_DRAFT
            elif self.instance.status == Assignment.STATUS_SCHEDULED:
                self.initial['publish_choice'] = self.PUBLISH_CHOICE_SCHEDULED
            else:
                self.initial['publish_choice'] = self.PUBLISH_CHOICE_NOW

    def clean(self):
        """Валідація: перевіряємо наявність хоча б одного вмісту."""
        cleaned_data = super().clean()
        description = cleaned_data.get('description', '').strip()
        link_url = cleaned_data.get('link_url', '').strip()
        files_uploaded = cleaned_data.get('files', [])

        # Перевіряємо що є хоча б щось
        if not description and not link_url and not files_uploaded:

            raise ValidationError(
                'Додайте хоча б одне з: опис завдання, посилання або файл.'
            )

        # Валідація відкладеної публікації
        publish_choice = cleaned_data.get('publish_choice')
        scheduled_at = cleaned_data.get('scheduled_at')

        if publish_choice == self.PUBLISH_CHOICE_SCHEDULED and not scheduled_at:
            self.add_error(
                'scheduled_at',
                'Вкажіть дату та час відкладеної публікації.'
            )

        # Валідація адресації (Кому призначено: обов'язково клас або індивідуально)
        is_individual = cleaned_data.get('is_individual')
        student_name = cleaned_data.get('student_name', '').strip()
        classes = cleaned_data.get('classes')

        if is_individual:
            if not student_name:
                self.add_error(
                    'student_name',
                    "Обов'язково вкажіть прізвище та ім'я учня або учениці для індивідуального завдання."
                )
        else:
            if not classes or classes.count() == 0:
                self.add_error(
                    'classes',
                    'Обовʼязково оберіть хоча б один клас або позначте як індивідуальне завдання.'
                )

        return cleaned_data

    def save_with_status(self, teacher, commit=True):
        """Зберігає завдання з правильним статусом на основі publish_choice."""
        instance = super().save(commit=False)
        instance.teacher = teacher

        publish_choice = self.cleaned_data.get('publish_choice')

        if publish_choice == self.PUBLISH_CHOICE_DRAFT:
            instance.status = Assignment.STATUS_DRAFT
        elif publish_choice == self.PUBLISH_CHOICE_SCHEDULED:
            instance.status = Assignment.STATUS_SCHEDULED
            instance.scheduled_at = self.cleaned_data.get('scheduled_at')
        else:
            instance.status = Assignment.STATUS_PUBLISHED

        if commit:
            instance.save()
            self.save_m2m()

        return instance


# ─── Форма здачі роботи учнем ──────────────────────────────────────────────────

class SubmissionForm(forms.Form):
    """Форма здачі роботи учнем по конкретному завданню."""

    full_name = forms.CharField(
        label="Прізвище та ім'я",
        max_length=200,
        widget=forms.TextInput(attrs={
            'placeholder': "Наприклад: Іванченко Тарас",
            'class': 'form-input',
            'autocomplete': 'name',
            'id': 'id_full_name',
        })
    )
    class_group = forms.ModelChoiceField(
        label='Клас',
        queryset=ClassGroup.objects.none(),
        empty_label='— Оберіть клас —',
        widget=forms.Select(attrs={
            'class': 'form-input',
            'id': 'id_class_group',
        })
    )
    files = MultipleFileField(
        label='Файли роботи (можна прикріпити декілька)',
        required=False,
        widget=MultipleFileInput(attrs={
            'class': 'form-file-input',
            'accept': '*/*',
            'id': 'id_files',
        })
    )
    file = forms.FileField(
        label='Файл роботи',
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'form-file-input',
            'accept': '*/*',
            'id': 'id_file',
        })
    )
    link = forms.URLField(
        label='Або посилання на роботу',
        required=False,
        widget=forms.URLInput(attrs={
            'placeholder': 'https://...',
            'class': 'form-input',
            'id': 'id_link',
        })
    )
    comment_student = forms.CharField(
        label='Коментар (необов\'язково)',
        required=False,
        widget=forms.Textarea(attrs={
            'placeholder': 'Додаткова інформація до здачі...',
            'class': 'form-input',
            'rows': 3,
            'id': 'id_comment_student',
        })
    )

    def __init__(self, *args, assignment=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.assignment = assignment
        if assignment:
            # Якщо завдання індивідуальне, автоматично підставляємо прізвище та ім'я учня
            if assignment.is_individual and assignment.student_name and not self.initial.get('full_name'):
                self.fields['full_name'].initial = assignment.student_name

            # Показуємо тільки класи, яким призначено це завдання (або всі)
            assigned_classes = assignment.classes.all().order_by('grade', 'letter')
            if assigned_classes.exists():
                self.fields['class_group'].queryset = assigned_classes
                first_class = assigned_classes.first()
                if not self.initial.get('class_group'):
                    self.fields['class_group'].initial = first_class
                self.fields['class_group'].empty_label = None
            else:
                self.fields['class_group'].queryset = ClassGroup.objects.all().order_by('grade', 'letter')
        else:
            self.fields['class_group'].queryset = ClassGroup.objects.all().order_by('grade', 'letter')

    def clean(self):
        cleaned_data = super().clean()
        files = cleaned_data.get('files') or []
        file = cleaned_data.get('file')
        link = cleaned_data.get('link')

        raw_list = list(files) if isinstance(files, list) else ([files] if files else [])
        if file and file not in raw_list:
            raw_list.append(file)

        # Дедуплікація файлів за назвою та розміром (щоб уникнути подвійного завантаження)
        seen_keys = set()
        all_uploaded = []
        for f in raw_list:
            if not f:
                continue
            key = (getattr(f, 'name', None), getattr(f, 'size', None))
            if key not in seen_keys:
                seen_keys.add(key)
                all_uploaded.append(f)

        cleaned_data['all_files'] = all_uploaded

        if not all_uploaded and not link:
            raise forms.ValidationError(
                'Будь ласка, завантажте файл(и) роботи або вкажіть посилання.'
            )
        return cleaned_data

    def save(self, assignment):
        """Зберігає здачу роботи з кількома файлами, інтелектуальною нормалізацією та прив'язкою до учня."""
        from .models import Submission, SubmissionFile, Student
        from .student_matcher import resolve_canonical_student_name, is_same_student_identity
        from .utils import optimize_uploaded_file

        class_grp = self.cleaned_data['class_group']
        last_name, first_name = resolve_canonical_student_name(
            self.cleaned_data['full_name'],
            class_group=class_grp
        )

        all_files = self.cleaned_data.get('all_files') or []

        # Знаходимо або створюємо запис Student для учня
        student_obj = None
        for s in Student.objects.filter(class_group=class_grp):
            if is_same_student_identity(last_name, first_name, s.last_name, s.first_name):
                student_obj = s
                break
        if not student_obj and (last_name or first_name):
            student_obj = Student.objects.create(
                last_name=last_name or "Учень",
                first_name=first_name,
                class_group=class_grp
            )

        # Визначаємо, чи учень здає роботу повторно (перездача / робота над помилками)
        from django.db.models import Q
        q_prev = Q(assignment=assignment, class_group=class_grp)
        if student_obj:
            q_prev &= (Q(student=student_obj) | (Q(last_name__iexact=last_name) & Q(first_name__iexact=first_name)))
        else:
            q_prev &= (Q(last_name__iexact=last_name) & Q(first_name__iexact=first_name))

        prev_submissions = list(Submission.objects.filter(q_prev).order_by('-submitted_at'))
        is_resub = len(prev_submissions) > 0
        attempt_num = len(prev_submissions) + 1
        latest_prev = prev_submissions[0] if prev_submissions else None

        submission = Submission(
            assignment=assignment,
            student=student_obj,
            last_name=last_name,
            first_name=first_name,
            class_group=class_grp,
            file=None,  # Прив'язуємо нижче без дублювання файлу на диску
            link=self.cleaned_data.get('link') or None,
            comment_student=self.cleaned_data.get('comment_student') or None,
            is_resubmission=is_resub,
            resubmission_attempt=attempt_num,
            previous_submission=latest_prev,
            is_latest_attempt=True,
        )
        submission.save()

        # Оновлюємо попередні спроби учня: вони позначаються як не останні
        if prev_submissions:
            Submission.objects.filter(id__in=[p.id for p in prev_submissions]).update(is_latest_attempt=False)

        # Зберігаємо всі завантажені файли як окремі SubmissionFile об'єкти (рівно по 1 разу)
        saved_sub_files = []
        for f in all_files:
            try:
                if hasattr(f, 'seek'):
                    f.seek(0)
            except Exception:
                pass
            orig_name = getattr(f, 'name', '') or 'file'
            opt_f = optimize_uploaded_file(f)
            try:
                if hasattr(opt_f, 'seek'):
                    opt_f.seek(0)
            except Exception:
                pass
            sub_file = SubmissionFile.objects.create(
                submission=submission,
                file=opt_f,
                original_name=orig_name
            )
            saved_sub_files.append(sub_file)

        # Встановлюємо шлях до першого файлу для зворотної сумісності без збереження копії на диску
        if saved_sub_files and saved_sub_files[0].file:
            submission.file.name = saved_sub_files[0].file.name
            submission.save(update_fields=['file'])

        return submission


# ─── Форма створення / редагування учня ───────────────────────────────────────
class StudentForm(forms.ModelForm):
    """Форма додавання та редагування профілю учня вчителем."""

    sync_submissions = forms.BooleanField(
        label="Синхронізувати попередні здані роботи учня",
        required=False,
        initial=True,
        help_text="Оновити ім'я та клас у всіх раніше зданих роботах цього учня"
    )

    class Meta:
        model = Student
        fields = ['last_name', 'first_name', 'class_group', 'notes']
        widgets = {
            'last_name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Наприклад: Шевченко',
                'id': 'id_student_last_name',
                'required': True,
            }),
            'first_name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Наприклад: Тарас',
                'id': 'id_student_first_name',
                'required': True,
            }),
            'class_group': forms.Select(attrs={
                'class': 'form-input',
                'id': 'id_student_class_group',
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-input',
                'rows': 2,
                'placeholder': 'Нотатки про учня (необовʼязково)...',
                'id': 'id_student_notes',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['class_group'].queryset = ClassGroup.objects.all().order_by('grade', 'letter')


# ─── Форма імпорту списку учнів та класів ────────────────────────────────────
class StudentImportForm(forms.Form):
    """Форма імпорту списку учнів через завантаження файлу або вставку тексту."""

    IMPORT_MODES = [
        ('text', '📝 Вставка тексту зі списком'),
        ('file', '📁 Завантаження файлу (CSV / TXT / TSV)'),
    ]

    import_mode = forms.ChoiceField(
        choices=IMPORT_MODES,
        initial='text',
        widget=forms.RadioSelect(attrs={'class': 'import-mode-radio'})
    )
    raw_text = forms.CharField(
        label="Список учнів",
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-input code-font',
            'rows': 8,
            'placeholder': "Вставте список учнів. Приклади:\nІваненко Тарас, 9-А\nПетренко Олена, 9-А\nКоваленко Дмитро, 10-Б\n\nАбо оберіть клас нижче і вставте лише імена:\nШевченко Тарас\nЛеся Українка",
            'id': 'id_import_raw_text'
        })
    )
    file = forms.FileField(
        label="Файл зі списком (.csv, .txt, .tsv)",
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'form-file-input',
            'accept': '.csv,.txt,.tsv',
            'id': 'id_import_file'
        })
    )
    default_class = forms.ModelChoiceField(
        queryset=ClassGroup.objects.all().order_by('grade', 'letter'),
        label="Клас за замовчуванням (якщо клас не вказано в рядку)",
        required=False,
        empty_label="— Визначити автоматично з рядка —",
        widget=forms.Select(attrs={'class': 'form-input', 'id': 'id_import_default_class'})
    )
    create_missing_classes = forms.BooleanField(
        label="Автоматично створювати класи, якщо їх ще немає в системі",
        initial=True,
        required=False,
        widget=forms.CheckboxInput(attrs={'id': 'id_create_missing_classes'})
    )



