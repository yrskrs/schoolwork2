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
200 with certificate validation enabled. The additional Photos and font-manager
issues found during that audit were resolved in the follow-up below.

Office rollback: restore its three previous address fields from the owner's
private configuration, then remove the two Office locations and reload nginx.
The Viewer proxy rollback described above is independent of document data.


## Follow-up: Photos and font manager, 2026-11-06

Photos defaults to the user's `/Photos` source folder. That folder was absent
for the audited owner account, so authenticated DAV SEARCH returned 404.
Creating the normal empty `Photos` folder through Files restored SEARCH to 207;
“On this day” now correctly displays an empty state without a new Photos error.
Existing files were not moved. Other users with intentionally different photo
sources should keep them; verify their configured paths rather than changing
all accounts or suppressing 404 responses globally.

The document-server font API returned 502 because Supervisor's `adminpanel`
program was stopped with `autostart=false`. The connector correctly reported
that the service must be enabled. The host backup is
`/home/yrsadmin/euro-office/ds-adminpanel.before-font-fix.conf`.
Setting that program's `autostart=true`, rereading and updating the program
restored it without restarting document editing services. The helper
`enable-font-manager.sh` reproduces this change and makes a private backup.
This configuration survives restart of the existing container. When replacing
or recreating the document-server container, set its supported environment
variable `ADMINPANEL_ENABLED=true`; otherwise a fresh image defaults to disabled.
No global Docker privileges or Nextcloud file permissions were changed.

The public `/office/admin` and `/office/admin/` routes return 404. The font API
remains available to Nextcloud via its internal document-server URL; direct
requests without a JWT return 401. Nextcloud admin checks and the existing
short-lived service JWT remain enforced. No bootstrap credentials are published.

Verified: font list 200, regeneration accepted 202, status polling 200, final
`status=done` with `error=null` (39 seconds). The UI reported “Font regeneration
completed successfully.” No fonts were uploaded or deleted, and no documents
were changed for this follow-up. `nginx -t` and the helper's `sh -n` passed.
Rollback of the font-service change: restore the backed-up Supervisor program
file, reread and update only `adminpanel`. Retain the public admin-route block.
