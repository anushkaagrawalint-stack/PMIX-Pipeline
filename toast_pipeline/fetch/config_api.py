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
from datetime import date, datetime

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


def _walk_groups(groups: list, m_name: str, parent_name: str, sink: dict,
                 group_sink: dict | None = None,
                 top_group: str | None = None) -> None:
    for grp in groups:
        if not isinstance(grp, dict):
            continue
        g_name = grp.get("name") or parent_name
        # top_group is the level-1 group name — matches Toast Web "Menu Group" column.
        # Nested subgroups inherit the top-level name so all items in a group
        # consistently map to the same group regardless of nesting depth.
        effective_top = top_group or g_name
        if group_sink is not None and grp.get("guid"):
            group_sink[grp["guid"]] = {"menu": m_name, "group": effective_top}
        for item in grp.get("menuItems") or []:
            if isinstance(item, dict) and item.get("guid"):
                sink[item["guid"]] = {"menu": m_name, "group": effective_top}
        nested = grp.get("menuGroups") or grp.get("subgroups") or []
        if nested:
            _walk_groups(nested, m_name, g_name, sink,
                         group_sink=group_sink, top_group=effective_top)


def build_lookups(cfg: dict[str, object]) -> dict[str, dict]:
    """Flatten config payloads into guid -> name lookup dicts."""
    lk: dict[str, dict] = {"dining": {}, "menu": {}, "menu_group": {}, "menu_group_guid": {}, "sales_cat": {}, "alt_pay": {}, "option_group": {}}

    for d in _as_list(cfg.get("dining_options"), "diningOptions"):
        lk["dining"][d.get("guid", "")] = d.get("name", "")

    menus_payload = cfg.get("menus") or {}
    if isinstance(menus_payload, dict):
        for ref in (menus_payload.get("modifierGroupReferences") or {}).values():
            if isinstance(ref, dict) and ref.get("guid") and ref.get("name"):
                lk["option_group"][ref["guid"]] = ref["name"]

    for menu in _as_list(cfg.get("menus"), "menus"):
        m_name = menu.get("name", "")
        if menu.get("guid"):
            lk["menu"][menu["guid"]] = m_name
        _walk_groups(menu.get("menuGroups") or [], m_name, "", lk["menu_group"],
                     group_sink=lk["menu_group_guid"])

    for sc in _as_list(cfg.get("sales_categories"), "salesCategories"):
        lk["sales_cat"][sc.get("guid", "")] = sc.get("name", "")

    for ap in _as_list(cfg.get("alt_payment_types"), "alternatePaymentTypes"):
        lk["alt_pay"][ap.get("guid", "")] = ap.get("name", "")

    return lk


def _business_date_to_date(business_date: int) -> date:
    return datetime.strptime(str(business_date), "%Y%m%d").date()


def build_time_lookups(snapshots: list[tuple[date, object]], latest_cfg: dict[str, object]):
    """Sale-time-aware menu resolver, built from daily menu-config snapshots
    (ascending, from db.fetch_menu_snapshots). Menu restructures must never
    rewrite order history — see MENU_SALETIME_RESOLUTION_SPEC.md section 2.

    Returns resolve(item_group_guid, item_guid, business_date) -> {"menu": ..., "group": ...} | {}
    Resolution order (first hit wins):
      1. group guid in the as-of snapshot (latest snapshot_date <= business_date,
         clamped to the earliest snapshot for dates before the first snapshot).
      2. group guid in the nearest other snapshot — scan backward, then forward.
      3. item guid in the as-of snapshot.
      4. item guid in the latest config (today's behavior, last resort).
    """
    per_snapshot: list[tuple[date, dict, dict]] = []
    for snapshot_date, payload in snapshots:
        group_guid_map: dict = {}
        item_guid_map: dict = {}
        for menu in _as_list(payload, "menus"):
            m_name = menu.get("name", "")
            _walk_groups(menu.get("menuGroups") or [], m_name, "", item_guid_map,
                         group_sink=group_guid_map)
        per_snapshot.append((snapshot_date, group_guid_map, item_guid_map))

    latest_item_guid_map = build_lookups(latest_cfg)["menu_group"]

    def resolve(item_group_guid: str, item_guid: str, business_date: int) -> dict:
        if not per_snapshot:
            return latest_item_guid_map.get(item_guid, {})

        target = _business_date_to_date(business_date)
        as_of = 0  # clamps to the earliest snapshot if target predates all of them
        for i, (d, _, _) in enumerate(per_snapshot):
            if d <= target:
                as_of = i
            else:
                break

        if item_group_guid:
            hit = per_snapshot[as_of][1].get(item_group_guid)
            if hit:
                return hit
            for i in range(as_of - 1, -1, -1):
                hit = per_snapshot[i][1].get(item_group_guid)
                if hit:
                    return hit
            for i in range(as_of + 1, len(per_snapshot)):
                hit = per_snapshot[i][1].get(item_group_guid)
                if hit:
                    return hit

        hit = per_snapshot[as_of][2].get(item_guid)
        if hit:
            return hit

        return latest_item_guid_map.get(item_guid, {})

    return resolve
