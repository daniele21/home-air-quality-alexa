"""Experimental Amazon *device-code* login for the Alexa collector.

The MAP endpoints are undocumented. This is based on Amazon's code-pair
flow, verified for Audible but NOT yet verified for Alexa's device type.
Do not send Amazon passwords, one-time codes or private device codes here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import math
import time
from typing import Any, Callable, Awaitable
from urllib.parse import urlsplit

MARKETPLACES = {"it": "it", "de": "de", "fr": "fr", "es": "es", "uk": "co.uk", "us": "com"}
MIN_POLL_SECONDS = 5.0
MAX_BACKOFF = 60.0


class DeviceAuthorizationError(Exception):
    """Safe, non-secret description of a device-code authorization failure."""


@dataclass(frozen=True)
class CodePair:
    public_code: str
    private_code: str
    expires_in: float
    poll_seconds: float


def parse_code_pair(payload: dict[str, Any]) -> CodePair:
    try:
        public = payload["public_code"]
        private = payload["private_code"]
        expires = float(payload["expires_in"])
        interval = float(payload["polling_interval_in_seconds"])
        if not all(isinstance(v, str) and v.strip() for v in (public, private)):
            raise ValueError("missing code")
        if not all(math.isfinite(n) and n > 0 for n in (expires, interval)):
            raise ValueError("invalid timing")
        return CodePair(public, private, expires, max(interval, MIN_POLL_SECONDS))
    except (TypeError, KeyError, ValueError) as exc:
        raise DeviceAuthorizationError("Unexpected code-pair response from Amazon") from exc


def _error_code(payload: dict[str, Any]) -> str | None:
    error = payload.get("response", {}).get("error") if isinstance(payload.get("response"), dict) else None
    if error is None:
        error = payload.get("error")
    if isinstance(error, dict):
        return next((error[k] for k in ("code", "error_code", "type") if isinstance(error.get(k), str)), None)
    return error if isinstance(error, str) else None


async def _post_json(session: Any, url: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, dict[str, Any], float | None]:
    """Read response without printing cookies, bodies, codes or tokens."""
    async with session.post(url, json=payload, headers=headers, timeout=20) as response:
        status = response.status
        retry = response.headers.get("Retry-After")
        try:
            retry_after = max(0.0, float(retry)) if retry is not None else None
        except (ValueError, TypeError):
            retry_after = None
        try:
            content = await response.json(content_type=None)
        except (ValueError, TypeError):
            content = {}
        return status, content if isinstance(content, dict) else {}, retry_after


def _registration_body(serial: str, device_type: str, auth_pair: CodePair, domain: str, client: Any) -> dict[str, Any]:
    from aioamazondevices.const.http import (
        AMAZON_APP_ID, AMAZON_APP_NAME, AMAZON_APP_VERSION,
        AMAZON_CLIENT_OS, AMAZON_DEVICE_SOFTWARE_VERSION,
    )
    import base64
    import secrets

    frc = base64.b64encode(secrets.token_bytes(313)).decode("ascii").rstrip("=")
    return {
        "requested_extensions": ["device_info", "customer_info"],
        "cookies": {
            "website_cookies": [],
            "domain": f".amazon.{domain}",
        },
        "registration_data": {
            "domain": "Device",
            "app_version": AMAZON_APP_VERSION,
            "device_type": device_type,
            "device_name": f"%FIRST_NAME%\\u0027s%DUPE_STRATEGY_1ST%{AMAZON_APP_NAME}",
            "os_version": AMAZON_CLIENT_OS,
            "device_serial": serial,
            "device_model": "iPhone",
            "app_name": AMAZON_APP_NAME,
            "software_version": AMAZON_DEVICE_SOFTWARE_VERSION,
        },
        "auth_data": {
            "code_pair": {
                "public_code": auth_pair.public_code,
                "private_code": auth_pair.private_code,
            },
            "use_global_authentication": "true",
        },
        "user_context_map": {"frc": frc},
        "requested_token_type": [
            "bearer", "mac_dms", "website_cookies", "store_authentication_cookie"
        ],
    }


def parse_registration(payload: dict[str, Any], serial: str) -> dict[str, Any]:
    """Extract exactly the stored-login schema aioamazondevices expects."""
    try:
        success = payload["response"]["success"]
        tokens = success["tokens"]
        bearer = tokens["bearer"]
        mac = tokens["mac_dms"]
        extensions = success["extensions"]
        device_info = extensions["device_info"]
        if device_info.get("device_serial_number") != serial:
            raise ValueError("registration serial mismatch")
        cookies = tokens["website_cookies"]
        website_cookies = {c["Name"]: c["Value"].replace('"', "") for c in cookies}
        store_auth = tokens["store_authentication_cookie"]
        if not isinstance(store_auth.get("cookie"), str) or not store_auth["cookie"]:
            raise ValueError("session cookie missing")
        return {
            "adp_token": mac["adp_token"],
            "device_private_key": mac["device_private_key"],
            "access_token": bearer["access_token"],
            "refresh_token": bearer["refresh_token"],
            "expires": (datetime.now(timezone.utc) + timedelta(seconds=int(bearer["expires_in"]))).timestamp(),
            "website_cookies": website_cookies,
            "store_authentication_cookie": store_auth,
            "device_info": device_info,
            "customer_info": extensions["customer_info"],
        }
    except (ValueError, TypeError, KeyError) as exc:
        raise DeviceAuthorizationError(
            "Amazon registered the code but returned data incompatible with aioamazondevices"
        ) from exc


async def login_device_code(api: Any, session: Any, marketplace: str,
                            on_code: Callable[[str, str], None],
                            *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                            monotonic: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Authenticate via code pair and initialize the existing Alexa client.

    Nothing is persisted until the registration, cookie refresh, and capability
    setup have completed. Callers must store the returned dict as a secret.
    """
    if marketplace not in MARKETPLACES:
        raise DeviceAuthorizationError("Unsupported Amazon marketplace")
    from aioamazondevices.const.http import AMAZON_DEVICE_TYPE, REQUEST_AGENT

    domain = MARKETPLACES[marketplace]
    api_host = f"api.amazon.{domain}"
    base_url = f"https://{api_host}"
    verification_url = f"https://www.amazon.{domain}/code"
    serial = api.login._serial  # Matches library's dummy-device registration identity.
    headers = {
        "Accept": "application/json",
        "User-Agent": REQUEST_AGENT["Amazon"],
        "x-amzn-identity-auth-domain": api_host,
    }
    request_time = monotonic()
    pair_payload = {
        "code_data": {"device_serial": serial, "device_type": AMAZON_DEVICE_TYPE, "domain": "Device"},
        "code_properties": {"code_duration": "600", "code_length": "5"},
    }
    try:
        status, data, _ = await _post_json(session, base_url + "/auth/create/codepair", headers, pair_payload)
    except (OSError, asyncio.TimeoutError) as exc:
        raise DeviceAuthorizationError("Could not connect to Amazon identity service") from exc
    if status != 200 or _error_code(data):
        raise DeviceAuthorizationError(f"Amazon declined device-code creation (HTTP {status}; {_error_code(data) or 'unknown error'})")
    pair = parse_code_pair(data)
    deadline = request_time + pair.expires_in
    on_code(verification_url, pair.public_code)
    body = _registration_body(serial, AMAZON_DEVICE_TYPE, pair, domain, api)
    delay = pair.poll_seconds
    failures = 0
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise DeviceAuthorizationError("The device code expired; start login-browser again")
        await sleep(min(delay, remaining))
        if monotonic() >= deadline:
            raise DeviceAuthorizationError("The device code expired; start login-browser again")
        try:
            status, data, retry_after = await _post_json(session, base_url + "/auth/register", headers, body)
        except (OSError, asyncio.TimeoutError):
            status, data, retry_after = 503, {}, None
        error = _error_code(data)
        if status == 200 and isinstance(data.get("response"), dict) and "success" in data["response"]:
            login_data = parse_registration(data, serial)
            login = api.login
            state = login._session_state_data
            state.login_stored_data = login_data
            # The client starts with the target marketplace, not the default US.
            state.country_specific_data(f"https://www.amazon.{domain}")
            try:
                await login._domain_refresh_auth_cookies()
                await login._register_device_capabilities()
                await login.obtain_account_customer_id()
            except Exception as exc:
                raise DeviceAuthorizationError(
                    f"Amazon linked the device, but Alexa session initialization failed ({type(exc).__name__})"
                ) from exc
            login_data["site"] = state.retail_site_url.human_repr().rstrip("/")
            return login_data
        if status == 401 and isinstance(error, str) and error.lower() == "unauthorized":
            delay = pair.poll_seconds
            failures = 0
            continue
        if status == 429 or status >= 500:
            failures += 1
            delay = min(MAX_BACKOFF, pair.poll_seconds * 2 ** min(failures, 4))
            if retry_after is not None and math.isfinite(retry_after):
                delay = max(delay, retry_after)
            continue
        raise DeviceAuthorizationError(
            f"Amazon rejected device-code registration (HTTP {status}; {error or 'unexpected response'})"
        )
