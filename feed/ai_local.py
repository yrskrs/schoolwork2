"""Local AI endpoints and the separate time budget they need.

A school's own GPU is much slower than a hosted API: on an RTX 4060 with
``qwen3-vl-8b-instruct`` one page costs roughly 11-18 seconds of vision prefill
and the answer is generated at about 13 tokens per second. The hosted default
(``AI_REQUEST_DEADLINE`` = 300 s) is therefore too small for local checks, while
hosted providers must keep their short timeouts.

Local endpoints are detected automatically from their address (loopback,
private LAN ranges, ``.local`` names, Docker host aliases), and the teacher can
override the detection explicitly. Their limits live on the connection itself,
so nothing changes for Gemini, Groq, OpenAI, OpenRouter or Cloudflare.
"""
import ipaddress
from urllib.parse import urlsplit

LOCAL_HOSTNAMES = {'localhost', 'host.docker.internal', '0.0.0.0', '::1', 'gateway.docker.internal'}
LOCAL_SUFFIXES = ('.local', '.lan', '.internal', '.home')

DEFAULT_LOCAL_DEADLINE = 600
DEFAULT_LOCAL_TIMEOUT = 300
DEFAULT_LOCAL_IMAGE_PX = 1024
LOCAL_DEADLINE_RANGE = (120, 1800)
LOCAL_TIMEOUT_RANGE = (60, 600)
LOCAL_IMAGE_PX_CHOICES = (512, 640, 768, 896, 1024)


def _host(url):
    text = str(url or '').strip()
    if not text:
        return ''
    try:
        parsed = urlsplit(text if '://' in text else 'http://' + text)
        return (parsed.hostname or '').lower()
    except ValueError:
        return ''


def is_local_url(url):
    """True when the address points at this machine or the school LAN."""
    host = _host(url)
    if not host:
        return False
    if host in LOCAL_HOSTNAMES or host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def is_local_connection(connection):
    """Explicit teacher choice wins; otherwise the address is judged."""
    connection = connection or {}
    if connection.get('provider') != 'custom':
        return False
    explicit = connection.get('local')
    if explicit is True:
        return True
    if explicit is False:
        return False
    return is_local_url(connection.get('custom_url', ''))


def local_limits(connection, job_timeout=None):
    """Effective local-AI limits for one connection, safe for any input."""
    connection = connection or {}
    local = is_local_connection(connection)

    def number(name, default, low, high):
        try:
            value = int(connection.get(name) or default)
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    deadline = number('local_deadline', DEFAULT_LOCAL_DEADLINE, *LOCAL_DEADLINE_RANGE)
    if job_timeout:
        # The worker kills a job after AI_JOB_TIMEOUT; never promise more than
        # that, and keep a minute for saving the result.
        deadline = min(deadline, max(LOCAL_DEADLINE_RANGE[0], int(job_timeout) - 60))
    return {
        'local': local,
        'local_deadline': deadline,
        'local_timeout': number('local_timeout', DEFAULT_LOCAL_TIMEOUT, *LOCAL_TIMEOUT_RANGE),
        'local_image_px': number('local_image_px', DEFAULT_LOCAL_IMAGE_PX, 512, 1024),
    }


def config_limits(config):
    """Local limits carried by an attempt config produced by request_configs()."""
    return {
        'local': bool(config.get('local')),
        'local_deadline': int(config.get('local_deadline') or DEFAULT_LOCAL_DEADLINE),
        'local_timeout': int(config.get('local_timeout') or DEFAULT_LOCAL_TIMEOUT),
        'local_image_px': int(config.get('local_image_px') or DEFAULT_LOCAL_IMAGE_PX),
    }
