# Adopted v2 prototype specification

Revision: prototype-1, 2026-10-09. User authorized implementation and then requested a checkpoint after the starting prompts. This revision adopts D01–D18 from [DECISIONS.md](DECISIONS.md) as **engineering prototype defaults**, not as externally validated dairy business policy. The original [requirements](source/requirements-v2.pdf) remain unchanged. All unchanged requirements apply; the addendum resolves contradictions. User instructions take precedence. Legacy rules apply only to the still-running v1 code, archived in [legacy-v1-spec.md](source/legacy-v1-spec.md).

## Scope and adoption

One plant, seven demo hubs, about seventy booths, packaged dairy, two distribution levels, six roles across web/booth/hub/driver surfaces. One FastAPI modular backend, React/MapLibre, SQLAlchemy/Alembic/SQLite, heuristic Planner interface, offline LightGBM quantile family and a driver PWA/Android wrapper. Keep v1 operating until the migration gate. Pharma, payments, raw collection, production, multiple plants, native iOS and OR-Tools are excluded.

All D01–D18 are adopted together with these clarifications:

- D01: packing groups are trip/stop/SKU; no cross-booth sharing. One physical container occupies one capacity slot in prototype-1. Full-container grams are a conservative planning bound, including tare. Optional measured unit/tare weights must not exceed that bound at full packing. Returnable container debt is separate from disposable cartons.
- D02: synthetic demand is the model target, accepted delivery the operational actual; never substitute one for the other.
- D03/D04: atomic command/event effects with deduplication/outbox; rewind forks a run. Neither database nor clock exists yet at the P01 checkpoint.
- D05: exactly 30%/20% change is within the limit; zero-to-positive requires approval. Pending proposal does not alter effective order. Cut-off equality is closed. Closed tomorrow is an explicit audited exception before booth cut-off.
- D06: when supply meets rational floor targets, first distribute `ceil(sum(floor_targets))` units by cap-aware largest remainder, then the remainder by unmet demand. Under insufficient floor supply, Critical receives up to full orders, then High/Normal share proportionally. Stable ID resolves ties. An 80% target is not a guarantee under impossible supply. Negative supply is invalid, zero supply valid.
- D07: ceil RSS buffer in units, then pack spares independently; cap by remaining slots, conservative grams, lane and real available stock. It is a heuristic, not a service-level guarantee.
- D08/D09: lots expire after projected acceptance plus margin; reservations/confirmed receipts fund dispatch. Prototype temperature support remains an assumption pending operational validation.
- D10/D11: explicit unplanned quantities when infeasible; dated bilateral pre-dispatch lending only; service-cycle exceptions for split lanes and urgent recovery.
- D12/D13: server scope checks everywhere; wall-clock authentication/POD expiry. Unconfirmed handover is provisional custody, not usable receiver inventory.
- D14: three global quantile regressors, as-of features and untouched holdout; failed promotion stays failed. No model training at this checkpoint.
- D15/D16: secure phone connection and physical-device verification remain gates; deterministic run comparisons use fixed routes and action inputs, with idle jumps for the short story.
- D17: **booth ready-by means unloading completion**. Store arrival and completion separately; risk uses completion. Plant-to-hub must finish receiving before dependent dispatch. Overnight shifts have explicit dates. Business timezone Asia/Kolkata; persisted timestamps UTC.
- D18: configurable Allocra name, dairy only.

## Single-source defaults

[backend/v2/domain/settings.py](../../backend/v2/domain/settings.py) is the executable default registry for v2. [SETTINGS.md](SETTINGS.md) gives units/ownership/effective rules. It does not overwrite legacy `backend/config.py`; the two versions remain isolated until migration. No hardcoded packaging weights, seed stock, or dataset factors are asserted as measured facts.

## Domain boundaries and delivery gates

[ARCHITECTURE.md](ARCHITECTURE.md) defines service/schema ownership; [CONTRACTS.md](CONTRACTS.md) specifies the implemented P01 DTO and transition boundaries. [TRACEABILITY.md](TRACEABILITY.md) maps all PDF sections and R01–R22 to owners. [ACCEPTANCE.md](ACCEPTANCE.md) remains the test/gate authority. P01 provides pure domain types and calculations, not working persistence, auth, orders, scheduler, planner algorithm or apps.

R01–R20 are addressed by adopted D01–D18 and assigned implementation tests. R21 remains a P07 seed-capacity/real-operator validation gate. R22 remains a P10/P22/P28 provider policy, road verification and offline capability gate. Adoption resolves design direction, not proof of implementation or real-world suitability.

## External validation still required

Operator confirmation: cut-off/dispatch times, full-container mass/tare, permitted simultaneous temperature lanes, actual shelf life/margins, shortage priorities, demand observations, van ownership/cost and crate handling. Physical-device evidence, authoritative calendar/climate sources, trained model metrics and production security remain future gates. No source statement is treated as permission to publish, contact people or delete data.
