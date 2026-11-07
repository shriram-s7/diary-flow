# P03 authentication and visible workspace

Updated 2026-10-11. P03 adds a real database-backed v2 login and read-only role workspace, accessible at `/platform`. Product/network editors remain P04–P06; ordering and delivery apps remain later tasks. There are no fake save buttons or simulated success responses.

## Open it

From the repository root, after closing any old servers occupying ports 8600/5600:

```powershell
.\start-v2-demo.ps1
```

Then open [the v2 workspace](http://localhost:5600/platform). The existing simulator also has an “Open Allocra v2 workspace” link. The script installs declared backend dependencies, explicitly migrates the local database, seeds an isolated `auth-demo` run once, creates a random local signing key if absent and enables prototype one-tap login. Existing runs are not replaced. The local key is in ignored data/auth-signing.key and must not be shared or committed.

The small auth-demo network has one illustrative plant/hub/booth and one assumed dairy product to demonstrate persistence and scope; it is **not** P07's complete verified Chennai network. Seed PINs are random and discarded; use the clearly labelled demo buttons. Real account provisioning is future admin setup work. Phone/PIN login is implemented and integration-tested with dedicated test accounts; no default public PIN is supplied.

Manual configuration before starting backend:

```powershell
$env:ALLOCRA_DB = 'D:\DIARYFLOW-LOGISTICS\data\allocra-v2.sqlite3'
$env:ALLOCRA_SIGNING_KEY_FILE = 'D:\DIARYFLOW-LOGISTICS\data\auth-signing.key'
$env:ALLOCRA_DEMO = '1'
```

Outside explicit prototype mode, remove ALLOCRA_DEMO: demo listing/login/view-as endpoints return404. Missing signing key yields an unconfigured status and503 on protected operations rather than silently opening access. JWT requires at least32 key bytes. Never use the prototype starter on a publicly exposed server. Legacy v1 endpoints still represent their independent unrestricted simulator; they must be retired or protected before any production v2 deployment.

## Implemented boundaries

- PBKDF2-HMAC-SHA256,600000 iterations, independent salt per PIN;6–12 digit PIN validation.
- HS256 JWT with fixed algorithm, issuer/audience/required claims, wall-time issued/not-before/expiry checks;8-hour sessions.
- Session token hash stored in DB; logout revocation, user disabled/session-version and current role grants checked on every request and each WebSocket snapshot. Token claims do not confer stale grants.
- Persistent per-account5 and per-address30 attempts in 15 wall minutes, counted before expensive hash checks. Hashed throttle keys avoid storing raw address/phone identifiers there. Failed logins use generic responses. Throttles can temporarily lock a shared demo identity; expiry clears that window.
- Role shape plus semantic active hub/booth-home/driver-user checks. All six role filters; driver trip assignments rechecked. Admin operations read-only, plant manager hub trip mutation denied, unknown actions denied.
- Shared `policy.py` guards for future read/export/file/operation handlers. No report/photo/edit handler exists yet; every future handler must use current identity plus the correct resource policy. Tests of present endpoints are not a claim that nonexistent future endpoints have been verified.
- `execute_authenticated` checks current identity/resource policy **inside the command transaction before deduplication replay**, rejects actor mismatch, and records real/effective actors on delegated commands. Future queued mutations must call it; raw internal execute() remains trusted infrastructure, not an exposed endpoint.
- Explicit demo-only one-tap users and admin View as; immutable security audit includes real/effective actors. Delegated access also checks current admin role/active status.
- No-store HTTP responses; bearer tokens only in memory on the UI. Sign-out clears visible data and a refresh requires sign-in. No offline data caching introduced at this stage. Signed-in scopes refresh periodically; HTTP403/401 clears the session.
- `/api/v2/*` via existing Vite proxy maps to backend `/v2/*`. Subapplication routes: status, auth/login, auth/logout, auth/demo-users, auth/demo-login, auth/view-as, me, network, network/{id}, products, trips, stream. WebSocket receives token in first frame, not URL/access logs, and revalidates each snapshot. This is an initial scoped read stream, not P22 live motion or P08 global clock.

## Migration and checks

New Alembic0003_auth adds persistent login limits, append-only security audit and explicit demo-user markers. Earlier migrations remain intact. PostgreSQL runtime remains unverified.

Final full backend regression: **214 passed in 120.60s**, one run from backend with `.\venv\Scripts\python.exe -m pytest -q`. Includes 102 legacy, 52 domain, 37 database and 23 authentication cases, including delegated-command actor auditing. Dependency consistency (`pip check`) and whitespace checks passed.

The sandbox blocked Windows asyncio socket initialization; those hung test harnesses were stopped and HTTP/WebSocket tests passed with approved unrestricted execution. This was an environment issue, not a waived test.

Production frontend build passed (112 modules); existing MapLibre bundle warning >500kB remains. Browser checks ran with installed Edge because the pinned Playwright Chromium executable was absent. Desktop1440x900 and mobile 390x844 passed login, saved catalogue, View as, scoped data, logout, refresh and no page errors. Screenshots: `.cache/p03-browser/`. Phone emulation is not physical Android/PWA/GPS acceptance.

Reproduce browser checks with running configured backend/frontend (test used ports 18600/15600):

```powershell
.\backend\venv\Scripts\python.exe e2e/v2_auth_check.py --url http://localhost:5600 --channel msedge
```

References consulted: [PyJWT claim and signature validation](https://pyjwt.readthedocs.io/en/stable/usage.html), [Python password derivation primitives](https://docs.python.org/3/library/hashlib.html). This is tested prototype authentication, not a penetration-test or zero-bug guarantee.

## P04 extension

Admin-only /setup product/settings/history routes and road-validation preflight now use current identity and default-deny setup policy. Mutation commands recheck authority transactionally before replay. See [CATALOG.md](CATALOG.md) for implemented routes and remaining scope.
