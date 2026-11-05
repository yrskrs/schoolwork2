"""Do not retain bearer URLs in Gunicorn access logs."""
import re
from gunicorn.glogging import Logger


class AccessLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        if environ.get('PATH_INFO', '').startswith('/access/'):
            atoms['r'] = f"{environ.get('REQUEST_METHOD', 'GET')} /access/***/ {environ.get('SERVER_PROTOCOL', 'HTTP/1.1')}"
            atoms['U'] = '/access/***/'
            atoms['q'] = ''
        # Also cover Referer and custom log atoms for raw URI/environment fields.
        for key, value in atoms.items():
            if isinstance(value, str) and '/access/' in value:
                atoms[key] = re.sub(r'/access/[^?\s"<>]*', '/access/***/', value)
        return atoms
