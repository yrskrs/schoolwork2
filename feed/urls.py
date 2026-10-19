"""Маршрути застосунку feed для SchoolNet+."""

from django.urls import path
from . import views, outside_students
from . import journal_api
from . import school_api_v2
from . import views_updates
from . import python_runner
from . import views_cloud
from .views_dialogs import ui_dialog
from .submission_notifications import submission_notifications
from school_sync import django_backend as roster_views

urlpatterns = [
    path('api/submission-notifications/', submission_notifications, name='submission_notifications'),
    path('ui/dialog/<str:kind>/', ui_dialog, name='ui_dialog'),
    path('healthz/', views_updates.healthz_view, name='healthz'),
    path('teacher/updates/', views_updates.system_updates_view, name='system_updates'),
    path('teacher/updates/api/check/', views_updates.updates_check_api_view, name='updates_check_api'),
    path('teacher/updates/api/start/', views_updates.updates_start_api_view, name='updates_start_api'),
    path('teacher/updates/api/status/', views_updates.updates_status_api_view, name='updates_status_api'),
    path('teacher/updates/api/rollback/', views_updates.updates_rollback_api_view, name='updates_rollback_api'),
    path("api/ai/jobs/<uuid:job_id>/", views.ai_job_status, name="ai_job_status"),
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
    path('api/classes/', views.api_classes_list, name='api_classes_list'),
    path('api/teacher-schedules/', views.api_teacher_schedules, name='api_teacher_schedules'),

    # ── Здача робіт та кабінет учня ───────────────────────────────────────────
    path('assignment/<int:pk>/submit/', views.submit_assignment, name='submit_assignment'),
    path('assignment/<int:pk>/submit/success/', views.submit_success, name='submit_success'),
    path('submission/<int:submission_id>/', views.submission_detail, name='submission_detail'),
    path('my-submissions/', views.student_submissions_portal, name='student_submissions_portal'),
    path('my-submissions/alias/', views.student_submissions_portal, name='my_submissions'),


    # ── Майстер першого запуску та авторизація вчителя ───────────────────────
    path('setup/', views.first_run_setup, name='first_run_setup'),
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
    path('teacher/assignment/<int:pk>/ai-understanding/', views.assignment_ai_understanding, name='assignment_ai_understanding'),
    path('assignment/<int:pk>/ai-understanding/', views.assignment_ai_understanding, name='assignment_ai_understanding_student'),

    # ── Перегляд та перевірка робіт (File Viewer & Dashboard) ─────────────────
    path('teacher/assignment/<int:pk>/submissions/', views.assignment_submissions, name='assignment_submissions'),
    path('teacher/submissions/', views.all_submissions_dashboard, name='all_submissions_dashboard'),
    path('teacher/view-file/<int:submission_id>/', views.view_file, name='view_file'),
    path('teacher/submission/<int:submission_id>/python/', python_runner.python_run, name='python_run'),
    path('teacher/submission/<int:submission_id>/preview/', views.review_document_preview, name='review_document_preview'),
    path('teacher/submission/<int:submission_id>/preview/assets/<str:asset_name>/', views.review_preview_asset, name='review_preview_asset'),
    path('teacher/submission/<int:submission_id>/duplicates/', views.review_duplicates, name='review_duplicates'),
    path('teacher/submission/<int:submission_id>/file/', views.view_submission_raw_file, name='view_submission_raw_file'),
    path('teacher/submission/<int:submission_id>/html/<str:token>/<path:sub_path>', views.submission_html_asset, name='submission_html_asset'),
    path('teacher/submission/<int:submission_id>/file/<path:sub_path>', views.view_submission_raw_file, name='view_submission_raw_file_asset'),
    path('teacher/submission/<int:submission_id>/web-proxy/', views.view_submission_web_proxy, name='view_submission_web_proxy'),
    path('teacher/comment/delete/<int:comment_id>/', views.delete_comment, name='delete_comment'),

    # ── Оцінювання та коментарі (AJAX) ───────────────────────────────────────
    path('teacher/submission/<int:sub_id>/grade/', views.grade_submission, name='grade_submission'),
    path('teacher/submission/<int:sub_id>/coauthors/', views.teacher_submission_coauthors, name='teacher_submission_coauthors'),
    path('teacher/submissions/mass-grade/', views.mass_grade_submissions, name='mass_grade_submissions'),
    path('teacher/submission/<int:sub_id>/comment/', views.add_submission_comment, name='add_submission_comment'),
    path('teacher/submission/<int:sub_id>/toggle-ignore-plagiarism/', views.toggle_submission_ignore_plagiarism, name='toggle_submission_ignore_plagiarism'),
    path('teacher/submission/<int:sub_id>/delete/', views.delete_submission, name='delete_submission'),
    path('api/teacher/live-status/', views.teacher_live_status, name='teacher_live_status'),

    # ── Електронний журнал оцінок та експорт у CSV / PDF ─────────────────────
    path('teacher/gradebook/', views.gradebook, name='gradebook'),
    path('teacher/export/', views.export_grades, name='export_grades'),
    path('teacher/reports/', views.teacher_reports, name='teacher_reports'),
    path('teacher/reports/pdf/', views.teacher_reports_pdf, name='teacher_reports_pdf'),

    # ── Історія учня ──────────────────────────────────────────────────────────
    path('teacher/student/<str:student_name>/', views.student_detail, name='student_detail'),

    # ── Керування профілями учнів та імпорт класів ───────────────────────────
    path('teacher/students/', views.teacher_students, name='teacher_students'),
    path('teacher/outside-students/', outside_students.overview, name='outside_students'),
    path('teacher/students/merge/', views.teacher_student_merge, name='teacher_student_merge'),
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
    path('assignment/file/<int:file_id>/download/', views.file_download, name='file_download'),

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
    path('teacher/assignment/generate-criteria/', views.teacher_generate_assignment_criteria, name='teacher_generate_assignment_criteria'),

    # ── Самоперевірка учня (ШІ) ──────────────────────────────────────────────────────
    path('submission/<int:submission_id>/student-ai-check/', views.student_ai_self_check, name='student_ai_self_check'),
    path('teacher/submission/<int:sub_id>/accept-student-ai/', views.accept_student_ai_grade, name='accept_student_ai_grade'),

    # ── Перенесення уроків, 30-денний календар, розклад та експорт/імпорт ────────
    path('teacher/assignment/<int:pk>/reschedule/', views.reschedule_assignment, name='reschedule_assignment'),
    path('api/next-lesson-slot/', views.api_next_lesson_slot, name='api_next_lesson_slot'),
    path('teacher/rescheduled-calendar/', views.teacher_rescheduled_calendar, name='teacher_rescheduled_calendar'),
    path('api/today-schedule/', views.api_today_schedule, name='api_today_schedule'),
    path('teacher/conducted-lessons/update/', views.update_conducted_lessons, name='update_conducted_lessons'),
    path('teacher/assignment/<int:pk>/export-zip/', views.export_assignment_zip, name='export_assignment_zip'),
    path('teacher/assignments/export-day-zip/', views.export_assignments_day_zip, name='export_assignments_day_zip'),
    path('teacher/assignments/import-zip/', views.import_assignments_zip, name='import_assignments_zip'),

    # ── Інтеграція з локальним та зовнішніми журналами оцінок (API v1) ────────
    path('api/v1/journal/grades/', journal_api.api_journal_export_grades, name='api_journal_export_grades'),
    path('api/v2/journal/grades/', school_api_v2.grades, name='api_school_grades_v2'),
    path('api/v2/journal/roster/', school_api_v2.roster, name='api_school_roster_v2'),
    path('api/v2/roster-sync/', roster_views.roster_api, name='roster_sync_api'),
    path('teacher/roster-sync/', roster_views.roster_ui, name='roster_sync'),
    path('api/v1/journal/roster/', journal_api.api_journal_roster, name='api_journal_roster'),
    path('api/v1/journal/pull/', journal_api.api_journal_pull_from_external, name='api_journal_pull_from_external'),

    # ── Інтеграція хмарних сховищ (Nextcloud та Google Drive) ─────────────────
    path('teacher/cloud/status/', views_cloud.teacher_cloud_status, name='teacher_cloud_status'),
    path('teacher/cloud/test-nextcloud/', views_cloud.teacher_cloud_test_nextcloud, name='teacher_cloud_test_nextcloud'),
    path('teacher/cloud/save-nextcloud/', views_cloud.teacher_cloud_save_nextcloud, name='teacher_cloud_save_nextcloud'),
    path('teacher/cloud/google/start/', views_cloud.teacher_cloud_google_auth_start, name='teacher_cloud_google_auth_start'),
    path('teacher/cloud/google/callback/', views_cloud.teacher_cloud_google_callback, name='teacher_cloud_google_callback'),
    path('teacher/cloud/test-google/', views_cloud.teacher_cloud_test_google, name='teacher_cloud_test_google'),
    path('teacher/cloud/save-google/', views_cloud.teacher_cloud_save_google, name='teacher_cloud_save_google'),
    path('teacher/cloud/browse/', views_cloud.teacher_cloud_browse, name='teacher_cloud_browse'),
    path('teacher/cloud/download/', views_cloud.teacher_cloud_download, name='teacher_cloud_download'),
    path('teacher/cloud/resolve/', views_cloud.teacher_cloud_resolve, name='teacher_cloud_resolve'),
    path('teacher/cloud/imports/', views_cloud.teacher_cloud_imports, name='teacher_cloud_imports'),
    path('teacher/cloud/imports/<uuid:job_id>/cancel/', views_cloud.teacher_cloud_import_cancel, name='teacher_cloud_import_cancel'),
]
