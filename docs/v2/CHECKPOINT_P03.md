# P03 checkpoint — 2026-10-11

Status: P03 complete for the authentication foundation and currently exposed resources. **4/31 tasks complete**; G0 passed, G1 incomplete. Next P04 network/products/fleet/settings mutation APIs, then P05 inventory and P06 setup editors. No claim that final platform is bugless or production-ready.

## User-visible change

`/platform` now renders a connected v2 login and role workspace. The original app links to it. Six explicit prototype identities expose database-backed records only within scope. Admin can View as a demo user with real/effective actor audit. Product/network editing is not present yet and is clearly marked on the page.

Start with `start-v2-demo.ps1` and open http://localhost:5600/platform. See [AUTH.md](AUTH.md) for setup, security configuration, assumptions and exact test commands/results. No temporary test servers are intended to remain running after this checkpoint.

## Changed areas

- `backend/v2/auth/`: password verification, JWT/session lifecycle, throttling, current identity/scope checks, default-deny resource policy, pre-replay authenticated command bridge and explicit prototype bootstrap.
- `backend/v2/app.py` + `backend/api.py`: mounted isolated v2 endpoints/read stream.
- `backend/v2/db/migrations/versions/0003_auth.py`: forward-only schema addition. Existing schema revisions unchanged.
- `backend/v2/db/commands.py`: optional authorization hook inside transaction before replay; existing internal callers preserved.
- `frontend/src/v2/`, `frontend/src/main.jsx`: visible login/role shell, in-memory token, scoped lists, empty/loading/error states, legacy navigation link.
- Pinned PyJWT dependency; prototype launch script; auth tests and desktop/mobile browser check.

## Evidence

**214 tests passed in 120.60s in one final full-suite run** (102 legacy + 52 domain + 37 database + 23 auth). Dependency consistency and whitespace checks passed; all local documentation links and 31 task entries verified. Temporary test servers stopped; ports 15600 and 18600 have no listeners. Frontend production build passed with existing chunk warning. Desktop/mobile browser checks passed; screenshots reviewed. No physical phone, real-time delivery, model, setup-editor or future-resource authorization acceptance claimed.

## Resume precisely

Read prompts/P04.md, AUTH.md, DATABASE.md, TARGET_SPEC.md and PROGRESS.md. Build catalog mutation services behind require_setup/role policies and execute_authenticated. Never accept client actor/scope IDs as authority. Add migrations for new fields; preserve0001–0003. Keep preview/read-only shell truthful until editing works end-to-end. Re-run role negative tests for each new endpoint, export and file route. Update this handoff and progress at each milestone.
