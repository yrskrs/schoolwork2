"""Durable AI work and self-check reservations, shared by every web process."""
import uuid

from django.conf import settings
from django.db import models


class AIJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=24)
    status = models.CharField(max_length=16, default='queued', db_index=True)
    submission = models.ForeignKey('feed.Submission', null=True, blank=True, on_delete=models.CASCADE)
    assignment = models.ForeignKey('feed.Assignment', null=True, blank=True, on_delete=models.CASCADE)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    parameters = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    http_status = models.PositiveSmallIntegerField(default=200)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [models.UniqueConstraint(
            fields=['submission'], condition=models.Q(status__in=['queued', 'running']),
            name='one_active_ai_job_per_submission',
        ), models.UniqueConstraint(
            fields=['assignment'], condition=models.Q(kind='understanding', status__in=['queued', 'running']),
            name='one_active_ai_understanding_per_assignment',
        )]


class StudentAICheckReservation(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    assignment = models.ForeignKey('feed.Assignment', on_delete=models.CASCADE)
    job = models.ForeignKey(AIJob, null=True, blank=True, on_delete=models.SET_NULL, related_name='reservations')
    used = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Спроба самоперевірки ШІ'
