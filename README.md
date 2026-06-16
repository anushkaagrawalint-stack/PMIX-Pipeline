# PMix Pipeline — Kutlerri

Toast POS → Neon Postgres ETL for the RASA PMix / Menu Engineering system.
Phase 1 scope: extraction, raw landing, parse/clean, star-schema merge,
reconciliation. The analytics layer (R365 costs, Bikky retention, Menu
Engineering MVs) lands in the `analytics` schema in later phases.

## Architecture

```
Toast API ──> raw.*  ──> parse + clean ──> staging.* ──> merge ──> public.* (star schema)
              (immutable JSONB)            (rebuilt per run)        facts + dims
```

- **raw**: untouched Toast payloads. Audit trail and re-parse source.
- **staging**: parsed, cleaned, validated rows for the current pull. Truncated each run.
- **public**: production star schema. `fact_order_lines` grain = 1 row per
  selection per check. Voids preserved with `is_voided`; every revenue query
  must carry `WHERE NOT is_voided`.
- **analytics**: reserved for the Kutlerri layer (costs, retention, ME MVs).

### Non-negotiable semantics

1. **ordersBulk filters on modified date, not business date.** We over-fetch
   a padded window and attribute by `payload.businessDate` (local service
   day), deduping on `order_guid`. Never attribute by UTC timestamps.
2. **Dining options are null on the order payload.** Resolved via
   `/config/v2/diningOptions`; the run HALTS if that fetch is empty.
3. **`appliedMenu` is always null on bulk orders.** Menu/menu-group resolved
   at load time from `/menus/v2/menus`, which also keeps `dim_item.menu_group`
   current.
4. **Revenue = SUM(line_total) WHERE NOT is_voided** — pre-tax, pre-discount,
   excludes service charges. Differs from Toast UI "Net Sales" by exactly the
   service-charge total (verify per `scripts/check_reconciliation.py`).
5. **Cleaning is CSV-driven** (`mappings/`). A modifier or item grouping is
   never collapsed/relabeled/merged without sign-off; changes are reviewed
   CSV diffs, not code.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in Toast creds, location GUIDs, Neon URL
python -m toast_pipeline.cli init-db
```

## Run

```bash
# Daily incremental (yesterday, back-padded 2 days to catch late voids/edits)
python -m toast_pipeline.cli run

# Spike / backfill: one location, one week
python -m toast_pipeline.cli run --start 2026-06-01 --end 2026-06-07 --locations BALLPARK

# Validate counts and attribution
python -m toast_pipeline.cli validate
python scripts/check_reconciliation.py --start 2026-06-01 --end 2026-06-07
```

Backfills are capped at 45 days per invocation — split longer ranges.

## Scheduling

`.github/workflows/daily_pipeline.yml` runs daily at 11:00 UTC with a
concurrency lock; manual backfills via workflow_dispatch. Repo secrets
required: `TOAST_CLIENT_ID`, `TOAST_CLIENT_SECRET`, `LOCATIONS`,
`DATABASE_URL`.

## Phase-1 acceptance checklist

- [ ] One location-week pulled, landed, merged with zero staging→public drift
- [ ] Net item sales tie to Toast UI Sales Summary (delta == service charges, per check)
- [ ] All five locations on the daily cron
- [ ] One month tied to the GAS pipeline's Clean Menu Breakdown per item per channel
- [ ] Channel attribution `OTHER`/unattributed rate < 1% (then refine the CASE + overrides)

## Roadmap (later phases)

- `analytics.dim_recipe_cost` — R365 ingest, period grain
- `analytics.fact_retention` — Bikky ingest with period-staleness guard
- Cost engine: pink-sheet modifier-weighted costs in SQL over `fact_modifiers`
- `analytics.mv_menu_engineering` — Menu Mix `(1/n)×0.7` strict `>`,
  margin `Σ margin$ / Σ net sales$` strict `>`, quadrants per channel × period × location
- Dashboard rewire: Next.js app reads Postgres instead of xlsx uploads
