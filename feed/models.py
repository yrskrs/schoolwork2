"""
Моделі Django для шкільної мікро-соціальної мережі SchoolNet.

Структура:
    - Subject      — навчальний предмет
    - ClassGroup   — клас (наприклад: 9А, 10Б)
    - Teacher      — профіль вчителя (пов'язаний з Django User)
    - Assignment   — навчальне завдання з файлами, статусами, фільтрами
    - AssignmentFile — прикріплені файли до завдання
"""

import os
import re
import math
import json
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone



# ─── Предмет ──────────────────────────────────────────────────────────────────
class Subject(models.Model):
    """Навчальний предмет (Математика, Фізика тощо)."""
    name = models.CharField('Назва предмету', max_length=100, unique=True)
    icon = models.CharField(
        'Емодзі-іконка',
        max_length=10,
        default='📚',
        help_text='Емодзі для відображення поруч із назвою'
    )
    color = models.CharField(
        'Колір (HEX)',
        max_length=7,
        default='#6366f1',
        help_text='Колір мітки предмету, наприклад #6366f1'
    )
    created_by = models.ForeignKey(
        'Teacher',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_subjects',
        verbose_name='Створено вчителем'
    )

    class Meta:
        verbose_name = 'Предмет'
        verbose_name_plural = 'Предмети'
        ordering = ['name']

    def __str__(self):
        return f"{self.icon} {self.name}"


# ─── Клас ─────────────────────────────────────────────────────────────────────
class ClassGroup(models.Model):
    """Клас або група учнів (наприклад: 9А, 10Б, 11В)."""
    name = models.CharField('Назва класу', max_length=20, unique=True)
    grade = models.PositiveSmallIntegerField('Паралель (цифра)', default=9)
    letter = models.CharField('Літера класу', max_length=3, default='А')
    created_by = models.ForeignKey(
        'Teacher',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_classes',
        verbose_name='Створено вчителем'
    )

    class Meta:
        verbose_name = 'Клас'
        verbose_name_plural = 'Класи'
        ordering = ['grade', 'letter']

    def __str__(self):
        return self.name



# ─── Учень ────────────────────────────────────────────────────────────────────
class Student(models.Model):
    """Профіль учня школи."""
    first_name = models.CharField("Ім'я", max_length=100)
    last_name = models.CharField("Прізвище", max_length=100)
    class_group = models.ForeignKey(
        ClassGroup,
        on_delete=models.CASCADE,
        related_name='students',
        verbose_name='Клас'
    )
    notes = models.TextField('Нотатки вчителя', blank=True, default='')
    created_at = models.DateTimeField('Створено', auto_now_add=True)
    updated_at = models.DateTimeField('Оновлено', auto_now=True)

    class Meta:
        verbose_name = 'Учень'
        verbose_name_plural = 'Учні'
        ordering = ['class_group__grade', 'class_group__letter', 'last_name', 'first_name']
        unique_together = [('last_name', 'first_name', 'class_group')]

    def __str__(self):
        c_name = self.class_group.name if self.class_group else '—'
        return f"{self.last_name} {self.first_name} ({c_name})"

    def get_full_name(self):
        return f"{self.last_name} {self.first_name}".strip()

    def get_initials(self):
        ln = (self.last_name or '').strip()
        fn = (self.first_name or '').strip()
        if ln and fn:
            return f"{ln[0]}{fn[0]}".upper()
        if ln:
            return ln[:2].upper()
        if fn:
            return fn[:2].upper()
        return "У"

    def get_submissions_count(self):
        """Рахує кількість зданих робіт цього учня."""
        from .student_matcher import is_same_student_identity
        if not self.class_group_id:
            return 0
        subs = Submission.objects.filter(class_group_id=self.class_group_id)
        count = 0
        for s in subs:
            if is_same_student_identity(self.last_name, self.first_name, s.last_name, s.first_name):
                count += 1
        return count

    def sync_submissions(self, old_last_name=None, old_first_name=None, old_class_group=None):
        """
        Синхронізує здані роботи при зміні імені, прізвища або класу учня.
        """
        from .student_matcher import is_same_student_identity
        
        search_last = old_last_name or self.last_name
        search_first = old_first_name or self.first_name
        search_class = old_class_group or self.class_group

        if not search_class:
            return 0

        qs = Submission.objects.filter(class_group=search_class)
        matching_ids = []
        for sub in qs:
            if is_same_student_identity(search_last, search_first, sub.last_name, sub.first_name):
                matching_ids.append(sub.id)

        updated_count = 0
        if matching_ids:
            updated_count = Submission.objects.filter(id__in=matching_ids).update(
                last_name=self.last_name,
                first_name=self.first_name,
                class_group=self.class_group,
                student=self
            )
        return updated_count


# ─── Профіль вчителя ──────────────────────────────────────────────────────────
class Teacher(models.Model):
    """Профіль вчителя, прив'язаний до облікового запису Django."""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='teacher_profile',
        verbose_name='Обліковий запис'
    )
    full_name = models.CharField('ПІБ вчителя', max_length=200)
    subjects = models.ManyToManyField(
        Subject,
        verbose_name='Предмети',
        blank=True
    )
    classes = models.ManyToManyField(
        ClassGroup,
        verbose_name='Класи',
        blank=True
    )
    avatar_color = models.CharField(
        'Колір аватара',
        max_length=7,
        default='#6366f1'
    )
    avatar_image = models.ImageField(
        'Фото аватара',
        upload_to='teacher_avatars/',
        null=True,
        blank=True,
        help_text='Завантажте квадратне фото або зображення для аватара'
    )

    class Meta:
        verbose_name = 'Вчитель'
        verbose_name_plural = 'Вчителі'

    def __str__(self):
        return self.full_name

    def get_initials(self):
        """Повертає ініціали для відображення в аватарі."""
        parts = self.full_name.split()
        if len(parts) >= 2:
            return f"{parts[0][0]}{parts[1][0]}".upper()
        return self.full_name[:2].upper()

    def get_default_subject(self):
        """Повертає предмет, якщо він один — для автопідстановки."""
        subjects = self.subjects.all()
        if subjects.count() == 1:
            return subjects.first()
        return None


# ─── Завдання ─────────────────────────────────────────────────────────────────
class Assignment(models.Model):
    """
    Навчальне завдання з підтримкою:
    - кількох статусів (чернетка / відкладена публікація / опубліковано)
    - індивідуальних завдань для конкретного учня
    - прикріплення файлів та посилань
    """

    # Статуси завдання
    STATUS_PUBLISHED = 'published'
    STATUS_DRAFT = 'draft'
    STATUS_SCHEDULED = 'scheduled'
    STATUS_ARCHIVED = 'archived'

    STATUS_CHOICES = [
        (STATUS_PUBLISHED, '✅ Опубліковано'),
        (STATUS_DRAFT, '📝 Чернетка'),
        (STATUS_SCHEDULED, '⏰ Відкладена публікація'),
        (STATUS_ARCHIVED, '📦 Архів'),
    ]

    # ── Основні поля ─────────────────────────────────────────────────────────
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name='assignments',
        verbose_name='Вчитель'
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Предмет'
    )
    title = models.CharField('Тема завдання', max_length=300)
    description = models.TextField('Опис / умова завдання', blank=True)

    # ── Адресація (клас або індивідуально) ──────────────────────────────────
    classes = models.ManyToManyField(
        ClassGroup,
        blank=True,
        verbose_name='Призначено класам'
    )
    is_individual = models.BooleanField(
        'Індивідуальне завдання',
        default=False,
        help_text='Якщо True — завдання для конкретного учня'
    )
    student_name = models.CharField(
        "Ім'я учня",
        max_length=200,
        blank=True,
        help_text="Повне ім'я учня для індивідуального завдання"
    )

    # ── Посилання (опціонально) ───────────────────────────────────────────────
    link_url = models.URLField(
        'Посилання',
        blank=True,
        help_text='Зовнішнє або внутрішнє посилання на матеріал'
    )
    link_label = models.CharField(
        'Підпис посилання',
        max_length=200,
        blank=True,
        help_text='Текст кнопки/посилання (якщо порожньо — URL)'
    )
    youtube_url = models.URLField(
        'Посилання на YouTube відео',
        blank=True,
        help_text='Відео буде вбудовано безпосередньо на сторінці завдання'
    )

    # ── Статус та публікація ───────────────────────────────────────────────────
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT
    )
    scheduled_at = models.DateTimeField(
        'Час відкладеної публікації',
        null=True,
        blank=True
    )
    due_date = models.DateField(
        'Термін виконання',
        null=True,
        blank=True
    )

    # ── Метадані ──────────────────────────────────────────────────────────────
    created_at = models.DateTimeField('Створено', auto_now_add=True)
    updated_at = models.DateTimeField('Оновлено', auto_now=True)
    published_at = models.DateTimeField('Опубліковано', null=True, blank=True)
    unarchived_at = models.DateTimeField('Час розархівації', null=True, blank=True)
    views_count = models.PositiveIntegerField('Кількість переглядів', default=0)

    # ── Зв'язок з оригіналом (для дублювання) ────────────────────────────────
    duplicated_from = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='duplicates',
        verbose_name='Дубліковано з'
    )

    # ── Дефолтні критерії ШІ для цього завдання ─────────────────────────────
    default_ai_preset = models.ForeignKey(
        'AICriteriaPreset',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='default_for_assignments',
        verbose_name='Шаблон оцінювання за замовчуванням'
    )
    default_ai_grs = models.TextField(
        'Активні ГР за замовчуванням (JSON)',
        blank=True,
        default='',
        help_text='JSON-список code ГР, наприклад ["ГР 1","ГР 2"]'
    )

    # ── Самоперевірка учнем (Student AI Self-Check) ───────────────────────────
    allow_student_ai_check = models.BooleanField(
        'Дозволити учням самоперевірку ШІ',
        default=False,
        help_text='Якщо True — учень може 1 раз перевірити здану роботу через ШІ'
    )
    allow_ai_usage = models.BooleanField(
        'Дозволити використання ШІ учнями',
        default=False,
        help_text='Якщо увімкнено, учням дозволено використовувати генеративний ШІ для виконання завдання'
    )

    class Meta:
        verbose_name = 'Завдання'
        verbose_name_plural = 'Завдання'
        ordering = ['-published_at', '-created_at']

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"

    def is_visible(self):
        """Чи видиме завдання для учнів (опубліковане або час настав)."""
        if self.status == self.STATUS_PUBLISHED:
            return True
        if self.status == self.STATUS_SCHEDULED and self.scheduled_at:
            return timezone.now() >= self.scheduled_at
        return False

    @property
    def youtube_video_id(self):
        """Витягує чистий 11-значний ID відео YouTube з будь-якого формату посилання."""
        if not self.youtube_url:
            return ""
        import re
        from urllib.parse import urlparse, parse_qs
        clean_url = str(self.youtube_url).strip()
        patterns = [
            r'(?:youtu\.be\/|youtube\.com\/(?:embed\/|v\/|shorts\/|live\/|watch\?(?:.*&)?v=))([a-zA-Z0-9_-]{11})',
            r'youtube\.com\/[^\/]+\/.*[?&]v=([a-zA-Z0-9_-]{11})',
            r'[\?&]v=([a-zA-Z0-9_-]{11})',
            r'youtu\.be\/([a-zA-Z0-9_-]{11})',
        ]
        for p in patterns:
            m = re.search(p, clean_url)
            if m:
                return m.group(1)
        try:
            parsed = urlparse(clean_url)
            if 'youtube.com' in parsed.netloc:
                qs = parse_qs(parsed.query)
                if 'v' in qs and qs['v'] and len(qs['v'][0]) == 11:
                    return qs['v'][0]
            elif 'youtu.be' in parsed.netloc:
                path = parsed.path.strip('/')
                if len(path) == 11:
                    return path
        except Exception:
            pass
        return ""

    @property
    def youtube_embed_url(self):
        """Конвертує посилання на YouTube у стандартний формат для вбудовування."""
        vid = self.youtube_video_id
        if vid:
            return f"https://www.youtube.com/embed/{vid}?rel=0"
        return ""

    @property
    def youtube_watch_url(self):
        """Пряме посилання на YouTube для відкриття у новій вкладці."""
        vid = self.youtube_video_id
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
        return self.youtube_url or ""


    @property
    def all_youtube_videos(self):
        """Повертає список усіх відео YouTube до завдання (основне + додаткові)."""
        videos = []
        if self.youtube_url and self.youtube_embed_url:
            videos.append({
                'title': 'YouTube відео',
                'embed_url': self.youtube_embed_url,
                'watch_url': self.youtube_watch_url or self.youtube_url,
            })
        for ylink in self.youtube_links.all():
            if ylink.embed_url:
                videos.append({
                    'title': ylink.title or f"Відео {len(videos) + 1}",
                    'embed_url': ylink.embed_url,
                    'watch_url': ylink.watch_url or ylink.url,
                })
        return videos

    @property
    def days_ago(self):
        """Кількість днів з моменту публікації завдання."""
        if not self.published_at:
            return 0
        from django.utils import timezone
        pub_date = timezone.localtime(self.published_at).date()
        today = timezone.localtime(timezone.now()).date()
        delta = (today - pub_date).days
        return max(0, delta)

    @property
    def age_card_class(self):
        """CSS-клас візуального затемнення / старіння картки завдання."""
        days = self.days_ago
        if days == 0:
            return "card-age-today"
        elif days == 1:
            return "card-age-yesterday"
        elif days == 2:
            return "card-age-2days"
        elif days == 3:
            return "card-age-3days"
        else:
            return "card-age-older"

    @property
    def relative_published_display(self):
        """
        Форматування дати публікації відповідно до вимог:
        - Сьогодні -> "Сьогодні, 21.08"
        - Вчора -> "Вчора, 20.08"
        - Раніше -> День тижня + дата, наприклад "Понеділок, 17.08"
        """
        if not self.published_at:
            return ""
        from django.utils import timezone
        local_dt = timezone.localtime(self.published_at)
        pub_date = local_dt.date()
        today = timezone.localtime(timezone.now()).date()
        delta = (today - pub_date).days

        day_month = local_dt.strftime('%d.%m')

        uk_weekdays = {
            0: 'Понеділок',
            1: 'Вівторок',
            2: 'Середа',
            3: 'Четвер',
            4: 'Пʼятниця',
            5: 'Субота',
            6: 'Неділя',
        }

        if delta == 0:
            return f"Сьогодні, {day_month}"
        elif delta == 1:
            return f"Вчора, {day_month}"
        else:
            weekday = uk_weekdays.get(pub_date.weekday(), '')
            return f"{weekday}, {day_month}"

    @property
    def is_published_today(self):
        """Чи опубліковано сьогодні."""
        return self.days_ago == 0

    @property
    def is_published_yesterday(self):
        """Чи опубліковано вчора."""
        return self.days_ago == 1

    @property
    def unarchived_display(self):
        """Форматований підпис розархівації."""
        if not self.unarchived_at:
            return ""
        from django.utils import timezone
        local_dt = timezone.localtime(self.unarchived_at)
        return f"Розархівовано {local_dt.strftime('%d.%m')}"

    @property
    def student_display(self):
        """
        Генерує коректне та ввічливе звернення з урахуванням роду:
        - "Для учениці: Марія Коваль"
        - "Для учня: Тарас Шевченко"
        - "Для учня / учениці: ..." (якщо не визначено однозначно)
        """
        if not self.student_name:
            return ""

        name = self.student_name.strip()
        parts = [p.lower().strip(',.!') for p in name.split()]

        female_names = {
            'анна', 'ганна', 'марія', 'марічка', 'софія', 'дарина', 'дарʼя', 'дарья', 'дарія', 'даша',
            'єва', 'вікторія', 'віка', 'поліна', 'анастасія', 'настя', 'мілана', 'соломія', 'соломійка',
            'вероніка', 'злата', 'ярина', 'олена', 'катерина', 'катя', 'ірина', 'іра', 'оксана',
            'тетяна', 'таня', 'юлія', 'юля', 'наталія', 'наталя', 'наташа', 'ольга', 'оля', 'світлана',
            'надія', 'любов', 'діана', 'аліна', 'христина', 'марʼяна', 'маряна', 'інна', 'владислава',
            'валерія', 'олександра', 'саша', 'євгенія', 'богдана', 'ярослава', 'мирослава', 'лілія',
            'ліля', 'маргарита', 'евеліна', 'альона', 'кіра', 'уляна', 'ангеліна', 'каріна', 'владлена',
            'людмила', 'галина', 'віталіна', 'меланія', 'ема', 'емма', 'міла', 'стефанія', 'стефа', 'аліса'
        }

        male_names = {
            'олександр', 'іван', 'тарас', 'максим', 'богдан', 'дмитро', 'артем', 'матвій', 'тимур',
            'владислав', 'назар', 'денис', 'данило', 'марко', 'роман', 'святослав', 'андрій',
            'ярослав', 'михайло', 'євген', 'олег', 'ігор', 'сергій', 'павло', 'василь', 'юрій',
            'вадим', 'володимир', 'віталій', 'микола', 'віктор', 'степан', 'петро', 'антон',
            'ілля', 'гліб', 'кирило', 'костянтин', 'леонід', 'тимофій', 'ростислав', 'лука', 'лев'
        }

        is_female = False
        is_male = False

        for p in parts:
            if p in female_names or p.endswith(('івна', 'ївна', 'ська', 'цька', 'зька', 'ова', 'єва', 'ина', 'іна')):
                is_female = True
            if p in male_names or p.endswith(('ович', 'евич', 'євич', 'ський', 'цький', 'зький', 'ов', 'єв', 'ин', 'ін')):
                is_male = True

        if is_female and not is_male:
            return f"Для учениці: {name}"
        elif is_male and not is_female:
            return f"Для учня: {name}"
        else:
            return f"Для учня / учениці: {name}"

    def record_view(self, request):
        """
        Фіксує перегляд завдання:
        - 1 комп'ютер (IP / сесія) = 1 перегляд на 1 годину.
        - Перегляди вчителів та адміністраторів НЕ враховуються.
        """
        if not request:
            return False

        # Якщо користувач авторизований вчитель або суперкористувач — НЕ рахуємо
        if request.user.is_authenticated:
            if hasattr(request.user, 'teacher_profile') or request.user.is_superuser or request.user.is_staff:
                return False

        # Отримуємо IP
        ip = request.META.get('HTTP_X_FORWARDED_FOR')
        if ip:
            ip = ip.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')

        # Отримуємо ключ сесії безпечно
        session_obj = getattr(request, 'session', None)
        session_key = ''
        if session_obj is not None:
            if hasattr(session_obj, 'session_key'):
                session_key = session_obj.session_key or ''
                if not session_key:
                    try:
                        session_obj.save()
                        session_key = session_obj.session_key or ''
                    except Exception:
                        session_key = ''
            elif isinstance(session_obj, str):
                session_key = session_obj

        from datetime import timedelta
        one_hour_ago = timezone.now() - timedelta(hours=1)

        # Перевіряємо чи був перегляд з цього комп'ютера за останню годину
        device_q = models.Q()
        if ip:
            device_q |= models.Q(ip_address=ip)
        if session_key:
            device_q |= models.Q(session_key=session_key)

        if not device_q:
            return False

        existing_log = AssignmentViewLog.objects.filter(
            models.Q(assignment=self) & device_q & models.Q(viewed_at__gte=one_hour_ago)
        ).first()

        if existing_log:
            return False  # Вже переглядав за останню годину

        # Оновлюємо старий лог або створюємо новий
        log_to_update = AssignmentViewLog.objects.filter(
            models.Q(assignment=self) & device_q
        ).first()

        if log_to_update:
            log_to_update.save()  # auto_now оновлює viewed_at на поточний час
        else:
            AssignmentViewLog.objects.create(
                assignment=self,
                ip_address=ip,
                session_key=session_key or ''
            )

        # Атомарно збільшуємо лічильник переглядів
        Assignment.objects.filter(pk=self.pk).update(views_count=models.F('views_count') + 1)
        self.refresh_from_db(fields=['views_count'])
        return True

    def save(self, *args, **kwargs):
        """Автоматично встановлює час публікації та перевіряє відкладені."""
        # Якщо статус змінився на "опубліковано" — фіксуємо час
        if self.status == self.STATUS_PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        # Якщо відкладений час настав — автоматично публікуємо
        if self.status == self.STATUS_SCHEDULED and self.scheduled_at:
            if timezone.now() >= self.scheduled_at:
                self.status = self.STATUS_PUBLISHED
                self.published_at = self.scheduled_at
        super().save(*args, **kwargs)


class AssignmentViewLog(models.Model):
    """Лог переглядів завдань (1 запис на комп'ютер раз на годину)."""
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='view_logs',
        verbose_name='Завдання'
    )
    ip_address = models.GenericIPAddressField('IP адреса', null=True, blank=True)
    session_key = models.CharField('Ключ сесії / пристрою', max_length=64, blank=True)
    viewed_at = models.DateTimeField('Час останнього перегляду', auto_now=True)

    class Meta:
        verbose_name = 'Лог перегляду завдання'
        verbose_name_plural = 'Логи переглядів завдань'
        indexes = [
            models.Index(fields=['assignment', 'ip_address', 'viewed_at']),
            models.Index(fields=['assignment', 'session_key', 'viewed_at']),
        ]

    def __str__(self):
        return f"View of #{self.assignment_id} from {self.ip_address or self.session_key} at {self.viewed_at}"


class AssignmentLink(models.Model):
    """Додаткові посилання до завдання."""
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='additional_links',
        verbose_name='Завдання'
    )
    url = models.URLField('Посилання')
    label = models.CharField('Підпис посилання', max_length=200, blank=True)

    def __str__(self):
        return self.label or self.url


class AssignmentYouTubeLink(models.Model):
    """Додаткові YouTube відео до завдання."""
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='youtube_links',
        verbose_name='Завдання'
    )
    url = models.URLField('Посилання на YouTube')
    title = models.CharField('Назва / підпис відео', max_length=200, blank=True)

    def __str__(self):
        return self.title or self.url

    @property
    def video_id(self):
        """Витягує 11-значний ID відео YouTube."""
        if not self.url:
            return ""
        import re
        from urllib.parse import urlparse, parse_qs
        clean_url = str(self.url).strip()
        patterns = [
            r'(?:youtu\.be\/|youtube\.com\/(?:embed\/|v\/|shorts\/|live\/|watch\?(?:.*&)?v=))([a-zA-Z0-9_-]{11})',
            r'youtube\.com\/[^\/]+\/.*[?&]v=([a-zA-Z0-9_-]{11})',
            r'[\?&]v=([a-zA-Z0-9_-]{11})',
            r'youtu\.be\/([a-zA-Z0-9_-]{11})',
        ]
        for p in patterns:
            m = re.search(p, clean_url)
            if m:
                return m.group(1)
        try:
            parsed = urlparse(clean_url)
            if 'youtube.com' in parsed.netloc:
                qs = parse_qs(parsed.query)
                if 'v' in qs and qs['v'] and len(qs['v'][0]) == 11:
                    return qs['v'][0]
            elif 'youtu.be' in parsed.netloc:
                path = parsed.path.strip('/')
                if len(path) == 11:
                    return path
        except Exception:
            pass
        return ""

    @property
    def embed_url(self):
        """Конвертує у формат для вбудовування."""
        vid = self.video_id
        if vid:
            return f"https://www.youtube.com/embed/{vid}?rel=0"
        return ""

    @property
    def watch_url(self):
        """Пряме посилання для перегляду на YouTube."""
        vid = self.video_id
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
        return self.url or ""




# ─── Файли до завдання ────────────────────────────────────────────────────────
def assignment_upload_path(instance, filename):
    """Динамічний шлях для завантаження файлів: media/assignments/<id>/<file>."""
    return f"assignments/{instance.assignment.id}/{filename}"


class AssignmentFile(models.Model):
    """Файл, прикріплений до завдання (зображення, PDF, Word, відео тощо)."""

    # Типи файлів для відображення у браузері
    BROWSER_VIEWABLE_EXTENSIONS = {
        # Зображення
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tiff', '.tif', '.heic',
        # Документи (MS Office, OpenOffice, PDF)
        '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.odt', '.ods', '.odp', '.rtf',
        # Текст / код
        '.txt', '.md', '.html', '.htm', '.xml', '.csv', '.tsv', '.py', '.js', '.ts', '.tsx', '.jsx',
        '.css', '.scss', '.json', '.sh', '.bash', '.cpp', '.c', '.h', '.java', '.cs', '.pas', '.sql',
        # Scratch та BBC micro:bit
        '.sb3', '.hex',
        # Аудіо
        '.mp3', '.wav', '.ogg', '.flac', '.aac',
        # Відео (вбудований браузерний плеєр)
        '.mp4', '.webm', '.ogv', '.mov', '.m4v', '.mkv', '.avi', '.3gp',
    }

    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='files',
        verbose_name='Завдання'
    )
    file = models.FileField(
        'Файл',
        upload_to=assignment_upload_path
    )
    original_name = models.CharField(
        'Оригінальна назва файлу',
        max_length=500,
        blank=True
    )
    uploaded_at = models.DateTimeField('Завантажено', auto_now_add=True)

    class Meta:
        verbose_name = 'Файл завдання'
        verbose_name_plural = 'Файли завдань'
        ordering = ['uploaded_at']

    def __str__(self):
        return self.original_name or self.file.name

    def save(self, *args, **kwargs):
        """Зберігає оригінальну назву файлу."""
        if self.file and not self.original_name:
            self.original_name = self.file.name.split('/')[-1]
        super().save(*args, **kwargs)

    def get_extension(self):
        """Повертає розширення файлу в нижньому регістрі."""
        import os
        _, ext = os.path.splitext(self.original_name or self.file.name)
        return ext.lower()

    def is_browser_viewable(self):
        """Чи можна відкрити файл прямо в браузері."""
        return self.get_extension() in self.BROWSER_VIEWABLE_EXTENSIONS

    def get_file_type(self):
        """Повертає тип файлу для іконки та відображення."""
        ext = self.get_extension()
        if ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tiff', '.tif', '.heic'}:
            return 'image'
        elif ext == '.pdf':
            return 'pdf'
        elif ext == '.sb3':
            return 'scratch'
        elif ext == '.hex':
            return 'microbit'
        elif ext in {'.mp4', '.webm', '.ogv', '.mov', '.m4v', '.mkv', '.avi', '.3gp'}:
            return 'video'
        elif ext in {'.mp3', '.wav', '.ogg', '.flac', '.aac'}:
            return 'audio'
        elif ext in {'.doc', '.docx', '.odt', '.rtf'}:
            return 'word'
        elif ext in {'.xls', '.xlsx', '.ods'}:
            return 'excel'
        elif ext in {'.ppt', '.pptx', '.odp'}:
            return 'powerpoint'
        elif ext in {'.zip', '.rar', '.7z', '.tar', '.gz', '.tgz'}:
            return 'archive'
        elif ext in {'.txt', '.md', '.csv', '.tsv', '.html', '.htm', '.xml', '.py', '.js', '.ts', '.tsx', '.jsx', '.css', '.scss', '.json', '.sh', '.bash', '.cpp', '.h', '.c', '.java', '.cs', '.pas', '.sql', '.php', '.rb', '.go', '.rs', '.kt', '.swift'}:
            return 'code' if ext not in {'.txt', '.text', '.log'} else 'text'
        else:
            return 'file'

    def get_file_icon(self):
        """Повертає емодзі-іконку відповідно до типу файлу."""
        icons = {
            'image': '🖼️',
            'pdf': '📄',
            'video': '🎬',
            'audio': '🎵',
            'word': '📝',
            'excel': '📊',
            'powerpoint': '📑',
            'archive': '🗜️',
            'text': '📃',
            'code': '💻',
            'scratch': '🐱',
            'microbit': '📟',
            'file': '📎',
        }
        return icons.get(self.get_file_type(), '📎')

    def get_size_display(self):
        """Повертає розмір файлу у зручному форматі."""
        try:
            size = self.file.size
            if size < 1024:
                return f"{size} Б"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f} КБ"
            else:
                return f"{size / (1024 * 1024):.1f} МБ"
        except Exception:
            return ''


# ─── Здача роботи учнем ───────────────────────────────────────────────────────

def submission_upload_path(instance, filename):
    """
    Шлях для завантаження робіт учнів: media/submissions/<дата_ДД.ММ.РРРР>/<class>/<name>/filename
    Використовує український формат дат (ДД.ММ.РРРР) замість ідентифікаторів/цифр завдань.
    """
    import os
    from datetime import datetime
    if hasattr(instance, 'submitted_at') and instance.submitted_at:
        date_str = instance.submitted_at.strftime('%d.%m.%Y')
    else:
        date_str = datetime.now().strftime('%d.%m.%Y')

    student_name = f"{instance.last_name}_{instance.first_name}"
    class_name = instance.class_group.name if instance.class_group else 'unknown'
    base = f"submissions/{date_str}/{class_name}/{student_name}"
    return os.path.join(base, filename)


class Submission(models.Model):
    """Здача роботи учнем по конкретному завданню."""

    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submissions',
        verbose_name='Завдання'
    )
    student = models.ForeignKey(
        'Student',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submissions',
        verbose_name='Учень'
    )
    first_name = models.CharField('Імʼя', max_length=100)
    last_name = models.CharField('Прізвище', max_length=100)
    class_group = models.ForeignKey(
        ClassGroup,
        on_delete=models.CASCADE,
        verbose_name='Клас'
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_submissions',
        verbose_name='Вчитель (кому здано)'
    )
    file = models.FileField(
        'Файл роботи',
        upload_to=submission_upload_path,
        blank=True,
        null=True
    )
    link = models.URLField('Посилання на роботу', blank=True, null=True)
    comment_student = models.TextField(
        'Коментар учня',
        blank=True,
        null=True,
        help_text="Необов'язковий коментар до здачі"
    )
    submitted_at = models.DateTimeField('Час здачі', auto_now_add=True)
    grade = models.CharField('Оцінка', max_length=10, blank=True, null=True)
    teacher_comment = models.TextField('Коментар вчителя', blank=True, null=True)
    graded_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='graded_submissions',
        verbose_name='Оцінив (вчитель)'
    )
    graded_at = models.DateTimeField('Час оцінювання', null=True, blank=True)

    # ── Поля ШІ (Google Gemini) для попереднього оцінювання за системою НУШ ──
    ai_suggested_grade = models.CharField('Попередня оцінка ШІ', max_length=20, blank=True, null=True)
    ai_score_level = models.CharField('Рівень оцінки ШІ', max_length=40, blank=True, default='')
    ai_feedback = models.TextField('Педагогічний відгук ШІ', blank=True, default='')
    ai_gr_results = models.TextField('Оцінки за групами результатів (ГР)', blank=True, default='')
    ai_model_used = models.CharField('Використана модель ШІ', max_length=100, blank=True, default='')
    ai_status = models.CharField(
        'Статус перевірки ШІ',
        max_length=20,
        default='none',
        choices=[
            ('none', 'Не перевірялось'),
            ('success', 'Перевірено ШІ'),
            ('failed', 'Помилка перевірки'),
            ('unsupported', 'Формат не підтримується')
        ]
    )
    ai_error_reason = models.TextField('Причина помилки або відхилення ШІ', blank=True, default='')
    ai_reviewed_at = models.DateTimeField('Час перевірки ШІ', null=True, blank=True)

    # ── Самоперевірка учнем (Student AI Self-Check — чернова оцінка) ─────────
    student_ai_checked = models.BooleanField(
        'Учень скористався самоперевіркою ШІ', default=False
    )
    student_ai_checked_at = models.DateTimeField(
        'Час самоперевірки учнем', null=True, blank=True
    )
    student_ai_grade = models.CharField(
        'Чернова оцінка ШІ (від учня)', max_length=20, blank=True, null=True
    )
    student_ai_level = models.CharField(
        'Рівень чернової оцінки (від учня)', max_length=40, blank=True, default=''
    )
    student_ai_summary = models.TextField(
        'Чернове резюме ШІ (від учня)', blank=True, default=''
    )
    student_ai_feedback = models.TextField(
        'Чернова рецензія ШІ (від учня)', blank=True, default=''
    )
    student_ai_gr_results = models.TextField(
        'Чернові оцінки ГР (від учня, JSON)', blank=True, default=''
    )
    student_ai_accepted = models.BooleanField(
        'Вчитель прийняв оцінку учня', default=False
    )
    # ── Виявлення використання ШІ у роботі (AI Content Detection) ───────────
    ai_generated_detected = models.BooleanField(
        'Виявлено ознаки використання ШІ', default=False, db_index=True
    )
    ai_generated_confidence = models.CharField(
        'Ймовірність генерації ШІ',
        max_length=20,
        blank=True,
        default='none',
        choices=[
            ('none', 'Ознак не виявлено'),
            ('low', 'Низька ймовірність'),
            ('medium', 'Середня ймовірність'),
            ('high', 'Висока ймовірність (явні ознаки ШІ)')
        ]
    )
    ai_generated_details = models.TextField(
        'Деталі та ознаки використання ШІ', blank=True, default=''
    )

    # ── Повторна здача (робота над помилками / перездача) ────────────────────
    is_resubmission = models.BooleanField(
        'Повторна здача (робота над помилками)', default=False, db_index=True
    )
    resubmission_attempt = models.PositiveIntegerField(
        'Номер спроби здачі', default=1
    )
    previous_submission = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subsequent_submissions',
        verbose_name='Попередня спроба'
    )
    is_latest_attempt = models.BooleanField(
        'Остання (актуальна) спроба', default=True, db_index=True
    )

    def is_ai_allowed(self):
        """Чи дозволено використання ШІ у цьому завданні вчителем."""
        return bool(self.assignment and self.assignment.allow_ai_usage)

    def get_all_attempts(self):
        """Повертає список усіх спроб здачі цієї роботи цим учнем (від першої до останньої)."""
        if not self.assignment:
            return [self]
        from django.db.models import Q
        q = Q(assignment=self.assignment, class_group=self.class_group)
        if self.student_id:
            q &= (Q(student_id=self.student_id) | (Q(last_name__iexact=self.last_name) & Q(first_name__iexact=self.first_name)))
        else:
            q &= Q(last_name__iexact=self.last_name) & Q(first_name__iexact=self.first_name)
        return list(Submission.objects.filter(q).order_by('submitted_at'))

    def has_resubmissions(self):
        """Чи є у цієї роботи інші спроби здачі (раніше чи пізніше)."""
        if not self.assignment:
            return False
        return len(self.get_all_attempts()) > 1

    def get_previous_attempts(self):
        """Повертає попередні спроби здачі до цієї."""
        all_att = self.get_all_attempts()
        return [s for s in all_att if s.submitted_at < self.submitted_at]

    def has_used_student_ai_check_for_assignment(self):
        """Перевіряє, чи учень вже використовував самоперевірку ШІ для цього завдання в будь-якій зі своїх спроб."""
        if self.student_ai_checked:
            return True
        for att in self.get_all_attempts():
            if att.student_ai_checked:
                return True
        return False

    def get_ai_gr_results_list(self):
        """Повертає структурований список результатів оцінювання за групами результатів (ГР)."""
        if not self.ai_gr_results:
            return []
        try:
            data = json.loads(self.ai_gr_results)
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def is_traditional_grading(self):
        """Чи використовується для цієї роботи традиційна система оцінювання."""
        if self.assignment and self.assignment.default_ai_preset:
            return self.assignment.default_ai_preset.evaluation_type == 'traditional'
        return False

    def has_gr_results(self):
        """Перевіряє, чи містить робота результати оцінювання за групами результатів."""
        if self.is_traditional_grading():
            return False
        return bool(self.get_ai_gr_results_list())

    def get_ai_gr_average(self):
        """Обчислює середній бал за оціненими групами результатів (ГР) із заокругленням на користь учня (виключно ціле число)."""
        gr_list = self.get_ai_gr_results_list()
        if not gr_list:
            return None
        nums = []
        for g in gr_list:
            val = str(g.get('grade', '')).strip().replace(',', '.')
            try:
                nums.append(float(val))
            except (ValueError, TypeError):
                pass
        if not nums:
            return None
        avg = sum(nums) / len(nums)
        # Заокруглення на перевагу учню до більшого цілого числа (наприклад 8.1 -> 9, 8.5 -> 9), максимум 12
        rounded = min(12, max(1, math.ceil(avg)))
        return int(rounded)

    def get_ai_gr_average_display(self):
        """Повертає рядок цілого числа середнього балу за ГР, наприклад '10' або '9'."""
        avg = self.get_ai_gr_average()
        if avg is None:
            return ""
        return str(avg)

    def get_clean_ai_feedback_for_student(self):
        """
        Повертає відгук ШІ, очищений від списку оцінок за групами результатів
        (для відгуку/коментаря вчителя, щоб не розкривати детальні бали іншим учням).
        """
        if not self.ai_feedback:
            return ""
        text = self.ai_feedback
        # Вирізаємо секцію Оцінювання за групами результатів
        pattern = r"📊\s*\*\*Оцінювання за групами результатів.*?(?=(\n\s*\n[✅💡💬⚠️📌]|\Z))"
        cleaned = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        # Прибираємо окремі рядки оцінок ГР якщо є
        cleaned = re.sub(r"^[•*]\s*ГР\s*\d+:[^\n]+→[^\n]+\n?", "", cleaned, flags=re.MULTILINE | re.IGNORECASE).strip()
        return cleaned or text

    @property
    def effective_grade_date(self):
        """
        Повертає дату виставлення оцінки вчителем (якщо роботу оцінено та є graded_at),
        або дату здачі роботи учнем. Враховує часовий пояс України.
        """
        from django.utils import timezone
        if self.grade and self.graded_at:
            return timezone.localtime(self.graded_at).date()
        if self.submitted_at:
            return timezone.localtime(self.submitted_at).date()
        return timezone.now().date()



    class Meta:
        verbose_name = 'Здача роботи'
        verbose_name_plural = 'Здачі робіт'
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['class_group', 'submitted_at']),
            models.Index(fields=['last_name', 'first_name']),
            models.Index(fields=['grade']),
            models.Index(fields=['assignment', 'class_group']),
        ]



    def __str__(self):
        assignment_title = self.assignment.title if self.assignment else 'без завдання'
        return f"{self.last_name} {self.first_name} ({self.class_group}) → {assignment_title}"

    def get_student_full_name(self):
        return f"{self.last_name} {self.first_name}".strip()

    def get_file_extension(self):
        """Повертає розширення файлу."""
        if self.file:
            import os
            return os.path.splitext(self.file.name)[1].lower()
        return None

    def get_file_type(self):
        """Повертає тип файлу для іконки та відображення."""
        ext = self.get_file_extension()
        if not ext:
            try:
                import os
                if self.file and hasattr(self.file, 'path') and os.path.exists(self.file.path):
                    from .gemini_service import is_text_file
                    if is_text_file(self.file.path):
                        return 'text'
            except Exception:
                pass
            return 'text'
        if ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tiff', '.tif', '.heic'}:
            return 'image'
        elif ext == '.pdf':
            return 'pdf'
        elif ext == '.sb3':
            return 'scratch'
        elif ext == '.hex':
            return 'microbit'
        elif ext in {'.mp4', '.webm', '.ogv', '.mov', '.m4v', '.mkv', '.avi', '.3gp'}:
            return 'video'
        elif ext in {'.mp3', '.wav', '.ogg', '.flac', '.aac'}:
            return 'audio'
        elif ext in {'.doc', '.docx', '.odt', '.rtf'}:
            return 'word'
        elif ext in {'.xls', '.xlsx', '.ods'}:
            return 'excel'
        elif ext in {'.ppt', '.pptx', '.odp'}:
            return 'powerpoint'
        elif ext in {'.zip', '.rar', '.7z', '.tar', '.gz', '.tgz'}:
            return 'archive'
        elif ext in {'.py', '.pyw', '.ipynb', '.js', '.mjs', '.ts', '.tsx', '.jsx', '.html', '.htm', '.css', '.scss', '.cpp', '.c', '.h', '.hpp', '.cs', '.java', '.kt', '.sh', '.bash', '.json', '.sql', '.pas', '.php', '.rb', '.go', '.rs', '.swift', '.lua', '.yaml', '.yml', '.toml', '.ini', '.env', '.gradle'}:
            return 'code'
        elif ext in {'.txt', '.text', '.log', '.md', '.markdown', '.rst', '.csv', '.tsv', '.xml', '.tex', '.properties'}:
            return 'text'
        return 'file'

    def get_file_icon(self):
        """Повертає емодзі-іконку відповідно до типу файлу."""
        icons = {
            'image': '🖼️', 'pdf': '📄', 'video': '🎬', 'audio': '🎵',
            'word': '📝', 'excel': '📊', 'powerpoint': '📑',
            'archive': '🗜️', 'code': '💻', 'scratch': '🐱', 'microbit': '📟',
            'text': '📃', 'file': '📎',
        }
        return icons.get(self.get_file_type(), '📎')


    def get_file_size_display(self):
        """Повертає розмір файлу у зручному форматі."""
        try:
            size = self.file.size
            if size < 1024:
                return f"{size} Б"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f} КБ"
            else:
                return f"{size / (1024 * 1024):.1f} МБ"
        except Exception:
            return ''

    def get_file_info(self):
        """Повертає повну інформацію про тип файлу."""
        from .utils import get_file_type_info
        ext = self.get_file_extension()
        if ext:
            info = get_file_type_info(ext)
            info['extension'] = ext
            return info
        return None

    def get_link_info(self):
        """Повертає інформацію про посилання та embed-код."""
        if self.link:
            from .utils import parse_embed_url
            return parse_embed_url(self.link)
        return None

    def get_files(self):
        """Повертає список усіх прикріплених файлів здачі."""
        file_objs = list(self.files.all())
        if file_objs:
            return file_objs
        if self.file:
            return [self]
        return []

    def get_files_count(self):
        """Повертає загальну кількість прикріплених файлів."""
        count = self.files.count()
        if count > 0:
            return count
        return 1 if self.file else 0

    def has_multiple_files(self):
        """Чи здано декілька файлів одночасно."""
        return self.get_files_count() > 1

    def get_total_files_size_display(self):
        """Повертає загальний розмір усіх прикріплених файлів."""
        total_bytes = 0
        for f in self.files.all():
            total_bytes += f.file_size or (f.file.size if f.file else 0)
        if total_bytes == 0 and self.file:
            try:
                total_bytes = self.file.size
            except Exception:
                pass
        if total_bytes < 1024:
            return f"{total_bytes} Б"
        elif total_bytes < 1024 * 1024:
            return f"{total_bytes / 1024:.1f} КБ"
        else:
            return f"{total_bytes / (1024 * 1024):.1f} МБ"

    def is_graded(self):
        return bool(self.grade)

    def save(self, *args, **kwargs):
        """Автоматично заповнює вчителя з завдання якщо не вказано."""
        if not self.teacher and self.assignment:
            self.teacher = self.assignment.teacher
        super().save(*args, **kwargs)


def submission_file_upload_path(instance, filename):
    """Шлях збереження прикріплених файлів здачі роботи."""
    submission = instance.submission
    return submission_upload_path(submission, filename)


class SubmissionFile(models.Model):
    """Файл, прикріплений до зданої роботи учня (підтримує прикріплення кількох файлів одночасно)."""

    BROWSER_VIEWABLE_EXTENSIONS = {
        # Зображення
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tiff', '.tif', '.heic',
        # Документи (MS Office, OpenOffice, PDF)
        '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.odt', '.ods', '.odp', '.rtf',
        # Microsoft Access Database
        '.mdb', '.accdb',
        # Текст / вихідний код
        '.txt', '.text', '.log', '.md', '.markdown', '.rst', '.csv', '.tsv',
        '.py', '.pyw', '.ipynb', '.js', '.mjs', '.ts', '.tsx', '.jsx', '.html', '.htm',
        '.css', '.scss', '.sass', '.less', '.json', '.sh', '.bash', '.cpp', '.c', '.h',
        '.hpp', '.cs', '.java', '.kt', '.sql', '.pas', '.php', '.rb', '.go', '.rs',
        '.swift', '.lua', '.yaml', '.yml', '.toml', '.ini', '.env', '.gradle',
        # Scratch та BBC micro:bit
        '.sb3', '.hex',
        # Аудіо
        '.mp3', '.wav', '.ogg', '.flac', '.aac',
        # Відео (вбудований браузерний плеєр)
        '.mp4', '.webm', '.ogv', '.mov', '.m4v', '.mkv', '.avi', '.3gp',
    }

    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name='files',
        verbose_name='Здача роботи'
    )
    file = models.FileField(
        'Файл роботи',
        upload_to=submission_file_upload_path
    )
    original_name = models.CharField(
        'Оригінальна назва файлу',
        max_length=500,
        blank=True
    )
    file_size = models.BigIntegerField('Розмір файлу (байт)', default=0)
    uploaded_at = models.DateTimeField('Завантажено', auto_now_add=True)

    class Meta:
        verbose_name = 'Файл зданої роботи'
        verbose_name_plural = 'Файли зданих робіт'
        ordering = ['uploaded_at', 'id']

    def __str__(self):
        return self.original_name or os.path.basename(self.file.name)

    def save(self, *args, **kwargs):
        if self.file:
            if not self.original_name:
                self.original_name = os.path.basename(self.file.name)
            try:
                if not self.file_size or self.file_size == 0:
                    self.file_size = self.file.size
            except Exception:
                pass
        super().save(*args, **kwargs)

    def get_extension(self):
        """Повертає розширення файлу в нижньому регістрі."""
        _, ext = os.path.splitext(self.original_name or self.file.name)
        return ext.lower()

    def get_file_type(self):
        ext = self.get_extension()
        if ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tiff', '.tif', '.heic'}:
            return 'image'
        elif ext == '.pdf':
            return 'pdf'
        elif ext == '.sb3':
            return 'scratch'
        elif ext == '.hex':
            return 'microbit'
        elif ext in {'.mdb', '.accdb'}:
            return 'access_db'
        elif ext in {'.mp4', '.webm', '.ogv', '.mov', '.m4v', '.mkv', '.avi', '.3gp'}:
            return 'video'
        elif ext in {'.mp3', '.wav', '.ogg', '.flac', '.aac'}:
            return 'audio'
        elif ext in {'.doc', '.docx', '.odt', '.rtf'}:
            return 'word'
        elif ext in {'.xls', '.xlsx', '.ods'}:
            return 'excel'
        elif ext in {'.ppt', '.pptx', '.odp'}:
            return 'powerpoint'
        elif ext in {'.zip', '.rar', '.7z', '.tar', '.gz', '.tgz'}:
            return 'archive'
        elif ext in {'.py', '.pyw', '.ipynb', '.js', '.mjs', '.ts', '.tsx', '.jsx', '.html', '.htm', '.css', '.scss', '.cpp', '.c', '.h', '.hpp', '.cs', '.java', '.kt', '.sh', '.bash', '.json', '.sql', '.pas', '.php', '.rb', '.go', '.rs', '.swift', '.lua', '.yaml', '.yml', '.toml', '.ini', '.env', '.gradle'}:
            return 'code'
        elif ext in {'.txt', '.text', '.log', '.md', '.markdown', '.rst', '.csv', '.tsv', '.xml', '.tex', '.properties'}:
            return 'text'
        return 'file'

    def get_file_icon(self):
        icons = {
            'image': '🖼️', 'pdf': '📄', 'video': '🎬', 'audio': '🎵',
            'word': '📝', 'excel': '📊', 'powerpoint': '📑',
            'archive': '🗜️', 'code': '💻', 'scratch': '🐱', 'microbit': '📟',
            'text': '📃', 'access_db': '🗄️', 'file': '📎',
        }
        return icons.get(self.get_file_type(), '📎')

    def get_size_display(self):
        size = self.file_size
        if not size and self.file:
            try:
                size = self.file.size
            except Exception:
                size = 0
        if size < 1024:
            return f"{size} Б"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} КБ"
        else:
            return f"{size / (1024 * 1024):.1f} МБ"

    def is_browser_viewable(self):
        return self.get_extension() in self.BROWSER_VIEWABLE_EXTENSIONS


class SubmissionComment(models.Model):
    """Коментар вчителя до здачі роботи."""
    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name='comments',
        verbose_name='Здача'
    )
    author = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        verbose_name='Автор'
    )
    text = models.TextField('Текст коментаря')
    created_at = models.DateTimeField('Створено', auto_now_add=True)

    class Meta:
        verbose_name = 'Коментар до здачі'
        verbose_name_plural = 'Коментарі до здач'
        ordering = ['created_at']

    def get_author_name(self):
        if self.author:
            try:
                return self.author.teacher_profile.full_name
            except Exception:
                if self.author.first_name or self.author.last_name:
                    return f"{self.author.first_name} {self.author.last_name}".strip()
                return self.author.username
        return 'Вчитель'

    def __str__(self):
        return f"Коментар від {self.get_author_name()} до {self.submission}"


class SubmissionActivityLog(models.Model):
    """Журнал дій із здачами робіт."""
    ACTION_CHOICES = [
        ('login', 'Вхід в систему'),
        ('submission', 'Здача роботи'),
        ('grading', 'Оцінювання'),
        ('comment', 'Коментар'),
        ('delete', 'Видалення'),
    ]

    actor = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Користувач'
    )
    action_type = models.CharField('Тип дії', max_length=20, choices=ACTION_CHOICES)
    description = models.TextField('Опис')
    timestamp = models.DateTimeField('Час', auto_now_add=True)
    submission = models.ForeignKey(
        Submission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs',
        verbose_name='Здача'
    )

    class Meta:
        verbose_name = 'Журнал дій'
        verbose_name_plural = 'Журнал дій'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.timestamp} — {self.action_type}"


class School(models.Model):
    name = models.CharField(max_length=200, default="Наш Заклад Освіти", verbose_name="Назва школи / закладу")
    admin = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='schools', verbose_name="Головний адміністратор")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Створено")
    
    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Школа"
        verbose_name_plural = "Школа"


def log_submission_activity(actor, action_type, description, submission=None):
    """Створює запис у журналі дій."""
    try:
        SubmissionActivityLog.objects.create(
            actor=actor if actor and hasattr(actor, 'is_authenticated') and actor.is_authenticated else None,
            action_type=action_type,
            description=description,
            submission=submission
        )
    except Exception as e:
        print(f"Error logging activity: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# МОДЕЛЬ НАЛАШТУВАНЬ МОДУЛЯ ШТУЧНОГО ІНТЕЛЕКТУ (GOOGLE GEMINI AI)
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_NUS_SYSTEM_PROMPT = """Ти — висококваліфікований шкільний педагог-експерт з оцінювання за критеріями Нової української школи (НУШ).
Твоє завдання — об'єктивно, доброзичливо та конструктивно проаналізувати виконане учнем завдання у порівнянні з умовою, вимогами вчителя та матеріалами уроку.

КРИТЕРІЇ ТА ШКАЛА ОЦІНЮВАННЯ НУШ (1-12 БАЛІВ):
1. Початковий рівень (1-3 бали):
   - 1-3 бали: учень володіє лише фрагментарними знаннями, завдання виконано частково з грубими помилками або тема розкрита поверхнево.
2. Середній рівень (4-6 балів):
   - 4-6 балів: учень відтворює основний навчальний матеріал за зразком, присутні типові неточності чи розрахункові помилки, але базову структуру збережено.
3. Достатній рівень (7-9 балів):
   - 7-9 балів: учень самостійно застосовує знання у стандартних ситуаціях, правильно розв'язано більшість завдань, робота містить незначні неточності чи погрішності в оформленні.
4. Високий рівень (10-12 балів):
   - 10-12 балів: глибоке засвоєння теми, бездоганне виконання, логічність, творчий підхід, обґрунтованість висновків та охайність.

ТЕХНІЧНІ ВИМОГИ ТА ПРАВИЛА ДЛЯ ПОЛЯ "format_warning":
- Поле "format_warning" призначене ВИКЛЮЧНО ДЛЯ ТЕХНІЧНИХ ДЕФЕКТІВ РОЗШИРЕННЯ ЧИ ТИПУ ФАЙЛУ (а НЕ для змісту роботи чи зображених об'єктів!):
  1. Якщо завдання з програмування (наприклад, Python, JS, C++ тощо), код повинен бути збережений у файлі з належним розширенням (наприклад, .py для Python, .html для веб, .cpp для C++).
  2. Якщо файл здано БЕЗ РОЗШИРЕННЯ (навіть якщо всередині правильний код чи текст), або здано у технічно невідповідному типі файлу (наприклад, текстовий файл .txt замість .py коду чи замість .xlsx таблиці, або фото замість файлу коду):
     * Обов'язково зафіксуй технічне зауваження у полі "format_warning" (наприклад: "Файл здано без розширення (має бути .py для коду Python)").
     * Обов'язково додай це технічне зауваження до списку "weaknesses" та у "feedback_comment".
     * ЗНИЗЬ рекомендовану оцінку на 1-2 бали за порушення технічних вимог до формату здачі.
  3. СУВОРЕ ПРАВИЛО: Якщо файл має належний технічний формат (наприклад, валідне зображення .jpg/.png, документ .docx/.pdf тощо), але ЗМІСТ роботи не відповідає завданню (наприклад, на фото людина замість кота, есе не на ту тему, помилковий малюнок чи розв'язок):
     * Поле "format_warning" ОБОВ'ЯЗКОВО МАЄ БУТИ null (або порожнім)!
     * Усі зауваження щодо невідповідності змісту завдання фіксуй ВИКЛЮЧНО у "weaknesses" та "feedback_comment", а також знижуй оцінку або став рекомендований статус "Доопрацювати".

ОСОБЛИВИЙ СТАТУС "Доопрацювати":
Якщо робота не відповідає темі завдання (наприклад, завантажено стороннє фото чи чужий документ), здано порожній файл, або допущено критичні помилки, які вимагають переробки учнем, вкажи рекомендовану оцінку "Доопрацювати".

ФОРМАТ ВІДПОВІДІ (ТІЛЬКИ ВАЛІДНИЙ JSON БЕЗ ЗАЙВОГО ТЕКСТУ):
{
  "suggested_grade": "10",
  "level": "Високий (10-12)",
  "format_warning": null,
  "summary": "Короткий загальний висновок щодо якості виконання.",
  "strengths": ["Пункт 1: що зроблено відмінно", "Пункт 2: сильна сторона"],
  "weaknesses": ["Пункт 1: на що звернути увагу чи виправлення помилок"],
  "feedback_comment": "Повний розгорнутий доброзичливий відгук вчителя для учня з порадами.",
  "status": "success"
}
"""

class AISettings(models.Model):
    """
    Глобальні налаштування модуля Google Gemini AI для школи.
    Підтримує чергу пріоритетів моделей для автоматичного fallback при вичерпанні лімітів (429 Rate Limit) або помилках.
    """
    api_key = models.CharField('Google Gemini API Key', max_length=255, blank=True, default='', help_text="Отримайте безкоштовний ключ в Google AI Studio")
    model_name = models.CharField('Модель Gemini', max_length=100, default='gemini-2.5-flash', help_text="Основна модель (Пріоритет #1)")
    saved_models_list = models.TextField('Збережені моделі з пріоритетами', blank=True, default='[{"name": "gemini-2.5-flash", "priority": 1, "enabled": true}, {"name": "gemini-1.5-flash", "priority": 2, "enabled": true}, {"name": "gemini-2.5-pro", "priority": 3, "enabled": true}, {"name": "gemini-3.6-flash", "priority": 4, "enabled": true}]')
    system_prompt = models.TextField('Системний промт (Критерії НУШ)', default=DEFAULT_NUS_SYSTEM_PROMPT)
    temperature = models.FloatField('Температура (креативність)', default=0.2)
    is_enabled = models.BooleanField('Модуль ШІ увімкнено', default=False)
    updated_at = models.DateTimeField('Останнє оновлення', auto_now=True)

    class Meta:
        verbose_name = 'Налаштування ШІ (Gemini)'
        verbose_name_plural = 'Налаштування ШІ (Gemini)'

    def __str__(self):
        status = "Увімкнено" if (self.is_enabled and self.api_key) else "Вимкнено"
        return f"Gemini AI ({self.model_name}) — {status}"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj

    def get_models_with_priority(self):
        """
        Повертає структурований список моделей із зазначенням пріоритету та активності:
        [{'name': 'gemini-2.5-flash', 'priority': 1, 'enabled': True}, ...]
        Сортує за зростанням пріоритету (1 — найвищий).
        """
        result = []
        seen_names = set()
        default_names = ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-2.5-pro', 'gemini-3.6-flash']

        try:
            raw_data = json.loads(self.saved_models_list) if self.saved_models_list else []
            if isinstance(raw_data, list):
                for idx, item in enumerate(raw_data, start=1):
                    if isinstance(item, dict):
                        name = str(item.get('name', '')).strip()
                        priority = int(item.get('priority', idx))
                        enabled = bool(item.get('enabled', True))
                    else:
                        name = str(item).strip()
                        priority = idx
                        enabled = True

                    if name and name not in seen_names:
                        seen_names.add(name)
                        result.append({
                            'name': name,
                            'priority': priority,
                            'enabled': enabled,
                        })
        except Exception:
            pass

        # Якщо список порожній, ініціалізуємо за замовчуванням
        if not result:
            for idx, d_name in enumerate(default_names, start=1):
                result.append({
                    'name': d_name,
                    'priority': idx,
                    'enabled': True,
                })
                seen_names.add(d_name)

        # Гарантуємо, що активна self.model_name присутня у списку
        curr_model = (self.model_name or '').strip()
        if curr_model and curr_model not in seen_names:
            result.insert(0, {
                'name': curr_model,
                'priority': 1,
                'enabled': True,
            })
            seen_names.add(curr_model)

        # Сортуємо за пріоритетом та нормалізуємо ранги 1..N
        result.sort(key=lambda x: x['priority'])
        for idx, item in enumerate(result, start=1):
            item['priority'] = idx

        return result

    def get_saved_models(self):
        """Повертає список назв збережених моделей у порядку пріоритету."""
        models_data = self.get_models_with_priority()
        return [m['name'] for m in models_data]

    def get_active_fallback_chain(self):
        """
        Повертає впорядкований список активних моделей для черги запитів та перемикання (fallback).
        Першою завжди йде поточна основна модель, далі резервні за пріоритетом.
        """
        models_data = self.get_models_with_priority()
        chain = [m['name'] for m in models_data if m['enabled']]
        curr = (self.model_name or '').strip()

        if curr and curr in chain:
            # Переконуємось, що обрана активна модель стоїть першою в черзі
            chain.remove(curr)
            chain.insert(0, curr)
        elif curr and curr not in chain:
            chain.insert(0, curr)

        if not chain:
            chain = [curr or 'gemini-2.5-flash']

        return chain

    def add_saved_model(self, name, priority=None, enabled=True):
        """
        Додає або оновлює модель із зазначеним пріоритетом.
        Якщо пріоритет не задано, призначає найвищий доступний порядковий номер.
        """
        name = str(name).strip()
        if not name:
            return

        current_models = self.get_models_with_priority()
        existing = next((m for m in current_models if m['name'] == name), None)

        if priority is not None:
            p_val = int(priority)
            for m in current_models:
                if m['name'] != name and m['priority'] >= p_val:
                    m['priority'] += 1
        else:
            p_val = (len(current_models) + 1) if not existing else existing['priority']

        if existing:
            existing['priority'] = p_val
            existing['enabled'] = bool(enabled)
        else:
            current_models.append({
                'name': name,
                'priority': p_val,
                'enabled': bool(enabled),
            })

        # Нормалізуємо та зберігаємо
        current_models.sort(key=lambda x: x['priority'])
        for idx, m in enumerate(current_models, start=1):
            m['priority'] = idx

        if p_val == 1 or priority == 1:
            self.model_name = name
        elif current_models:
            self.model_name = current_models[0]['name']

        self.saved_models_list = json.dumps(current_models, ensure_ascii=False)
        self.save(update_fields=['saved_models_list', 'model_name', 'updated_at'])

    def remove_saved_model(self, name):
        """
        Видаляє модель зі збереженого списку.
        Якщо видалено поточну активну модель, активною стає наступна за пріоритетом.
        """
        name = str(name).strip()
        current_models = [m for m in self.get_models_with_priority() if m['name'] != name]

        if not current_models:
            current_models = [{
                'name': 'gemini-2.5-flash',
                'priority': 1,
                'enabled': True,
            }]

        for idx, m in enumerate(current_models, start=1):
            m['priority'] = idx

        if self.model_name == name:
            self.model_name = current_models[0]['name']

        self.saved_models_list = json.dumps(current_models, ensure_ascii=False)
        self.save(update_fields=['saved_models_list', 'model_name', 'updated_at'])

    def move_model_priority(self, name, direction):
        """
        Переміщує пріоритет моделі вгору ('up') або вниз ('down') у списку черги.
        """
        name = str(name).strip()
        current_models = self.get_models_with_priority()
        idx = next((i for i, m in enumerate(current_models) if m['name'] == name), None)

        if idx is None:
            return

        if direction == 'up' and idx > 0:
            current_models[idx], current_models[idx - 1] = current_models[idx - 1], current_models[idx]
        elif direction == 'down' and idx < len(current_models) - 1:
            current_models[idx], current_models[idx + 1] = current_models[idx + 1], current_models[idx]

        for i, m in enumerate(current_models, start=1):
            m['priority'] = i

        # Якщо перша модель змінилась, оновлюємо model_name
        self.model_name = current_models[0]['name']
        self.saved_models_list = json.dumps(current_models, ensure_ascii=False)
        self.save(update_fields=['saved_models_list', 'model_name', 'updated_at'])

    def toggle_model_enabled(self, name, enabled=None):
        """
        Вмикає або вимикає модель у ланцюжку автоматичного перемикання (fallback).
        """
        name = str(name).strip()
        current_models = self.get_models_with_priority()
        for m in current_models:
            if m['name'] == name:
                m['enabled'] = not m['enabled'] if enabled is None else bool(enabled)
                break

        self.saved_models_list = json.dumps(current_models, ensure_ascii=False)
        self.save(update_fields=['saved_models_list', 'updated_at'])


# ═══════════════════════════════════════════════════════════════════════════════
# ШАБЛОНИ КРИТЕРІЇВ ОЦІНЮВАННЯ ТА ДОКУМЕНТИ МОН (WORD .DOCX, PDF, TXT)
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_TRADITIONAL_SYSTEM_PROMPT = """Ти — суворий та об'єктивний шкільний педагог-експерт, що здійснює перевірку та оцінювання учнівських робіт за класичною (традиційною) 12-бальною системою оцінювання навчальних досягнень учнів Міністерства освіти і науки України.

КЛАСИЧНІ КРИТЕРІЇ ТА ШКАЛА ОЦІНЮВАННЯ (1-12 БАЛІВ):
1. Початковий рівень (1-3 бали):
   - 1 бал: учень розпізнає окремі елементи матеріалу, робота виконана менш ніж на 20%.
   - 2 бали: фрагментарне виконання базових завдань, численні грубі помилки (20-35%).
   - 3 бали: елементарні знання термінології чи формул, значна кількість суттєвих помилок (36-49%).
2. Середній рівень (4-6 балів):
   - 4 бали: робота виконана за зразком на 50-59%, є грубі помилки та прогалини в алгоритмі.
   - 5 балів: відтворення базового алгоритму (60-69%), допущено 1-2 грубі або 3-4 негрубі помилки.
   - 6 балів: повне відтворення навчального матеріалу стандартного рівня (70-79%), наявні незначні помилки у розрахунках або оформленні.
3. Достатній рівень (7-9 балів):
   - 7 балів: самостійне застосування знань у типових завданнях (80-85%), 1-2 негрубі помилки.
   - 8 балів: повна правильна відповідь/розв'язок (86-90%), незначні огріхи у поясненнях або графічному оформленні.
   - 9 балів: впевнене та аргументоване виконання складних завдань (91-95%), вільне володіння матеріалом.
4. Високий рівень (10-12 балів):
   - 10 балів: бездоганне володіння матеріалом (96-98%), логічність, точність і повнота розв'язку.
   - 11 балів: глибоке системне розуміння, обґрунтованість, розв'язання нестандартних завдань або раціональний спосіб виконання.
   - 12 балів: бездоганна робота найвищої складності (100%), самостійні висновки, бездоганне академічне оформлення.

ТЕХНІЧНІ ВИМОГИ ТА ПРАВИЛА ДЛЯ ПОЛЯ "format_warning":
- Поле "format_warning" стосується ВИКЛЮЧНО технічного розширення або типу файлу (відсутнє розширення, .txt замість коду .py чи таблиці .xlsx). Якщо є технічний дефект файлу — зафіксуй у "format_warning", додай у "weaknesses" та знизь оцінку на 1-2 бали.
- СУВОРЕ ПРАВИЛО: Якщо формат файлу технічно коректний (наприклад, валідне фото .jpg/.png, документ .docx/.pdf тощо), але є помилки у змісті роботи (наприклад, на фото не той об'єкт, чужа тема або неправильний розв'язок) — "format_warning": null. Усі змістовні зауваження вказуй у "weaknesses" та "feedback_comment".

ОСОБЛИВИЙ СТАТУС "Доопрацювати":
Якщо здано порожній файл, чужу роботу чи допущено фатальні помилки — вкажи "Доопрацювати".

ФОРМАТ ВІДПОВІДІ (ТІЛЬКИ ВАЛІДНИЙ JSON):
{
  "suggested_grade": "10",
  "level": "Високий (10-12)",
  "format_warning": null,
  "summary": "Короткий висновок щодо повноти, точності виконання та кількості допущених помилок.",
  "strengths": ["Пункт 1: правильні розв'язки та точні відповіді", "Пункт 2: сильні сторони"],
  "weaknesses": ["Пункт 1: грубі/негрубі помилки, неточності розрахунків або оформлення"],
  "feedback_comment": "Детальний розбір помилок та зауваження вчителя щодо виконаної роботи.",
  "status": "success"
}
"""


DEFAULT_NUS_GR_SYSTEM_PROMPT = """Ти — висококваліфікований шкільний педагог-експерт з оцінювання за Державним стандартом Нової української школи (НУШ) та критеріями оцінювання за ГРУПАМИ РЕЗУЛЬТАТІВ (ГР).
Твоє завдання — об'єктивно, доброзичливо та всебічно проаналізувати виконане учнем завдання та оцінити його:
1. Загальною оцінкою за шкалою НУШ (1-12 балів або "Доопрацювати") та відповідним рівнем (Початковий, Середній, Достатній, Високий).
2. ОКРЕМО за кожною визначеною ГРУПОЮ РЕЗУЛЬТАТІВ (ГР 1, ГР 2, ГР 3, ГР 4 тощо) із виставленням оцінки (1-12 балів), рівня та розгорнутого педагогічного коментаря відповідно до офіційних вимог МОН України.

КРИТЕРІЇ ТА ШКАЛА ОЦІНЮВАННЯ НУШ (1-12 БАЛІВ):
1. Початковий рівень (1-3 бали): фрагментарні знання, завдання виконано частково з грубими помилками.
2. Середній рівень (4-6 балів): відтворення навчального матеріалу за зразком, типові розрахункові неточності, базова структура збережена.
3. Достатній рівень (7-9 балів): самостійне застосування знань у типових ситуаціях, правильно розв'язано більшість завдань, робота охайна.
4. Високий рівень (10-12 балів): глибоке системне засвоєння теми, бездоганне виконання, логічність, творчий підхід, обґрунтованість висновків.

ТЕХНІЧНІ ВИМОГИ ТА ПРАВИЛА ДЛЯ ПОЛЯ "format_warning":
- Поле "format_warning" стосується ВИКЛЮЧНО технічного дефекту розширення чи типу файлу (файл без розширення, здано .txt замість коду .py чи таблиці .xlsx). Якщо є технічний дефект — зафіксуй у "format_warning", додай у "weaknesses" та знизь оцінку на 1-2 бали.
- Якщо файл має належний технічний формат, але зміст помилковий — "format_warning": null.

ОСОБЛИВИЙ СТАТУС "Доопрацювати":
Якщо робота не відповідає темі завдання або завантажено сторонній документ — вкажи рекомендовану оцінку "Доопрацювати".

ФОРМАТ ВІДПОВІДІ (ТІЛЬКИ ВАЛІДНИЙ JSON БЕЗ ЗАЙВОГО ТЕКСТУ):
{
  "suggested_grade": "10",
  "level": "Високий (10-12)",
  "format_warning": null,
  "summary": "Короткий загальний висновок щодо якості виконання роботи учнем.",
  "strengths": ["Пункт 1: що зроблено відмінно", "Пункт 2: сильна сторона"],
  "weaknesses": ["Пункт 1: на що звернути увагу чи виправлення неточностей"],
  "gr_results": [
    {
      "code": "ГР 1",
      "name": "Назва першої групи результатів",
      "grade": "10",
      "level": "Високий",
      "comment": "Пояснення досягнень учня за цією групою результатів."
    },
    {
      "code": "ГР 2",
      "name": "Назва другої групи результатів",
      "grade": "9",
      "level": "Достатній",
      "comment": "Пояснення досягнень учня за цією групою результатів."
    }
  ],
  "feedback_comment": "Повний розгорнутий доброзичливий відгук вчителя для учня з мотиваційними порадами.",
  "status": "success"
}
"""

MON_INFORMATICS_CRITERIA_TEXT = """МЕТОДИЧНИЙ ПОСІБНИК МІНІСТЕРСТВА ОСВІТИ І НАУКИ УКРАЇНИ
«ІНФОРМАТИЧНА ОСВІТНЯ ГАЛУЗЬ: ЯК ОЦІНЮВАТИ В НУШ» (5–9 КЛАСИ)

ОЦІНЮВАННЯ ВІДБУВАЄТЬСЯ ЗА 4 КЛЮЧОВИМИ АСПЕКТАМИ ТА ОБОВ'ЯЗКОВИМИ ГРУПАМИ РЕЗУЛЬТАТІВ (ГР 1-4):

1. ГРУПА РЕЗУЛЬТАТІВ 1 (ГР 1). Працює з інформацією, даними, моделями (теоретичні знання):
   • Оцінюється розуміння впливу ІТ на суспільство і власне життя, вміння ефективно працювати з даними, оцінювати достовірність джерел, будувати інформаційні моделі реальних об'єктів, явищ і процесів.
   • Шкала рівнів та балів (1-12):
     - 1-3 бали (Початковий): фрагментарні знання; не розрізняє або плутає об'єкти та їх властивості; відповіді неточні, з грубими логічними помилками.
     - 4-6 балів (Середній): називає основні об'єкти та базові властивості; знаходить прості зв'язки за допомогою вчителя або за зразком; формулює типові припущення.
     - 7-9 балів (Достатній): самостійно знаходить інформацію, визначає властивості; порівнює об'єкти за ознаками з різних джерел; обирає спосіб візуалізації (схеми, таблиці).
     - 10-12 балів (Високий): встановлює логічні зв'язки; критично оцінює інформацію за критеріями; обґрунтовує структуру й форму подання; робить узагальнені висновки та планує вдосконалення.

2. ГРУПА РЕЗУЛЬТАТІВ 2 (ГР 2). Створює інформаційні продукти (практичні уміння):
   • Оцінюється вміння складати лінійні, розгалужені та циклічні алгоритми; створювати і налагоджувати програмні проєкти (Python, Turtle, Scratch тощо); опрацьовувати дані різних типів (текст, таблиці, слайди, код); створювати цілісні інформаційні продукти самостійно або у співпраці.
   • Шкала рівнів та балів (1-12):
     - 1-3 бали (Початковий): створено лише чернетку без оформлення; не зміг написати код або створив лише порожню базову рамку без кольору чи форм.
     - 4-6 балів (Середній): готовий документ або проєкт за базовою структурою з помилками; програма працює частково або з порушенням кольорів/пропорцій.
     - 7-9 балів (Достатній): якісний продукт з власними формулюваннями; працюючий код у Python (Turtle) або іншій системі, алгоритми коректні, логічна побудова, дотримано базових пропорцій.
     - 10-12 балів (Високий): візуально й логічно довершений продукт з авторським стилем; повністю функціональна та оптимізована програма в Python, точне дотримання пропорцій та висока алгоритмічна культура.

3. ГРУПА РЕЗУЛЬТАТІВ 3 (ГР 3). Працює в цифровому середовищі (технічні навички):
   • Оцінюється усвідомлене використання цифрових пристроїв та ІКТ; організація власного цифрового середовища; збереження результатів у належних форматах (.py, .docx, .html, .xlsx тощо) з коректними назвами файлів згідно з інструкцією; використання мережних і хмарних сервісів.
   • Шкала рівнів та балів (1-12):
     - 1-3 бали (Початковий): файл не зберіг або зберіг у непридатному форматі; файл не відкривається; назва файлу хаотична або відсутня.
     - 4-6 балів (Середній): файл зберіг, але назва файлу лише частково відповідає вимогам або допущені помилки при виборі розширення/типу файлу.
     - 7-9 балів (Достатній): файл збережено у відповідному форматі, назва відповідає загальним вимогам інструкції або близька до них.
     - 10-12 балів (Високий): бездоганне збереження у належному форматі; повна відповідність назви файлу регламенту; вільне орієнтування у цифрових сервісах.

4. ГРУПА РЕЗУЛЬТАТІВ 4 (ГР 4). Безпечно та відповідально працює з інформаційними технологіями (етичне ставлення):
   • Оцінюється етична та безпечна взаємодія в цифровому просторі; дотримання авторського права та академічної доброчесності; обов'язкове зазначення перевірених джерел; захист персональних даних; правила безпеки паролів; протидія кібербулінгу й фішингу.
   • Шкала рівнів та балів (1-12):
     - 1-3 бали (Початковий): використовував неперевірені джерела; скопіював чужу роботу без зазначення автора; порушив правила цифрової безпеки.
     - 4-6 балів (Середній): окремі помилки в авторському праві чи безпеці; знає типові правила, але потребує нагадування або контролю.
     - 7-9 балів (Достатній): використано та коректно вказано перевірені джерела; роботу виконано самостійно з дотриманням цифрової етики та безпеки.
     - 10-12 балів (Високий): суворе дотримання норм етики та безпеки; чітке цитування джерел; самостійність, відповідальність, дотримання термінів; усвідомлений захист етичної позиції."""


class AICriteriaPreset(models.Model):
    """
    Шаблон критеріїв оцінювання для модуля ШІ (Google Gemini).
    Дозволяє обирати систему оцінювання:
    - Оцінювання за групами результатів НУШ (ГР1, ГР2, ГР3, ГР4)
    - Компетентнісне оцінювання НУШ
    - Класична 1-12 балів
    - Власні критерії вчителя та прикріплені файли МОН (.docx, .doc, .pdf, .xlsx, .txt, .md, .rtf).
    """
    EVAL_TYPE_CHOICES = [
        ('nus_gr', '🌟 Оцінювання за групами результатів НУШ (ГР 1-4)'),
        ('nus', '🌟 Загальне компетентнісне оцінювання за стандартами НУШ'),
        ('traditional', '📊 Класична / Традиційна система оцінювання (1-12 балів)'),
        ('custom', '📝 Власні критерії / Методичні рекомендації МОН'),
    ]

    name = models.CharField('Назва шаблону', max_length=200)
    description = models.TextField('Короткий опис', blank=True, default='')
    evaluation_type = models.CharField('Тип системи оцінювання', max_length=30, choices=EVAL_TYPE_CHOICES, default='nus_gr')
    system_prompt = models.TextField('Системний промт інструкції для ШІ', blank=True, default='')
    
    # Визначення груп результатів (JSON або список рядків)
    gr_definitions = models.TextField('Визначення груп результатів (JSON або перелік)', blank=True, default='')

    # Прикріплений файл з інструкцією / критеріями МОН (Word, PDF, Excel, TXT, MD, RTF)
    document_file = models.FileField('Документ із критеріями МОН (.docx, .pdf, .xlsx, .txt, .md)', upload_to='ai_criteria_docs/', blank=True, null=True)
    document_name = models.CharField('Назва файлу', max_length=255, blank=True, default='')
    extracted_criteria_text = models.TextField('Видобутий текст критеріїв із файлу', blank=True, default='')

    is_default = models.BooleanField('Активний за замовчуванням', default=False)
    is_system = models.BooleanField('Системний базовий шаблон', default=False)
    created_at = models.DateTimeField('Створено', auto_now_add=True)
    updated_at = models.DateTimeField('Оновлено', auto_now=True)

    class Meta:
        verbose_name = 'Шаблон критеріїв оцінювання ШІ'
        verbose_name_plural = 'Шаблони критеріїв оцінювання ШІ'
        ordering = ['-is_default', '-is_system', 'id']

    def __str__(self):
        star = " ⭐ (За замовчуванням)" if self.is_default else ""
        return f"{self.name}{star}"

    def get_gr_list(self):
        """Повертає структурований список груп результатів [{'code': 'ГР 1', 'name': '...'}]"""
        if self.evaluation_type == 'traditional':
            return []
        if self.gr_definitions and self.gr_definitions.strip():
            try:
                parsed = json.loads(self.gr_definitions)
                if isinstance(parsed, list) and len(parsed) > 0:
                    return parsed
            except Exception:
                pass
            lines = [l.strip() for l in self.gr_definitions.splitlines() if l.strip()]
            res = []
            for idx, line in enumerate(lines, 1):
                if ':' in line:
                    code, name = line.split(':', 1)
                    res.append({'code': code.strip(), 'name': name.strip()})
                elif ' - ' in line:
                    code, name = line.split(' - ', 1)
                    res.append({'code': code.strip(), 'name': name.strip()})
                else:
                    res.append({'code': f'ГР {idx}', 'name': line})
            if res:
                return res

        # Стандартні ГР за назвою або типом
        name_lower = self.name.lower()
        if 'інформат' in name_lower:
            return [
                {'code': 'ГР 1', 'name': 'Пошук, критична оцінка даних та безпека в цифровому просторі'},
                {'code': 'ГР 2', 'name': 'Створення інформаційних продуктів та програмування'},
                {'code': 'ГР 3', 'name': 'Алгоритмічне та критичне мислення / розв\'язування задач'}
            ]
        elif 'математ' in name_lower or 'алгебр' in name_lower or 'геометр' in name_lower:
            return [
                {'code': 'ГР 1', 'name': 'Дослідження проблемних ситуацій та математичне моделювання'},
                {'code': 'ГР 2', 'name': 'Розв\'язання математичних завдань і вправ'},
                {'code': 'ГР 3', 'name': 'Критичне осмислення та аргументація результатів'}
            ]
        elif 'мов' in name_lower or 'літератур' in name_lower or 'читан' in name_lower:
            return [
                {'code': 'ГР 1', 'name': 'Усна взаємодія (сприймання, аудіювання, діалог)'},
                {'code': 'ГР 2', 'name': 'Письмова взаємодія / Робота з текстом (читання та аналіз)'},
                {'code': 'ГР 3', 'name': 'Письмова продукція (створення власних текстів та есе)'},
                {'code': 'ГР 4', 'name': 'Дослідження мовлення та мовних закономірностей'}
            ]
        elif 'природ' in name_lower or 'фізик' in name_lower or 'хімі' in name_lower or 'біолог' in name_lower or 'географ' in name_lower:
            return [
                {'code': 'ГР 1', 'name': 'Проведення природничих досліджень та спостережень'},
                {'code': 'ГР 2', 'name': 'Опрацювання та систематизація наукової інформації'},
                {'code': 'ГР 3', 'name': 'Наукове пояснення явищ природи та розв\'язання проблем'}
            ]
        elif 'істор' in name_lower or 'громадян' in name_lower or 'суспільств' in name_lower:
            return [
                {'code': 'ГР 1', 'name': 'Орієнтування в історичному часі та просторі'},
                {'code': 'ГР 2', 'name': 'Критична робота з історичними джерелами та інформацією'},
                {'code': 'ГР 3', 'name': 'Встановлення причинно-наслідкових зв\'язків та висновки'}
            ]

        if self.evaluation_type == 'nus_gr':
            return [
                {'code': 'ГР 1', 'name': 'Опрацювання інформації та дослідження проблемних ситуацій'},
                {'code': 'ГР 2', 'name': 'Практична діяльність та виконання завдань'},
                {'code': 'ГР 3', 'name': 'Критичне осмислення, аргументація та формулювання висновків'},
                {'code': 'ГР 4', 'name': 'Академічна грамотність та дотримання стандартів предмету'}
            ]
        return []

    def extract_and_save_document_text(self):
        """Вилучає текст із прикріпленого файлу Word / PDF / Excel / TXT / MD тощо."""
        if not self.document_file:
            return
        try:
            from .document_parsers import extract_text_from_document
            file_src = self.document_file
            try:
                if hasattr(self.document_file, 'path') and os.path.exists(self.document_file.path):
                    file_src = self.document_file.path
            except Exception:
                file_src = self.document_file

            orig_name = self.document_name or (os.path.basename(self.document_file.name) if self.document_file.name else "")
            text, ok, err = extract_text_from_document(file_src, orig_name)
            if ok and text:
                self.extracted_criteria_text = text
                if not self.document_name and self.document_file.name:
                    self.document_name = os.path.basename(self.document_file.name)
        except Exception as e:
            print(f"Error extracting criteria text: {e}")

    def save(self, *args, **kwargs):
        # Якщо встановлено за замовчуванням, скидаємо інші
        if self.is_default:
            AICriteriaPreset.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)

        # Якщо системний промпт порожній, призначаємо згідно типу
        if not self.system_prompt:
            if self.evaluation_type == 'traditional':
                self.system_prompt = DEFAULT_TRADITIONAL_SYSTEM_PROMPT
            elif self.evaluation_type == 'nus_gr':
                self.system_prompt = DEFAULT_NUS_GR_SYSTEM_PROMPT
            else:
                self.system_prompt = DEFAULT_NUS_SYSTEM_PROMPT

        super().save(*args, **kwargs)

        # Якщо є прикріплений файл і текст ще не видобуто, вилучаємо
        if self.document_file and (not self.extracted_criteria_text or not self.document_name):
            try:
                self.extract_and_save_document_text()
                super().save(update_fields=['extracted_criteria_text', 'document_name'])
            except Exception:
                pass

    def get_full_prompt(self):
        """
        Повертає повний набір інструкцій для ШІ:
        Системний промпт + опис Груп Результатів (ГР) + додаткові критерії МОН із прикріпленого документа (якщо є).
        """
        prompt = (self.system_prompt or DEFAULT_NUS_GR_SYSTEM_PROMPT).strip()

        gr_list = self.get_gr_list()
        if gr_list and len(gr_list) > 0:
            prompt += f"\n\n═══════════════════════════════════════════════════════════════════\n"
            prompt += f"ОБОВ'ЯЗКОВІ ГРУПИ РЕЗУЛЬТАТІВ (ГР) ДЛЯ ДЕТАЛЬНОГО ОЦІНЮВАННЯ ЦІЄЇ РОБОТИ:\n"
            prompt += f"═══════════════════════════════════════════════════════════════════\n"
            for gr in gr_list:
                prompt += f"• {gr.get('code', 'ГР')}: {gr.get('name', '')}\n"
            prompt += f"\nОБОВ'ЯЗКОВО поверни у відповіді JSON масив 'gr_results' з оцінкою (1-12 балів), рівнем та коментарем для КОЖНОЇ з перелічених вище груп результатів!\n"
            prompt += f"═══════════════════════════════════════════════════════════════════\n"

        if self.extracted_criteria_text and self.extracted_criteria_text.strip():
            doc_label = f" «{self.document_name}»" if self.document_name else ""
            prompt += f"\n\n═══════════════════════════════════════════════════════════════════\n"
            prompt += f"ДОДАТКОВІ ОФІЦІЙНІ КРИТЕРІЇ ОЦІНЮВАННЯ ТА ВИМОГИ МОН (З ПРИКРІПЛЕНОГО ДОКУМЕНТА{doc_label}):\n"
            prompt += f"═══════════════════════════════════════════════════════════════════\n"
            prompt += self.extracted_criteria_text.strip()
            prompt += f"\n═══════════════════════════════════════════════════════════════════\n"
            prompt += f"ОБОВ'ЯЗКОВО суворо враховуй наведені вище критерії та інструкції з документу при виставленні оцінок за ГР, загальної оцінки та формулюванні відгуку!\n"
        return prompt

    @classmethod
    def ensure_default_presets(cls, force_recreate=False):
        """Гарантує наявність базових системних шаблонів за стандартами НУШ, групами результатів (ГР) та МОН."""
        # Якщо вже є хоча б один шаблон і не вимагається примусове відновлення -
        # НЕ перестворюємо видалені вчителем шаблони
        if not force_recreate and cls.objects.exists():
            if not cls.objects.filter(is_default=True).exists():
                first_p = cls.objects.first()
                if first_p:
                    first_p.is_default = True
                    first_p.save(update_fields=['is_default'])
            return

        # 1. Головний базовий шаблон за групами результатів (ГР 1-4)
        nus_gr_preset = cls.objects.filter(name__icontains="Оцінювання за групами результатів").first()
        if not nus_gr_preset:
            nus_gr_preset = cls.objects.create(
                name="🌟 Оцінювання за групами результатів НУШ (ГР 1-4)",
                description="Комплексне оцінювання за 4 обов'язковими групами результатів Держстандарту НУШ (Дослідження, Практична діяльність, Критичне мислення, Грамотність).",
                evaluation_type='nus_gr',
                system_prompt=DEFAULT_NUS_GR_SYSTEM_PROMPT,
                gr_definitions=json.dumps([
                    {'code': 'ГР 1', 'name': 'Опрацювання інформації та дослідження проблемних ситуацій'},
                    {'code': 'ГР 2', 'name': 'Практична діяльність та виконання навчальних завдань'},
                    {'code': 'ГР 3', 'name': 'Критичне осмислення, аргументація та міркування'},
                    {'code': 'ГР 4', 'name': 'Академічна грамотність та дотримання правил предмету'}
                ], ensure_ascii=False),
                is_default=False,
                is_system=True
            )

        # 2. Офіційні критерії МОН з Інформатики (ГР 1-4) за посібником НУШ
        info_gr_preset = cls.objects.filter(name__icontains="Інформатична освітня галузь").first()
        info_gr_defs = json.dumps([
            {'code': 'ГР 1', 'name': 'Працює з інформацією, даними, моделями (теоретичні знання)'},
            {'code': 'ГР 2', 'name': 'Створює інформаційні продукти (практичні уміння)'},
            {'code': 'ГР 3', 'name': 'Працює в цифровому середовищі (технічні навички)'},
            {'code': 'ГР 4', 'name': 'Безпечно та відповідально працює з інформаційними технологіями (етичне ставлення)'}
        ], ensure_ascii=False)

        if not info_gr_preset:
            info_gr_preset = cls.objects.create(
                name="🌟 НУШ: Інформатична освітня галузь (ГР 1-4)",
                description="Офіційні методичні рекомендації МОН України «Як оцінювати в НУШ»: 4 групи результатів (Інформація та моделі, Створення продуктів і код, Цифрове середовище, Безпека та етика).",
                evaluation_type='nus_gr',
                system_prompt=DEFAULT_NUS_GR_SYSTEM_PROMPT,
                gr_definitions=info_gr_defs,
                document_name="Посібник МОН: Як оцінювати в НУШ (Інформатика).pdf",
                extracted_criteria_text=MON_INFORMATICS_CRITERIA_TEXT,
                is_default=False,
                is_system=True
            )
        elif force_recreate:
            info_gr_preset.name = "🌟 НУШ: Інформатична освітня галузь (ГР 1-4)"
            info_gr_preset.description = "Офіційні методичні рекомендації МОН України «Як оцінювати в НУШ»: 4 групи результатів (Інформація та моделі, Створення продуктів і код, Цифрове середовище, Безпека та етика)."
            info_gr_preset.gr_definitions = info_gr_defs
            info_gr_preset.document_name = "Посібник МОН: Як оцінювати в НУШ (Інформатика).pdf"
            info_gr_preset.extracted_criteria_text = MON_INFORMATICS_CRITERIA_TEXT
            info_gr_preset.save()

        # 3. Математична галузь (ГР 1-3)
        if not cls.objects.filter(name__icontains="Математична освітня галузь").exists():
            cls.objects.create(
                name="🌟 НУШ: Математична освітня галузь (ГР 1-3)",
                description="Оцінювання з математики, алгебри та геометрії за групами результатів МОН: моделювання, розв'язання задач, критичне осмислення.",
                evaluation_type='nus_gr',
                system_prompt=DEFAULT_NUS_GR_SYSTEM_PROMPT,
                gr_definitions=json.dumps([
                    {'code': 'ГР 1', 'name': 'Дослідження проблемних ситуацій та математичне моделювання'},
                    {'code': 'ГР 2', 'name': 'Розв\'язання математичних завдань, вправ та рівнянь'},
                    {'code': 'ГР 3', 'name': 'Критичне осмислення та аргументація отриманих результатів'}
                ], ensure_ascii=False),
                is_default=False,
                is_system=True
            )

        # 4. Мовно-літературна галузь (ГР 1-4)
        if not cls.objects.filter(name__icontains="Мовно-літературна галузь").exists():
            cls.objects.create(
                name="🌟 НУШ: Мовно-літературна галузь (ГР 1-4)",
                description="Оцінювання з української мови, літератури та іноземних мов за 4 групами результатів (Усна взаємодія, Робота з текстом, Письмова продукція, Мовні явища).",
                evaluation_type='nus_gr',
                system_prompt=DEFAULT_NUS_GR_SYSTEM_PROMPT,
                gr_definitions=json.dumps([
                    {'code': 'ГР 1', 'name': 'Усна взаємодія (сприймання на слух, аудіювання, говоріння)'},
                    {'code': 'ГР 2', 'name': 'Письмова взаємодія / Робота з текстом (читання та аналіз)'},
                    {'code': 'ГР 3', 'name': 'Письмова продукція (створення власних текстів, висловлювань, есе)'},
                    {'code': 'ГР 4', 'name': 'Дослідження мовлення, орфографічна та пунктуаційна грамотність'}
                ], ensure_ascii=False),
                is_default=False,
                is_system=True
            )

        # 5. Базовий шаблон НУШ (загальний)
        nus_preset = cls.objects.filter(evaluation_type='nus', name__icontains="Загальне компетентнісне").first()
        if not nus_preset:
            nus_preset = cls.objects.create(
                name="🌟 Оцінювання за стандартами НУШ (Загальне компетентнісне)",
                description="Компетентнісний підхід, 4 рівні (Початковий, Середній, Достатній, Високий), формувальне оцінювання та мотиваційний відгук.",
                evaluation_type='nus',
                system_prompt=DEFAULT_NUS_SYSTEM_PROMPT,
                is_default=True,
                is_system=True
            )

        # 6. Традиційна система 1-12
        trad_preset = cls.objects.filter(evaluation_type='traditional').first()
        if not trad_preset:
            cls.objects.create(
                name="📊 Класична / Традиційна система оцінювання (1-12 балів)",
                description="Традиційна шкала 1-12 балів МОН: оцінювання за точністю, повнотою розв'язку, аналізом грубих і негрубих помилок.",
                evaluation_type='traditional',
                system_prompt=DEFAULT_TRADITIONAL_SYSTEM_PROMPT,
                is_default=False,
                is_system=True
            )

        # Якщо жоден не встановлено як дефолтний, робимо перший наявний дефолтним
        if not cls.objects.filter(is_default=True).exists():
            first_p = cls.objects.first()
            if first_p:
                first_p.is_default = True
                first_p.save(update_fields=['is_default'])





