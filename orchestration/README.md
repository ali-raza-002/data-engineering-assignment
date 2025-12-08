# Orchestration

`definitions.py` is a Dagster asset graph over the same modules the README runs by hand.

```bash
dagster dev -f orchestration/definitions.py
```

- **Assets.** Three bronze ingests feed `silver_crash`, which feeds geo enrichment, census
  geography and road snapping, then `gold_analysis`, `gold_crash_risk` and
  `gold_lead_decision`. `silver_contracts` and `drift_report` run the contract and drift
  checks as assets of their own, so a violation shows up in the graph instead of only in a log.
- **Partitions.** Only `bronze_montgomery` is partitioned (daily, from 2026-09-01), because it is
  the only source with a real incremental watermark. A failed day is re-run on its own with
  `dagster asset materialize --partition <date>`, without a full rebuild.
- **Retries.** Assets that cross the network retry three times with exponential backoff from
  30 s. Transform assets do not retry: they are pure functions of local data, so a failure there
  is a bug or a contract violation, and retrying it would only hide when it started.
- **Schedule.** `daily_refresh` materialises every asset at 06:00.

The choice of Dagster over Airflow and Prefect is argued in `../OPERATIONS.md`.
