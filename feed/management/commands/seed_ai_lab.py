"""Create the explicitly isolated local AI laboratory roster, without assignments."""
import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from feed.models import AICriteriaPreset, ClassGroup, School, Student, Subject, Teacher


class Command(BaseCommand):
    help = 'Створити тестового вчителя та 7 класів по 5 учнів у локальній AI-лабораторії.'

    @transaction.atomic
    def handle(self, *args, **options):
        if os.environ.get('SCHOOLNET_LOCAL_LAB') != '1':
            raise CommandError('Дозволено лише в середовищі SCHOOLNET_LOCAL_LAB=1.')
        password = os.environ.get('LAB_TEACHER_PASSWORD', '')
        if len(password) < 12:
            raise CommandError('Задайте LAB_TEACHER_PASSWORD довжиною від 12 символів.')
        user, created = User.objects.get_or_create(username='teacher_lab')
        if created:
            user.set_password(password)
            user.first_name = 'Тестовий'
            user.last_name = 'Учитель'
            user.is_staff = user.is_superuser = True
            user.save()
        elif not user.check_password(password):
            raise CommandError('Пароль чинного teacher_lab відрізняється; його не змінено.')
        teacher, _ = Teacher.objects.get_or_create(
            user=user, defaults={'full_name': 'Тестовий учитель ШІ-лабораторії'})
        School.objects.get_or_create(name='Локальна ШІ-лабораторія', defaults={'admin': user})
        subject, _ = Subject.objects.get_or_create(
            name='Інформатика', defaults={'icon': '💻', 'color': '#3b82f6'})
        teacher.subjects.add(subject)
        names = [('Анна', 'Бондаренко'), ('Максим', 'Коваль'), ('Софія', 'Мельник'),
                 ('Данило', 'Шевченко'), ('Марія', 'Ткаченко')]
        for grade in range(5, 12):
            group, _ = ClassGroup.objects.get_or_create(
                name=f'{grade}-А', defaults={'grade': grade, 'letter': 'А', 'created_by': teacher})
            teacher.classes.add(group)
            for first_name, last_name in names:
                Student.objects.get_or_create(
                    class_group=group, first_name=first_name, last_name=last_name,
                    defaults={'notes': 'Вигаданий учень локальної ШІ-лабораторії.'})
            if group.students.count() != 5:
                raise CommandError(f'{group.name}: кількість учнів відрізняється від 5; дані не видалено.')
        AICriteriaPreset.ensure_default_presets()
        self.stdout.write(self.style.SUCCESS(
            'Готово: teacher_lab; 7 класів (5-А … 11-А), по 5 учнів. Завдання не створювалися.'))
