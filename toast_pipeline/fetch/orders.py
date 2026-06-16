"""Fetch orders via /orders/v2/ordersBulk.

CRITICAL SEMANTICS (learned the hard way, documented in the reference
architecture): startDate/endDate filter on Toast's *modified* timestamp, not
business date. An order placed Monday but voided Thursday appears in
Thursday's window. Therefore:
  - we over-fetch a padded modified-date window,
  - attribute every order to payload.businessDate (local service day),
  - dedupe on order_guid at landing (PK upsert).

Rate limit: 5 req/s per location. One worker per location keeps quotas
independent; within a worker a simple pacing sleep keeps us under the cap.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import requests

from .. import auth, config

log = logging.getLogger(__name__)

_MIN_INTERVAL = 1.0 / config.MAX_REQ_PER_SEC


def _iso(dt: datetime) -> str:
    # Toast expects ISO-8601 with milliseconds and offset, e.g. 2026-06-01T00:00:00.000+0000
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def fetch_orders(
    location: "config.Location",
    window_start: date,
    window_end: date,
    session: requests.Session | None = None,
) -> Iterator[dict]:
    """Yield raw order payloads for a modified-date window [start, end]."""
    sess = session or requests.Session()
    start_dt = datetime.combine(window_start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(window_end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    page = 1
    last_call = 0.0
    while True:
        wait = _MIN_INTERVAL - (time.time() - last_call)
        if wait > 0:
            time.sleep(wait)
        last_call = time.time()

        resp = sess.get(
            f"{config.TOAST_HOST}/orders/v2/ordersBulk",
            headers=auth.auth_headers(location.guid),
            params={
                "startDate": _iso(start_dt),
                "endDate": _iso(end_dt),
                "pageSize": config.PAGE_SIZE,
                "page": page,
            },
            timeout=120,
        )
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", "2"))
            log.warning("%s: 429 rate-limited, sleeping %ss", location.code, retry)
            time.sleep(retry)
            continue
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            return
        log.info("%s: page %d -> %d orders", location.code, page, len(batch))
        yield from batch
        if len(batch) < config.PAGE_SIZE:
            return
        page += 1


def business_date_of(order: dict) -> int | None:
    """payload.businessDate is an int yyyymmdd in restaurant-local terms.

    NEVER attribute by UTC timestamps — businessDate is the local service day
    and is what ties out to Toast's own Sales Summary.
    """
    bd = order.get("businessDate")
    return int(bd) if bd else None
