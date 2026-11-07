# P01 domain and transport contracts

Implemented at `backend/v2/domain/`; these modules are pure and are not mounted into v1 FastAPI. P02/P03 implement persistence and authorization; P18 implements the planner. `Value` models reject unknown fields, are frozen, and validate scalar constraints. Nested collections in the planner are tuples/frozensets. Do not use Pydantic `model_copy(update=...)` on untrusted input: that method bypasses validation. Reconstruct validated models at service boundaries.

## Units and formulas

`Packaging`: SKU, ml/g pack size, units/container, conservative full-container grams, returnable/type/lane, optional paired unit gross/tare grams. `pack` operates per stop/SKU group. A partial container still occupies one slot. `PackedQuantity` keeps volume, product mass, conservative planning mass, optional measured gross and returnable count separate. Unit counts reject floats, strings and booleans. No arbitrary float rounding into stock.

`buffer_units`: exact rational squared spread and integer square root to calculate ceiling without near-square floating point errors. `hub_default` expects already filtered open booths, one spread per order, and usable unreserved stock. `capped_spare_units` limits desired units by supply, slots, conservative mass and lane; it does **not** reserve inventory or validate lot expiry. P05/P18 own those checks.

`requires_approval` cross-multiplies absolute difference and threshold, including zero defaults. Caller decides closed-booth exception/time/role. `travel_minutes` applies supplied segment weather factors and multiplies hours by60; caller must split unaffected segments. `stop_times` returns arrival and completion including receiving-window wait; durations round up to microsecond precision. `slack_minutes` compares deadline to completion. `trip_cost` expects total distance including return; `utilisation` rejects over-capacity values. No helper implies network routing or real sensor validation.

`daily_events` resolves D-1/D defaults to UTC; business timezone is fixed modern Asia/Kolkata (UTC+05:30). Prototype dates before2000 unsupported. `shift_on` interprets end time earlier than start as following day; equal start/end rejected. No persisted clock is implemented here.

## Commands and errors

CommandContext contains command_id, run_id, authenticated actor_id (server must supply/verify it), expected_revision, delivery_date, aware occurred_at and optional reason. Transport idempotency uses command_id; P02 stores scoped identity+payload hash+result. A command context alone proves neither authenticity nor idempotency.

CommandResult[T] contains schema_version=1, correlation_id, committed revision, separate sim_time/wall_time and typed data. Decimal values serialize as JSON strings to preserve precision; integer units remain JSON integers. Clients must display backend-computed quantities, not reconstruct formulas from those strings.

Example quantity result:

```json
{"units":25,"slots":2,"planning_g":26000,"returnable_containers":2,"litres":"12.5","product_kg":null,"measured_gross_g":null}
```

ErrorDetail: code, message, optional field/current_revision. Future v2 HTTP mapping:400 malformed transport/JSON;401 missing or invalid identity;403 unauthorized scope;404 absent resource;409 stale revision/illegal transition/idempotency payload mismatch;422 validated domain input/constraint failure;429 throttling. V1's existing400 validation remains unchanged. Services may consistently conceal inaccessible IDs with404, but must test that policy. No HTTP handlers are added by P01.

## Lifecycle edges and required service guards

The exact transition tables are in `lifecycle.TRANSITIONS`; illegal/unknown/self transitions raise InvalidTransition. Deduplication belongs before transition evaluation, so a repeated successful command returns its previous result rather than attempting a self-transition.

| Kind | Main path / alternatives | Additional guards for future services |
|---|---|---|
| order | draft -> confirmed -> locked -> allocated -> dispatched -> delivered -> closed; dispatched may enter exception -> delivered/closed | role, cut-off, valid allocation, accepted receipt or audited exception; zero orders may advance without physical trip via explicit service logic |
| proposal | pending -> approved/rejected/expired/superseded; all terminal | proposal version, approver scope, exact cut-off and supersession |
| trip | planned -> loading -> dispatched -> returning -> completed; planned/loading cancellation; dispatched/returning blocking then dispatched/returning | stock, driver/vehicle, shift, manifest; blocked trip must retain interrupted leg context, cannot choose arbitrary resume direction |
| receipt | pending -> unconfirmed -> confirmed or disputed -> confirmed | actual custody, receiver/challenge, disjoint accepted/refused/damaged, inventory transaction |
| event | pending -> waiting/running; waiting -> running; running -> completed/failed/waiting; failed -> running | dependency and scheduler lease; atomic effects/journal; completed immutable |
| day | open -> closing -> closed/closed_with_exceptions or back to open | reconciliation and authorized explicit exceptions; closed data immutable |

Pending proposals are a separate aggregate, not the effective order state. Cancellation after dispatch is a recovery operation, not deletion. Order updates before cut-off keep the current lifecycle state and increment aggregate revision. Transition helper checks edges only; it is not authorization.

## Planner protocol

PlannerInput: run/revision/day/level/source/dispatch, allocation lines and completion deadlines, packaging catalogue, vehicles with driver/home/dated shift/simultaneous lane configurations, available stock lots/expiry and directed road km/minutes. Vehicle default speeds/rates remain external settings until P18 snapshots them with all richer operational inputs. No coordinates are required by the pure protocol: routing workers resolve directed legs before invoking it.

PlannerOutput: input_revision, trips with vehicle/driver/departure/return, ordered stops with arrival/completion and allocation-unit assignments, total closed-tour km, unplanned allocation-unit reasons. Split assignments allowed; `validate_coverage` ensures exact planned+unplanned conservation, input revision, vehicle/driver references, receiver match and unique stop IDs. It is explicitly **not** the full P18 capacity/lane/shift/route/expiry/concurrent-driver feasibility validator. Do not dispatch based solely on this check.

The protocol may gain versioned fields as P18 requirements become concrete; no optimization algorithm, live planner, persistent API or user-visible v2 feature is claimed by this checkpoint.
