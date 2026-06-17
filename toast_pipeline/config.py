"""Configuration: environment variables, locations, constants."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

TOAST_HOST = os.environ.get("TOAST_HOST", "https://ws-api.toasttab.com")
TOAST_CLIENT_ID = os.environ.get("TOAST_CLIENT_ID", "")
TOAST_CLIENT_SECRET = os.environ.get("TOAST_CLIENT_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")  # Neon connection string (sslmode=require)

PAGE_SIZE = 100
MAX_REQ_PER_SEC = 5          # Toast limit is per-location; one worker per location
DEFAULT_BACKPAD_DAYS = 2     # used by scheduled cron run to catch late edits/voids
HOURLY_BACKPAD_HOURS = 3     # used by the dashboard refresh trigger (on-demand hourly pull)
CATERING_VOID_WINDOW_DAYS = 30  # weekly catchup window to capture late catering cancellations
MAX_WINDOW_DAYS = 45


@dataclass(frozen=True)
class Location:
    code: str          # short code used everywhere downstream
    name: str
    guid: str          # Toast restaurant external GUID


def load_locations() -> list[Location]:
    """LOCATIONS env var: CODE:Name:guid,CODE:Name:guid,...

    Example:
      LOCATIONS=BALLPARK:Ballpark:abc-123,MVT:MVT:def-456
    """
    spec = os.environ.get("LOCATIONS", "")
    locs: list[Location] = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        code, name, guid = part.split(":", 2)
        locs.append(Location(code=code.strip(), name=name.strip(), guid=guid.strip()))
    if not locs:
        raise SystemExit("No LOCATIONS configured — set the LOCATIONS env var (see .env.example)")
    return locs
