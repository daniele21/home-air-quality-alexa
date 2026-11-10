"""Offline tests: Alexa GraphQL metadata discovery does not touch measurements."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from home_air_quality_alexa.history_probe import summarize_schema_response


class HistoryProbeTests(unittest.TestCase):
    def test_extracts_only_relevant_schema_query_names(self):
        data = {
            "data": {
                "__schema": {
                    "queryType": {
                        "name": "Query",
                        "fields": [
                            {"name": "endpoints"},
                            {"name": "deviceHistory"},
                            {"name": "airQualityTrend"},
                            {"name": "other"},
                            {"name": "sensorTelemetry"},
                            {"name": "deviceHistory"},
                        ],
                    }
                }
            }
        }
        output = summarize_schema_response(data)
        self.assertEqual(output["status"], "metadata_available")
        self.assertEqual(
            output["candidates"],
            ["airQualityTrend", "deviceHistory", "sensorTelemetry"],
        )
        self.assertEqual(output["query_fields_seen"], 5)
        self.assertNotIn("endpoints", output["candidates"])

    def test_introspection_disabled_is_inconclusive(self):
        payload = {"errors": [
            {"message": "Sensitive account token: THIS_MUST_NOT_APPEAR"}
        ]}
        output = summarize_schema_response(payload)
        self.assertEqual(output["status"], "inconclusive")
        self.assertEqual(output["reason"], "schema_introspection_unavailable")
        self.assertNotIn("THIS_MUST_NOT_APPEAR", str(output))

    def test_empty_schema_not_confused_with_missing(self):
        output = summarize_schema_response({"data": {"__schema": {"queryType": {"fields": []}}}})
        self.assertEqual(output["status"], "metadata_available")
        self.assertEqual(output["candidates"], [])
        self.assertEqual(output["query_fields_seen"], 0)

    def test_disallows_raw_field_values(self):
        payload = {"data": {"__schema": {"queryType": {"fields": [
            {"name": "sensorHistory", "description": "SECRET_DATA"},
            {"name": None, "secret": "SECRET_DATA"},
        ]}}}}
        output = summarize_schema_response(payload)
        self.assertNotIn("SECRET_DATA", str(output))


if __name__ == "__main__":
    unittest.main()
