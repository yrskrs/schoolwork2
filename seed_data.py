"""
Скрипт початкового заповнення бази даних SchoolNet.

Запуск:
    python manage.py shell < seed_data.py
або через management command:
    python manage.py runscript seed  (якщо встановлено django-extensions)

Що створює:
    - Предмети (Математика, Фізика, Хімія, Інформатика, Українська мова...)
    - Класи (9А, 9Б, 10А, 10Б, 11А)
    - Облікові записи вчителів
    - Тестові завдання
"""

import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'schoolnet.settings')
django.setup()

from django.contrib.auth.models import User
from feed.models import Subject, ClassGroup, Teacher, Assignment, AICriteriaPreset, AISettings
from django.utils import timezone

print("🚀 Заповнення бази даних SchoolNet...")

# ── Предмети ──────────────────────────────────────────────────────────────────
subjects_data = [
    {'name': 'Математика', 'icon': '📐', 'color': '#3b82f6'},
    {'name': 'Фізика', 'icon': '⚡', 'color': '#8b5cf6'},
    {'name': 'Хімія', 'icon': '🧪', 'color': '#10b981'},
    {'name': 'Біологія', 'icon': '🧬', 'color': '#06b6d4'},
    {'name': 'Інформатика', 'icon': '💻', 'color': '#6366f1'},
    {'name': 'Українська мова', 'icon': '📖', 'color': '#f59e0b'},
    {'name': 'Українська література', 'icon': '📚', 'color': '#ef4444'},
    {'name': 'Англійська мова', 'icon': '🌍', 'color': '#ec4899'},
    {'name': 'Географія', 'icon': '🗺️', 'color': '#84cc16'},
    {'name': 'Історія', 'icon': '🏛️', 'color': '#f97316'},
    {'name': 'Мистецтво', 'icon': '🎨', 'color': '#e879f9'},
    {'name': 'Фізична культура', 'icon': '⚽', 'color': '#14b8a6'},
]

subjects = {}
for data in subjects_data:
    s, created = Subject.objects.get_or_create(name=data['name'], defaults=data)
    subjects[data['name']] = s
    print(f"  {'✅ Створено' if created else '⏭ Вже є'}: {s}")

# ── Класи ─────────────────────────────────────────────────────────────────────
classes_data = [
    {'name': '9А', 'grade': 9, 'letter': 'А'},
    {'name': '9Б', 'grade': 9, 'letter': 'Б'},
    {'name': '10А', 'grade': 10, 'letter': 'А'},
    {'name': '10Б', 'grade': 10, 'letter': 'Б'},
    {'name': '11А', 'grade': 11, 'letter': 'А'},
    {'name': '11Б', 'grade': 11, 'letter': 'Б'},
]

classes = {}
for data in classes_data:
    c, created = ClassGroup.objects.get_or_create(name=data['name'], defaults=data)
    classes[data['name']] = c
    print(f"  {'✅ Створено' if created else '⏭ Вже є'}: {c}")

# ── Адміністратор ─────────────────────────────────────────────────────────────
admin_user, admin_created = User.objects.get_or_create(username='admin')
if admin_created:
    admin_user.set_password('SchoolNet2024')
    admin_user.is_staff = True
    admin_user.is_superuser = True
    admin_user.save()
    print(f"  ✅ Створено адміністратора: admin (пароль: SchoolNet2024)")
else:
    print("  ⏭ Адміністратор admin вже існує")

# Ініціалізація стандартних критеріїв НУШ
AICriteriaPreset.ensure_default_presets()
print("  ✅ Стандартні критерії НУШ перевірено/створено")

# ── Вчителі ───────────────────────────────────────────────────────────────────
teachers_data = [
    {
        'username': 'teacher',
        'password': 'school2026',
        'full_name': 'Коваленко Ольга Іванівна',
        'subjects': ['Математика', 'Інформатика'],
        'classes': ['9А', '9Б', '10А', '10Б', '11А', '11Б'],
        'avatar_color': '#6366f1',
    },
    {
        'username': 'teacher2',
        'password': 'school2026',
        'full_name': 'Шевченко Микола Петрович',
        'subjects': ['Фізика', 'Хімія'],
        'classes': ['10А', '10Б', '11А'],
        'avatar_color': '#8b5cf6',
    },
]

teachers = {}
for data in teachers_data:
    user, created = User.objects.get_or_create(username=data['username'])
    if created:
        user.set_password(data['password'])
        user.save()

    teacher, _ = Teacher.objects.get_or_create(
        user=user,
        defaults={
            'full_name': data['full_name'],
            'avatar_color': data['avatar_color'],
        }
    )
    teacher.subjects.set([subjects[s] for s in data['subjects'] if s in subjects])
    teacher.classes.set([classes[c] for c in data['classes'] if c in classes])
    teachers[data['username']] = teacher
    print(f"  {'✅ Створено' if created else '⏭ Вже є'}: {teacher} (логін: {data['username']}, пароль: {data['password']})")

# ── Тестові завдання ──────────────────────────────────────────────────────────
if Assignment.objects.count() == 0:
    teacher1 = teachers.get('teacher')
    teacher2 = teachers.get('teacher2')

    # Завдання 1
    a1 = Assignment.objects.create(
        teacher=teacher1,
        subject=subjects.get('Математика'),
        title='Параграф 12 — Квадратні рівняння. Домашнє завдання',
        description='Розв\'язати задачі з параграфу 12:\n\n'
                    '1. Вправа 5 (всі пункти)\n'
                    '2. Задача 8 на стор. 145\n'
                    '3. Скласти власне рівняння та розв\'язати його\n\n'
                    'Термін здачі: до п\'ятниці.',
        status=Assignment.STATUS_PUBLISHED,
        published_at=timezone.now(),
    )
    a1.classes.set([classes['9А'], classes['9Б']])
    print(f"  ✅ Завдання: {a1}")

    # Завдання 2
    a2 = Assignment.objects.create(
        teacher=teacher1,
        subject=subjects.get('Інформатика'),
        title='Практична робота №3 — Алгоритми сортування',
        description='Реалізуйте на мові Python наступні алгоритми:\n\n'
                    '• Bubble Sort (бульбашкове сортування)\n'
                    '• Selection Sort (сортування вибором)\n'
                    '• Порівняйте їх швидкість на масивах різних розмірів\n\n'
                    'Збережіть код у файл sorting.py та принесіть на наступний урок.',
        status=Assignment.STATUS_PUBLISHED,
        published_at=timezone.now(),
        link_url='https://uk.wikipedia.org/wiki/Алгоритм_сортування',
        link_label='Теоретичний матеріал (Вікіпедія)',
    )
    a2.classes.set([classes['10А'], classes['10Б']])
    print(f"  ✅ Завдання: {a2}")

    # Завдання 3 — індивідуальне
    a3 = Assignment.objects.create(
        teacher=teacher2,
        subject=subjects.get('Фізика'),
        title='Індивідуальна задача — Закони Ньютона',
        description='Розв\'яжіть задачу підвищеного рівня складності:\n\n'
                    'Тіло масою 5 кг рухається під дією сили 20 Н. Знайдіть прискорення '
                    'тіла та відстань, яку воно пройде за 3 секунди починаючи з стану спокою.',
        status=Assignment.STATUS_PUBLISHED,
        published_at=timezone.now(),
        is_individual=True,
        student_name='Мельник Артем Олексійович',
    )
    a3.classes.set([classes['10А']])
    print(f"  ✅ Завдання (індив.): {a3}")

    # Завдання 4 — чернетка
    a4 = Assignment.objects.create(
        teacher=teacher1,
        subject=subjects.get('Математика'),
        title='[ЧЕРНЕТКА] Контрольна робота — Геометрія',
        description='Питання до контрольної роботи. РЕДАГУВАТИ ПЕРЕД ПУБЛІКАЦІЄЮ!',
        status=Assignment.STATUS_DRAFT,
    )
    a4.classes.set([classes['11А'], classes['11Б']])
    print(f"  📝 Чернетка: {a4}")

    print("\n✅ Тестові завдання створено!")
else:
    print("  ⏭ Завдання вже існують, пропускаємо")

print("\n🎉 Готово! Дані успішно завантажено.")
print("\n📌 Облікові дані вчителів:")
print("   Логін: teacher    | Пароль: school2026  (Математика, Інформатика)")
print("   Логін: teacher2   | Пароль: school2026  (Фізика, Хімія)")
print("\n🌐 Запуск: python manage.py runserver 0.0.0.0:8000")
print("   Стрічка: http://localhost:8000/")
print("   Панель:  http://localhost:8000/teacher/login/")
