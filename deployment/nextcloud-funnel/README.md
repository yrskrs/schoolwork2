# Nextcloud: guarded Viewer close through Funnel

Optional deployment companion for the snap installation, separate from SchoolNet.
It runs as an unprivileged user, binds only 127.0.0.1:18080 and forwards to
127.0.0.1:80. Host networking preserves Nextcloud's existing trusted loopback
proxy boundary and HTTPS scheme; no trusted-proxy list is expanded.
The installed Nextcloud 35.0.2 Viewer calls `Sidebar.setFullScreenMode(false)` on
close even when the lazily initialized Sidebar has no such method. The browser
reported a TypeError after closing the text viewer. The immutable snap is not edited.

The HTML Viewer URL receives a cache marker so existing browser caches load the
corrected module. Only the exact affected call in `/apps/viewer/js/viewer-main.mjs` is guarded with a
function check. Other assets, WebDAV, streaming request bodies and WebSockets pass
through. The original app authentication remains in Nextcloud. A future corrected
asset does not match the replacement string.

Before changing a Funnel route, save its current JSON configuration. Start this
companion with `docker compose up -d`, check `nginx -t`, compare the proxied script
with the original, and verify login, upload, edit, download and viewer close. Then
point only the existing Nextcloud HTTPS route to `http://127.0.0.1:18080`; leave the
other routes untouched. Verify the route configuration again after the change.
Force reload an already-open browser tab so it loads the corrected asset.

Rollback: point that same Nextcloud route back to its original upstream, then stop
this Compose project. Do not remove or restore Nextcloud data volumes for this
proxy-only change. Remove the companion after the upstream application has fixed
the callback and that release has been tested.


## Existing Office service

After the owner separately approves public exposure, `/office/` can forward the
already installed document service on loopback 8800. The proxy uses the vendor's
[virtual-path headers](https://github.com/ONLYOFFICE/document-server-proxy/blob/master/nginx/proxy-to-virtual-path.conf).
One exact `/sdkjs/common/device_scale.js` path is forwarded too because this
editor emits an absolute resource URL. Do not route all unknown paths to Office.
Keep the current JWT secret; enable certificate verification. Set the public
Office URL to the same HTTPS host and port with `/office/`, its internal URL to
`http://127.0.0.1:8800/`, and the Office-to-Nextcloud URL to a reachable internal
address of this server. These addresses are installation-specific. Never commit
the secret or a full private Nextcloud configuration.

On 2026-11-06, a disposable DOCX was created, edited, saved and reopened with its
text intact, then moved to normal Trash. The original root directory retained
four files and five folders. Healthcheck, editor API and exact SDK path returned
200 with certificate validation enabled. Photos “On this day” still returned 404
for DAV SEARCH, and EuroOffice font administration returned 500; these additional
features are unresolved and are not concealed by the proxy.

Office rollback: restore its three previous address fields from the owner's
private configuration, then remove the two Office locations and reload nginx.
The Viewer proxy rollback described above is independent of document data.
