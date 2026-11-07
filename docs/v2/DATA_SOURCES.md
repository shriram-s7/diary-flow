# Evidence and data-source register

Review date 2026-10-08. This is a **research handoff**, not a completed dairy dataset bibliography. No synthetic history has been generated and no model trained. Do not describe demo weights, demand factors, holidays, capacities or coordinates as verified facts.

## Technical references checked for this review

| Topic | Primary source | Used for |
|---|---|---|
| Browser offline prerequisites | https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API/Using_Service_Workers | Secure context/localhost caveat. |
| SQLite concurrency | https://sqlite.org/isolation.html | One writer; short transaction design. |
| Quantile training | https://github.com/lightgbm-org/LightGBM/blob/main/docs/Parameters.rst | Quantile objective and alpha. |
| Time-aware evaluation | https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html | Future-data leakage avoidance. |

## Required research in P10

1. Official Tamil Nadu holiday notifications for 2025 and 2026, plus all dates covered by generation/evaluation/forecast horizon (including next year when necessary). Record notification/document URL, issuing authority, publication/retrieval date and exact observed/festival dates. Do not use a model's memorized dates.
2. Official IMD/Regional Meteorological Centre Chennai climate normals or another explicitly licensed authoritative station dataset. Distinguish monthly normals, observed historical weather and forecast weather. A monthly normal does not validate an invented daily rain sequence.
3. Public dairy/retail demand studies that actually support weekday/festival/temperature relationships at a comparable geography/product/channel. If none support an exact coefficient, label the coefficient an assumption. Never cite a broad milk production total as evidence for booth sales factors.
4. Routing/geocoding/map provider policies: OSRM endpoint suitability and limits, attribution, tiles/offline caching rights, coordinate verification process. Download no large map regions without checking license/permission.
5. Packaging, weights and transport assumptions: preferably manufacturer/operator documentation or supplied measurements; otherwise explicit demo-only parameters. Software lane flags do not establish actual cold-chain compliance.

## Per-source record format

```text
source_id:
title / publisher:
exact_url:
publication_date / retrieved_date:
geography / period / unit:
claim_supported:
claim_not_supported:
license_and_reuse_notes:
stored_asset_path_and_sha256 (if downloaded):
transformation_and_missing_values:
status: verified | assumed | unavailable
```

Save generator parameter provenance next to each parameter: direct measured value, literature-informed assumption or arbitrary demo choice. Include fixed seed, generator version, dataset hash, date range, number of booths/SKUs and missing rows. Do not publish identifiable driver/customer data in model artifacts or screenshots. Every metric based on generated data carries “Synthetic data”; show methodology and limits in the app and model card.
