from django.http import Http404
from django.shortcuts import render, get_object_or_404
from .models import TailscaleConnection, AccessLink, Assignment, AssignmentFile, Submission, AIJob
from .access_control import (is_external, is_teacher, grant_from_cookie, scope_assignments,
    scope_submissions, media_allowed)


class PublicAccessMiddleware:
    """Fail closed for Funnel, check revocation on EVERY request, including files and POSTs."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.access_link = None
        request.public_access = False
        request.public_open_access = False
        if not request.path_info.startswith('/static/'):
            connection = TailscaleConnection.objects.filter(pk=1).first() or TailscaleConnection()
            request.tailscale_connection = connection
            request.public_access = is_external(request, connection)
            request.public_open_access = bool(request.public_access and connection.enabled and connection.allow_without_link)
            if request.META.get('HTTP_TAILSCALE_FUNNEL_REQUEST') == '?1':
                request.META['wsgi.url_scheme'] = 'https'
            if request.public_access and not is_teacher(request):
                pk = grant_from_cookie(request)
                if pk and connection.enabled and not request.public_open_access:
                    link = AccessLink.objects.select_related('class_group', 'student__class_group', 'assignment').filter(pk=pk).first()
                    if link and link.active:
                        request.access_link = link
        response = self.get_response(request)
        if request.public_access or request.path_info.startswith('/access/'):
            response['Cache-Control'] = 'private, no-store'
            response['Referrer-Policy'] = 'same-origin'
            response['X-Robots-Tag'] = 'noindex, nofollow'
        if request.path_info.startswith('/access/'):
            # Django's request logger runs after the middleware stack.
            request.path = '/access/***/'
        return response

    def process_view(self, request, view, args, kwargs):
        if not request.public_access or is_teacher(request):
            return None
        name = request.resolver_match.url_name
        # Only these authentication endpoints are reachable without a pupil grant.
        if name in ('access_enter', 'teacher_login', 'teacher_logout'):
            return None
        connection = request.tailscale_connection
        if not connection.enabled or (not request.public_open_access and not request.access_link):
            return render(request, 'feed/access_required.html', {
                'disabled': not connection.enabled,
            }, status=403)
        link = request.access_link
        if request.path_info.startswith('/media/'):
            if not media_allowed(request, kwargs.get('path', '')):
                raise Http404
            return None
        public_names = {'index', 'feed_fragment', 'calendar_fragment', 'feed_check_updates',
            'clear_class_filter', 'api_classes_list', 'ui_dialog', 'submission_notifications', 'api_students_autocomplete',
            'assignment_detail', 'submit_assignment', 'submit_success', 'submission_detail',
            'student_submissions_portal', 'my_submissions', 'ai_job_status', 'file_preview', 'file_view', 'file_download',
            'assignment_ai_understanding_student', 'student_ai_self_check', 'client_heartbeat', 'client_disconnect'}
        if name not in public_names:
            raise Http404
        assignment_id = kwargs.get('pk') if name in {'assignment_detail', 'submit_assignment', 'submit_success', 'assignment_ai_understanding_student'} else None
        if name in ('file_preview', 'file_view', 'file_download'):
            assignment_id = get_object_or_404(AssignmentFile, pk=kwargs['file_id']).assignment_id
        if assignment_id:
            get_object_or_404(scope_assignments(request, Assignment.objects.filter(status__in=['published', 'archived'])), pk=assignment_id)
        if name == 'ai_job_status':
            job = get_object_or_404(AIJob, pk=kwargs['job_id'])
            if job.kind == 'understanding':
                get_object_or_404(scope_assignments(request, Assignment.objects.filter(
                    status__in=['published', 'archived'], allow_student_ai_understanding=True)), pk=job.assignment_id)
            elif job.kind == 'student_check':
                get_object_or_404(scope_submissions(request, Submission.objects.all()), pk=job.submission_id)
            else:
                raise Http404
        if 'submission_id' in kwargs:
            get_object_or_404(scope_submissions(request, Submission.objects.filter(assignment__status__in=['published', 'archived'])), pk=kwargs['submission_id'])
        if link and link.restricted_class_id:
            for key in ('class', 'class_id', 'class_group_id'):
                value = request.GET.get(key)
                if value and value not in ('all', str(link.restricted_class_id)):
                    raise Http404
        return None
