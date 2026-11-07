# Feasibility and flaw review

## Verdict

The core idea is sound: link forecast suggestions, confirmed orders, constrained allocations, two-level distribution, delivery evidence, and reconciliation. Retaining FastAPI, React, MapLibre and the routing/disruption work is sensible. One plant and about 70 booths is a useful prototype boundary. Three role-specific phone surfaces can share one React codebase; three unrelated applications and backends are unnecessary.

It is **not a flawless or production-ready specification**. Several rules contradict one another or omit the information needed to implement them safely. Fix the design before agents train models or rebuild operational screens. This review is an engineering review of the document and repository, not validation of Chennai dairy practices, cold-chain performance, demand realism, or pharmaceutical suitability.

## Findings that change the design

| ID | Severity | Source | Problem and concrete consequence | Recommended resolution |
|---|---|---|---|---|
| R01 | Critical | §§4, 19 | Units, physical crates, cartons, and mass are conflated. Two booths ordering 1 unit each require two delivery crates if not repacked; ceil(2/24)=1 undercounts. Multiplying a partial crate by full-crate weight is not actual product mass. | D01: integer units, packing groups, conservative planning mass, separate returnable assets. |
| R02 | Critical | §§12.2, 16 | Delivered units are called actual demand, while day close also generates actual demand. A stockout delivering 60 against demand 100 would teach the model that demand is 60. | D02: latent synthetic demand, ordered, sold if available, accepted delivery, losses and unmet demand are different fields. |
| R03 | Critical | §§15, 20 | Exactly-once events, restart, offline retries, and rewind are promised without transactional semantics. Replaying receipt can double stock and crates. Rewinding one day can invalidate all later balances. | D03/D04: idempotent business commands, event journal/outbox, version checks, isolated simulation runs; never rewrite operational history. |
| R04 | High | §§7, 8 | Hub default computes at 17:00 while booth approvals can remain unresolved until 18:00. It can roll up quantities that later disappear. “Dependencies wait” conflicts with expiry/fallback. | D05: committed effective quantity plus separate pending proposal; finalize approvals and recompute dependent defaults at 18:00 atomically. |
| R05 | High | §§9, 19 | “Guaranteed 80%” cannot be guaranteed with 20% supply. Priority order versus proportional fallback is ambiguous; Critical receivers alone can exhaust supply. | D06: deterministic policy with a target floor, scarcity tiers, integer largest-remainder apportionment and stable ties. |
| R06 | High | §19 | Van spare formula produces units but is labelled crates. A spread of 24 units can become 12 crates instead of 12 units. Sum-of-squares buffering assumes independence despite correlated rain/festival effects. | D07: name spare_units, convert through packing, reserve supply, label RSS as heuristic and calibrate. |
| R07 | High | §§4, 5, 10 | A lane flag does not prove mixed-temperature carriage is feasible. Insulation alone does not prove 2–8 °C throughout a trip. Shelf life at dispatch can expire before arrival. | D08: configurable compatibility, operational qualification assumptions, lot expiry through expected acceptance; production validation external. |
| R08 | High | §§3, 10 | Expected plant allocation is not received stock. Split truck receipts, short receipts, hub stock and reservations are unspecified; vans can double-use or invent inventory. | D09: lot-based inventory movements/reservations; partial receipts unlock only actually available units. |
| R09 | High | §§10, 11 | “Every allocated crate on exactly one trip” is impossible with inadequate fleet/lane/shift capacity. Reroutes can also overrun shifts. | D10: explicit unplanned quantities/infeasibility; never silently overload or equate shortage with success. |
| R10 | High | §§3, 10 | One delivery per booth conflicts with split lanes and urgent top-ups. Temporary lending changes ownership, stock, scope, plans and crate debts. | D11: one normal service cycle, explicit multiple-stop exceptions; dated lending with bilateral acceptance and revision checks. |
| R11 | High | §§6, 12, 20 | PIN+JWT alone lacks rate limits, revocation, WebSocket scoping and offline authorization. Demo impersonation can bypass roles if implemented broadly. | D12: scoped server authorization everywhere, security time independent of simulation, isolated demo mode and explicit impersonation audit. |
| R12 | High | §§12, 20 | Offline edits can arrive after reassignment/day close or twice. A 4-digit POD code needs expiry, attempts and single-use handling. | D13: durable outbox, immutable command IDs, server conflict handling; separate custody and confirmation states. |
| R13 | High | §§16, 24 | One global LightGBM model does not automatically produce three quantiles; future weather/lag leakage can make accuracy falsely excellent. Today+7 is an eight-date horizon. | D14: global model family with quantile heads, as-of features, chronological evaluation and explicit horizon. |
| R14 | High | §16 | Beating the baseline is a desired result, not a guaranteed implementation property. A generator and model sharing assumptions can produce self-congratulatory metrics. One year cannot establish festival generalization. | D14: untouched holdout, naive baseline fallback, multiple generator stress regimes, honest failure report and separate research gate. |
| R15 | High | §§12, 20, 21 | Plain HTTP over a phone's LAN IP does not supply the secure context needed for service workers. APK/GPS/background/offline behavior requires real-device testing. | D15: trusted HTTPS for full PWA demo, honest basic-HTTP fallback, device acceptance evidence. |
| R16 | High | §§15, 21 | 14:00 to 06:00 is 16 hours: at 120× it takes 8 minutes, before interaction; a full 7-minute demo needs idle-time event jumps. Live network nondeterminism defeats strict replay equality. | D16: fixed cached routes and recorded actions in deterministic tests; story fast-forwards idle intervals. |
| R17 | Medium | §§7, 15, 19 | Minutes since midnight fail on overnight shifts/dates; road km/speed is hours while service/slack are minutes. Ready-by could mean arrival or completed unload. | D17: dated timezone-aware instants, ×60 travel conversion; expose arrival and completion distinctly. |
| R18 | Medium | §§15, 17 | Generated historical demand does not imply historical actual deliveries, route costs or POD evidence. Synthetic calendars could invent operational records. | D02/D16: demand history separate from simulated operational runs; explicit provenance per record. |
| R19 | Medium | §§18, 20 | SQLite durability and fast ticks require an execution model. Persisting every UI frame across many workers is not a viable design. | D03: short writes, a single prototype scheduler, snapshots plus durable events, benchmark measured endpoints. |
| R20 | Medium | §§1, 22 | Amoxicillin is mentioned up front but explicitly deferred later. Dairy transport assumptions must not become pharmaceutical claims. | D18: dairy-only v2; generic packaging interfaces but no pharma functionality or training in this release. |
| R21 | Medium | §§18, 23 | Seed coordinates, crate weights, fleet sizes and daily total demand have not been validated. Three 400-crate trucks may or may not cover generated demand. | Seed capacity feasibility report, assumption labels, explicit infeasible scenarios; do not tune data just to fake success. |
| R22 | Medium | §§18, 20 | Road-cache warming alone does not make maps/tiles/geocoding or live navigation available offline. Third-party routing outages can lead to dangerous straight-line instructions. | Provider limits/attribution review; cached route/list fallback; approximate routes never treated as safe real navigation. |

## Diagram facts recovered visually

Page 8's timeline, missing from extracted prose: D-1 14:00 suggestions; 16:00 booth cut-off; 17:00 hub defaults; 18:00 hub cut-off/allocation; plant runs 20:00–23:30; hub vans D 01:00–05:00; day close D 06:00. These are configurable demo assumptions. Page 23 visibly contains raw LaTeX text, so use the corrected formulas in DECISIONS.md rather than copying that rendering into UI.

## Actual repository baseline

Reviewed at commit `05a9d87c2633e562d28c70b308fd55425c14c556`; initial tracked working tree was clean. No repository AGENTS.md was found. Root CLAUDE.md is an existing v1 specification and is ignored by .gitignore: **a handoff that only overwrites that file will not be portable through Git**.

| Area | Evidence | Assessment |
|---|---|---|
| Backend domain | backend/models.py: Plant.milk_available_l, Hub.demand_l, Truck/Stop | Litre-based v1; substantial new schema required. |
| Persistence | backend/state.py; backend/api.py lifespan resets empty world | In-memory operational state, no database migration system in requirements. Route disk cache is not business persistence. |
| Planning | backend/planner.py: plan_dispatch, plan_world | Adapt algorithm behind new interface; cannot replace litre field names and assume correctness. |
| Live engine | backend/disruptions.py, control_tower.py, timing.py, simulation.py | Valuable behavior and tests; tightly coupled to old world/truck models. Extract/adapt incrementally. |
| Frontend | frontend/src/App.jsx, DriverView.jsx, hooks, map/TruckAnimator.js | Reuse theme, map motion and interaction patterns; role dashboards and ordering workflows are new. |
| Offline | frontend/public/sw.js | Fetch-through with cache lookup on failure but no cache population; not evidence of offline delivery sync. |
| Auth/ML | dependencies, models and API reviewed | No implemented v2 role/scoped authentication or ML pipeline found. |
| Documentation drift | README vs CLAUDE | Plant coordinates, clock scaling and traffic factor differ. Do not import conflicting defaults blindly. |

Baseline validation on 2026-10-08: backend suite produced 99 passes and 3 temporary-directory permission setup errors. The same 3 tests passed when rerun using a workspace-owned temp directory (102 tests accounted for across those runs). Frontend production build passed after the sandbox's process-spawn restriction was lifted; Vite warned of a >500 kB chunk. No browser, phone, stress, real routing or v2 acceptance run was performed.

## Product/business uncertainties

Before claiming real-world fitness, interview a dairy operator about actual cut-offs, van ownership and costs, case sizes/tare, shelf-life/temperature handling, accepting default orders, demand visibility, refusals, crate deposits, and shortage priorities. Prototype settings can support these answers later; software cannot validate the assumptions by itself. Real demand forecasting needs sales/lost-sales or comparable demand observations, not only deliveries. The accepted v2 boundary excludes payments, invoices, production, raw collection, multiple plants, native iOS and pharma.

## External technical checks

Service workers require a secure context; localhost is a development exception, not a general LAN-IP exemption. [MDN service-worker guide](https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API/Using_Service_Workers).

SQLite has a single writer; WAL allows readers and a writer to coexist. The proposal for short transactions and one prototype scheduler follows this constraint. [SQLite isolation](https://sqlite.org/isolation.html).

LightGBM exposes quantile objective and alpha; the proposed three-model family is an implementation choice for the PDF's three quantile outputs. [LightGBM parameters](https://github.com/lightgbm-org/LightGBM/blob/main/docs/Parameters.rst).

Temporal evaluation must avoid training on future observations. [Scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html). These checks do not verify the domain assumptions above.
