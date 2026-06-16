"""Database access — psycopg3 against Neon.

Per-order savepoints at landing: a single bad order rolls back only itself,
never the batch.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import psycopg
from psycopg import sql

from . import config

log = logging.getLogger(__name__)
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def connect() -> psycopg.Connection:
    if not config.DATABASE_URL:
        raise SystemExit("DATABASE_URL not set")
    return psycopg.connect(config.DATABASE_URL, autocommit=False)


def init_schema(conn: psycopg.Connection) -> None:
    for f in sorted(SQL_DIR.glob("00[1-4]*.sql")):
        log.info("applying %s", f.name)
        conn.execute(f.read_text())
    conn.commit()


def open_pull_run(conn: psycopg.Connection, window_start: date, window_end: date,
                  locations: list[str]) -> int:
    row = conn.execute(
        "INSERT INTO raw.pull_runs (window_start, window_end, locations) "
        "VALUES (%s,%s,%s) RETURNING pull_run_id",
        (window_start, window_end, locations),
    ).fetchone()
    conn.commit()
    return row[0]


def close_pull_run(conn: psycopg.Connection, run_id: int, status: str,
                   fetched: int, upserted: int, error: str | None = None) -> None:
    conn.rollback()  # clear any aborted transaction so this update can commit
    conn.execute(
        "UPDATE raw.pull_runs SET finished_at=now(), status=%s, "
        "orders_fetched=%s, orders_upserted=%s, error=%s WHERE pull_run_id=%s",
        (status, fetched, upserted, error, run_id),
    )
    conn.commit()


def land_raw_order(conn: psycopg.Connection, run_id: int, location_code: str,
                   order: dict) -> bool:
    """Upsert one raw payload under a savepoint. Returns True if written."""
    guid = order.get("guid")
    if not guid:
        return False
    try:
        with conn.transaction():  # savepoint when nested
            conn.execute(
                """
                INSERT INTO raw.toast_orders
                    (order_guid, location_code, business_date, modified_date, payload, pull_run_id)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (order_guid) DO UPDATE SET
                    business_date = EXCLUDED.business_date,
                    modified_date = EXCLUDED.modified_date,
                    payload       = EXCLUDED.payload,
                    pull_run_id   = EXCLUDED.pull_run_id,
                    fetched_at    = now()
                WHERE EXCLUDED.modified_date IS NULL
                   OR raw.toast_orders.modified_date IS NULL
                   OR EXCLUDED.modified_date >= raw.toast_orders.modified_date
                """,
                (guid, location_code, order.get("businessDate"),
                 order.get("modifiedDate"), json.dumps(order), run_id),
            )
        return True
    except Exception:
        log.exception("failed to land order %s — skipped, batch continues", guid)
        return False


_RAW_UPSERT = """
    INSERT INTO raw.toast_orders
        (order_guid, location_code, business_date, modified_date, payload, pull_run_id)
    VALUES (%s,%s,%s,%s,%s,%s)
    ON CONFLICT (order_guid) DO UPDATE SET
        business_date = EXCLUDED.business_date,
        modified_date = EXCLUDED.modified_date,
        payload       = EXCLUDED.payload,
        pull_run_id   = EXCLUDED.pull_run_id,
        fetched_at    = now()
    WHERE EXCLUDED.modified_date IS NULL
       OR raw.toast_orders.modified_date IS NULL
       OR EXCLUDED.modified_date >= raw.toast_orders.modified_date
"""


def land_raw_orders_batch(conn: psycopg.Connection, run_id: int, location_code: str,
                          orders: list[dict]) -> int:
    """Land a page of orders in one batched round-trip (psycopg pipelines
    executemany). Falls back to per-order savepoints if the batch fails, so a
    single bad order still can't spoil the page."""
    rows = [
        (o.get("guid"), location_code, o.get("businessDate"),
         o.get("modifiedDate"), json.dumps(o), run_id)
        for o in orders if o.get("guid")
    ]
    if not rows:
        return 0
    try:
        with conn.cursor() as cur:
            cur.executemany(_RAW_UPSERT, rows)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        log.warning("batch landing failed for a page at %s — retrying per-order", location_code)
        landed = 0
        for o in orders:
            if land_raw_order(conn, run_id, location_code, o):
                landed += 1
        conn.commit()
        return landed


def land_raw_orders(conn: psycopg.Connection, run_id: int, location_code: str,
                    orders: list[dict]) -> int:
    """Batch-upsert raw payloads (one network round trip per batch instead of
    one per order). Falls back to per-order landing if the batch fails, so a
    single bad order still can't spoil the rest."""
    rows = [(o.get("guid"), location_code, o.get("businessDate"),
             o.get("modifiedDate"), json.dumps(o), run_id)
            for o in orders if o.get("guid")]
    if not rows:
        return 0
    stmt = """
        INSERT INTO raw.toast_orders
            (order_guid, location_code, business_date, modified_date, payload, pull_run_id)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (order_guid) DO UPDATE SET
            business_date = EXCLUDED.business_date,
            modified_date = EXCLUDED.modified_date,
            payload       = EXCLUDED.payload,
            pull_run_id   = EXCLUDED.pull_run_id,
            fetched_at    = now()
        WHERE EXCLUDED.modified_date IS NULL
           OR raw.toast_orders.modified_date IS NULL
           OR EXCLUDED.modified_date >= raw.toast_orders.modified_date
    """
    try:
        with conn.cursor() as cur:
            cur.executemany(stmt, rows)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        log.warning("batch landing failed for %s — retrying per order", location_code)
        landed = 0
        for o in orders:
            if land_raw_order(conn, run_id, location_code, o):
                landed += 1
        conn.commit()
        return landed


def land_raw_config(conn: psycopg.Connection, run_id: int, location_code: str,
                    cfg: dict[str, object]) -> None:
    for ctype, payload in cfg.items():
        conn.execute(
            "INSERT INTO raw.toast_config (location_code, config_type, payload, pull_run_id) "
            "VALUES (%s,%s,%s,%s)",
            (location_code, ctype, json.dumps(payload), run_id),
        )
    conn.commit()


def truncate_staging(conn: psycopg.Connection) -> None:
    conn.execute(
        "TRUNCATE staging.order_lines, staging.modifiers, staging.checks, "
        "staging.payments, staging.adjustments"
    )
    conn.commit()


_COPY_SPECS = {
    "order_lines": ("staging.order_lines",
        ["selection_guid","order_guid","check_guid","location_code","business_date",
         "item_guid","item_multi_loc_id","raw_name","clean_name","menu_guid","menu_name",
         "menu_group_name","sales_category","dining_option","quantity","line_total",
         "pre_discount","is_voided","is_deferred"]),
    "modifiers": ("staging.modifiers",
        ["modifier_guid","parent_selection","order_guid","location_code","business_date",
         "raw_name","clean_name","depth","quantity","price","is_blocklisted","is_voided"]),
    "checks": ("staging.checks",
        ["check_guid","order_guid","location_code","business_date","is_voided",
         "tax_amount","total_amount"]),
    "payments": ("staging.payments",
        ["payment_guid","check_guid","order_guid","location_code","business_date",
         "payment_type","alt_payment_name","amount","tip_amount"]),
    "adjustments": ("staging.adjustments",
        ["order_guid","check_guid","selection_guid","location_code","business_date",
         "kind","name","amount"]),
}


def bulk_stage(conn: psycopg.Connection, kind: str, rows: list[dict]) -> int:
    """COPY rows into a staging table. Dedupes on first key column to keep
    overlapping windows safe before PK insertion."""
    if not rows:
        return 0
    table, cols = _COPY_SPECS[kind]
    seen: set = set()
    deduped: list[dict] = []
    keyc = cols[0] if kind != "modifiers" else None
    for r in rows:
        if kind == "modifiers":
            k = (r["modifier_guid"], r["parent_selection"])
        elif kind == "adjustments":
            k = id(r)  # no natural key
        else:
            k = r[keyc]
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)

    stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(*table.split(".")),
        sql.SQL(",").join(sql.Identifier(c) for c in cols),
    )
    with conn.cursor() as cur, cur.copy(stmt) as cp:
        for r in deduped:
            cp.write_row([r.get(c) for c in cols])
    conn.commit()
    return len(deduped)


def seed_locations(conn: psycopg.Connection) -> None:
    """Ensure every configured location exists in dim_location before merging."""
    for loc in config.load_locations():
        conn.execute(
            "INSERT INTO public.dim_location (location_code, toast_guid, display_name) "
            "VALUES (%s,%s,%s) "
            "ON CONFLICT (location_code) DO UPDATE SET "
            "toast_guid = EXCLUDED.toast_guid, display_name = EXCLUDED.display_name",
            (loc.code, loc.guid, loc.name),
        )
    conn.commit()


def fetch_raw_orders(conn: psycopg.Connection, location_code: str,
                     window_start, window_end):
    """Yield raw payloads for a location whose business_date OR fetch window
    overlaps [start, end]. Used by reparse — no Toast API calls."""
    s = int(window_start.strftime("%Y%m%d"))
    e = int(window_end.strftime("%Y%m%d"))
    with conn.cursor(name="raw_orders_cur") as cur:
        cur.execute(
            "SELECT payload FROM raw.toast_orders "
            "WHERE location_code = %s AND (business_date BETWEEN %s AND %s OR business_date IS NULL)",
            (location_code, s, e),
        )
        for (payload,) in cur:
            yield payload


def fetch_latest_config(conn: psycopg.Connection, location_code: str) -> dict[str, object]:
    """Reassemble the most recent config snapshot for a location from raw."""
    out: dict[str, object] = {}
    rows = conn.execute(
        """
        SELECT DISTINCT ON (config_type) config_type, payload
        FROM raw.toast_config
        WHERE location_code = %s
        ORDER BY config_type, pull_run_id DESC
        """,
        (location_code,),
    ).fetchall()
    for ctype, payload in rows:
        out[ctype] = payload
    return out


def merge_to_public(conn: psycopg.Connection) -> None:
    seed_locations(conn)
    conn.execute((SQL_DIR / "005_merge_to_public.sql").read_text())
    conn.commit()
