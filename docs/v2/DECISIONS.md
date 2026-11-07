# Specification addendum and decision register

Status: **ADOPTED FOR PROTOTYPE-1** on 2026-10-09 under the user's implementation request. See TARGET_SPEC.md for clarifications. This is engineering adoption, not operator validation or a claim that all decisions are implemented. Agents may implement reversible technical choices within their authorization, but must not silently claim business validation. Changes to adopted policies require new decision revisions plus regression examples. Source: PDF sections cited in REVIEW.md.

## D01 — Canonical quantities and packing

Store nonnegative integer `units`; store SKU pack volume in ml or mass in g with an explicit dimension. `litres = units * pack_size_ml / 1000`; do not convert mass products to litres. Store kg as decimal or integer grams, never binary-float ledger balances. `crate_slots = ceil(units / units_per_container)` per physical packing group, zero units means zero slots. Prototype packing group is `(trip, stop, SKU)` with no cross-booth sharing; optional future repacking must create explicit packing records.

Distinguish `container_type`, `returnable`, physical containers and vehicle capacity slots; cartons need not be returnable. Use conservative `planning_kg = crate_slots * full_container_kg` until unit weight and tare are supplied. Label that as an upper-bound estimate, not actual net product weight. If measured weights exist, actual gross = units * unit_gross_weight + containers * tare. Plant-to-hub packing may consolidate; hub outbound packing is recomputed after unpack/repack and creates its own crate movements.

Examples: 25 units of 24/unit crates -> 2 slots; 1 unit at each of 2 booths -> 2 outbound slots; 25 x 500 ml = 12.5 L. Two SKU lines of 12 units each do not automatically share a crate.

## D02 — Demand and delivery are separate

Store `synthetic_demand_units`, `ordered_units`, `allocated_units`, `shipped_units`, `accepted_units`, `refused_units`, `damaged_units`, and optional `observed_sales_units` separately. Day-close delivery actuals are accepted units; forecast accuracy actuals are synthetic demand for the demo. If only delivered data exists in a real pilot, record censored/unobserved demand rather than relabelling delivery as demand. Damage/refusal categories must be disjoint or have explicitly separate dimensions so they are not subtracted twice. Historical demand rows do not constitute past trip/POD records.

## D03 — Persistence and effectively-once processing

One database is the authority. Every accepted business command commits domain mutations, audit, deduplication result and outbox records together. Unique command keys plus payload hashes reject reusing a key for different payloads. Events keyed by `(run_id, delivery_date, event_type, scope)` become complete in the same transaction as their effects. Crash before commit retries safely; crash after commit returns prior result and republishes only the unsent outbox. Workers may retry; business effects occur once.

SQLite prototype: local disk, foreign keys enabled, short transactions, bounded busy timeout, WAL, one scheduler/writer coordinator. SQLAlchemy repositories own sessions per unit of work, no session shared across threads. Motion derives from committed route/progress checkpoints and elapsed simulation time. Acceptable restart contract: no committed business data lost; deterministic replay from last checkpoint plus persisted commands, not “every rendered pixel persisted.” Plan/routing jobs run outside locks and install only when input revision still matches. PostgreSQL readiness requires migration/test evidence, not just use of an ORM.

## D04 — Rewind and destructive resets

`SimulationRun` owns its clock, days, settings snapshots, commands and all derived stock/crate history. Rewind creates a new run from a consistent checkpoint or replay; later records in the old run stay intact. No rewind of wall-clock production history. Reset/load defaults creates or replaces only the selected demo run after explicit UI confirmation; it cannot delete users/real history implicitly. Expired device queues reference the old run and must never mutate its replacement. History inspection is read-only and does not move the live clock.

## D05 — Cut-offs and pending changes

Quantities have an effective committed value and an independent requested value. Unapproved proposals never become committed totals. At 16:00 ordinary booth editing locks; pending large changes may be decided until strictly before 18:00. At 17:00 hub defaults use effective booth quantities and show unresolved proposals separately. Every accepted booth decision recomputes the hub default revision. At 18:00 unresolved booth changes expire to suggestions, dependent hub defaults recompute, stale hub proposals are revalidated against the new default, unresolved hub proposals fall back, then hub orders lock and allocation runs atomically in that sequence.

Boundary rule: server simulation time < cut-off permits action; equality is closed. Server serializes approval vs scheduler races. Mark Closed tomorrow is a separate explicit action before booth cut-off, exempt from percentage approval and audited; it zeros all booth lines and buffer contributions. Post-cut-off closure is a manager operation. Changes exactly 30%/20% do not need approval; greater do. Default zero/request zero needs none; zero to positive requires approval with a reason. A new request supersedes old pending approval.

## D06 — Deterministic shortage allocation

This interpretation resolves ambiguous source language; owner may choose another policy in P00. Per SKU remove closed/zero-order receivers. Orders and supply are integer units. Supply <= 0 gives zero; supply >= total gives each its order and leaves excess at source. Floor target per receiver = 0.8 * order (rational arithmetic).

If supply covers all floor targets: apportion integer units across targets using largest remainder, then allocate remaining supply proportional to unmet demand, respecting each order cap. If supply cannot cover targets: serve Critical receivers up to full orders first (proportionally among them when insufficient), then share any remainder proportional to High and Normal orders together. This deliberately does not give High a hidden second scarcity tier; a different tier policy must be configured and tested explicitly. Stable ties use receiver ID. Largest remainder never allocates above order and redistributes cap surplus. Log algorithm version, priority, target, supply and tie resolution. “80% target” is UI wording, never “guaranteed” under impossible supply.

Examples: orders A=100/B=100, both Normal, supply=120 ->60/60. A Critical=100, B Normal=100, supply=100 ->100/0. Two Critical orders 100 each and supply=1 ->1/0 by stable ID. Orders 3/7, supply=8 ->2/6. Manual override requires reason, closed-receiver zero, quantities <= orders unless the order itself is amended, supply conservation and pre-dispatch revision check. Unit allocations never round upward to create supply; crate slots are a separate derived constraint.

## D07 — Buffers, spares and supply

`spread_units_i = max(0, p90_i - p50_i)`; `buffer_units = ceil(z_hub * sqrt(sum(spread_units_i**2)))`. `hub_default_units = max(0, sum(effective_booth_orders) + buffer_units - usable_unreserved_stock_units)`. Exclude closed booths. This RSS buffer is a heuristic, not a proven service-level quantile: p90-p50 is not standard deviation and demand correlations are ignored. Report/calibrate it with aggregate backtests before operational use.

`desired_spare_units = ceil(z_van * sqrt(sum(spread_units_i**2)))`, chosen instead of source's ambiguous round. Pack spares separately by SKU, reserve them once from hub inventory, and cap them by BOTH residual slots and kg, lane compatibility, expiry, and actual unreserved stock. No extra phantom stock: hub buffer funds vehicle spares; spares are not added again to the hub order. Example spread 24, z_van .5 ->12 units ->1 slot at 24 per crate, not 12 crates. Settings/rounding version stored with each derived record.

## D08 — Lanes, shelf life and lots

Use lot/batch identity and expiry for dairy inventory now, because usable stock already depends on shelf life. FEFO for available suitable lots. Require a lot to remain usable at projected receiver acceptance, including configured margin; expiry at dispatch is insufficient. Damaged/expired/unknown-quality returns are quarantined, not automatically sellable. Lane support includes an explicit compatible simultaneous-lane configuration or compartment capacity; do not assume one chamber supports ambient/chilled/frozen together. Prototype lane/temperature compliance is a configured assumption, not a sensor-verified claim.

## D09 — Stock reservations and receipts

Append-only stock movement ledger for receive, reserve/release, load, accept, return, damage, expiry and counted adjustment. Internal transfer balances source/destination or in-transit custody; audit adjustments with reason. Never use allocation as receipt. Confirmed accepted hub receipt credits inventory once. Dispatch consumes reserved available stock. Override unlocks verified on-hand stock only, never negative stock. Short receipts reallocate/replan undispatched work; already committed delivery custody remains factual. Counts and balances reconcile per SKU/lot/location, including vehicles.

## D10 — Planning feasibility

Hard constraints: source ownership, simultaneous driver/vehicle availability, slots, kg, lane/compartment compatibility, usable inventory, no closure crossing, shifts including service and return. Soft objective: maximum allocated units served, delivery deadlines, fewest vehicles, distance, deterministic ties. Output `unplanned` lines with machine-readable reasons when no feasible plan exists; dispatch cannot claim full success. Every planned allocation is assigned once, and planned+unplanned equals allocation. Freeze a versioned manifest on dispatch; no direct UI edits to dispatched plans. Live unavoidable lateness/shift overrun creates a manager exception; it never silently rewrites original compliance.

## D11 — Service cycles and lending

One normal order cycle per booth/date. Multiple deliveries allowed only as explicit split-lane, partial recovery, urgent top-up or lending recovery exceptions. Separate home hub from effective fulfillment hub for `(run, date, booth)`. Lend only before either affected booth manifest dispatches in prototype; receiving manager accepts, both plan revisions and stock reservations change atomically, otherwise conflict/replan. Order ownership, fulfillment scope and crate-custody attribution are explicit. One accepted loan per booth/day; permanent network assignment stays unchanged. Post-dispatch lending is deferred, not disguised as a normal edit.

## D12 — Authorization and time domains

Six roles from §6. Server scope checks on queries, mutations, reports, files and WebSocket subscriptions; driver sees only assigned trips, not all world state. Hash PINs with a suitable password hash, rate-limit attempts, revoke sessions and redact tokens/phones/PINs in logs. JWT signature/expiry validation and server-side current user/scope checks. Security token expiry and login throttling use wall time; cut-offs use simulation time. Prototype one-tap login/impersonation only in explicit demo configuration; record real actor plus impersonated actor. Admin operations read-only except setup and authorized prototype controls; plant cannot mutate a hub's van operations. Different surfaces may share one identity with explicitly assigned roles.

## D13 — Offline delivery and POD

IndexedDB durable action queue and cached assigned-trip snapshot; each command includes run, actor, trip/stop, expected revision, client-generated ID, client occurrence time and server receipt time. Server rechecks current permission and manifests; exact retries return original result; conflicts remain visible for resolution. Never last-write-wins inventory. Clear cached data on logout/reassignment where possible and reject revoked queued actions server-side.

Receiver confirmation and custody movement are separate states: physical handover recorded, awaiting confirmation, confirmed, disputed. Unconfirmed handover is visible in provisional custody but not accepted receipt/usable receiver stock until policy confirms it. Four-digit challenge is scoped to receiver/stop, short-lived on wall time, rate-limited, hashed, single-use; driver cannot fetch it. Offline entries are provisional until server validation. App confirmation and code retries converge on one acceptance. Day close blocks or explicitly records unresolved exceptions; late sync cannot silently rewrite a closed record. Corrections use audited adjustment records.

## D14 — ML contract

One global model family across booths/SKUs, normally 3 LightGBM quantile regressors (alpha .1/.5/.9); not one fitted model that magically emits all quantiles. Features only as known at issue time: prior observations, shifted rolling windows, known calendars, weather forecasts or normals (labelled), never realized future weather/demand. At 14:00 D-1, yesterday-to-delivery lag may not be final; use actual availability timestamps. Today through today+7 inclusive means eight forecast dates with issue/horizon fields. Future lags require recursive forecasts or direct-horizon models, not hidden future actuals. Cold-start fallback pooled by SKU/type; deterministic baseline remains available on missing model or failed promotion.

Freeze generator/config seed, data hash, cutoff, train/tuning windows and final 60-day holdout. Split by date across ALL booths/SKUs; rolling-origin one-day and multi-day evaluation uses only information available at each origin. Tune before holdout; do not keep changing seed/data until test wins. Report median WAPE versus weekday-last-week plus per-SKU/type metrics, pinball loss, interval coverage and width. MAPE excludes zero-actual rows with exclusion count; WAPE undefined when denominator zero. Enforce nonnegative monotonic quantiles, disclose correction. Failing baseline gate means NOT ACCEPTED for ML promotion; workflow may continue on explicit labelled baseline mode. A future change in build order must record that gate exception.

Reasons may use validated model contributions; otherwise label them contextual factors, not explanations of why the model made a decision. Synthetic ground truth at close is deterministic per `(generator_version, seed, booth, SKU, date)`, independent of forecast/policy and event execution order. Same-generator metrics demonstrate mechanics only; add changed-noise/correlation and sparse-history stress tests. Forecast regeneration creates a new issuance, never overwrites the forecast used for an order.

## D15 — Phone and network delivery

Use trusted HTTPS/WSS for installable/offline PWA on LAN phones; document certificate/device setup or choose an authorized trusted deployment later. HTTP LAN remains limited browser-demo mode with unavailable features labelled. Separate secure APK connection config. Real-device matrix covers offline refresh, queued POD replay, background GPS, permissions, battery restrictions and app restart. Simulator is default; do not mix simulated progress and phone GPS as two authorities. Navigation link hands off to external maps; offline road-cache does not imply offline third-party maps or background tracking works.

## D16 — Deterministic demo and history

Story steps are scheduled scenario actions through real domain services, not independent UI-only state. Idle clock intervals may jump; live motion <=120x, narration does not silently pause the clock. Exact comparison uses same run inputs, routes, seed, settings, command schedule and deterministic scheduler; normalize IDs/wall timestamps in record comparison. A jump to a story step replays predecessors or restores a consistent checkpoint. Synthetic historical operations require actual simulated runs; otherwise show only demand/forecast history and unknown operational KPIs.

## D17 — Dated time and formulas

Delivery day D includes preparatory events on D-1. Store UTC instants plus business zone Asia/Kolkata and delivery_date; preserve simulated vs wall timestamps. Resolve overnight shift dates explicitly (e.g. D-1 20:00 to D 02:00). `travel_min = road_km / adjusted_kmph * 60`; rain .6 and traffic .4 apply only on affected segments, multiply when both apply. Service = 5 + .2*handled_crate_slots minutes; source loading duration configurable. Return leg counts for shift, cost and fuel. ETA is arrival; completion = arrival + waiting + service. Propose booth ready-by as unloading completion deadline (stricter than source arrival-slack); expose both and record P00 decision. Risk from deadline minus selected promise timestamp: >=30 Low, >=10 Medium, >=0 High, else Late. No client formula copies.

## D18 — Scope boundary

V2 dairy prototype only. APP_NAME configurable with Allocra as working value. No pharma profile, medical claims, payments, raw collection, production scheduling, multiple plants, native iOS or OR-Tools upgrade. Generic SKU/lot interfaces may enable later work; those features must not expand this project's definition of done.

## Adoption record

| Revision | Decision IDs | Status | Actor/date | Rationale/evidence |
|---|---|---|---|---|
| review-1 | D01–D18 | Proposed only | Engineering review, 2026-10-08 | No implementation requested in this delivery |
| prototype-1 | D01–D18 | Adopted engineering defaults | Codex, 2026-10-09 | User authorized implementation; TARGET_SPEC.md resolves packing, rounding and completion-deadline details. Operator assumptions remain unverified. |
