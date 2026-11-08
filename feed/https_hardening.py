"""HTTPS-only cookie/HSTS hardening for the public Funnel, without breaking LAN HTTP."""
from ipaddress import ip_address
from urllib.parse import urlsplit

from django.conf import settings


def _public_host(request):
    candidates = []
    base = getattr(settings, 'PUBLIC_BASE_URL', '') or ''
    if base:
        candidates.append(base)
    connection = getattr(request, 'tailscale_connection', None)
    if connection is None:
        try:
            from .access_models import TailscaleConnection
            connection = TailscaleConnection.objects.filter(pk=1).first()
        except Exception:
            connection = None
    if connection is not None and getattr(connection, 'public_url', ''):
        candidates.append(connection.public_url)
    for origin in getattr(settings, 'CSRF_TRUSTED_ORIGINS', []):
        if str(origin).lower().startswith('https://'):
            candidates.append(origin)
    for value in candidates:
        host = urlsplit(str(value)).hostname
        if host:
            return host.lower().rstrip('.')
    return ''


def _trusted_https(request):
    """Accept Secure/HSTS only for the configured public HTTPS host.

    Forwarded protocol alone is not enough: the request must target the
    configured Funnel host and come from a local/loopback proxy address (or
    carry Tailscale's own Funnel marker). A LAN client cannot turn the HTTP
    site into a Secure-cookie site merely by inventing a header.
    """
    host = request.get_host().split(':')[0].lower().rstrip('.')
    public_host = _public_host(request)
    if not public_host or host != public_host:
        return False
    secure = request.is_secure() or request.META.get('HTTP_X_FORWARDED_PROTO', '').lower() == 'https'
    if not secure:
        return False
    if request.META.get('HTTP_TAILSCALE_FUNNEL_REQUEST') == '?1':
        return True
    try:
        address = ip_address(request.META.get('REMOTE_ADDR', ''))
    except ValueError:
        return False
    return address.is_loopback or address.is_private


class HTTPSHardeningMiddleware:
    """Add HSTS and Secure to session/CSRF cookies for trusted HTTPS responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if _trusted_https(request):
            response['Strict-Transport-Security'] = (
                f'max-age={int(getattr(settings, "HTTPS_HSTS_MAX_AGE", 86400))}'
            )
            for cookie_name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
                if cookie_name in response.cookies:
                    response.cookies[cookie_name]['secure'] = True
        return response
