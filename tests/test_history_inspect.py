"""Offline tests for introspection of two known Alexa GraphQL history fields."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from home_air_quality_alexa.history_inspect import (
    ROOT_QUERY, build_type_query, named_type, summarize_root,
    summarize_types, type_string,
)

def node(name):
    return {"kind": "SCALAR", "name": name, "ofType": None}

def nn(typ):
    return {"kind": "NON_NULL", "name": None, "ofType": typ}

def list_of(typ):
    return {"kind": "LIST", "name": None, "ofType": typ}


class HistoryInspectTests(unittest.TestCase):
    def test_graphql_document_is_root_schema_only(self):
        self.assertIn("__schema", ROOT_QUERY)
        self.assertIn("fields(includeDeprecated: true)", ROOT_QUERY)
        self.assertNotIn("airQualityHistory(", ROOT_QUERY)

    def test_schema_type_parsing(self):
        self.assertEqual(type_string(nn(list_of(nn(node("ExampleInput"))))), "[ExampleInput!]!")
        self.assertEqual(named_type(nn(list_of(nn(node("ExampleInput"))))), "ExampleInput")
        self.assertEqual(named_type(None), None)
        self.assertEqual(type_string({}), "unknown")

    def test_extracts_only_two_expected_query_signatures(self):
        data = {"data": {"__schema": {"queryType": {"fields": [
            {"name": "airQualityHistory",
             "args": [
                 {"name": "endpointId", "type": nn(node("String"))},
                 {"name": "query", "type": nn({"kind": "INPUT_OBJECT", "name": "AirQueryInput"})},
             ],
             "type": nn({"kind": "OBJECT", "name": "AirHistoryResponse"})},
            {"name": "getPropertySceneHistoricalDataUrl",
             "args": [{"name": "id", "type": node("ID")}], "type": node("String")},
            {"name": "getCurrentDeviceState",
             "args": [{"name": "privateToken", "type": node("String")}],
             "type": {"kind": "OBJECT", "name": "PrivateType"}},
        ]}}}}
        result, types = summarize_root(data)
        self.assertEqual(result["status"], "metadata_available")
        self.assertEqual(len(result["fields"]), 2)
        self.assertEqual(result["fields"][0]["name"], "airQualityHistory")
        self.assertEqual(result["fields"][0]["arguments"], [
            {"name": "endpointId", "type": "String!"},
            {"name": "query", "type": "AirQueryInput!"},
        ])
        self.assertEqual(result["fields"][0]["returns"], "AirHistoryResponse!")
        self.assertEqual(types, ["AirHistoryResponse", "AirQueryInput"])
        self.assertNotIn("privateToken", str(result))

    def test_introspection_disabled_is_inconclusive_and_no_errors_echoed(self):
        result, types = summarize_root({"errors": [
            {"message": "cookie=DO_NOT_LOG_ME"}
        ]})
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(types, [])
        self.assertNotIn("DO_NOT_LOG_ME", str(result))

    def test_constructs_limited_whitelisted_introspection_for_named_types(self):
        query = build_type_query(["AirHistoryResponse", "AirQueryInput"])
        self.assertIn('t0: __type(name: "AirHistoryResponse")', query)
        self.assertIn("inputFields", query)
        self.assertIn("enumValues", query)
        self.assertNotIn("airQualityHistory(", query)
        with self.assertRaises(ValueError):
            build_type_query(["foo } mutation {"])
        with self.assertRaises(ValueError):
            build_type_query(["T" + str(x) for x in range(11)])

    def test_summarizes_types_ignoring_descriptions_values(self):
        payload = {"data": {
            "t0": {"name": "AirHistoryResponse", "kind": "OBJECT",
                   "fields": [{"name": "samples", "type": list_of({"kind":"OBJECT", "name":"AirSample"}),
                               "description": "SECRET_TOKEN"}]},
            "t1": {"name": "AirQueryInput", "kind": "INPUT_OBJECT",
                   "inputFields": [{"name": "from", "type": nn(node("String")),
                                    "defaultValue": "SECRET_TOKEN"}]},
        }}
        result = summarize_types(payload, ["AirHistoryResponse", "AirQueryInput"])
        self.assertEqual(result[0]["fields"], [{"name": "samples", "type": "[AirSample]"}])
        self.assertEqual(result[1]["input_fields"], [{"name": "from", "type": "String!"}])
        self.assertNotIn("SECRET_TOKEN", str(result))

if __name__ == "__main__":
    unittest.main()
