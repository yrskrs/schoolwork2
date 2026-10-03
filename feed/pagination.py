"""Page ordered QuerySets without materializing the entire school feed."""
from django.db.models import QuerySet
from django.utils.functional import cached_property


class AssignmentChain:
    ordered = True

    def __init__(self, *parts):
        self.parts = parts

    @cached_property
    def lengths(self):
        return [part.count() if isinstance(part, QuerySet) else len(part) for part in self.parts]

    def count(self):
        return sum(self.lengths)

    def __len__(self):
        return self.count()

    def __getitem__(self, key):
        if not isinstance(key, slice):
            result = self[key:key + 1]
            if not result:
                raise IndexError(key)
            return result[0]
        start, stop, step = key.indices(self.count())
        result, offset = [], 0
        for part, length in zip(self.parts, self.lengths):
            left, right = max(0, start - offset), min(length, stop - offset)
            if left < right:
                result.extend(part[left:right])
            offset += length
            if offset >= stop:
                break
        return result[::step]

    def __iter__(self):
        for part in self.parts:
            yield from part
