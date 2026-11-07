# P04b network checkpoint

Network CRUD and persisted road evidence implemented. P04 remains partial: fleet/drivers/users/grants and remaining settings effect rules are still pending. Overall 4/31 tasks complete; G1 not passed.

## Implementation

- Admin GET/POST/PUT/DELETE `/v2/setup/network/{id}`, list `/v2/setup/network?as_of=YYYY-MM-DD`, history `/v2/setup/history/node/{id}`. Same command envelope as products; data: name, kind, parent_id, lat, lon, priority, active. Unknown fields, including client road-validation flags, rejected.
- Network changes use server road validation outside connections. Persist raw/snapped coordinates, distance and provenance in immutable dated snapshots; no invented road validation for imported demo nodes.
- Identity rechecked inside the write transaction after road work. Stale concurrent edits fail. Exact retries use saved results without repeating provider calls, but still require current authorization.
- One plant, hub-to-plant and booth-to-hub relationships, active-parent checks and case-insensitive effective-name uniqueness. Validate all future scheduled boundaries to prevent active children outliving their parent.
- No level changes. Referenced nodes cannot be reparented/deactivated/deleted; plant deletion denied. Historical parent references are retained conservatively. Deletion uses a tombstone and never reuses the ID.
- Date cannot precede current simulation/business date, a started day or latest scheduled revision. Existing baselines saved before first edit; future creates remain hidden. Old plan/trip inputs are untouched.
- `/network`, detail reads, role scope validation and shared node policy now use effective snapshots. A hub manager does not gain early access to future-home assignments. Existing base node columns retain baseline identity; all new domain readers must resolve effective nodes.
- Migration `0005_network` adds append-only node_revisions with node/command FKs and SQLite immutable triggers. Earlier migrations unchanged; 54 application tables. Local DB backed up to data/backups/pre-p04b-20261011.sqlite3, upgraded, integrity passed.

## Limits

Setup mutation tests use declared road fixtures. The earlier one-coordinate live OSRM smoke passed; no additional external road requests were made in this checkpoint. The demo nodes remain explicitly unverified baselines until edited. Full seeded network, road legality and truck suitability remain P07/provider gates.

Base node names/parents retain original values for foreign keys, so physical uniqueness/reference restrictions can conservatively reject reuse of a historical name or old parent even after a dated change. No automatic destructive cleanup is performed. Reparenting a granted booth requires a future coordinated account-management flow; currently rejected.

No setup editing screens or fleet/user CRUD implemented. APIs are ready for future editors, not a completed admin UI. SQLite is verified; PostgreSQL runtime/trigger parity unverified.

## Verification

Initial isolated network run: 7 passed in 10.86s. Final network run: **9 passed in 13.59s**, including race during road work and preservation of a pinned plan. Full regression and live preview results recorded at completion below.

## Next exact work

P04c fleet/drivers/users/grants APIs. Read prompts/P04.md, CATALOG.md, AUTH.md and this checkpoint. Enforce current admin setup permission; define date/revision semantics and active home relationships; positive capacities/rates; permitted lane configurations; explicit timezone-aware dated overnight shifts. Do not rewrite saved dispatched vehicle/driver inputs. Block referenced deletion; secure cross-run user changes and revoke changed sessions. Add migrations after 0005 only. Keep preview running and update PROGRESS/HANDOFF at milestones.


## P04b final verification

Full backend regression: **264 passed in 236.11s**, one run. Final focused network run: **9 passed in 13.59s**, adding two cases (road-work race and pinned plan preservation) beyond that full run. 266 distinct current cases covered across runs, not one full 266-test invocation. Final subsequent code change was only the /status checkpoint description. Dependency consistency, whitespace and all local documentation links/31-task index verified.

Live installed Edge browser checks passed at1440x900 and390x844 after network integration: login, persisted catalogue, scoped network, View as booth, logout, refresh and no page errors. Admin setup-network GET returned three existing demo baselines through the frontend proxy; OpenAPI registers mutation routes; temporary read-test session revoked. No live network records edited and no additional external road calls. Preview remains running; P04c fleet/drivers/users/grants is next.
