"""list_locations.py — discover the restaurant GUIDs your Toast credential
can access, and print a ready-to-paste LOCATIONS= line for the .env file.

Usage (from the pmix-pipeline folder, with .venv active and TOAST_CLIENT_ID /
TOAST_CLIENT_SECRET already in .env):

    python scripts/list_locations.py

If automatic discovery isn't available for your credential type, you can pass
GUIDs by hand (e.g. copied from Toast Web) and the script will look up each
location's name so you know which is which:

    python scripts/list_locations.py guid1 guid2 guid3 guid4 guid5
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from toast_pipeline import auth, config  # noqa: E402

CODE_HINTS = {
    "ballpark": "BALLPARK", "mvt": "MVT", "mt vernon": "MVT", "mount vernon": "MVT",
    "nl": "NL", "navy": "NL", "mosaic": "MOSAIC", "rockville": "ROCKVILLE",
}


def _code_for(name: str, used: set[str]) -> str:
    low = (name or "").lower()
    for hint, code in CODE_HINTS.items():
        if hint in low and code not in used:
            return code
    base = "".join(ch for ch in (name or "LOC").upper() if ch.isalnum())[:10] or "LOC"
    code, i = base, 2
    while code in used:
        code, i = f"{base}{i}", i + 1
    return code


def _try_get(sess: requests.Session, path: str, headers: dict) -> tuple[int, object]:
    r = sess.get(f"{config.TOAST_HOST}{path}", headers=headers, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def discover(sess: requests.Session) -> list[dict]:
    """Try listing endpoints that return accessible restaurants."""
    token_headers = {"Authorization": f"Bearer {auth.get_token(sess)}"}
    for path in ("/partners/v1/restaurants", "/restaurants/v1/restaurants"):
        status, body = _try_get(sess, path, token_headers)
        print(f"  trying {path} -> HTTP {status}")
        if status == 200 and isinstance(body, list) and body:
            out = []
            for r in body:
                guid = r.get("restaurantGuid") or r.get("guid") or ""
                name = (r.get("restaurantName") or r.get("locationName")
                        or r.get("name") or "")
                if guid:
                    out.append({"guid": guid, "name": name})
            if out:
                return out
    return []


def lookup_names(sess: requests.Session, guids: list[str]) -> list[dict]:
    """Given GUIDs, fetch each restaurant's name via the restaurants API."""
    out = []
    for guid in guids:
        headers = auth.auth_headers(guid)
        status, body = _try_get(sess, f"/restaurants/v1/restaurants/{guid}", headers)
        name = ""
        if status == 200 and isinstance(body, dict):
            gen = body.get("general") or {}
            name = gen.get("name") or gen.get("locationName") or body.get("name") or ""
            loc = body.get("location") or {}
            extra = loc.get("address1") or ""
            print(f"  {guid}  ->  {name}   {extra}")
        else:
            print(f"  {guid}  ->  HTTP {status} (check the GUID)")
        out.append({"guid": guid, "name": name})
    return out


def main() -> None:
    if not config.TOAST_CLIENT_ID or not config.TOAST_CLIENT_SECRET:
        raise SystemExit("Set TOAST_CLIENT_ID and TOAST_CLIENT_SECRET in .env first.")

    sess = requests.Session()
    print("Authenticating with Toast...")
    auth.get_token(sess)
    print("  authentication OK — credentials work.\n")

    manual_guids = [a.strip() for a in sys.argv[1:] if a.strip()]
    if manual_guids:
        print("Looking up the GUIDs you provided:")
        found = lookup_names(sess, manual_guids)
    else:
        print("Discovering accessible restaurants:")
        found = discover(sess)
        if not found:
            print("\nAutomatic discovery isn't enabled for this credential type.")
            print("Get the GUIDs from Toast Web (Edit Location IDs page) and re-run:")
            print("  python scripts/list_locations.py <guid1> <guid2> <guid3> <guid4> <guid5>")
            return

    used: set[str] = set()
    parts = []
    print("\nLocations found:")
    for r in found:
        code = _code_for(r["name"], used)
        used.add(code)
        print(f"  {code:<10} {r['name']:<30} {r['guid']}")
        parts.append(f"{code}:{(r['name'] or code).replace(',', ' ').replace(':', ' ')}:{r['guid']}")

    print("\nPaste this line into your .env file (replacing the existing LOCATIONS line):\n")
    print("LOCATIONS=" + ",".join(parts))


if __name__ == "__main__":
    main()
