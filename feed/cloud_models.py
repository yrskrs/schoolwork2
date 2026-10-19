"""Durable imports: source references and integrity evidence, never credentials."""
import uuid
from django.db import models
from django.utils import timezone


class CloudImport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    teacher = models.ForeignKey('feed.Teacher', on_delete=models.CASCADE)
    provider = models.CharField(max_length=16)
    source = models.CharField(max_length=1500)
    request_key = models.CharField(max_length=80)
    status = models.CharField(max_length=16, default='queued', db_index=True)
    filename = models.CharField(max_length=500, blank=True)
    content_type = models.CharField(max_length=200, blank=True)
    bytes_received = models.PositiveBigIntegerField(default=0)
    total_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    source_verified = models.BooleanField(default=False)
    exported = models.BooleanField(default=False)
    error = models.CharField(max_length=600, blank=True)
    error_code = models.CharField(max_length=24, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    lease = models.UUIDField(null=True, blank=True)
    assignment_file = models.ForeignKey('feed.AssignmentFile', null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [models.UniqueConstraint(fields=['teacher', 'request_key'], name='unique_cloud_import_request')]
