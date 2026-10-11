"""Build plausible sample values from JSON schemas and parameter types."""

from __future__ import annotations

from typing import Any

SAMPLE_UUID = "00000000-0000-4000-8000-000000000001"

_FORMATS = {
    "email": "user@example.com",
    "uuid": SAMPLE_UUID,
    "date": "2026-01-15",
    "date-time": "2026-01-15T10:00:00Z",
    "time": "10:00:00",
    "uri": "https://example.com",
    "url": "https://example.com",
    "ipv4": "127.0.0.1",
    "ipv6": "::1",
    "password": "Str0ng-password!",
}

_PARAM_SAMPLES = {
    "integer": 1,
    "int": 1,
    "number": 1.0,
    "float": 1.0,
    "boolean": True,
    "uuid": SAMPLE_UUID,
    "slug": "test-slug",
    "path": "test",
    "string": "test",
    "str": "test",
}


def sample_param(type_name: str, schema: dict[str, Any] | None = None, components=None, name: str = "") -> Any:
    if schema:
        return sample(schema, components or {})
    if type_name == "string" and _looks_like_id(name):
        return 1  # untyped <pk>, <id>, <user_id>: an identifier is more useful than "test"
    return _PARAM_SAMPLES.get(type_name, "test")


def _looks_like_id(name: str) -> bool:
    name = name.lower()
    return name in ("pk", "id") or name.endswith(("_id", "_pk"))


def resolve_ref(ref: str, components: dict[str, Any]) -> dict[str, Any]:
    # "#/components/schemas/Item" -> components["schemas"]["Item"]
    node: Any = components
    for part in ref.split("/")[2:]:
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def sample(schema: dict[str, Any], components: dict[str, Any] | None = None, _depth: int = 0) -> Any:
    """Return a value that satisfies the main constraints of `schema`."""
    components = components or {}
    if _depth > 15 or not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        return sample(resolve_ref(schema["$ref"], components), components, _depth + 1)
    for key in ("example", "default", "const"):
        if key in schema:
            return schema[key]
    if schema.get("examples"):
        examples = schema["examples"]
        return examples[0] if isinstance(examples, list) else next(iter(examples.values()), None)
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [s for s in schema[key] if s.get("type") != "null"] or schema[key]
            return sample(options[0], components, _depth + 1)
    if "allOf" in schema:
        merged: dict[str, Any] = {}
        for part in schema["allOf"]:
            value = sample(part, components, _depth + 1)
            if isinstance(value, dict):
                merged.update(value)
            elif value is not None:
                return value
        return merged

    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((t for t in kind if t != "null"), None)
    if kind is None:
        kind = "object" if "properties" in schema else "string"

    if kind == "object":
        props = schema.get("properties", {})
        required = schema.get("required")
        # Without a "required" list every property is filled, which suits DRF serializers.
        names = required if required is not None else list(props)
        return {
            name: sample(props.get(name, {}), components, _depth + 1)
            for name in names
            if not props.get(name, {}).get("readOnly")
        }
    if kind == "array":
        item = sample(schema.get("items", {}), components, _depth + 1)
        count = max(schema.get("minItems", 1), 1)
        return [item] * count if item is not None else []
    if kind == "integer":
        return _bounded(schema, 1)
    if kind == "number":
        return float(_bounded(schema, 1))
    if kind == "boolean":
        return True
    if kind == "null":
        return None
    return _sample_string(schema)


def _bounded(schema: dict[str, Any], default: int) -> int:
    value = default
    if "minimum" in schema:
        value = max(value, schema["minimum"])
    if "exclusiveMinimum" in schema and isinstance(schema["exclusiveMinimum"], (int, float)):
        value = max(value, schema["exclusiveMinimum"] + 1)
    if "maximum" in schema:
        value = min(value, schema["maximum"])
    return value


def _sample_string(schema: dict[str, Any]) -> str:
    fmt = schema.get("format")
    if fmt in _FORMATS:
        return _FORMATS[fmt]
    value = "string"
    min_len = schema.get("minLength", 0)
    max_len = schema.get("maxLength")
    if len(value) < min_len:
        value = value + "x" * (min_len - len(value))
    if max_len is not None:
        value = value[:max_len]
    return value


def has_required_fields(schema: dict[str, Any], components: dict[str, Any] | None = None) -> bool:
    """True when an empty JSON object would not satisfy `schema`."""
    if "$ref" in schema:
        schema = resolve_ref(schema["$ref"], components or {})
    if schema.get("required"):
        return True
    return any(has_required_fields(part, components) for part in schema.get("allOf", []))
