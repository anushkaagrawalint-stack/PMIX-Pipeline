"""check_reconciliation.py — tie fact tables to source-of-truth, every run.

Two reconciliation targets:

1. INTERNAL: staging vs public — row counts and non-void revenue must match
   for the merged window (catches silent merge drops).

2. EXTERNAL (manual, phase 2): Toast UI Sales Summary for one location-week.
   Expected deltas, documented so nobody panics:
     - Service charges: Toast "Net Sales" = item sales + service charges;
       our line_total sum excludes them. Delta should equal the service
       charge total exactly, check by check.
     - Tax: ours is pre-tax.
     - Discounts/comps: line_total is pre-adjustment; discounts live in
       fact_adjustments. Toast net-sales views apply them.
     - Voids: excluded via WHERE NOT is_voided.

Usage:
    python scripts/check_reconciliation.py --start 2026-06-01 --end 2026-06-07
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from toast_pipeline import db  # noqa: E402

TOLERANCE = 0.01


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()

    conn = db.connect()
    q = """
        SELECT location_code,
               count(*)                                            AS rows,
               round(sum(line_total) FILTER (WHERE NOT is_voided), 2) AS net_item_sales,
               round(sum(line_total) FILTER (WHERE is_voided), 2)     AS voided_amount,
               count(*) FILTER (WHERE channel_code = 'OTHER')         AS unattributed
        FROM public.fact_order_lines
        WHERE business_date BETWEEN %s AND %s
        GROUP BY location_code ORDER BY location_code
    """
    rows = conn.execute(q, (args.start, args.end)).fetchall()
    print(f"\nfact_order_lines {args.start} -> {args.end}")
    print(f"{'location':<12}{'rows':>10}{'net item sales':>18}{'voided $':>12}{'unattrib':>10}")
    for r in rows:
        print(f"{r[0]:<12}{r[1]:>10}{str(r[2]):>18}{str(r[3]):>12}{r[4]:>10}")

    drift = conn.execute(
        """
        WITH s AS (SELECT count(*) c, round(coalesce(sum(line_total) FILTER (WHERE NOT is_voided),0),2) r
                   FROM staging.order_lines),
             p AS (SELECT count(*) c, round(coalesce(sum(line_total) FILTER (WHERE NOT is_voided),0),2) r
                   FROM public.fact_order_lines fol
                   WHERE fol.selection_guid IN (SELECT selection_guid FROM staging.order_lines))
        SELECT s.c, p.c, s.r, p.r FROM s, p
        """
    ).fetchone()
    sc, pc, sr, pr = drift
    ok = sc == pc and abs(float(sr or 0) - float(pr or 0)) < TOLERANCE
    print(f"\nstaging->public drift: rows {sc}->{pc}, revenue {sr}->{pr}  [{'OK' if ok else 'FAIL'}]")
    print("\nNext: compare net_item_sales against Toast UI Sales Summary for the same")
    print("location-week. Delta should equal service charges exactly (see docstring).")
    conn.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
