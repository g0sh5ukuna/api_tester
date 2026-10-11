from routeprobe.schema import SAMPLE_UUID, has_required_fields, sample, sample_param


def test_object_with_ref_and_constraints():
    components = {"schemas": {"Item": {
        "type": "object",
        "required": ["name", "price", "owner"],
        "properties": {
            "name": {"type": "string", "minLength": 8},
            "price": {"type": "number", "exclusiveMinimum": 0},
            "owner": {"type": "string", "format": "email"},
            "note": {"type": "string"},
        },
    }}}
    value = sample({"$ref": "#/components/schemas/Item"}, components)
    assert value == {"name": "stringxx", "price": 1.0, "owner": "user@example.com"}


def test_nullable_enum_and_array():
    assert sample({"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 5}]}) == 5
    assert sample({"enum": ["b", "a"]}) == "b"
    assert sample({"type": "array", "items": {"type": "string", "format": "uuid"}}) == [SAMPLE_UUID]


def test_without_required_list_fills_every_writable_property():
    schema = {"type": "object", "properties": {"id": {"type": "integer", "readOnly": True}, "name": {"type": "string"}}}
    assert sample(schema) == {"name": "string"}


def test_param_samples():
    assert sample_param("integer") == 1
    assert sample_param("string", name="pk") == 1
    assert sample_param("string", name="user_id") == 1
    assert sample_param("string", name="slug") == "test"
    assert sample_param("uuid") == SAMPLE_UUID


def test_has_required_fields():
    assert has_required_fields({"required": ["a"]})
    assert not has_required_fields({"type": "object", "properties": {}})
    assert has_required_fields({"$ref": "#/components/schemas/A"}, {"schemas": {"A": {"required": ["x"]}}})
