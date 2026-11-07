# P04c fleet/driver checkpoint

Vehicle and driver CRUD implemented. P04 remains partial: user/account/grant management and remaining settings effect details are unfinished. Overall 4/31 complete; no G1 completion claimed.

## API contract

All routes require current admin setup scope, via /api/v2 proxy or /v2 backend. Use the existing mutation envelope (command_id, expected_revision, effective_date, occurred_at, reason, data). Body data is a complete replacement, not a patch.

| Method | Route after /v2/setup | Purpose |
|---|---|---|
| GET | /fleet/vehicle or /fleet/driver | Dated list, optional as_of date |
| GET | /fleet/{kind}/{id} | Dated detail |
| POST/PUT | /vehicles/{id} | Create/replace vehicle |
| POST/PUT | /drivers/{id} | Create/replace driver |
| DELETE | /fleet/{kind}/{id} | Protected dated tombstone |
| GET | /history/{kind}/{id} | Append-only history |

Vehicle data: name, home_id, level (plant/hub), positive integer capacity_slots/capacity_g, lane_configurations, status, six-digit hex colour, positive fuel_km_per_l and cost_per_km, shift {start,end}, optional driver_id. Shifts must include explicit dates and offsets and end after start; start cannot precede the effective business date. Overnight intervals must state the following date. No implicit recurring-shift rollover. Null driver_id explicitly means unassigned, not ready to dispatch.

Each lane configuration is a nonempty list of permitted simultaneous lanes (ambient/chilled/frozen); duplicates are rejected. Separate configurations are alternatives, not permission to combine them. This stores declared vehicle capability; it does not prove physical refrigeration suitability or implement the future planner's load validation.

Driver data: name, licence, user_id, home_id, active. No raw PIN on drivers; existing login account must be active for an active driver and already have a grant in the same run. Login reassignment is rejected. Account provisioning and driver grants are the next P04 subtask; the current API does not create accounts or automatically grant driver access.

## Rules and history

- Home must be an active plant/hub at every relevant scheduled network/fleet boundary; vehicle level must match. Active assigned driver must share the home. Overlapping shifts on two active vehicles assigned to one driver are rejected.
- Current simulation/business date, started-day guard, monotonic effective dates, optimistic revisions, exact retries, audit and outbox follow the catalog contract. Identity rechecked before idempotent replay inside the same write transaction.
- Future changes retain prior effective values. All operational consumers must call fleet.resolve_fleet/list_fleet; base rows are retained foreign-key identity/baselines. Existing imported vehicles with missing shifts/rates expose nulls instead of fabricated defaults; update them before dispatch.
- Driver authentication now resolves dated active/user identity. Deactivation invalidates that driver's access when effective; assigned active vehicle configuration must first be corrected. No token-lifetime extension.
- Delete protects trip/location/grant references and saved vehicle assignment history. Tombstones prevent ID reuse. Network deletion/reparent/deactivation also protects home references in saved fleet history.
- Edits never rewrite plan/trip manifests. Future dispatch must snapshot resolved fleet/driver data; existing saved inputs are tested unchanged. No current planner or dispatch claimed.
- New migration 0006_fleet adds append-only fleet_revisions: 55 application tables. SQLite immutable triggers and command FK; earlier migrations preserved. Local backup data/backups/pre-p04c-20261011.sqlite3, upgrade and integrity check passed. PostgreSQL runtime remains unverified.

## Verification

Focused fleet suite: **21 passed in 15.41s**, including invalid capacity/lanes/rates/shifts, bad home/driver links, overlap, current driver denial after deactivation, cross-run reads, tombstones, immutable history, exact retries and preservation of a dispatched trip's manifest. Full regression result recorded below when finished.

Installed Edge browser check passed desktop1440x900 and mobile390x844 after restart: sign-in, persisted catalogue, View as, scope isolation, logout, refresh and no page errors. No UI source changes; fleet editors remain P06. No new provider requests, live fleet mutations, model training or public deployment.

## Next exact work: P04d

Implement user/account and grant administration: phone/PIN creation/reset with hashing outside write locks, no secret in command/audit/result logs, run membership protections, active flags, session revocation, last-admin protection, current semantic role scope and reference-safe removal. Coordinate driver provisioning without temporary overprivileged membership. Test all role/ID substitution and cross-run effects, replay after revocation, self-lockout/last admin, driver links and account audit. Finish settings app_name immediate refresh semantics. Then review all original P04 acceptance before marking done; P05 inventory and P06 editors follow.


## P04c final verified result

Full backend regression: **287 passed in 158.23s**, one run, from backend using `.\venv\Scripts\python.exe -m pytest -q`; includes 21 new fleet cases and all previous regressions. Only later application edit was the /status descriptive checkpoint label. Dependency consistency, whitespace and local documentation links/31-task index passed.

Live preview: HTTP200, correct configured status, admin fleet reads returned 1 existing demo driver and 0 vehicles (no fabricated fleet); temporary read-test session revoked. Installed Edge desktop/mobile checks passed. No live fleet data was changed. Database at0006_fleet after verified backup/upgrade. Final listeners: backend17580:8600 (launcher1764), frontend17140:5600. Leave running; inspect actual PIDs before future restart.

Next P04d: account/grant administration and remaining settings effect rules. P04 remains partial and overall4/31 complete. No claim of completed fleet editors or final platform.
