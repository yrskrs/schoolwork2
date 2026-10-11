"""Native class identity applies to both optional roster protocols."""


def has_stable_class_roster(group):
    from .models import RosterReplica, RosterState
    if not group:
        return False
    state = RosterState.objects.filter(pk=1).values_list('data', flat=True).first() or {}
    return any(row.get('class_id') == group.pk for row in state.get('journal_imports', {}).values()) or RosterReplica.objects.filter(data__kind='class', data__native_id=group.pk).exists()
