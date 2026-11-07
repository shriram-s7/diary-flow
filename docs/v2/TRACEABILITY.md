# Requirements coverage and execution ownership

Every numbered PDF section is mapped here. Coverage means an execution assignment exists, not implementation is complete. Optional features are distinguished from mandatory v2 behavior. Acceptance vectors refer to ACCEPTANCE.md; corrective decisions refer to DECISIONS.md.

| PDF section / pages | Deliverables and constraints | Tasks | Key checks / corrections |
|---|---|---|---|
| 1 / p1 | Configurable name; packed dairy; two levels; web plus three phone surfaces; scope exclusions | P00,P04,P15,P20,P25 | D18; no pharma/payment expansion |
| 2 / pp2–3 | Retain routing/live engine/design, replace litres/in-memory model/time/demand source | P00–P02,P18,P22,P29 | Preserve v1 while migrating; no fabricated SKU conversion |
| 3 / pp3–4 | Plant/hub/booth tree; one home hub; day loan; receipt dependency | P04,P07,P19,P21 | I01,I02; D09,D11 |
| 4 / pp4–5 | Eight SKU catalogue, lanes, units/container, shelf life, unit/crate/litre/kg displays | P01,P04–P07,P18 | Q01–Q04; D01,D08; frozen optional |
| 5 / pp5–6 | Vehicle/driver home, slots/kg, lane, shifts, cost, colour; edit/duplicate/deactivate/delete | P04,P06,P07,P18,P19 | P01,P02; historical snapshots and no overlapping driver trips |
| 6 / pp6–7 | Six roles, phone/PIN, scopes, demo login, audit, separate ordering/dispatch surfaces | P03,P06,P15,P20,P25 | S01,S02; D12 |
| 7 / pp8–9 | D-1 preparation, ordered daily events, cut-offs, dispatch, receipt, day close | P08,P13,P14,P19,P27 | C01–C04,O04; D05,D17; page8 diagram transcribed |
| 8 / pp9–10 | Auto-confirm defaults, ±30/20%, closed, reminders, approval fallback, late-change rules, line records | P13–P16,P23 | O01–O05; D05 |
| 9 / pp10–11 | Two-level shortage allocation, priorities, floor, proportional remainder, notifications, overrides | P17,P20 | A01–A04; D06 |
| 10 / pp11–12 | One Planner interface, constraints, objectives, loading sheets, replanning, loan | P18–P21 | P01–P04; D01,D08–D11 |
| 11 / pp12–13 | Rain/traffic/closures, automatic reroute, costly/no-route alert, urgent A/B/C, refusals, decision log | P22,P23,P24 | L01,L02,D02; D03,D10 |
| 12 / pp13–14 | Driver screens/navigation, load check, POD app/code, issues, messages/calls, return/empties, offline, GPS, APK | P19,P24–P26 | D01–D05,U01,U02; D13,D15 |
| 13 / p15 | Four thread types, ack/escalation, page15 notification matrix, one reminder, call-started only | P16,P23,P24 | Dedupe/restart/membership; D12,D17 |
| 14 / pp16–17 | Shared shell, plant/hub/admin role screens, approval/supply/plan/live tabs, day close | P06,P09,P12,P20,P27 | Role consistency, layouts, stored reconciliation |
| 15 / pp17–18 | Calendar, global clock, speeds, next event/run day, forward events, rewind/reset, restart equality | P08,P09,P27–P29 | C01–C04; D03,D04,D16 |
| 16 / pp18–19 | Sources,365-day generator, global quantiles, as-of features, last60-day holdout, baseline/metrics, forecast reasons/ML page | P10–P12,P27 | M01–M05; D02,D14 |
| 17 / pp19–21 | Entity schema and persistent DB, forecast/order/allocation/trip/line/crate/audit/clock relations | P01,P02,P04,P05 and feature tasks | ARCHITECTURE schema blueprint adds missing lot/reservation/command/run entities |
| 18 / pp21–22 | Seed, map add/move, CSV templates/errors/preview/add/replace/export, editable settings | P04,P06,P07 | Transactional import;300m snap; history-safe replace |
| 19 / pp22–24 | Shared formulas, packing, hub buffer, spares, approval, allocation, ETA/cost/risk, accuracy | P01,P11,P13,P17,P18 | D01,D06,D07,D14,D17 |
| 20 / pp24–25 | Stack, persistence, performance, nonblocking network, reconnect, consistency/security/UX/LAN | P02,P03,P08,P22,P25,P26,P28,P29 | Tick/API benchmark + device gates; D03,D12,D15 |
| 21 / pp25–26 | Eight story steps, approximately 7min, jump/no auto-pause, laptop+phones/cache/frame fallback | P28,P29 | D16; full-day real records, trusted phone connection |
| 22 / pp26–27 | Roadmap excluded: collection/production/multiplant/clustering/OR-Tools/doorstep/payments/iOS/hardware/pharma | P00,P30 | D18; phone GPS in scope, external GPS hardware/loggers excluded |
| 23 / pp27–28 | Operator assumptions/open questions on times, packaging, channels, priorities, contractors/name | P00,P07,P10,P30 | Explicit assumption register, no real-world validation claim |
| 24 / pp28–29 | Phase gates and complete story/data traceability definition of done | P00–P30 | G0–G7; all unverified checks stay open |

## Review finding closure ownership

| Findings | Decision | Implementation ownership |
|---|---|---|
| R01 | D01 | P01,P04,P05,P18,P24 |
| R02,R18 | D02,D16 | P10–P12,P27,P28 |
| R03,R19 | D03,D04 | P02,P08,P22,P25,P29 |
| R04 | D05 | P13,P14 |
| R05 | D06 | P17 |
| R06 | D07 | P01,P13,P18,P23 |
| R07 | D08 | P04,P05,P18,P19 |
| R08 | D09 | P05,P19,P24,P27 |
| R09 | D10 | P18,P19,P22,P23 |
| R10 | D11 | P21 |
| R11 | D12 | P03,P16,P25 |
| R12 | D13 | P24,P25,P27 |
| R13,R14 | D14 | P10,P11,P12 |
| R15 | D15 | P25,P26,P28 |
| R16 | D16 | P08,P28,P29 |
| R17 | D17 | P01,P08,P18 |
| R20 | D18 | P00,P30 |
| R21 | D01,D08,D10 | P07,P10,P18 |
| R22 | D15,D16 | P07,P10,P22,P28 |

## Optional/deferred distinctions

Frozen lane is optional in the dairy demo; lane model must reject unsupported frozen loads even if no frozen SKU is seeded. Tamil translation is optional; English required. Native iOS, OR-Tools and pharmaceutical profile are roadmap. Real GPS hardware and temperature loggers are out of scope; optional phone-GPS mode and its device testing are in scope for the driver. Browser push is conditional on platform permissions; in-app notifications are required. Online hosting is optional and requires separate authorization; LAN connectivity with honest HTTPS/offline handling is the demo target.

## Definition of whole-system done

All phase gates have evidence; a presenter runs a complete story day across six roles, including both delivery levels, approval/shortage, disruption, urgent request, confirmed delivery, returns and closed reconciliation. Restart and retry cannot duplicate business effects. Quantities/time/status trace to shared calculations and persisted records. Phone offline/GPS/APK claims have actual device evidence. Model promotion is backed by untouched holdout comparison or an explicitly accepted scope revision that does not falsely claim the original ML requirement passed. Synthetic assumptions and remaining real-world validation needs stay visible.
