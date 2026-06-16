"""Deterministic cleaning, driven by audited CSV lookups in mappings/.

Standing rule (inherited from the manual PMix process and the reference
architecture): a modifier or item grouping is never collapsed, relabeled, or
merged without explicit sign-off. Mapping changes are reviewed CSV diffs,
not code changes.

mappings/
  name_mappings.csv      raw_name -> canonical_name (typos, renames, cross-menu variants)
  modifier_blocklist.csv pollution to exclude ("Please Include Utensils", ...)
  modifier_tags.csv      canonical modifier name -> mod_type (base/sauce/veggie/...)
"""
from __future__ import annotations

import csv
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

MAPPINGS_DIR = Path(__file__).resolve().parents[2] / "mappings"

# Suffixes stripped from display names. NOTE the while-loop: a single pass
# misses stacked suffixes ("Grain Bowl - Club Feast - Gameday"). This was a
# real bug in the reference pipeline (single-pass suffix-strip), fixed here.
_SUFFIX_PATTERNS = [
    re.compile(r"\s*-\s*(club feast|gameday|side|catering|in house|in-house|ezcater)\s*$", re.IGNORECASE),
]

_MOJIBAKE = {
    "\u00e2\u20ac\u2122": "'",   # â€™
    "\u00e2\u20ac\u201c": "-",   # â€“
    "\u00e2\u20ac\u0153": '"',   # â€œ
    "\u00e2\u20ac\u009d": '"',
    "\u00c3\u00a9": "é",
}


@lru_cache(maxsize=1)
def name_mappings() -> dict[str, str]:
    return _load_two_col("name_mappings.csv", "raw_name", "canonical_name")


@lru_cache(maxsize=1)
def modifier_blocklist() -> set[str]:
    path = MAPPINGS_DIR / "modifier_blocklist.csv"
    with open(path, newline="", encoding="utf-8") as f:
        return {row["name"].strip().lower() for row in csv.DictReader(f) if row.get("name")}


@lru_cache(maxsize=1)
def modifier_tags() -> dict[str, str]:
    return _load_two_col("modifier_tags.csv", "canonical_name", "mod_type")


def _load_two_col(fname: str, key: str, val: str) -> dict[str, str]:
    path = MAPPINGS_DIR / fname
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            k = (row.get(key) or "").strip()
            v = (row.get(val) or "").strip()
            if k and v:
                out[k.lower()] = v
    return out


def repair_encoding(s: str) -> str:
    for bad, good in _MOJIBAKE.items():
        s = s.replace(bad, good)
    return unicodedata.normalize("NFC", s)


def clean_name(raw: str) -> str:
    """raw display name -> canonical name.

    Order matters: encoding repair -> whitespace collapse -> explicit mapping
    (exact, case-insensitive) -> suffix stripping (looped) -> re-check mapping.
    """
    s = repair_encoding(raw or "").strip()
    s = re.sub(r"\s+", " ", s)
    if not s:
        return s

    mapped = name_mappings().get(s.lower())
    if mapped:
        return mapped

    changed = True
    while changed:           # loop until stable — stacked suffixes
        changed = False
        for pat in _SUFFIX_PATTERNS:
            new = pat.sub("", s)
            if new != s:
                s, changed = new.strip(), True

    return name_mappings().get(s.lower(), s)


def is_blocklisted(name: str) -> bool:
    return name.strip().lower() in modifier_blocklist()


def mod_type_of(canonical: str) -> str | None:
    return modifier_tags().get(canonical.strip().lower())


@lru_cache(maxsize=1)
def mix_exclusions() -> set[str]:
    """Items excluded from menu-mix / Menu Engineering counts (markups,
    platform fees, gift cards). They remain in revenue facts; the analytics
    layer filters them when computing mix %, thresholds, and quadrants."""
    path = MAPPINGS_DIR / "mix_exclusions.csv"
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {row["name"].strip().lower() for row in csv.DictReader(f) if row.get("name")}


def is_mix_excluded(canonical: str) -> bool:
    return canonical.strip().lower() in mix_exclusions()
