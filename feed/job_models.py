"""Durable AI work and self-check reservations, shared by every web process."""
import uuid

from django.conf import settings
from django.db import models


class AIResourceState(models.Model):
    """Small transactional coordination rows; never contain prompts or API keys."""
    key = models.CharField(max_length=100, primary_key=True)
    data = models.JSONField(default=dict)


class AIBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)


class AIJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=24)
    status = models.CharField(max_length=16, default='queued', db_index=True)
    submission = models.ForeignKey('feed.Submission', null=True, blank=True, on_delete=models.CASCADE)
    assignment = models.ForeignKey('feed.Assignment', null=True, blank=True, on_delete=models.CASCADE)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    parameters = models.JSONField(default=dict)
    batch = models.ForeignKey(AIBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name='jobs')
    metrics = models.JSONField(default=dict)
    not_before = models.DateTimeField(null=True, blank=True, db_index=True)
    result = models.JSONField(default=dict)
    events = models.JSONField(default=list)
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
