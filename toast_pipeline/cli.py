"""CLI entry point.

  python -m toast_pipeline.cli init-db
  python -m toast_pipeline.cli run --start 2026-06-01 --end 2026-06-07 [--locations BALLPARK,MVT]
  python -m toast_pipeline.cli run            # self-healing window: yesterday back-padded 2 days
  python -m toast_pipeline.cli validate
  python -m toast_pipeline.cli bikky-instore  # load all P*IS.csv from Data/Bikkydata/InStore/

Stage order per run (mirrors the reference architecture):
  (0) truncate staging -> (1) config fetch (HALT on missing dining options)
  -> (2) order pull, raw landing -> (3) parse + clean -> staging
  -> (4) merge to public -> (5) validate counts
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import config, db
from .fetch import config_api, orders as orders_fetch
from .parse.orders import parse_order

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("cli")


def _pull_location(loc: config.Location, run_id: int, start: date, end: date) -> dict:
    """One worker per location: own connection, own lookups, own batch."""
    conn = db.connect()
    counts = {"fetched": 0, "landed": 0, "lines": 0}
    try:
        cfg = config_api.fetch_all_config(loc)
        db.land_raw_config(conn, run_id, loc.code, cfg)
        lookups = config_api.build_lookups(cfg)

        batch = {k: [] for k in ("order_lines", "modifiers", "checks", "payments", "adjustments")}
        page: list[dict] = []

        def _flush_page() -> None:
            if not page:
                return
            counts["landed"] += db.land_raw_orders_batch(conn, run_id, loc.code, page)
            for o in page:
                parsed = parse_order(o, loc.code, lookups)
                batch["order_lines"].extend(parsed.lines)
                batch["modifiers"].extend(parsed.modifiers)
                batch["checks"].extend(parsed.checks)
                batch["payments"].extend(parsed.payments)
                batch["adjustments"].extend(parsed.adjustments)
            page.clear()

        for order in orders_fetch.fetch_orders(loc, start, end):
            counts["fetched"] += 1
            page.append(order)
            if len(page) >= 100:
                _flush_page()
        _flush_page()
        conn.commit()

        for kind, rows in batch.items():
            n = db.bulk_stage(conn, kind, rows)
            log.info("%s: staged %d %s", loc.code, n, kind)
        counts["lines"] = len(batch["order_lines"])
        return counts
    finally:
        conn.close()


def cmd_run(args: argparse.Namespace) -> None:
    locs = config.load_locations()
    if args.locations:
        wanted = {c.strip().upper() for c in args.locations.split(",")}
        locs = [l for l in locs if l.code.upper() in wanted]

    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    start = (date.fromisoformat(args.start) if args.start
             else end - timedelta(days=config.DEFAULT_BACKPAD_DAYS))
    if (end - start).days > config.MAX_WINDOW_DAYS:
        raise SystemExit(f"window exceeds {config.MAX_WINDOW_DAYS} days — split the backfill")

    conn = db.connect()
    db.truncate_staging(conn)
    run_id = db.open_pull_run(conn, start, end, [l.code for l in locs])
    log.info("pull_run %d: %s -> %s for %s", run_id, start, end, [l.code for l in locs])

    fetched = landed = 0
    try:
        with ThreadPoolExecutor(max_workers=len(locs)) as ex:
            for counts in ex.map(lambda l: _pull_location(l, run_id, start, end), locs):
                fetched += counts["fetched"]
                landed += counts["landed"]

        db.merge_to_public(conn)
        db.close_pull_run(conn, run_id, "success", fetched, landed)
        log.info("run %d complete: fetched=%d landed=%d", run_id, fetched, landed)
        cmd_validate(args)
    except Exception as e:
        db.close_pull_run(conn, run_id, "failed", fetched, landed, error=str(e))
        raise
    finally:
        conn.close()


def cmd_init_db(args: argparse.Namespace) -> None:
    conn = db.connect()
    db.init_schema(conn)
    conn.close()
    log.info("schema initialized")


def cmd_reparse(args: argparse.Namespace) -> None:
    """Rebuild staging from the raw payloads already in the database — no
    Toast API calls. Use after changing mappings or cleaning rules, then
    follow with `merge` (or let this command do both with --merge)."""
    locs = config.load_locations()
    if args.locations:
        wanted = {c.strip().upper() for c in args.locations.split(",")}
        locs = [l for l in locs if l.code.upper() in wanted]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    conn = db.connect()
    db.truncate_staging(conn)
    total = 0
    for loc in locs:
        cfg = db.fetch_latest_config(conn, loc.code)
        if not cfg.get("dining_options"):
            log.warning("%s: no stored config found — skipping (run a pull first)", loc.code)
            continue
        lookups = config_api.build_lookups(cfg)
        batch = {k: [] for k in ("order_lines", "modifiers", "checks", "payments", "adjustments")}
        n = 0
        for payload in db.fetch_raw_orders(conn, loc.code, start, end):
            parsed = parse_order(payload, loc.code, lookups)
            batch["order_lines"].extend(parsed.lines)
            batch["modifiers"].extend(parsed.modifiers)
            batch["checks"].extend(parsed.checks)
            batch["payments"].extend(parsed.payments)
            batch["adjustments"].extend(parsed.adjustments)
            n += 1
        for kind, rows in batch.items():
            db.bulk_stage(conn, kind, rows)
        log.info("%s: reparsed %d raw orders -> %d lines", loc.code, n, len(batch["order_lines"]))
        total += n
    if total and getattr(args, "merge", False):
        db.merge_to_public(conn)
        log.info("merge complete")
    conn.close()
    if total and getattr(args, "merge", False):
        cmd_validate(args)


def cmd_merge(args: argparse.Namespace) -> None:
    """Merge whatever is currently in staging into public, then validate.
    Useful to finish a run whose pull succeeded but whose merge failed."""
    conn = db.connect()
    db.merge_to_public(conn)
    conn.close()
    log.info("merge complete")
    cmd_validate(args)


def cmd_validate(args: argparse.Namespace) -> None:
    conn = db.connect()
    checks = {
        "staging lines": "SELECT count(*) FROM staging.order_lines",
        "public lines": "SELECT count(*) FROM public.fact_order_lines",
        "staging revenue (non-void)":
            "SELECT round(coalesce(sum(line_total),0),2) FROM staging.order_lines WHERE NOT is_voided",
        "orphan modifiers":
            "SELECT count(*) FROM public.fact_modifiers fm "
            "LEFT JOIN public.fact_order_lines fol ON fol.selection_guid = fm.parent_selection "
            "WHERE fol.selection_guid IS NULL",
        "lines missing channel":
            "SELECT count(*) FROM public.fact_order_lines WHERE channel_code IS NULL",
    }
    for label, q in checks.items():
        val = conn.execute(q).fetchone()[0]
        log.info("validate | %-28s %s", label, val)
    conn.close()


_BIKKY_DATA_ROOT = Path(__file__).resolve().parents[1] / "Data" / "Bikkydata"

_BIKKY_COL_MAP = {
    "Item":                               "item_name",
    "Item id":                            "item_id",
    "Item revenue":                       "revenue",
    "Item revenue per location":          "revenue_per_loc",
    "Item revenue percentage":            "revenue_pct",
    "Item volume":                        "volume",
    "Item volume per location":           "volume_per_loc",
    "Item volume percentage":             "volume_pct",
    "Item aov":                           "aov",
    "Item guests":                        "guests",
    "N day item return rate":             "return_rate",
    "N day item reorder rate":            "reorder_rate",
    "Business date previous start":       "prev_period_start",
    "Business date previous end":         "prev_period_end",
    "Item revenue previous":              "revenue_prev",
    "Item revenue per location previous": "revenue_per_loc_prev",
    "Item revenue percentage previous":   "revenue_pct_prev",
    "Item volume previous":               "volume_prev",
    "Item volume per location previous":  "volume_per_loc_prev",
    "Item volume percentage previous":    "volume_pct_prev",
    "Item aov previous":                  "aov_prev",
    "Item guests previous":               "guests_prev",
    "N day item return rate previous":    "return_rate_prev",
    "N day item reorder rate previous":   "reorder_rate_prev",
}

_BIKKY_DATE_COLS    = {"prev_period_start", "prev_period_end"}
_BIKKY_NUMERIC_COLS = {
    "revenue", "revenue_per_loc", "revenue_pct",
    "volume", "volume_per_loc", "volume_pct",
    "aov", "guests", "return_rate", "reorder_rate",
    "revenue_prev", "revenue_per_loc_prev", "revenue_pct_prev",
    "volume_prev", "volume_per_loc_prev", "volume_pct_prev",
    "aov_prev", "guests_prev", "return_rate_prev", "reorder_rate_prev",
}

_BIKKY_UPSERT_TMPL = """
    INSERT INTO {table} (
        fiscal_year, period, item_name, item_id,
        revenue, revenue_per_loc, revenue_pct,
        volume, volume_per_loc, volume_pct,
        aov, guests, return_rate, reorder_rate,
        prev_period_start, prev_period_end,
        revenue_prev, revenue_per_loc_prev, revenue_pct_prev,
        volume_prev, volume_per_loc_prev, volume_pct_prev,
        aov_prev, guests_prev, return_rate_prev, reorder_rate_prev
    ) VALUES (
        %(fiscal_year)s, %(period)s, %(item_name)s, %(item_id)s,
        %(revenue)s, %(revenue_per_loc)s, %(revenue_pct)s,
        %(volume)s, %(volume_per_loc)s, %(volume_pct)s,
        %(aov)s, %(guests)s, %(return_rate)s, %(reorder_rate)s,
        %(prev_period_start)s, %(prev_period_end)s,
        %(revenue_prev)s, %(revenue_per_loc_prev)s, %(revenue_pct_prev)s,
        %(volume_prev)s, %(volume_per_loc_prev)s, %(volume_pct_prev)s,
        %(aov_prev)s, %(guests_prev)s, %(return_rate_prev)s, %(reorder_rate_prev)s
    )
    ON CONFLICT (fiscal_year, period, item_name) DO UPDATE SET
        item_id              = EXCLUDED.item_id,
        revenue              = EXCLUDED.revenue,
        revenue_per_loc      = EXCLUDED.revenue_per_loc,
        revenue_pct          = EXCLUDED.revenue_pct,
        volume               = EXCLUDED.volume,
        volume_per_loc       = EXCLUDED.volume_per_loc,
        volume_pct           = EXCLUDED.volume_pct,
        aov                  = EXCLUDED.aov,
        guests               = EXCLUDED.guests,
        return_rate          = EXCLUDED.return_rate,
        reorder_rate         = EXCLUDED.reorder_rate,
        prev_period_start    = EXCLUDED.prev_period_start,
        prev_period_end      = EXCLUDED.prev_period_end,
        revenue_prev         = EXCLUDED.revenue_prev,
        revenue_per_loc_prev = EXCLUDED.revenue_per_loc_prev,
        revenue_pct_prev     = EXCLUDED.revenue_pct_prev,
        volume_prev          = EXCLUDED.volume_prev,
        volume_per_loc_prev  = EXCLUDED.volume_per_loc_prev,
        volume_pct_prev      = EXCLUDED.volume_pct_prev,
        aov_prev             = EXCLUDED.aov_prev,
        guests_prev          = EXCLUDED.guests_prev,
        return_rate_prev     = EXCLUDED.return_rate_prev,
        reorder_rate_prev    = EXCLUDED.reorder_rate_prev,
        loaded_at            = now()
"""


def _bikky_coerce(val: str, col: str):
    v = val.strip()
    if not v:
        return None
    if col in _BIKKY_DATE_COLS:
        return date.fromisoformat(v)
    if col in _BIKKY_NUMERIC_COLS:
        try:
            return Decimal(v)
        except InvalidOperation:
            return None
    return v


def _load_bikky_dir(data_dir: Path, glob: str, period_pattern: str,
                    sql_file: str, table: str, label: str) -> None:
    files = sorted(data_dir.glob(glob))
    if not files:
        raise SystemExit(f"No {glob} files found in {data_dir}")

    conn = db.connect()
    sql_path = Path(__file__).resolve().parents[1] / "sql" / sql_file
    conn.execute(sql_path.read_text())
    conn.commit()

    upsert = _BIKKY_UPSERT_TMPL.format(table=table)
    for path in files:
        m = re.match(period_pattern, path.stem, re.IGNORECASE)
        if not m:
            log.warning("skipping %s — can't parse period/year from filename", path.name)
            continue
        period = int(m.group(1))
        fiscal_year = int(m.group(2))

        rows = []
        with path.open(newline="", encoding="utf-8-sig") as f:
            for raw in csv.DictReader(f):
                row: dict = {"fiscal_year": fiscal_year, "period": period}
                for csv_col, db_col in _BIKKY_COL_MAP.items():
                    row[db_col] = _bikky_coerce(raw.get(csv_col, ""), db_col)
                if row.get("item_name"):
                    rows.append(row)

        if rows:
            with conn.cursor() as cur:
                cur.executemany(upsert, rows)
            conn.commit()
        log.info("%s: %s period=%d year=%d → %d rows upserted",
                 label, path.name, period, fiscal_year, len(rows))

    conn.close()


def cmd_bikky_instore(args: argparse.Namespace) -> None:
    _load_bikky_dir(
        data_dir=_BIKKY_DATA_ROOT / "InStore",
        glob="P*IS.csv",
        period_pattern=r"P(\d{2})(\d{4})IS",
        sql_file="006_bikky_instore.sql",
        table="public.fact_bikky_instore",
        label="bikky-instore",
    )


def cmd_bikky_3pd(args: argparse.Namespace) -> None:
    _load_bikky_dir(
        data_dir=_BIKKY_DATA_ROOT / "3PD+Loyalty",
        glob="P*Del.csv",
        period_pattern=r"P(\d{2})(\d{4})Del",
        sql_file="007_bikky_3pd_loyalty.sql",
        table="public.fact_bikky_3pd_loyalty",
        label="bikky-3pd",
    )


_LOOKUP_DIR = Path(__file__).resolve().parents[1] / "Data" / "LookupData"


def cmd_load_lookups(args: argparse.Namespace) -> None:
    """Load all lookup Excel files into the lookup schema — no data dropped."""
    import openpyxl

    conn = db.connect()
    sql_root = Path(__file__).resolve().parents[1] / "sql"

    for sql_file in ["008_lookup_modifier_type.sql", "009_lookup_menu_breakdown.sql",
                     "010_lookup_parent_item_type.sql", "011_lookup_item_name_map.sql"]:
        conn.execute((sql_root / sql_file).read_text())
    conn.commit()

    # -------------------------------------------------------------------------
    # Sheet 1: LookupItemAndModifierType.xlsx
    # Section A (cols 6-8): modifier_name + item_type → modifier_type
    # Section B (cols 10-11): parent_item + item_type
    # -------------------------------------------------------------------------
    wb = openpyxl.load_workbook(_LOOKUP_DIR / "LookupItemAndModifierType.xlsx")
    ws = wb["Sheet1"]

    modifier_rows, parent_rows = [], []
    for row in ws.iter_rows(min_row=3, values_only=True):
        mod_name  = str(row[5]).strip() if row[5] else None
        item_type = str(row[6]).strip() if row[6] else None
        mod_type  = str(row[7]).strip() if row[7] else None
        if mod_name and item_type:
            modifier_rows.append((mod_name, item_type, mod_type))

        parent_item      = str(row[9]).strip()  if row[9]  else None
        parent_item_type = str(row[10]).strip() if row[10] else None
        if parent_item and parent_item_type:
            parent_rows.append((parent_item, parent_item_type))

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO lookup.modifier_type (modifier_name, item_type, modifier_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (modifier_name, item_type) DO UPDATE SET
                modifier_type = EXCLUDED.modifier_type,
                loaded_at     = now()
            """,
            modifier_rows,
        )
        cur.executemany(
            """
            INSERT INTO lookup.parent_item_type (parent_item, item_type)
            VALUES (%s, %s)
            ON CONFLICT (parent_item, item_type) DO NOTHING
            """,
            parent_rows,
        )
    conn.commit()
    log.info("load-lookups: lookup.modifier_type → %d rows upserted", len(modifier_rows))
    log.info("load-lookups: lookup.parent_item_type → %d rows upserted", len(parent_rows))

    # -------------------------------------------------------------------------
    # Sheet 2: LookupMenuBreakdown.xlsx
    # Section A (cols 1-2): raw_item_name → cleaned_item_name
    # Section B (cols 4-5): cleaned_item_name → category_1
    # Section C (cols 7-8): category_1 → category_2  (joined with B)
    # -------------------------------------------------------------------------
    wb2 = openpyxl.load_workbook(_LOOKUP_DIR / "LookupMenuBreakdown.xlsx")
    ws2 = wb2["Sheet1"]

    item_name_rows: dict[str, str] = {}
    cat1_map: dict[str, str] = {}
    cat2_map: dict[str, str] = {}

    for row in ws2.iter_rows(min_row=3, values_only=True):
        if row[0] and row[1]:
            item_name_rows[str(row[0]).strip()] = str(row[1]).strip()
        if row[3] and row[4]:
            cat1_map[str(row[3]).strip()] = str(row[4]).strip()
        if row[6] and row[7]:
            cat2_map[str(row[6]).strip()] = str(row[7]).strip()

    # item_name_map: every unique raw→cleaned pair
    name_map_rows = list(item_name_rows.items())

    # menu_breakdown: every unique cleaned name with its category chain
    breakdown_rows = [
        (cleaned, cat1, cat2_map.get(cat1))
        for cleaned, cat1 in cat1_map.items()
    ]

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO lookup.item_name_map (raw_item_name, cleaned_item_name)
            VALUES (%s, %s)
            ON CONFLICT (raw_item_name) DO UPDATE SET
                cleaned_item_name = EXCLUDED.cleaned_item_name,
                loaded_at         = now()
            """,
            name_map_rows,
        )
        cur.executemany(
            """
            INSERT INTO lookup.menu_breakdown (cleaned_item_name, category_1, category_2)
            VALUES (%s, %s, %s)
            ON CONFLICT (cleaned_item_name) DO UPDATE SET
                category_1 = EXCLUDED.category_1,
                category_2 = EXCLUDED.category_2,
                loaded_at  = now()
            """,
            breakdown_rows,
        )
    conn.commit()
    log.info("load-lookups: lookup.item_name_map → %d rows upserted", len(name_map_rows))
    log.info("load-lookups: lookup.menu_breakdown → %d rows upserted", len(breakdown_rows))
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="toast_pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    runp = sub.add_parser("run")
    runp.add_argument("--start")
    runp.add_argument("--end")
    runp.add_argument("--locations", help="comma-separated location codes; default all")
    runp.set_defaults(func=cmd_run)

    rp = sub.add_parser("reparse")
    rp.add_argument("--start", required=True)
    rp.add_argument("--end", required=True)
    rp.add_argument("--locations", help="comma-separated location codes; default all")
    rp.add_argument("--merge", action="store_true", help="merge to public after reparsing")
    rp.set_defaults(func=cmd_reparse)

    sub.add_parser("merge").set_defaults(func=cmd_merge)
    sub.add_parser("validate").set_defaults(func=cmd_validate)
    sub.add_parser("bikky-instore",
                   help="load all P*IS.csv from Data/Bikkydata/InStore/ into public.fact_bikky_instore"
                   ).set_defaults(func=cmd_bikky_instore)
    sub.add_parser("bikky-3pd",
                   help="load all P*Del.csv from Data/Bikkydata/3PD+Loyalty/ into public.fact_bikky_3pd_loyalty"
                   ).set_defaults(func=cmd_bikky_3pd)
    sub.add_parser("load-lookups",
                   help="load LookupItemAndModifierType.xlsx and LookupMenuBreakdown.xlsx into public"
                   ).set_defaults(func=cmd_load_lookups)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
