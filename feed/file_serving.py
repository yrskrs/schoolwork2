"""Stream school files; never run uploaded active content with the site's origin."""
import mimetypes
import os
import re
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header

from .permissions import require_assignment_access


def file_response(request, path, content_type=None, filename=None, attachment=False):
    if not os.path.isfile(path):
        raise Http404('Файл не знайдено')
    filename = filename or os.path.basename(path)
    content_type = content_type or mimetypes.guess_type(path)[0] or 'application/octet-stream'
    active = content_type.split(';', 1)[0] in {
        'text/html', 'application/xhtml+xml', 'image/svg+xml', 'application/xml', 'text/xml'
    }
    # Download active formats by default; educational HTML remains available as a file.
    attachment = attachment or active
    size = os.path.getsize(path)
    response = None
    raw_range = request.headers.get('Range', '')
    if raw_range and request.method in ('GET', 'HEAD'):
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', raw_range.strip())
        if not match or not any(match.groups()):
            response = HttpResponse(status=416)
        else:
            start, end = match.groups()
            if not start:
                start, end = max(0, size - int(end)), size - 1
            else:
                start, end = int(start), min(int(end), size - 1) if end else size - 1
            if start > end or start >= size:
                response = HttpResponse(status=416)
            else:
                def chunks():
                    with open(path, 'rb') as stream:
                        stream.seek(start)
                        remaining = end - start + 1
                        while remaining:
                            chunk = stream.read(min(64 * 1024, remaining))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            yield chunk
                response = StreamingHttpResponse(chunks() if request.method == 'GET' else [],
                                                 status=206, content_type=content_type)
                response['Content-Range'] = f'bytes {start}-{end}/{size}'
                response['Content-Length'] = end - start + 1
        if response.status_code == 416:
            response['Content-Range'] = f'bytes */{size}'
    if response is None:
        response = FileResponse(open(path, 'rb'), content_type=content_type)
    response['Accept-Ranges'] = 'bytes'
    response['Content-Disposition'] = content_disposition_header(attachment, filename)
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Frame-Options'] = 'SAMEORIGIN'
    if active:
        response['Content-Security-Policy'] = "sandbox; default-src 'none'; base-uri 'none'"
    return response


def public_media(request, path):
    """Keep the open student portal, but protect unpublished teacher materials."""
    from .models import Assignment, AssignmentFile
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise Http404('Файл не знайдено')
    if path.startswith(('school_archives/', 'logs_archives/')) and not (request.user.is_authenticated and
            (request.user.is_superuser or hasattr(request.user, 'teacher_profile'))):
        raise Http404('Файл не знайдено')
    assignment_path = re.match(r'assignments/(\d+)/', path)
    if assignment_path:
        assignment = Assignment.objects.filter(pk=assignment_path[1]).first()
        if assignment:
            require_assignment_access(request, assignment)
        else:
            raise Http404('Файл не знайдено')
    material = AssignmentFile.objects.select_related('assignment').filter(file=path).first()
    if material:
        require_assignment_access(request, material.assignment)
    if path.startswith('previews/'):
        match = re.match(r'previews/(\d+)(?:[/.]|$)', path)
        if match:
            material = AssignmentFile.objects.select_related('assignment').filter(pk=match[1]).first()
            if material:
                require_assignment_access(request, material.assignment)
    return file_response(request, str(target))
