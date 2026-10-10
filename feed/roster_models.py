from django.db import models


class RosterReplica(models.Model):
    ref = models.CharField(max_length=100, primary_key=True)
    data = models.JSONField(default=dict)


class RosterState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    data = models.JSONField(default=dict)
