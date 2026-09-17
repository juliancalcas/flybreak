"""Validates FlyBreak payloads (engine -> frontend) against their schemas.

Both engine and frontend import this contract instead of re-declaring the
shape of a tick (schema_v1.json) or the one-time synapse-sample graph
message (schema_graph_v1.json), so the two layers cannot silently drift
apart.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_PATH = Path(__file__).parent / "schema_v1.json"
_schema: dict[str, Any] | None = None

_GRAPH_SCHEMA_PATH = Path(__file__).parent / "schema_graph_v1.json"
_graph_schema: dict[str, Any] | None = None


def _load_schema() -> dict[str, Any]:
    global _schema
    if _schema is None:
        _schema = json.loads(_SCHEMA_PATH.read_text())
    return _schema


def validate_tick(tick: dict[str, Any]) -> None:
    """Raises jsonschema.ValidationError if `tick` does not match schema_v1."""
    jsonschema.validate(instance=tick, schema=_load_schema())


def is_valid_tick(tick: dict[str, Any]) -> bool:
    try:
        validate_tick(tick)
        return True
    except jsonschema.ValidationError:
        return False


def _load_graph_schema() -> dict[str, Any]:
    global _graph_schema
    if _graph_schema is None:
        _graph_schema = json.loads(_GRAPH_SCHEMA_PATH.read_text())
    return _graph_schema


def validate_graph(graph: dict[str, Any]) -> None:
    """Raises jsonschema.ValidationError if `graph` does not match
    schema_graph_v1 -- the one-time "synapse_graph" message a client
    receives right after connecting (see engine/server.py's
    _handle_client), distinct from a per-tick payload."""
    jsonschema.validate(instance=graph, schema=_load_graph_schema())


def is_valid_graph(graph: dict[str, Any]) -> bool:
    try:
        validate_graph(graph)
        return True
    except jsonschema.ValidationError:
        return False
