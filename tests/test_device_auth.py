"""Offline contract tests for the experimental browser device-code flow."""
import asyncio
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from home_air_quality_alexa.device_auth import (
    CodePair, DeviceAuthorizationError, _error_code,
    _registration_body, login_device_code, parse_code_pair, parse_registration,
)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    def monotonic(self):
        return self.now

    async def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


class Response:
    def __init__(self, status, data, retry_after=None):
        self.status = status
        self.data = data
        self.headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return self.data


class FakeSession:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def post(self, url, *, json, headers, timeout):
        self.requests.append((url, json, headers, timeout))
        return self.replies.pop(0)


class FakeState:
    def __init__(self):
        self.login_stored_data = {}
        self.retail_site_url = SimpleNamespace(human_repr=lambda: "https://www.amazon.it")

    def country_specific_data(self, site):
        self.site = site


class FakeLogin:
    def __init__(self):
        self._serial = "SERIAL"
        self._session_state_data = FakeState()
        self.called = []

    async def _domain_refresh_auth_cookies(self):
        self.called.append("cookie")

    async def _register_device_capabilities(self):
        self.called.append("capability")

    async def obtain_account_customer_id(self):
        self.called.append("customer")


def fake_const():
    root = ModuleType("aioamazondevices")
    const = ModuleType("aioamazondevices.const")
    http = ModuleType("aioamazondevices.const.http")
    http.AMAZON_DEVICE_TYPE = "A2IVLV5VM2W81"
    http.REQUEST_AGENT = {"Amazon": "AmazonWebView/Alexa-test"}
    http.AMAZON_APP_ID = "APP"
    http.AMAZON_APP_NAME = "AioAmazonDevices"
    http.AMAZON_APP_VERSION = "2.2"
    http.AMAZON_CLIENT_OS = "18.5"
    http.AMAZON_DEVICE_SOFTWARE_VERSION = "3560"
    return {"aioamazondevices": root, "aioamazondevices.const": const,
            "aioamazondevices.const.http": http}


def pair():
    return {"public_code": "ABCDE", "private_code": "secret",
            "expires_in": "600", "polling_interval_in_seconds": "5"}


def success(serial="SERIAL"):
    return {"response": {"success": {
        "tokens": {
            "bearer": {"access_token": "access", "refresh_token": "refresh", "expires_in": "3600"},
            "mac_dms": {"adp_token": "adp", "device_private_key": "private"},
            "website_cookies": [{"Name": "session-id", "Value": "cookie"}],
            "store_authentication_cookie": {"cookie": "stored"},
        },
        "extensions": {
            "device_info": {"device_serial_number": serial},
            "customer_info": {"home_region": "EU"},
        },
    }}}


class ParsingTests(unittest.TestCase):
    def test_pair_validation(self):
        self.assertEqual(parse_code_pair(pair()).poll_seconds, 5)
        with self.assertRaises(DeviceAuthorizationError):
            parse_code_pair({"public_code": "code", "private_code": "secret", "expires_in": -2,
                             "polling_interval_in_seconds": 3})
        with self.assertRaises(DeviceAuthorizationError):
            parse_code_pair({"public_code": "code"})

    def test_registration_is_strict_on_serial_and_cookie(self):
        record = parse_registration(success(), "SERIAL")
        self.assertEqual(record["refresh_token"], "refresh")
        with self.assertRaises(DeviceAuthorizationError):
            parse_registration(success("DIFFERENT"), "SERIAL")
        missing = success()
        del missing["response"]["success"]["tokens"]["store_authentication_cookie"]
        with self.assertRaises(DeviceAuthorizationError):
            parse_registration(missing, "SERIAL")

    def test_pending_not_confused_with_other_401(self):
        self.assertEqual(_error_code({"response": {"error": {"code": "Unauthorized"}}}), "Unauthorized")
        self.assertEqual(_error_code({"response": {"error": {"code": "AccessDenied"}}}), "AccessDenied")


class FlowTests(unittest.TestCase):
    def run_flow(self, fake, clock=None, market="it"):
        clock = clock or Clock()
        api = SimpleNamespace(login=FakeLogin())
        seen = []
        with patch.dict(sys.modules, fake_const()):
            coro = login_device_code(api, fake, market,
                                     lambda url, code: seen.append((url, code)),
                                     sleep=clock.sleep, monotonic=clock.monotonic)
            data = asyncio.run(coro)
        return data, api, fake.requests, clock, seen

    def test_approved_after_pending(self):
        responses = FakeSession(Response(200, pair()),
                                Response(401, {"response": {"error": {"code": "Unauthorized"}}}),
                                Response(200, success()))
        data, api, requests, clock, seen = self.run_flow(responses)
        self.assertEqual(data["site"], "https://www.amazon.it")
        self.assertEqual(seen, [("https://www.amazon.it/code", "ABCDE")])
        self.assertEqual([r[0] for r in requests],
                         ["https://api.amazon.it/auth/create/codepair",
                          "https://api.amazon.it/auth/register",
                          "https://api.amazon.it/auth/register"])
        self.assertEqual(requests[1][1]["auth_data"]["code_pair"]["private_code"], "secret")
        self.assertEqual(api.login.called, ["cookie", "capability", "customer"])
        self.assertEqual(clock.delays, [5, 5])
        self.assertEqual(requests[0][2]["x-amzn-identity-auth-domain"], "api.amazon.it")

    def test_code_expiry_fails_without_registration(self):
        code = pair()
        code["expires_in"] = 1
        f = FakeSession(Response(200, code))
        clock = Clock()
        with self.assertRaisesRegex(DeviceAuthorizationError, "expired"):
            self.run_flow(f, clock)
        self.assertEqual(len(f.requests), 1)

    def test_unexpected_401_is_rejected_not_polled(self):
        f = FakeSession(Response(200, pair()),
                        Response(401, {"response": {"error": {"code": "AccessDenied"}}}))
        with self.assertRaisesRegex(DeviceAuthorizationError, "rejected"):
            self.run_flow(f)
        self.assertEqual(len(f.requests), 2)

    def test_429_backoff_then_success(self):
        f = FakeSession(Response(200, pair()),
                        Response(429, {}, retry_after=12),
                        Response(200, success()))
        data, _, _, clock, _ = self.run_flow(f)
        self.assertEqual(clock.delays, [5, 12])
        self.assertEqual(data["device_info"]["device_serial_number"], "SERIAL")

    def test_unsupported_marketplace_rejected_before_network(self):
        with self.assertRaisesRegex(DeviceAuthorizationError, "marketplace"):
            self.run_flow(FakeSession(), market="INVALID")


if __name__ == "__main__":
    unittest.main()
