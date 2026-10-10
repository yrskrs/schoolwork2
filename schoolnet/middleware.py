"""Migrate only this app's valid legacy session into its own cookie."""
from importlib import import_module
from django.conf import settings


class LegacySessionCookieMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        legacy = request.COOKIES.get('sessionid')
        if not request.COOKIES.get(settings.SESSION_COOKIE_NAME) and legacy:
            store = import_module(settings.SESSION_ENGINE).SessionStore(session_key=legacy)
            if store.get('_auth_user_id'):
                request.session = store
                request.session.modified = True
        return self.get_response(request)
