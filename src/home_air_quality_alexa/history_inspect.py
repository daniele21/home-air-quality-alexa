"""Inspect the signatures and return shapes of identified Alexa history queries.

Uses at most two *read-only schema-introspection* GraphQL calls. It does not
request measurements, history URLs, tokens, or any other user data. Only field
and type names from the schema are printed.
"""

from __future__ import annotations

import json
import re
from http import HTTPMethod
from typing import Any

TARGETS = ("airQualityHistory", "getPropertySceneHistoricalDataUrl")
TYPE_NODE = "kind name ofType { kind name ofType { kind name ofType { kind name ofType { kind name } } } }"
ROOT_QUERY = (
    "query AlexaHistoryQuerySignatures { __schema { queryType { name "
    "fields(includeDeprecated: true) { name "
    f"args {{ name type {{ {TYPE_NODE} }} }} "
    f"type {{ {TYPE_NODE} }} "
    "} } } }"
)
MAX_TYPES = 10
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


def type_string(node: Any) -> str:
    """Format nested GraphQL LIST / NON_NULL type, fail closed on bad data."""
    if not isinstance(node, dict):
        return "unknown"
    kind = node.get("kind")
    if kind == "NON_NULL":
        return type_string(node.get("ofType")) + "!"
    if kind == "LIST":
        return "[" + type_string(node.get("ofType")) + "]"
    value = node.get("name")
    return value if isinstance(value, str) and NAME_RE.fullmatch(value) else "unknown"


def named_type(node: Any) -> str | None:
    """Unwrap GraphQL LIST/NON_NULL to named object/enum/input scalar."""
    for _ in range(8):
        if not isinstance(node, dict):
            return None
        value = node.get("name")
        if isinstance(value, str) and NAME_RE.fullmatch(value):
            return value
        node = node.get("ofType")
    return None


def _fields(items: Any, limit: int = 70) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    output = []
    for value in items:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            continue
        name = value["name"]
        if not NAME_RE.fullmatch(name):
            continue
        output.append({"name": name, "type": type_string(value.get("type"))})
    return output[:limit]


def summarize_root(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Emit only the two requested root queries and safely bounded type names."""
    data = payload.get("data")
    schema = data.get("__schema") if isinstance(data, dict) else None
    query_type = schema.get("queryType") if isinstance(schema, dict) else None
    fields = query_type.get("fields") if isinstance(query_type, dict) else None
    if not isinstance(fields, list):
        return {
            "status": "inconclusive",
            "reason": "schema_metadata_unavailable",
            "fields": [],
            "named_types": [],
        }, []

    output = []
    types: list[str] = []
    for field in fields:
        if not isinstance(field, dict) or field.get("name") not in TARGETS:
            continue
        args = _fields(field.get("args"))
        result_type = type_string(field.get("type"))
        output.append({"name": field["name"], "arguments": args, "returns": result_type})
        for obj in [field.get("type"), *(arg.get("type") for arg in field.get("args", []) if isinstance(arg, dict))]:
            nm = named_type(obj)
            if nm and nm not in types and nm not in ("String", "Int", "Float", "Boolean", "ID"):
                types.append(nm)
    output.sort(key=lambda f: TARGETS.index(f["name"]))
    return {
        "status": "metadata_available",
        "fields": output,
        "named_types": types[:MAX_TYPES],
        "note": "Signatures are metadata only. Query existence does not prove history is readable.",
    }, types[:MAX_TYPES]


def build_type_query(names: list[str]) -> str:
    """Only accept names returned by introspection, never arbitrary GraphQL."""
    if len(names) > MAX_TYPES or any(not NAME_RE.fullmatch(name) for name in names):
        raise ValueError("Invalid or excessive schema type list")
    body = " ".join(
        f"t{i}: __type(name: {json.dumps(name)}) "
        "{ name kind "
        f"fields(includeDeprecated: true) {{ name type {{ {TYPE_NODE} }} }} "
        f"inputFields {{ name type {{ {TYPE_NODE} }} }} "
        "enumValues { name } }"
        for i, name in enumerate(names)
    )
    return "query AlexaHistoryResultTypes { " + body + " }"


def summarize_types(payload: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    output = []
    for i, name in enumerate(names):
        entry = data.get(f"t{i}")
        if not isinstance(entry, dict) or entry.get("name") != name:
            continue
        record: dict[str, Any] = {"name": name, "kind": entry.get("kind") if entry.get("kind") in
                                  ("OBJECT", "INPUT_OBJECT", "ENUM", "SCALAR", "INTERFACE",
                                   "UNION") else "unknown"}
        if isinstance(entry.get("fields"), list):
            record["fields"] = _fields(entry["fields"])
        if isinstance(entry.get("inputFields"), list):
            record["input_fields"] = _fields(entry["inputFields"])
        if isinstance(entry.get("enumValues"), list):
            record["enum_values"] = [
                el["name"] for el in entry["enumValues"][:60]
                if isinstance(el, dict) and isinstance(el.get("name"), str)
                and NAME_RE.fullmatch(el["name"])
            ]
        output.append(record)
    return output


async def inspect_history_queries(api: Any) -> dict[str, Any]:
    """Perform at most two metadata-only requests through the existing client."""
    from aioamazondevices.const.http import REQUEST_AGENT, URI_NEXUS_GRAPHQL
    from yarl import URL

    url = URL.joinpath(api._session_state_data.alexa_website_url, URI_NEXUS_GRAPHQL)

    async def query(operation: str, code: str) -> dict[str, Any]:
        _, resp = await api._http_wrapper.session_request(
            method=HTTPMethod.POST,
            url=url,
            input_data={"operationName": operation, "query": code},
            json_data=True,
            extended_headers={"User-Agent": REQUEST_AGENT["Amazon"]},
            fail_fast=True,
        )
        value = await api._http_wrapper.response_to_json(resp, "")
        return value if isinstance(value, dict) else {}

    try:
        payload = await query("AlexaHistoryQuerySignatures", ROOT_QUERY)
    except Exception as exc:
        return {"status": "inconclusive", "reason": "request_failed",
                "error_class": type(exc).__name__, "fields": [], "named_types": []}

    root, names = summarize_root(payload)
    if root["status"] != "metadata_available" or not names:
        root["type_definitions"] = []
        return root
    try:
        payload = await query("AlexaHistoryResultTypes", build_type_query(names))
        root["type_definitions"] = summarize_types(payload, names)
        if not root["type_definitions"]:
            root["status"] = "partial_metadata"
            root["reason"] = "type_introspection_unavailable"
    except Exception as exc:
        root["type_definitions"] = []
        root["status"] = "partial_metadata"
        root["reason"] = "type_request_failed"
        root["error_class"] = type(exc).__name__
    return root
