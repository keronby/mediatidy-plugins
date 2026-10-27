"""Shared bounded JSON data schema used by Python author tools and tests."""
from __future__ import annotations
import json
import math
import re
from typing import Any


def validate_schema_definition(schema: dict | None, depth: int = 0) -> None:
    if schema is None:
        return
    assert isinstance(schema, dict) and depth <= 8
    assert not set(schema) - {"type", "properties", "required", "items", "enum", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "additionalProperties"}, "Unknown schema keyword"
    assert schema.get("type", "") in ("", "object", "array", "string", "number", "integer", "boolean", "null")
    properties, required = schema.get("properties", {}), schema.get("required", [])
    assert isinstance(properties, dict) and len(properties) <= 100
    assert isinstance(required, list) and len(required) <= 100 and all(isinstance(name, str) for name in required)
    assert len(schema.get("enum", [])) <= 100
    assert type(schema.get("additionalProperties", False)) is bool
    for name, child in properties.items():
        assert re.fullmatch(r"[a-z][A-Za-z0-9_]{1,63}", name) and name not in ("constructor", "prototype") and isinstance(child, dict)
        validate_schema_definition(child, depth + 1)
    validate_schema_definition(schema.get("items"), depth + 1)
    assert set(required) <= set(properties)
    for low, high in (("minLength", "maxLength"), ("minItems", "maxItems")):
        for key in (low, high):
            if key in schema:
                assert type(schema[key]) is int and schema[key] >= 0
        assert low not in schema or high not in schema or schema[low] <= schema[high]
    for key in ("minimum", "maximum"):
        if key in schema:
            assert type(schema[key]) in (int, float) and math.isfinite(schema[key])
    assert "minimum" not in schema or "maximum" not in schema or schema["minimum"] <= schema["maximum"]
    json.dumps(schema, allow_nan=False)


def validate(schema: dict | None, value: Any, depth: int = 0) -> None:
    if not schema:
        return
    if depth > 16:
        raise ValueError("Schema value exceeds depth")
    kind = schema.get("type")
    types: dict[str, type | tuple[type, ...]] = {"object": dict, "array": list, "string": str, "number": (int, float), "integer": int, "boolean": bool, "null": type(None)}
    if kind and (not isinstance(value, types[kind]) or kind in ("number", "integer") and isinstance(value, bool)):
        raise ValueError("Schema type mismatch")
    if schema.get("enum") and not any(json.dumps(value, sort_keys=True, allow_nan=False) == json.dumps(item, sort_keys=True, allow_nan=False) for item in schema["enum"]):
        raise ValueError("Schema enum mismatch")
    if kind == "object":
        if len(value) > 1000:
            raise ValueError("Schema object bounds")
        if not set(schema.get("required", [])) <= value.keys():
            raise ValueError("Schema required field missing")
        for key, item in value.items():
            if key not in schema.get("properties", {}) and not schema.get("additionalProperties", False):
                raise ValueError("Schema unknown property")
            validate(schema.get("properties", {}).get(key), item, depth + 1)
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10000):
            raise ValueError("Schema array bounds")
        for item in value:
            validate(schema.get("items"), item, depth + 1)
    if kind == "string" and (len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value))):
        raise ValueError("Schema string bounds")
    if kind in ("number", "integer") and (not math.isfinite(value) or value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf"))):
        raise ValueError("Schema number bounds")
