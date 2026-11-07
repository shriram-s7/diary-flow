# P04a checkpoint — 2026-10-11

P04 is **partial**, not complete. Overall tasks remain **4/31 complete**. P04a implements product packaging and dated settings APIs; P04b network/road validation and P04c fleet/drivers/users remain pending. G1 is not passed.

## Changes

- Added admin-only product create/read/replace/delete, complete settings read/replace, and immutable history APIs under /v2/setup.
- Dated revisions, head checks, validated packaging, protected started days, exact retry semantics and atomic audit/outbox. Referenced deletion denied; saved dispatched inputs remain unchanged.
- Current manager `/products` reads resolve dated snapshots and hide inactive/deleted products.
- New migration 0004_catalog; existing 0001–0003 preserved. CLI accepts the new revision. Local DB backed up then upgraded and integrity checked.
- Local preview remains running on http://localhost:5600/platform and API 8600. Backend restarted to load changes; frontend remains running. Existing screens are read-only; no P06 editor claimed.

## Verification

Final full backend suite: **238 passed in 210.74s**, one run, from backend using `.\venv\Scripts\python.exe -m pytest -q`. Includes 24 new catalog cases. Tests cover all six roles, concurrent edits, atomic rollback, immutable revisions, future-date visibility, started-day protection, dispatched snapshot preservation, baseline migration, measured packaging and settings validation. Initial focused run: 18 passed in 11.10s.

Live Edge browser check passed at 1440x900 and 390x844 after the upgrade: login, catalogue, View as, scope isolation, logout, refresh and no console errors. New setup GET APIs returned the saved product and Allocra registry through the frontend proxy; temporary smoke-test session revoked. No live catalogue mutations applied. Frontend source unchanged; no new build needed for these backend changes.

## Resume within P04

1. Read CATALOG.md and inspect its resolver rule before adding any domain consumer. Do not use original product fields as effective packaging.
2. P04b: network CRUD, parent/home relationships, road snapping outside transactions, raw/snapped coordinates and provenance, clear >300m/provider-unavailable rejection. Include an actual provider smoke test; mocked roads are not proof of road access.
3. P04c: fleet/drivers/users/grants CRUD; home-level and lane compatibility; positive capacities/rates; explicit dated overnight shifts; account revocation/scope changes; reference-safe deletions and immutable dispatched snapshots.
4. Finish settings ownership/effect details, including app_name immediate-refresh exception if needed. Current full-registry changes conservatively take effect only on an unstarted day.
5. Only mark P04 done when all its original acceptance items pass. Continue to P05 inventory, then P06 setup editors. Update PROGRESS/HANDOFF after each milestone. Do not rewrite existing migrations.

Uncommitted changes from earlier checkpoints remain preserved. No model training, routing validation, deployment or physical-phone acceptance in this checkpoint.

## Continuation: road-validation adapter

Added preflight endpoint and roads.py; network persistence is still pending. Final focused catalog/road tests: **43 passed in 17.66s** (26 catalog + 17 road). Combined with the preceding full 238-test run, 257 distinct current cases covered across runs; not a single full 257-test run. pip check and git diff --check passed. Live external provider test was auto-review rejected pending explicit coordinate/provider approval. No provider success or persisted road validation claimed.

Verification skill flow evidence: browser desktop/mobile PASS; proxy status configured=true; API setup/products returns one saved product; setup/settings returns Allocra; backend startup complete and successful 200 request logs; no frontend stderr. UI editing flow not implemented.


## Approved live OSRM check — 2026-10-11

User explicitly approved one test. At 07:31:32 UTC, the configured https://router.project-osrm.org nearest service returned Ok for illustrative coordinate 13.148,80.231. Snapped coordinate: 13.147988,80.231061. Adapter distance 6.738514m (provider distance 6.746160m), within 300m. Validated=true; road name absent and provider data version unknown. No network node was created or edited.

Evidence: .cache/p04-road-smoke.json and newly written .cache/route_cache/e529f52420b053c0b61b557e7e80a0364a68e3f0.json (matching nearest request key and response; last write 07:31:32 UTC). This supersedes the pending-approval limitation for this one smoke test only. It does not validate the full Chennai seed, road legality, or truck access. Network CRUD/persisted provenance and fleet/users still pending; P04 remains partial. Preview left running.
