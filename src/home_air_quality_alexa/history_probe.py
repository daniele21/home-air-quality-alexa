"""Read-only probe for discoverability of historical Alexa sensor APIs.

This tests only GraphQL *schema metadata* on Amazon's existing Nexus endpoint.
It does NOT fetch private sensor history, brute-force URLs, or mutate settings.
Success means a candidate field exists; it does NOT confirm data access.
"""

from __future__ import annotations

from http import HTTPMethod
from typing import Any

# Only root query-field names are inspected. Values/arguments are never read.
SCHEMA_QUERY = """
query AlexaHistorySchemaProbe {
  __schema {
    queryType {
      name
      fields {
        name
      }
    }
  }
}
"""

SEARCH_TERMS = (
    "history",
    "historical",
    "timeseries",
    "time_series",
    "trend",
    "sensor",
    "airquality",
    "air_quality",
    "measurement",
    "telemetry",
    "sample",
)


def summarize_schema_response(response: dict[str, Any]) -> dict[str, Any]:
    """Keep only public-ish schema names. Never log errors or their raw payloads."""
    data = response.get("data")
    schema = data.get("__schema") if isinstance(data, dict) else None
    qtype = schema.get("queryType") if isinstance(schema, dict) else None
    fields = qtype.get("fields") if isinstance(qtype, dict) else None

    if not isinstance(fields, list):
        return {
            "status": "inconclusive",
            "reason": "schema_introspection_unavailable",
            "candidates": [],
            "note": "No root schema fields were available; this does not rule out a private history API.",
        }

    names = sorted({
        item["name"]
        for item in fields
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    })
    matches = [name for name in names if any(term in name.lower() for term in SEARCH_TERMS)]
    return {
        "status": "metadata_available",
        "query_fields_seen": len(names),
        "candidates": matches,
        "note": (
            "Candidate names are not evidence that history is accessible; "
            "no historical measurements were requested or downloaded."
        ),
    }


async def probe_history_schema(api: Any) -> dict[str, Any]:
    """One read-only introspection POST, same authenticated host as device discovery."""
    from aioamazondevices.const.http import REQUEST_AGENT, URI_NEXUS_GRAPHQL
    from yarl import URL

    # Bound to the country-specific Alexa host determined by stored session.
    # Use the same headers/transport as aioamazondevices' existing device GraphQL query.
    payload = {"operationName": "AlexaHistorySchemaProbe", "query": SCHEMA_QUERY}
    try:
        _, resp = await api._http_wrapper.session_request(
            method=HTTPMethod.POST,
            url=URL.joinpath(api._session_state_data.alexa_website_url, URI_NEXUS_GRAPHQL),
            input_data=payload,
            json_data=True,
            extended_headers={"User-Agent": REQUEST_AGENT["Amazon"]},
            fail_fast=True,
        )
        response = await api._http_wrapper.response_to_json(resp, "")
        return summarize_schema_response(response)
    except Exception as exc:
        # Do not print request URLs or exception messages: library exceptions
        # may contain account details. Most failures are not evidence of no history.
        return {
            "status": "inconclusive",
            "reason": "request_failed",
            "error_class": type(exc).__name__,
            "candidates": [],
            "note": "Metadata request failed; no conclusion about historical data availability.",
        }
