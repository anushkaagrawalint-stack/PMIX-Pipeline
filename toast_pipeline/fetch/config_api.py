"""Config / Menus API lookups — attribution enrichment, fetched per location
BEFORE the order pull.

Why these exist (from the reference architecture):
  - diningOptions: Toast leaves dining-option names NULL on the order payload;
    we must resolve guid -> name from the Config API. If this fetch fails the
    run must HALT — channel attribution would be silently wrong otherwise.
  - menus: appliedMenu is always null on bulk orders, so menu / menu-group is
    resolved at load time from the Menus API; this also keeps dim_item.menu_group
    current each run.
  - salesCategories + alternatePaymentTypes: power channel attribution
    (catering / offsite / 3PD tagging).

Payload-shape notes (learned from live API):
  - /menus/v2/menus returns an OBJECT: {"restaurantGuid": ..., "lastUpdated": ...,
    "menus": [...]} — not a bare array. Handle both defensively.
  - menuGroups can NEST further menuGroups; walk recursively.
  - Config endpoints (/config/v2/...) return arrays; filter non-dict entries
    defensively.
"""
from __future__ import annotations

import logging
import time

import requests

from .. import auth, config

log = logging.getLogger(__name__)


def _get(sess: requests.Session, location: "config.Location", path: str) -> list | dict:
    for attempt in range(3):
        resp = sess.get(
            f"{config.TOAST_HOST}{path}",
            headers=auth.auth_headers(location.guid),
            timeout=60,
        )
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", "2")))
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"rate-limited 3x on {path} for {location.code}")


def fetch_all_config(location: "config.Location",
                     session: requests.Session | None = None) -> dict[str, object]:
    sess = session or requests.Session()
    out: dict[str, object] = {}

    out["dining_options"] = _get(sess, location, "/config/v2/diningOptions")
    if not out["dining_options"]:
        # HALT: orders carry null dining-option names; without this lookup
        # everything downstream mis-attributes.
        raise RuntimeError(f"{location.code}: diningOptions fetch returned empty — halting run")

    out["menus"] = _get(sess, location, "/menus/v2/menus")
    out["sales_categories"] = _get(sess, location, "/config/v2/salesCategories")
    out["alt_payment_types"] = _get(sess, location, "/config/v2/alternatePaymentTypes")
    return out


def _as_list(payload, key: str) -> list[dict]:
    """Normalize an API payload to a list of dicts, whether the API returned
    a bare array or an object wrapping the array under `key`."""
    if isinstance(payload, dict):
        payload = payload.get(key) or []
    if not isinstance(payload, list):
        return []
    return [x for x in payload if isinstance(x, dict)]


def _walk_groups(groups: list, m_name: str, parent_name: str, sink: dict) -> None:
    for grp in groups:
        if not isinstance(grp, dict):
            continue
        g_name = grp.get("name") or parent_name
        for item in grp.get("menuItems") or []:
            if isinstance(item, dict) and item.get("guid"):
                sink[item["guid"]] = {"menu": m_name, "group": g_name}
        nested = grp.get("menuGroups") or grp.get("subgroups") or []
        if nested:
            _walk_groups(nested, m_name, g_name, sink)


def build_lookups(cfg: dict[str, object]) -> dict[str, dict]:
    """Flatten config payloads into guid -> name lookup dicts."""
    lk: dict[str, dict] = {"dining": {}, "menu": {}, "menu_group": {}, "sales_cat": {}, "alt_pay": {}}

    for d in _as_list(cfg.get("dining_options"), "diningOptions"):
        lk["dining"][d.get("guid", "")] = d.get("name", "")

    for menu in _as_list(cfg.get("menus"), "menus"):
        m_name = menu.get("name", "")
        if menu.get("guid"):
            lk["menu"][menu["guid"]] = m_name
        _walk_groups(menu.get("menuGroups") or [], m_name, "", lk["menu_group"])

    for sc in _as_list(cfg.get("sales_categories"), "salesCategories"):
        lk["sales_cat"][sc.get("guid", "")] = sc.get("name", "")

    for ap in _as_list(cfg.get("alt_payment_types"), "alternatePaymentTypes"):
        lk["alt_pay"][ap.get("guid", "")] = ap.get("name", "")

    return lk
