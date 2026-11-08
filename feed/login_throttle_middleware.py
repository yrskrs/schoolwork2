"""Apply the shared failed-login lock to Django's admin login too."""
from django.http import HttpResponse

from .login_throttle import wait_seconds


class AdminLoginThrottleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST' and request.path.rstrip('/') == '/admin/login':
            wait = wait_seconds(request, request.POST.get('username', ''))
            if wait:
                return HttpResponse(
                    'Забагато невдалих спроб входу. Спробуйте пізніше.', status=429,
                    content_type='text/plain; charset=utf-8')
        return self.get_response(request)
