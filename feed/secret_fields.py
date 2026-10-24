"""One-way source tokens. The original key exists only in its creation response."""
import hashlib

from django.db import models

PREFIX = 'sha256$'


def token_hash(value):
    return PREFIX + hashlib.sha256(value.encode()).hexdigest()


class HashedTokenField(models.CharField):
    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value or value.startswith(PREFIX):
            return value
        return token_hash(value)
