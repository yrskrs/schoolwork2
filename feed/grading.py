"""Validate grades before any member of a group is updated."""
from django.core.exceptions import ValidationError


TEXT_GRADES = {'доопрацювати', 'доопрацювання', 'д', 'н', 'н/а', 'не атестовано',
               'зараховано', 'не зараховано', 'залік', 'незалік', 'зв', 'звільнено'}


def validate_grade(value):
    value = str(value or '').strip()
    if not value or value.casefold() in TEXT_GRADES:
        return value
    if value.isascii() and value.isdigit() and 1 <= int(value) <= 12:
        return str(int(value))
    raise ValidationError('Оцінка має бути цілим числом від 1 до 12 або підтримуваним статусом.')


def member_grades(data, members, default):
    default = validate_grade(default)
    return {member.pk: validate_grade(data.get(f'member_grade_{member.pk}', '').strip() or default)
            for member in members}
