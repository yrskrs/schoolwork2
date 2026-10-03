"""Fetch public web resources without exposing the school's internal network."""
import http.client
import ipaddress
import socket
import ssl
import urllib.request
from urllib.parse import urlsplit


def public_addresses(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Посилання на локальну або приватну мережу недоступне')
    return addresses


def validate_public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Потрібне публічне HTTP або HTTPS посилання')
    public_addresses(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))


class PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        # Connect to the validated address itself, preventing DNS rebinding.
        addresses = public_addresses(self.host, self.port)
        last_error = None
        for family, socktype, proto, _, address in addresses:
            stream = socket.socket(family, socktype, proto)
            stream.settimeout(self.timeout)
            try:
                stream.connect(address)
                self.sock = stream
                return
            except OSError as error:
                last_error = error
                stream.close()
        raise last_error


class PublicHTTPSConnection(PublicHTTPConnection):
    default_port = 443

    def connect(self):
        super().connect()
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


class PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(PublicHTTPConnection, request)


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PublicHTTPSConnection, request)


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def public_urlopen(request, timeout=10):
    validate_public_url(request.full_url if hasattr(request, 'full_url') else request)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), PublicHTTPHandler(), PublicHTTPSHandler(), PublicRedirectHandler())
    return opener.open(request, timeout=timeout)
