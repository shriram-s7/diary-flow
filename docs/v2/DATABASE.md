# P02 storage architecture and runbook

Implemented 2026-10-09. This is the storage foundation, not a deployed v2 API. The existing v1 app still uses its original world; no startup import silently creates, migrates or wipes this database.

## Setup and maintenance

Use the existing project virtual environment. From `backend`:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m v2.db --database ../data/allocra-v2.sqlite3 migrate
.\venv\Scripts\python.exe -m v2.db --database ../data/allocra-v2.sqlite3 check
.\venv\Scripts\python.exe -m v2.db --database ../data/allocra-v2.sqlite3 backup ../data/backups/my-new-backup.sqlite3
.\venv\Scripts\python.exe -m v2.db --database ../data/backups/my-new-backup.sqlite3 restore ../data/my-new-restored-db.sqlite3
```

Backup and restore require a **new destination path** and refuse to overwrite existing files. Restore validates SQLite integrity, foreign keys and Alembic revision; it does not swap the running database. Stop a future v2 server and explicitly change its database configuration after verification. Never copy only a live .sqlite3 file while ignoring its WAL. The snapshot operation uses SQLite's backup API with closed connections after completion.

This checkpoint initializes `data/allocra-v2.sqlite3` at `0002_business`, with no demo users or business data. An initial backup and a restored verification copy are in data/. Local data/backups are ignored by Git; they must be backed up separately from source. Tests use isolated .cache paths and never reset this file.

Pinned dependency closure is in `backend/requirements-v2.lock.txt`, referenced as constraints from requirements.txt. SQLAlchemy2.0.36/Alembic1.14.0 are tested compatible pins, not a claim of being the newest releases. No unrelated dependency upgrades were made.

## Schema coverage

49 application tables plus Alembic's version table. `schema_v1.py` is the frozen snapshot used by migrations 0001/0002; `schema.py` exposes the current metadata. Introduce new schema snapshots/migration revisions for future changes; never revise these historical definitions after this checkpoint. Explicit migrations create tables in dependency order, inside a transaction; application startup does not call create_all.

| Area | Tables |
|---|---|
| Foundation | runs, users, days, clocks, commands, audit, outbox, scheduled_events, scheduler_leases |
| Setup/security storage | nodes, products, drivers, vehicles, role_grants, auth_sessions, settings |
| Forecast provenance | model_artifacts, forecasts, demand_observations |
| Ordering | orders, order_lines, approvals, allocations |
| Stock | locations, inventory_lots, inventory_balances, reservations, stock_movements |
| Plans/live | plans, trips, stops, manifest_lines, routes, motion_checkpoints, disruptions |
| Delivery | delivery_attempts, delivery_lines, pod_confirmations, crate_movements |
| Recovery/communication | loans, urgent_requests, decisions, threads, thread_participants, messages, message_acks, notifications |
| Close | day_closes, close_adjustments |

Nodes represent the plant/hub/booth tree relationally with kind-constrained parent foreign keys and one plant per run. Shared operational identities use `(run_id,id)` and composite FKs to prevent cross-run references. Delivery-date entities refer to the run's Day where applicable. User identity is global; role grants and operational driver records belong to runs. No real user bootstrap/auth workflow is implemented before P03.

Integer units, grams, microsecond timestamps and coordinate e7 integers avoid floating point ledger values. Forecast quantiles use integer milliunits. UTCInstant accepts aware datetime only and round-trips UTC; security wall time and simulated business time are separate columns. JSON is used for immutable snapshots, geometry, model provenance, settings, explanations and command results; entity relationships and core quantities are relational.

Database constraints cover nonnegative/exact quantities, reservation<=stock, accepted+refused+damaged=offered, valid packaging/temperature lane, scope field shape, dates, unique event/command/notification keys, and FK ownership. SQLite triggers make committed command/audit/event records and ledger/close history immutable and reject overlapping vehicle/driver trip intervals, including updates. Cancelling a planned trip releases its overlap constraint; planning policy must still validate the transition.

## Unit of work and command protocol

`open_database` configures foreign_keys=ON, WAL, synchronous=FULL and bounded busy timeout on every connection. Reads use explicit BEGIN; writes use BEGIN IMMEDIATE to serialize read-check-write operations before stale state is read. Each unit of work owns its connection. No shared ORM sessions or in-memory global world is used by v2 storage.

`execute(engine, context, kind, payload, handler, sim_time=...)`:

1. Caller must authenticate and authorize. P02 is an internal persistence library, not a safe unauthenticated endpoint. actor_id from the client must never be trusted directly.
2. Compute canonical JSON hash of command context/kind/payload. Decimal must be encoded as string; nonfinite values/non-JSON data are rejected.
3. In one transaction, return the stored result for an exact retry, reject key reuse with changed inputs, or insert the command and call the handler.
4. Handler uses `effects.connection` and `effects.notify`; it must not commit, open independent domain transactions, or do network/training work. It returns a JSON object.
5. Domain updates, result, completion marker, audit, outbox and optional event journal commit together. Exception/process death before commit leaves no effects. A committed result survives process death before the caller receives it.

`compare_and_swap` applies aggregate edits only at expected revision. `reserve` is a minimal atomic reservation primitive that also checks revision and remaining quantity; concurrent60+60 against100 has one winner. It is **not** the complete FEFO, expiry, location-scope, release/consume or delivery inventory service (P05). Stored balances are a projection; only later inventory services should expose mutations and reconcile them against append-only movements. Direct SQL administration can violate higher-level service policy even when local constraints pass.

Payload changes, stale revisions, unavailable stock and lost leases raise Conflict for a future409 transport mapping. API handlers and exception translation are not mounted at this checkpoint.

## Events, scheduler and outbox

Completed scheduled events are unique per run/date/type/scope. Their input digest prevents an already completed event being replayed with different policy inputs; different invocation IDs with the same event key/input return the original result. A failed event has no completed journal row and can retry safely; pending/failed job orchestration belongs to P08.

One persistent `prototype_scheduler` lease governs the prototype database. Owners use distinct process-instance IDs. Acquire/renew/release use wall time and a monotonically increasing generation. Event writes require a live matching lease checked inside the same write transaction. A replaced or expired owner cannot process events. This primitive does not start any scheduler loop.

Outbox publication is **at least once**: publisher runs outside a DB transaction, then publication acknowledgment is stored. A crash between external delivery and acknowledgment may publish the same immutable event ID twice. Consumers must deduplicate by `(run_id,outbox_id)`; the notification table has that uniqueness with user ID. The database guarantees one business effect, not exactly-once delivery across a network. Scope filtering and actual notification delivery are P03/P16.

## Compatibility and limits

PostgreSQL DDL compilation is tested for every table/index. No PostgreSQL server was started; PostgreSQL locking, runtime migrations, append-only triggers and overlap enforcement have **not** passed acceptance. The runtime currently supports local file-backed SQLite only. Porting to PostgreSQL requires dialect-specific migration enforcement (the triggers above are SQLite-specific) and concurrency/recovery testing. This is explicit groundwork, not a production-readiness claim.

Future services still enforce catalog kind/scope relationships, FEFO, manifests, forecast issuance, auth, legal state transitions and no network under transactions. A successful schema migration is not delivery of those workflows. No v2 data is inferred from old litre-based state.

## References consulted

Explicit SQLite transaction control and connection-local foreign-key behavior follow the [SQLAlchemy SQLite documentation](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html). Programmatic migration connections follow [Alembic's cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html). These sources informed the infrastructure; the domain schema and acceptance tests are project-specific.

## Subsequent P03 revision — 2026-10-11

Current head 0003_auth adds login_limits,auth_audit,demo_users (52 application tables total). AUTH.md documents the connected endpoints and local auth-demo data. The earlier P02 empty-database statements describe that historical checkpoint, not the current seeded local database. Existing revisions remain unchanged.

## Current schema: 0004_catalog (2026-10-11)

Adds append-only catalog_revisions: 53 application tables. Local DB upgraded after data/backups/pre-p04a-20261011.sqlite3 backup; integrity passed. See [CATALOG.md](CATALOG.md). Dated packaging/settings must resolve through the catalog service; base product columns and runs.settings are preserved baselines, not current effective values. Existing 0001–0003 unchanged.


## P04b network update

Network create/read/replace/delete and immutable dated road provenance now implemented; see [CHECKPOINT_P04B.md](CHECKPOINT_P04B.md). Current schema 0005_network, 54 application tables. Existing role network reads resolve effective snapshots; do not read base node fields for current configuration. Next P04c fleet/drivers/users/grants. P04 remains partial, 4/31 complete. Earlier pending-network statements describe prior checkpoints and are superseded by this update.


## P04c fleet/driver update

Vehicle/driver CRUD, dated shifts, capacity/lane/rate and home validation, protected deletion and current driver access are implemented. See [CHECKPOINT_P04C.md](CHECKPOINT_P04C.md). Current schema 0006_fleet (55 application tables). P04 remains partial; next P04d account/grant management and remaining settings effect details. Previous pending-fleet notes are historical.
