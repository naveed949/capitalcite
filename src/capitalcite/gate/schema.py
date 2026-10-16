"""JSON Schema subset used by the tool gate. Unknown keywords fail closed at load."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_OBJECT_KEYS = frozenset({"type", "properties", "required", "additionalProperties"})
_PROP_KEYS = frozenset({"type", "minLength", "minimum", "maximum"})
_TYPES = frozenset({"object", "string", "integer", "number", "boolean", "null"})


def unsupported_schema(schema: Mapping[str, object]) -> str | None:
    """Why this schema is not in the supported subset. None means it may be used."""
    extra = set(schema) - _OBJECT_KEYS - _PROP_KEYS
    if extra:
        return "unsupported schema keys: " + ", ".join(sorted(extra))
    type_error = _check_type_field(schema.get("type"))
    if type_error is not None:
        return type_error
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            return "properties must be an object"
        for name, spec in properties.items():
            if not isinstance(name, str) or not name:
                return "property names must be non-empty strings"
            if not isinstance(spec, dict):
                return f"property {name} must be an object"
            extra_prop = set(spec) - _PROP_KEYS
            if extra_prop:
                return f"unsupported schema keys on {name}: " + ", ".join(sorted(extra_prop))
            type_error = _check_type_field(spec.get("type"))
            if type_error is not None:
                return f"{name}: {type_error}"
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list) or not all(isinstance(item, str) for item in required)
    ):
        return "required must be a list of strings"
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        return "additionalProperties must be a boolean"
    return None


def validate_args(schema: Mapping[str, object], args: Mapping[str, object]) -> str | None:
    """Return a stable error, or None when args match the schema."""
    return _validate(schema, args)


def _validate(schema: Mapping[str, object], value: object) -> str | None:
    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(str(item), value) for item in allowed):
            return "invalid args"
    if value is None:
        return None
    min_length = _as_int(schema.get("minLength"))
    if min_length is not None and isinstance(value, str) and len(value) < min_length:
        return "invalid args"
    minimum = _as_number(schema.get("minimum"))
    if (
        minimum is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value < minimum
    ):
        return "invalid args"
    maximum = _as_number(schema.get("maximum"))
    if (
        maximum is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > maximum
    ):
        return "invalid args"
    if not isinstance(value, Mapping):
        return None
    properties = schema.get("properties")
    props: dict[str, Any] = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    need = required if isinstance(required, list) else []
    for key in need:
        if key not in value:
            return "invalid args"
    additional = schema.get("additionalProperties", True)
    for key, item in value.items():
        if key in props:
            nested = props[key]
            if isinstance(nested, dict):
                error = _validate(nested, item)
                if error is not None:
                    return error
        elif additional is False:
            return "invalid args"
    return None


def _check_type_field(value: object) -> str | None:
    if value is None:
        return None
    items = value if isinstance(value, list) else [value]
    if not items or not all(isinstance(item, str) and item in _TYPES for item in items):
        return "type must be a supported JSON Schema type"
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _type_matches(expected: str, value: object) -> bool:
    if expected == "null":
        return value is None
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    return False
