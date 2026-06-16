"""Toast authentication — OAuth2 client-credentials.

One bearer token is issued per client credential set; the restaurant context
is supplied per-request via the Toast-Restaurant-External-ID header.
Tokens are cached until ~60s before expiry.
"""
from __future__ import annotations

import time

import requests

from . import config

_token: str | None = None
_expires_at: float = 0.0


def get_token(session: requests.Session | None = None) -> str:
    global _token, _expires_at
    if _token and time.time() < _expires_at - 60:
        return _token

    sess = session or requests.Session()
    resp = sess.post(
        f"{config.TOAST_HOST}/authentication/v1/authentication/login",
        json={
            "clientId": config.TOAST_CLIENT_ID,
            "clientSecret": config.TOAST_CLIENT_SECRET,
            "userAccessType": "TOAST_MACHINE_CLIENT",
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    tok = body["token"]
    _token = tok["accessToken"]
    _expires_at = time.time() + int(tok.get("expiresIn", 3600))
    return _token


def auth_headers(location_guid: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_token()}",
        "Toast-Restaurant-External-ID": location_guid,
    }
