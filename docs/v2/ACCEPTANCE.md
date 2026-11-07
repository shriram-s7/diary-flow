# Acceptance and verification plan

This is a future test plan, not passing test evidence. Task IDs refer to PROMPT_BOOK.md. Tests must exercise observable behavior and invariants, not merely mirror code. All mocks must be declared; fake-road tests cannot verify real road access.

## Phase gates

| Gate | Required tasks | Evidence required |
|---|---|---|
| G0 specification | P00 | Adopted policy register, tracked authoritative spec, contradictions resolved or explicitly gated. |
| G1 foundation/setup | P01–P07 | Migration/restart tests; scope denial tests; network/catalog/import CRUD; admin screens; valid seeded network and feasible baseline report. Auth moved earlier than PDF to avoid insecure APIs. |
| G2 clock | P08–P09 | Midnight/cut-off, duplicate event, crash recovery, rewind isolation, UI calendar tests. Clock service interfaces may use fixtures until business services exist; full equality rerun at G7. |
| G3 forecasting | P10–P12 | Reproducible source manifest and data, untouched holdout report, baseline comparison, eight-date inference, synthetic labels. Failed model metric leaves promotion incomplete. |
| G4 ordering | P13–P16 | Scoped booth/hub apps, zero/default/closed/approval boundaries, race-safe cutoffs, notifications. |
| G5 allocation/planning | P17–P21 | Supply and packing invariants, inventory receipt gate, both levels, feasible and infeasible plans, loan transaction, load sheets, dashboard consistency. |
| G6 live/mobile | P22–P27 | Restart-safe live engine; urgent quantities; receipt/POD/ledger; offline replay; actual-device APK/GPS; reconciled day close. |
| G7 final demo | P28–P30 | Full story day, role/device consistency, measured reliability, handoff/runbook and rollback; no fabricated completion. |

## Essential test vectors

| ID | Scenario | Expected result |
|---|---|---|
| Q01 | 25 units, 24/container, 500 ml | 2 slots, 12.5 L; kg explicitly conservative or measured. |
| Q02 | Two stops each 1 unit, same SKU | 2 slots without cross-stop crate sharing. |
| Q03 | 12 spare units; residual kg insufficient for 1 conservative full crate | No spare loaded; never round into invented capacity/supply. |
| Q04 | Nonreturnable carton delivered | Vehicle slots increase, returnable-crate debt does not. |
| O01 | Default 100; requests 130 and 131 | 130 direct; 131 pending with reason. Hub threshold 120/121. |
| O02 | Default zero -> request 0 / 1 | No division by zero; zero accepted, positive approval. |
| O03 | Closed booth with prior forecast | Orders, buffer contribution and allocation zero; forecast history retained. |
| O04 | Booth approval at 17:59:59 vs 18:00 | First may commit; second denied/expired, hub default uses effective version. |
| O05 | Same idempotency key with another payload | Conflict, not overwrite or second command. |
| A01 | Orders 100/100; Normal; supply 120 | 60/60; ordered and allocated remain distinct. |
| A02 | Critical100/Normal100; supply100 | 100/0 under adopted D06; log why. |
| A03 | Orders3/7; supply8 | 2/6; integer allocation sum8. |
| A04 | Random orders/supply/priorities, permutation of input | Nonnegative, <=order, sum=min(supply,total eligible), deterministic, closed=0. |
| I01 | Expected receipt100, accepted60 | Only60 enters available stock; dependent plans reflect40 shortage. |
| I02 | Two concurrent reserves each60 against100 | At most100 reserved; loser sees conflict/replan. |
| I03 | Expiry before projected acceptance; damaged return | Excluded/quarantined even if present at dispatch. |
| C01 | Advance D-1 15:50 to D 06:00 | Ordered events run once with scope dependencies; midnight dates correct. |
| C02 | Kill before commit / after commit before outbox publish | Retry has one business effect and eventually one recipient notification. |
| C03 | Rewind after queued offline delivery | Old run retains history; stale queued command cannot mutate new run. |
| C04 | Next event vs whole day, same seed/routes/actions | Same normalized business records, balances and metrics. |
| P01 | Overweight but under slot limit; wrong lane; driver overlap | Each infeasible, reasons explicit; no dispatch bypass. |
| P02 | Overnight trip with service/return beyond shift | Rejected pre-dispatch; live overrun alerted distinctly. |
| P03 | Allocated demand exceeds fleet capacity | Unplanned lines; planned+unplanned=allocated, no false all-served result. |
| P04 | Plan completes after inventory revision changed | Stale result rejected and job rerun/reviewed. |
| L01 | Roadblock on route; detour found / costly / timeout twice | Auto-reroute / reroute+alert / safe blocked; tick remains responsive. |
| L02 | Two urgent requests competing for same spare | At most available SKU units used once; preserve original commitments. |
| D01 | Driver repeats POD; receiver taps while code is submitted | One acceptance and one inventory/crate movement. |
| D02 | Offered24, damaged2, refused3, accepted19 | Conservation; no double subtraction; retained cargo tracked. |
| D03 | Handover without receiver confirmation | Unconfirmed visible, not silently accepted; close exception explicit. |
| D04 | Offline command after driver reassigned/role revoked | Rejected or explicit resolution; no cross-scope mutation. |
| D05 | Empties collected exceed recorded debt | No silent negative debt; explicit counted adjustment/dispute policy. |
| M01 | Change hidden future actual/weather values | Historical issued forecast does not change. |
| M02 | Forecast demand100, delivery60 due shortage | Accuracy compares to demand truth, not delivery60. |
| M03 | All-zero actual series | WAPE undefined with reason; MAPE zero exclusions counted. |
| M04 | Re-run generator in another event order | Same per-key actuals and dataset checksum. |
| M05 | Unseen booth, absent/corrupt model, horizon7 | Labelled fallback with no unavailable future lags. |
| S01 | Each role requests other hub/booth/driver resource by ID | Denied for read/write/export/file/WS; admin read-only operations upheld. |
| S02 | JWT expired on wall clock while sim paused/rewound | Invalid; simulation cannot extend authentication. |
| U01 | Phone offline refresh, queue, reconnect, server restart | Cached trip visible; queued state distinct; exactly-once reconciliation. |
| U02 | HTTP phone LAN vs trusted HTTPS | Limited mode explains unsupported offline; HTTPS PWA tested on device. |

## Reliability measurements

Use a documented machine, runtime versions, seed, warmed/cold route-cache state and sample size. Record tick duration p50/p95/max, slow ticks and longest heartbeat gap; target tick <200 ms. Measure API p95 <100 ms for named ordinary cached reads/mutations at demo scale (1 plant/7 hubs/70 booths/~30 vehicles), including clock at120x and multiple scoped clients. Route search and training should be asynchronous jobs; reporting only their quick enqueue latency is not end-to-end job performance. Record job completion latency separately.

Exercise healthy and unreachable routing providers with many disruptions; assert no blocked event loop, no network under database transactions, no process restart and no entry into a closure. Normal deterministic tests use fixed route fixtures; a separate real-route smoke test verifies snapping/geometry with provider availability documented. Re-run full business-day equality after all handlers and story actions are integrated.

## Screens and devices

Verify desktop 1440x900 and 1280x720; mobile 360x800 and 390x844 plus at least one real low-end Android device. All six roles must demonstrate scoped values and the same server totals for a shared authorized resource. Check keyboard access, focus, contrast, touch targets, scroll, panel overlap, connection status, empty/loading/error/conflict states, print layout and daylight readability. Device evidence records Android/browser versions and permissions; desktop emulation does not pass background GPS or APK acceptance.

## Completion evidence template

For every task record commands, exit status, relevant output/report paths, environment, scope/mocks, and manual tests. An unavailable phone, certificate, routing provider, real sales dataset or Android SDK is a named missing check, not a pass. Build success is not end-to-end success. Trained model files are not evidence of forecast quality. Every task remains partial if its required gate is unverified.
