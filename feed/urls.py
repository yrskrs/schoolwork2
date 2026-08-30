"""Маршрути застосунку feed для SchoolNet+."""

from django.urls import path
from . import views

urlpatterns = [
    # ── Публічна частина ──────────────────────────────────────────────────────
    path('', views.index, name='index'),
    path('feed/fragment/', views.feed_fragment, name='feed_fragment'),
    path('feed/calendar/', views.calendar_fragment, name='calendar_fragment'),
    path('feed/check-updates/', views.feed_check_updates, name='feed_check_updates'),
    path('api/server-stats/', views.server_stats, name='server_stats'),
    path('api/heartbeat/', views.client_heartbeat, name='client_heartbeat'),
    path('api/client-disconnect/', views.client_disconnect, name='client_disconnect'),
    path('assignment/<int:pk>/', views.assignment_detail, name='assignment_detail'),
    path('clear-class/', views.clear_class_filter, name='clear_class_filter'),

    # ── Здача робіт та кабінет учня ───────────────────────────────────────────
    path('assignment/<int:pk>/submit/', views.submit_assignment, name='submit_assignment'),
    path('assignment/<int:pk>/submit/success/', views.submit_success, name='submit_success'),
    path('submission/<int:submission_id>/', views.submission_detail, name='submission_detail'),
    path('my-submissions/', views.student_submissions_portal, name='student_submissions_portal'),


    # ── Авторизація вчителя ───────────────────────────────────────────────────
    path('teacher/login/', views.teacher_login, name='teacher_login'),
    path('teacher/logout/', views.teacher_logout, name='teacher_logout'),

    # ── Панель вчителя ────────────────────────────────────────────────────────
    path('teacher/', views.teacher_dashboard, name='teacher_dashboard'),
    path('teacher/settings/', views.teacher_settings_view, name='teacher_settings'),
    path('teacher/profile/', views.teacher_profile, name='teacher_profile'),

    # ── CRUD завдань ──────────────────────────────────────────────────────────
    path('teacher/assignment/create/', views.assignment_create, name='assignment_create'),
    path('teacher/assignment/<int:pk>/edit/', views.assignment_edit, name='assignment_edit'),
    path('teacher/assignment/<int:pk>/delete/', views.assignment_delete, name='assignment_delete'),
    path('teacher/assignment/<int:pk>/duplicate/', views.assignment_duplicate, name='assignment_duplicate'),
    path('teacher/assignment/<int:pk>/archive/', views.assignment_archive, name='assignment_archive'),
    path('teacher/assignment/<int:pk>/unarchive/', views.assignment_unarchive, name='assignment_unarchive'),
    path('teacher/assignment/<int:pk>/publish/', views.assignment_publish, name='assignment_publish'),

    # ── Перегляд та перевірка робіт (File Viewer & Dashboard) ─────────────────
    path('teacher/assignment/<int:pk>/submissions/', views.assignment_submissions, name='assignment_submissions'),
    path('teacher/submissions/', views.all_submissions_dashboard, name='all_submissions_dashboard'),
    path('teacher/view-file/<int:submission_id>/', views.view_file, name='view_file'),
    path('teacher/submission/<int:submission_id>/file/', views.view_submission_raw_file, name='view_submission_raw_file'),
    path('teacher/comment/delete/<int:comment_id>/', views.delete_comment, name='delete_comment'),

    # ── Оцінювання та коментарі (AJAX) ───────────────────────────────────────
    path('teacher/submission/<int:sub_id>/grade/', views.grade_submission, name='grade_submission'),
    path('teacher/submission/<int:sub_id>/comment/', views.add_submission_comment, name='add_submission_comment'),
    path('teacher/submission/<int:sub_id>/delete/', views.delete_submission, name='delete_submission'),

    # ── Електронний журнал оцінок та експорт у CSV ───────────────────────────
    path('teacher/gradebook/', views.gradebook, name='gradebook'),
    path('teacher/export/', views.export_grades, name='export_grades'),

    # ── Історія учня ──────────────────────────────────────────────────────────
    path('teacher/student/<str:student_name>/', views.student_detail, name='student_detail'),

    # ── Керування профілями учнів та імпорт класів ───────────────────────────
    path('teacher/students/', views.teacher_students, name='teacher_students'),
    path('teacher/students/<int:student_id>/edit/', views.teacher_student_edit, name='teacher_student_edit'),
    path('teacher/students/<int:student_id>/delete/', views.teacher_student_delete, name='teacher_student_delete'),
    path('teacher/students/import/', views.teacher_students_import, name='teacher_students_import'),
    path('teacher/students/export/', views.teacher_students_export, name='teacher_students_export'),

    # ── Журнал дій (Activity Log) та архіви логів ─────────────────────────────
    path('teacher/activity/', views.activity_log, name='activity_log'),
    path('teacher/activity/archive-now/', views.trigger_log_archive, name='trigger_log_archive'),
    path('teacher/activity/archives/<str:filename>/view/', views.view_log_archive, name='view_log_archive'),
    path('teacher/activity/archives/<str:filename>/download/', views.download_log_archive, name='download_log_archive'),

    # ── Керування закладом та вчителями ───────────────────────────────────────
    path('teacher/profiles/', views.teacher_profiles_view, name='teacher_profiles'),
    path('teacher/profiles/<int:profile_id>/edit/', views.teacher_profile_edit, name='teacher_profile_edit'),
    path('teacher/profiles/<int:profile_id>/delete/', views.teacher_profile_delete, name='teacher_profile_delete'),
    path('teacher/users/<int:user_id>/toggle-superadmin/', views.toggle_superadmin, name='toggle_superadmin'),
    path('teacher/school/settings/', views.school_settings, name='school_settings'),
    path('teacher/superadmin-mode/enter/', views.enter_superadmin_mode, name='enter_superadmin_mode'),
    path('teacher/superadmin-mode/exit/', views.exit_superadmin_mode, name='exit_superadmin_mode'),

    # ── Перегляд файлів завдання (матеріалів вчителя) ─────────────────────────
    path('assignment/file/<int:file_id>/preview/', views.file_preview, name='file_preview'),
    path('assignment/file/<int:file_id>/view/', views.file_view, name='file_view'),

    # ── Сервісні інструменти та оптимізації ────────────────────────────────────
    path('api/students-autocomplete/', views.api_students_autocomplete, name='api_students_autocomplete'),
    path('teacher/assignment/<int:pk>/download-zip/', views.download_assignment_submissions_zip, name='download_assignment_submissions_zip'),
    path('teacher/submission/<int:sub_id>/download-all/', views.download_submission_files_zip, name='download_submission_files_zip'),
    path('teacher/database/backup/', views.download_database_backup, name='download_database_backup'),

    # ── Модуль штучного інтелекту Google Gemini AI ─────────────────────────────
    path('teacher/ai-settings/', views.ai_settings_view, name='ai_settings'),
    path('teacher/ai/batch-check/', views.ai_batch_check_view, name='ai_batch_check'),
    path('teacher/ai/check/<int:submission_id>/', views.ai_check_single_submission, name='ai_check_single_submission'),
    path('teacher/ai/apply/<int:submission_id>/', views.ai_apply_suggested_grade, name='ai_apply_suggested_grade'),
    path('api/ai/test-connection/', views.api_test_gemini_connection, name='api_test_gemini_connection'),
    path('api/ai/get-batch-queue/', views.api_ai_get_batch_queue, name='api_ai_get_batch_queue'),
    path('api/ai/process-item/<int:submission_id>/', views.api_ai_process_item, name='api_ai_process_item'),

    # ── Самоперевірка учня (ШІ) ──────────────────────────────────────────────────────
    path('submission/<int:submission_id>/student-ai-check/', views.student_ai_self_check, name='student_ai_self_check'),
    path('teacher/submission/<int:sub_id>/accept-student-ai/', views.accept_student_ai_grade, name='accept_student_ai_grade'),
]
