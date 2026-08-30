"""Admin реєстрація моделей SchoolNet."""

from django.contrib import admin
from .models import (
    Subject, ClassGroup, Student, Teacher, Assignment, AssignmentFile,
    Submission, SubmissionFile, SubmissionComment, SubmissionActivityLog
)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'icon', 'color']
    search_fields = ['name']


@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'grade', 'letter']
    ordering = ['grade', 'letter']


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['last_name', 'first_name', 'class_group', 'created_at']
    list_filter = ['class_group']
    search_fields = ['last_name', 'first_name']
    ordering = ['class_group__grade', 'class_group__letter', 'last_name', 'first_name']


class AssignmentFileInline(admin.TabularInline):
    model = AssignmentFile
    extra = 1
    readonly_fields = ['uploaded_at']


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'user', 'avatar_color']
    filter_horizontal = ['subjects', 'classes']
    search_fields = ['full_name', 'user__username']


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ['title', 'teacher', 'subject', 'status', 'published_at', 'created_at']
    list_filter = ['status', 'subject', 'classes']
    search_fields = ['title', 'description', 'student_name']
    filter_horizontal = ['classes']
    inlines = [AssignmentFileInline]
    readonly_fields = ['created_at', 'updated_at', 'published_at']
    date_hierarchy = 'created_at'


@admin.register(AssignmentFile)
class AssignmentFileAdmin(admin.ModelAdmin):
    list_display = ['original_name', 'assignment', 'uploaded_at']
    readonly_fields = ['uploaded_at']


# ── Здачі робіт ─────────────────────────────────────────────────────────────

class SubmissionFileInline(admin.TabularInline):
    model = SubmissionFile
    extra = 1
    readonly_fields = ['uploaded_at', 'file_size']


class SubmissionCommentInline(admin.TabularInline):
    model = SubmissionComment
    extra = 0
    readonly_fields = ['created_at', 'author']


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ['get_student_full_name', 'class_group', 'assignment', 'grade', 'submitted_at', 'is_graded']
    list_filter = ['class_group', 'assignment', 'submitted_at']
    search_fields = ['last_name', 'first_name', 'assignment__title']
    readonly_fields = ['submitted_at', 'graded_at']
    inlines = [SubmissionFileInline, SubmissionCommentInline]
    date_hierarchy = 'submitted_at'

    @admin.display(boolean=True, description='Оцінено')
    def is_graded(self, obj):
        return bool(obj.grade)


@admin.register(SubmissionFile)
class SubmissionFileAdmin(admin.ModelAdmin):
    list_display = ['original_name', 'submission', 'file_size', 'uploaded_at']
    readonly_fields = ['uploaded_at', 'file_size']



@admin.register(SubmissionComment)
class SubmissionCommentAdmin(admin.ModelAdmin):
    list_display = ['get_author_name', 'submission', 'created_at']
    readonly_fields = ['created_at']


@admin.register(SubmissionActivityLog)
class SubmissionActivityLogAdmin(admin.ModelAdmin):
    list_display = ['action_type', 'description', 'actor', 'timestamp']
    list_filter = ['action_type', 'timestamp']
    readonly_fields = ['timestamp']
