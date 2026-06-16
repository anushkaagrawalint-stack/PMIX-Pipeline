"""Parse a raw Toast order payload into typed staging rows.

Grain:
  order -> checks -> selections (line items) -> modifiers (recursive, any depth)

Voids are PRESERVED (is_voided flags), never dropped — revenue views exclude
them at query time. Refund/void/discount events also land in adjustments.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..clean import normalize


@dataclass
class ParsedOrder:
    lines: list[dict] = field(default_factory=list)
    modifiers: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)
    adjustments: list[dict] = field(default_factory=list)


def parse_order(order: dict, location_code: str, lookups: dict) -> ParsedOrder:
    out = ParsedOrder()
    order_guid = order.get("guid", "")
    business_date = int(order.get("businessDate") or 0)
    if not order_guid or not business_date:
        return out  # unparseable — raw payload remains for forensics

    dining_guid = (order.get("diningOption") or {}).get("guid", "")
    dining_name = lookups["dining"].get(dining_guid, "")  # Config-API fallback: payload name is null

    for check in order.get("checks", []) or []:
        check_guid = check.get("guid", "")
        check_voided = bool(check.get("voided") or check.get("deleted"))
        out.checks.append({
            "check_guid": check_guid,
            "order_guid": order_guid,
            "location_code": location_code,
            "business_date": business_date,
            "is_voided": check_voided,
            "tax_amount": check.get("taxAmount"),
            "total_amount": check.get("totalAmount"),
        })

        for pay in check.get("payments", []) or []:
            alt_guid = (pay.get("otherPayment") or {}).get("guid", "")
            out.payments.append({
                "payment_guid": pay.get("guid", ""),
                "check_guid": check_guid,
                "order_guid": order_guid,
                "location_code": location_code,
                "business_date": business_date,
                "payment_type": pay.get("type"),
                "alt_payment_name": lookups["alt_pay"].get(alt_guid) or None,
                "amount": pay.get("amount"),
                "tip_amount": pay.get("tipAmount"),
            })
            if pay.get("refund"):
                out.adjustments.append({
                    "order_guid": order_guid, "check_guid": check_guid,
                    "selection_guid": None, "location_code": location_code,
                    "business_date": business_date, "kind": "REFUND",
                    "name": pay.get("type"),
                    "amount": (pay.get("refund") or {}).get("refundAmount"),
                })

        for sel in check.get("selections", []) or []:
            _parse_selection(sel, out, order_guid, check_guid, check_voided,
                             location_code, business_date, dining_name, lookups)
    return out


def _parse_selection(sel: dict, out: ParsedOrder, order_guid: str, check_guid: str,
                     check_voided: bool, location_code: str, business_date: int,
                     dining_name: str, lookups: dict) -> None:
    sel_guid = sel.get("guid", "")
    item = sel.get("item") or {}
    item_guid = item.get("guid", "") or ""
    raw_name = sel.get("displayName") or item.get("name") or ""
    clean = normalize.clean_name(raw_name)
    voided = bool(sel.get("voided")) or check_voided

    mg = lookups["menu_group"].get(item_guid, {})
    out.lines.append({
        "selection_guid": sel_guid,
        "order_guid": order_guid,
        "check_guid": check_guid,
        "location_code": location_code,
        "business_date": business_date,
        "item_guid": item_guid,
        "item_multi_loc_id": str(item.get("multiLocationId") or ""),
        "raw_name": raw_name,
        "clean_name": clean,
        "menu_guid": (sel.get("appliedMenu") or {}).get("guid") or None,  # always null on bulk; kept for honesty
        "menu_name": mg.get("menu"),
        "menu_group_name": mg.get("group"),
        "sales_category": lookups["sales_cat"].get((sel.get("salesCategory") or {}).get("guid", "")) or None,
        "dining_option": dining_name or None,
        "quantity": sel.get("quantity") or 0,
        "line_total": sel.get("price") or 0,          # gross, pre-adjustments, pre-tax
        "pre_discount": sel.get("preDiscountPrice"),
        "is_voided": voided,
        "is_deferred": bool(sel.get("deferred")),
    })
    if sel.get("voided"):
        out.adjustments.append({
            "order_guid": order_guid, "check_guid": check_guid,
            "selection_guid": sel_guid, "location_code": location_code,
            "business_date": business_date, "kind": "VOID",
            "name": clean, "amount": sel.get("price"),
        })

    _walk_modifiers(sel.get("modifiers") or [], out, sel_guid, order_guid,
                    location_code, business_date, voided, depth=1)


def _walk_modifiers(mods: list, out: ParsedOrder, parent_sel: str, order_guid: str,
                    location_code: str, business_date: int, parent_voided: bool,
                    depth: int) -> None:
    for mod in mods:
        raw = mod.get("displayName") or (mod.get("item") or {}).get("name") or ""
        clean = normalize.clean_name(raw)
        out.modifiers.append({
            "modifier_guid": mod.get("guid", ""),
            "parent_selection": parent_sel,
            "order_guid": order_guid,
            "location_code": location_code,
            "business_date": business_date,
            "raw_name": raw,
            "clean_name": clean,
            "depth": depth,
            "quantity": mod.get("quantity") or 1,
            "price": mod.get("price") or 0,
            "is_blocklisted": normalize.is_blocklisted(clean),
            "is_voided": bool(mod.get("voided")) or parent_voided,
        })
        nested = mod.get("modifiers") or []
        if nested:
            _walk_modifiers(nested, out, parent_sel, order_guid,
                            location_code, business_date, parent_voided, depth + 1)
