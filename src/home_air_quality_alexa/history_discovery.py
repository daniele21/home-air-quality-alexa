"""Minimal, history-only AQM device and sensor discovery.

The generic aioamazondevices.get_devices_data() path additionally fetches
notifications, routines, music and communication preferences. On 2026-11-10,
a communications request returned HTTP 429 and blocked all historical air
quality channels even though the stored Alexa login succeeded.

The history GraphQL call needs only the live endpoint identifiers and the
sensor interface/instance metadata. Discover exactly those through the
library's device refresh + sensor sync, without unrelated API requests.

This uses version-pinned internal library interfaces deliberately; unit tests
and a private live canary gate upgrades. Do not silently fall back to the
unrelated full-device discovery if an upstream API changes.
"""

from __future__ import annotations

from typing import Any

from .history_fetch import HistoryFetchError


async def discover_history_devices(api: Any) -> dict[str, Any]:
    """Read sensor identities without touching optional Alexa services.

    Must be called *after* login_mode_stored_data(). Does not open a SQLite
    database, request historical values or persist Amazon credentials.
    """
    try:
        handler = api._device_handler
        sensors = api._sensor_handler
        lock = api._sensor_sync_lock
        # Check the internal compatibility contract before making requests.
        if not callable(handler.update_devices) or not callable(sensors.full_sensor_sync):
            raise AttributeError("Unsupported aioamazondevices history discovery")
    except AttributeError as exc:
        raise HistoryFetchError(
            "Pinned Alexa library does not expose the history discovery contract"
        ) from exc

    async with lock:
        await handler.update_devices()
        sensors.full_sensor_sync(handler.devices, handler.endpoint_states)
    return handler.devices
