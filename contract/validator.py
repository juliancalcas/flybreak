"""Validates FlyBreak tick payloads (engine -> frontend) against schema_v1.json.

Both engine and frontend import this contract instead of re-declaring the
shape of a tick, so the two layers cannot silently drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_PATH = Path(__file__).parent / "schema_v1.json"
_schema: dict[str, Any] | None = None


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
