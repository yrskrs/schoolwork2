"""Optional public access. Secrets are independent of pupil/class identifiers."""
import secrets
from django.db import models
from django.utils import timezone


def access_token():
    return secrets.token_urlsafe(32)


class TailscaleConnection(models.Model):
    # Singleton; absent row means the optional integration has never been used.
    enabled = models.BooleanField(default=False)
    public_url = models.URLField(blank=True)
    allow_without_link = models.BooleanField('Не вимагати посилання доступу', default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class AccessLink(models.Model):
    KINDS = [('site', 'Увесь сайт'), ('class', 'Клас'),
             ('student', 'Персональне посилання учня'), ('assignment', 'Одне завдання')]
    teacher = models.ForeignKey('feed.Teacher', on_delete=models.CASCADE, related_name='access_links')
    name = models.CharField('Назва', max_length=160)
    kind = models.CharField('Доступ', max_length=16, choices=KINDS, default='site')
    token = models.CharField(max_length=64, unique=True, default=access_token, editable=False)
    class_group = models.ForeignKey('feed.ClassGroup', null=True, blank=True, on_delete=models.SET_NULL)
    student = models.ForeignKey('feed.Student', null=True, blank=True, on_delete=models.SET_NULL)
    assignment = models.ForeignKey('feed.Assignment', null=True, blank=True, on_delete=models.SET_NULL)
    expires_at = models.DateTimeField('Діє до', null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    visits = models.PositiveBigIntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    @property
    def active(self):
        if self.revoked_at or (self.expires_at and self.expires_at <= timezone.now()):
            return False
        if self.kind == 'class':
            return bool(self.class_group_id and self.class_group.integration_active)
        if self.kind == 'student':
            return bool(self.student_id and self.student.integration_active and self.student.class_group.integration_active)
        return self.kind == 'site' or bool(self.assignment_id)

    @property
    def restricted_class_id(self):
        if self.kind == 'student' and self.student_id:
            return self.student.class_group_id
        return self.class_group_id if self.kind == 'class' else None


class AccessVisitor(models.Model):
    link = models.ForeignKey(AccessLink, on_delete=models.CASCADE, related_name='browsers')
    browser_hash = models.CharField(max_length=64)
    first_seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['link', 'browser_hash'], name='access_link_browser_unique')]


class AccessLinkEvent(models.Model):
    """Audit trail contains no bearer token or student names."""
    link = models.ForeignKey(AccessLink, null=True, on_delete=models.SET_NULL)
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)
