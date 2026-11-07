# Product and settings APIs — P04a

Implemented 2026-10-11. This is the first P04 checkpoint, not completion of the full setup task. Network editing/road validation, vehicles, driver shifts and user/grant management remain pending. No setup editor UI is claimed.

## API contract

All `/v2/setup/*` routes require a current **admin** grant and bearer session, with `X-Run-Id` and `X-Grant-Id`. Through the browser proxy use `/api/v2/setup/*`. Other roles receive 403; no session receives 401. Mutation authority is checked again inside the command transaction before replay.

| Method | Path after /v2 | Behavior |
|---|---|---|
| GET | /setup/products?as_of=YYYY-MM-DD | Product snapshots effective on the requested date, including inactive products. Defaults to the current simulation business date, falling back to wall date until a clock exists. |
| GET | /setup/products/{id}?as_of=YYYY-MM-DD | One product snapshot; 404 if not yet created or deleted on that date. |
| POST | /setup/products/{id} | Create a new product ID at revision 1; expected_revision must be 0. |
| PUT | /setup/products/{id} | Replace the complete product definition against the latest revision. |
| DELETE | /setup/products/{id} | Dated tombstone, only if no saved business rows reference the product. Body contains mutation metadata. |
| GET | /setup/settings?as_of=YYYY-MM-DD | Effective complete settings registry and its revisions. |
| PUT | /setup/settings | Replace the complete registry; omitted registry fields are rejected, preventing silent reset to defaults. |
| GET | /setup/history/product/{id} | Newest-first immutable product versions, dates, tombstones and command IDs. |
| GET | /setup/history/settings/registry | Newest-first settings versions. |

Every mutation body contains `command_id`, `expected_revision`, `effective_date`, timezone-aware `occurred_at` and nonblank `reason`. POST/PUT also contain `data`. Reuse the exact body and command ID for a retry. Changing the payload, actor or context under the same ID returns 409. Do not generate a new timestamp for a retry. IDs/actor/scope are never accepted as client authorization.

Product `data` uses the domain packaging contract plus name, shelf_life_minutes and active. It includes sku_id matching the path; dimension ml/g, strictly positive integer packaging quantities and grams, container type, strict Boolean returnable, and ambient/chilled/frozen lane. Optional unit_gross_g and tare_g must both be supplied and fit within full_container_g. Unexpected fields are rejected. Read back measured weights through setup APIs; original products table does not carry them.

For settings, first GET the registry; copy the entire `data` object into PUT, change the intended fields, and use `head_revision` as expected_revision. Decimal values serialize as strings. Existing P01 cross-field validation remains authoritative, including cutoff order and risk thresholds.

## Revision and date rules

- Read responses carry `revision` for the effective version and `head_revision` for the latest scheduled version. Future creates are hidden before their effective date. To inspect scheduled products, query an explicit future date (9999-12-31 returns the final scheduled state); history retains tombstones.
- Revision numbers increase per resource. Effective dates cannot decrease relative to the latest scheduled revision. Multiple changes on one date resolve to the highest revision; earlier versions stay in history.
- Mutations cannot precede the current simulation business date (Asia/Kolkata), or wall business date before P08 creates a clock. Client occurred_at never determines effective date eligibility.
- A change cannot affect any already-started day, including open/closed/future-created day rows. Choose a date later than all such days. This checkpoint conservatively applies this rule to the whole settings registry, including app_name; the immediate-name refresh exception in SETTINGS.md is not yet implemented.
- Before the first edit of a pre-existing record, an immutable revision-0 baseline is saved from its current stored values. Its date 0001-01-01 is a sentinel for the imported baseline, not evidence that historical versions were reconstructed. Records without edits use their existing baseline.
- The products table retains foreign-key identity and its original values; its revision is the latest head. **All new operational consumers must call `resolve()`/`list_products()` for dated values. Never read base packaging fields directly.** `/products` now resolves the current date and hides inactive/deleted products. Run settings and the unused legacy `settings` table are not additional writable registries.
- No command rewrites plans, trips or manifest packaging. Future planning/order/dispatch services must pin these resolved snapshots in their own records. This checkpoint tests preservation of existing dispatched inputs; it does not implement dispatch.
- Deletion is a retained tombstone; IDs cannot be reused. Every declared foreign key into products is checked, across inventory/order/forecast/manifest references. Referenced products may be deactivated; existing history remains available. Future inventory services must reject deleted/inactive products at the intended transaction date.

## Atomicity and storage

New migration `0004_catalog` adds catalog_revisions (53 application tables total), with command FK and SQLite triggers blocking update/delete. Earlier migrations are unchanged. Commands, revision snapshots, audit, notification outbox and product head change commit or roll back together. Successful audit records contain actor/reason/hash; before/after snapshots link to the same command. Existing view-as auditing captures real/effective actors. Queued retries recheck current identity before returning saved results. No network call occurs inside these handlers.

Code: backend/v2/catalog/{models,tables,service,api}.py; integration: backend/v2/app.py; tests: backend/tests/v2/test_catalog.py. Metadata registration follows the P03 extension pattern; migrations create tables explicitly. PostgreSQL runtime and immutable-trigger parity remain unverified.

Local DB was backed up to `data/backups/pre-p04a-20261011.sqlite3` before migration. Integrity check passed at 0004_catalog. The running preview uses the upgraded DB; no example mutations were applied to it. API mutation tests use isolated temporary databases.

See [CHECKPOINT_P04A.md](CHECKPOINT_P04A.md) for final verification and exact remaining steps.

## P04b road-validation adapter (partial)

POST /v2/setup/locations/validate accepts strict numeric lat/lon for admins. Reuses existing routing.snap_to_road with a 3-second budget outside any database connection. Returns raw/snapped coordinates, conservative distance, road name and redacted provider origin/validation timestamp. Current permission is rechecked after provider work. Provider/cache retrieval time and data version are unknown and not fabricated. The 300m threshold is checked before rounding, with an independent geographic distance bound. Invalid/unavailable provider returns 503; >300m returns 422; no straight-line fallback.

This is a preflight response only, not persisted validation or a trusted client certificate. Network create/edit must call the adapter itself and save provenance atomically after revision and permission rechecks. That CRUD remains pending. Automated tests use declared fixtures. Live provider smoke was rejected by automatic approval review pending explicit authorization to send demo coordinate 13.148,80.231 to OSRM; no successful road validation claimed. Provider protocol reference: [OSRM nearest service](https://project-osrm.org/docs/v5.24.0/api/#nearest-service).


## Approved live OSRM check — 2026-10-11

User explicitly approved one test. At 07:31:32 UTC, the configured https://router.project-osrm.org nearest service returned Ok for illustrative coordinate 13.148,80.231. Snapped coordinate: 13.147988,80.231061. Adapter distance 6.738514m (provider distance 6.746160m), within 300m. Validated=true; road name absent and provider data version unknown. No network node was created or edited.

Evidence: .cache/p04-road-smoke.json and newly written .cache/route_cache/e529f52420b053c0b61b557e7e80a0364a68e3f0.json (matching nearest request key and response; last write 07:31:32 UTC). This supersedes the pending-approval limitation for this one smoke test only. It does not validate the full Chennai seed, road legality, or truck access. Network CRUD/persisted provenance and fleet/users still pending; P04 remains partial. Preview left running.


## P04b network update

Network create/read/replace/delete and immutable dated road provenance now implemented; see [CHECKPOINT_P04B.md](CHECKPOINT_P04B.md). Current schema 0005_network, 54 application tables. Existing role network reads resolve effective snapshots; do not read base node fields for current configuration. Next P04c fleet/drivers/users/grants. P04 remains partial, 4/31 complete. Earlier pending-network statements describe prior checkpoints and are superseded by this update.


## P04c fleet/driver update

Vehicle/driver CRUD, dated shifts, capacity/lane/rate and home validation, protected deletion and current driver access are implemented. See [CHECKPOINT_P04C.md](CHECKPOINT_P04C.md). Current schema 0006_fleet (55 application tables). P04 remains partial; next P04d account/grant management and remaining settings effect details. Previous pending-fleet notes are historical.
