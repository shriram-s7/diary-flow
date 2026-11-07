# Prototype-1 settings registry

Executable names/defaults: `backend/v2/domain/settings.py`. Admin owns business configuration; server owns transport/security values. Values are prototype assumptions. Future persistence must snapshot a revision for each run/order/manifest. Changing a setting must not recalculate closed history.

| Field | Default | Unit / time domain | Effective rule |
|---|---|---|---|
| app_name | Allocra | text | next configuration refresh |
| business_timezone | Asia/Kolkata | business date | new run only |
| suggestion_time | 14:00 | D-1 local | new day/run revision |
| booth_cutoff | 16:00 | D-1 local | new day/run revision |
| hub_default_time | 17:00 | D-1 local | new day/run revision |
| hub_cutoff | 18:00 | D-1 local | new day/run revision |
| plant_dispatch | 20:00 | D-1 local | new day/run revision |
| hub_dispatch | 01:00 | D local | new day/run revision |
| booth_ready_by | 05:00 | completion on D | new day/run revision |
| day_close | 06:00 | D local | new day/run revision |
| booth_approval_limit | 0.30 | ratio | new order revision |
| hub_approval_limit | 0.20 | ratio | new order revision |
| minimum_share | 0.80 | target ratio | new allocation revision |
| hub_buffer_z / van_buffer_z | 1 / 0.5 | heuristic multiplier | pinned forecast/order/plan revision |
| planning_speed_kmph | 30 | km/h | next undispatched plan |
| rain_factor / traffic_factor | 0.6 / 0.4 | dimensionless | impact/settings revision |
| dock_minutes / minutes_per_slot | 5 / 0.2 | simulation minutes | next undispatched plan |
| loading_minutes | 5 | simulation minutes | next undispatched plan; prototype assumption |
| fuel_km_per_l / cost_per_km | 5 / 18 | km/L, INR/km | vehicle override; frozen manifest |
| risk_low_minutes / risk_medium_minutes | 30 / 10 | simulation minutes | pinned report/plan |
| shelf_life_margin_minutes | 0 | minutes after acceptance | prototype only; operator validation required |
| detection_delay_minutes / detection_proximity_m | 5 / 300 | simulation min, metres | impact revision |
| detour_budget_seconds / detour_attempts | 4 / 2 | wall seconds, count | next job |
| costly_detour_minutes / costly_detour_ratio | 15 / 0.30 | added sim min, distance ratio | impact revision |
| reminder_minutes / acknowledgment_minutes | 30 / 5 | simulation minutes | event/message revision |
| speed_presets | 1,5,15,30,60,120 | simulation multiplier | prototype clock commands |
| tick_budget_ms / api_p95_budget_ms | 200 / 100 | wall milliseconds | measurement targets, not guarantees |

PIN hashing/token lifetimes/POD challenge limits will be selected during P03/P24 and must use wall time. No unsecured placeholder values are supplied. Per-SKU container sizes/weights, expiry, vehicle capabilities and stock are required catalog data, not universal defaults.

## P04a persistence status

Full-registry dated read/replace is implemented under /v2/setup/settings. See [CATALOG.md](CATALOG.md). Current prototype conservatively rejects changes affecting any started day, including app_name; immediate name-only refresh remains pending. settings.py is the sole default/validation registry; catalog_revisions stores versioned complete values. Existing run settings supply the baseline.
