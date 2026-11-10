"""Offline contract tests for the minimal Alexa history discovery path."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from home_air_quality_alexa.history_discovery import discover_history_devices
from home_air_quality_alexa.history_fetch import HistoryFetchError


class DiscoveryTests(unittest.TestCase):
    def build_api(self):
        device = SimpleNamespace(serial_number="SECRET_SERIAL", endpoint_id="SECRET_ENDPOINT")
        handler = SimpleNamespace(
            update_devices=AsyncMock(),
            devices={"private": device},
            endpoint_states={"private": {"features": []}},
        )
        sensors = SimpleNamespace(full_sensor_sync=Mock())
        unrelated = AsyncMock(side_effect=AssertionError("Unrelated Alexa API called"))
        api = SimpleNamespace(
            _device_handler=handler,
            _sensor_handler=sensors,
            _sensor_sync_lock=asyncio.Lock(),
            get_devices_data=unrelated,
        )
        return api

    def test_refreshes_only_device_metadata_and_sensor_map(self):
        api = self.build_api()
        result = asyncio.run(discover_history_devices(api))
        self.assertEqual(list(result), ["private"])
        api._device_handler.update_devices.assert_awaited_once_with()
        api._sensor_handler.full_sensor_sync.assert_called_once_with(
            api._device_handler.devices, api._device_handler.endpoint_states
        )
        api.get_devices_data.assert_not_awaited()

    def test_fail_closed_for_missing_library_contract(self):
        api = SimpleNamespace(get_devices_data=AsyncMock())
        with self.assertRaisesRegex(HistoryFetchError, "history discovery contract"):
            asyncio.run(discover_history_devices(api))
        api.get_devices_data.assert_not_awaited()

    def test_upstream_failure_does_not_trigger_fallback_or_stale_data(self):
        api = self.build_api()
        api._device_handler.update_devices.side_effect = RuntimeError(
            "private Amazon response SECRET_TOKEN"
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(discover_history_devices(api))
        api._sensor_handler.full_sensor_sync.assert_not_called()
        api.get_devices_data.assert_not_awaited()

    def test_internal_sensor_sync_is_not_awaited_and_called_exactly_once(self):
        api = self.build_api()
        asyncio.run(discover_history_devices(api))
        self.assertEqual(api._sensor_handler.full_sensor_sync.call_count, 1)


if __name__ == "__main__":
    unittest.main()
