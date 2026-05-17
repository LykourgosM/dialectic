"""Tests for _make_strict_schema (OpenAI strict-mode schema transform)."""

from __future__ import annotations

import json

from dialectic.agents.codex import _is_null_schema, _make_strict_schema
from dialectic.protocol import CritiqueItem, ReviewerCritique, WriterReport


def _walk_objects(node, path=""):
    if isinstance(node, dict):
        if "properties" in node:
            yield path, node
        for k, v in node.items():
            yield from _walk_objects(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk_objects(item, f"{path}[{i}]")


def test_additional_properties_false_on_every_object() -> None:
    strict = _make_strict_schema(WriterReport.model_json_schema())
    for path, obj in _walk_objects(strict):
        assert obj.get("additionalProperties") is False, (
            f"{path}: additionalProperties = {obj.get('additionalProperties')}"
        )


def test_all_properties_required() -> None:
    strict = _make_strict_schema(ReviewerCritique.model_json_schema())
    for path, obj in _walk_objects(strict):
        required = set(obj.get("required", []))
        properties = set(obj.get("properties", {}).keys())
        assert required == properties, f"{path}: required {required} != properties {properties}"


def test_idempotent() -> None:
    once = _make_strict_schema(ReviewerCritique.model_json_schema())
    twice = _make_strict_schema(once)
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)


def test_any_of_str_null_flattened_to_type_array() -> None:
    strict = _make_strict_schema(CritiqueItem.model_json_schema())
    file_prop = strict["properties"]["file"]
    assert file_prop["type"] == ["string", "null"]
    assert "anyOf" not in file_prop


def test_any_of_ref_null_kept_as_anyof() -> None:
    """When the non-null branch is a $ref we can't safely merge; anyOf must survive."""
    # WriterResponseBundle has Optional[WriterReport]? Actually WriterReport is not Optional here.
    # Use a constructed case: writer_responses is Optional[WriterResponseBundle] on RevisionRound.
    from dialectic.protocol import RevisionRound

    strict = _make_strict_schema(RevisionRound.model_json_schema())
    wr_field = strict["properties"]["writer_responses"]
    # Should retain anyOf because non-null is a $ref
    assert "anyOf" in wr_field
    assert any("$ref" in b for b in wr_field["anyOf"])
    assert any(_is_null_schema(b) for b in wr_field["anyOf"])


def test_strict_drops_unsupported_format() -> None:
    """`format: 'path'` from pydantic Path fields is stripped (not in OpenAI strict whitelist)."""
    schema = {
        "type": "object",
        "properties": {
            "p": {"type": "string", "format": "path"},
            "ts": {"type": "string", "format": "date-time"},
        },
    }
    strict = _make_strict_schema(schema)
    assert "format" not in strict["properties"]["p"]
    # date-time IS in the whitelist
    assert strict["properties"]["ts"]["format"] == "date-time"


def test_strict_object_without_properties_gets_well_formed() -> None:
    """A bare `type: object` (e.g. from dict[str, Any]) gets properties:{}, required:[]."""
    schema = {"type": "object"}
    strict = _make_strict_schema(schema)
    assert strict["additionalProperties"] is False
    assert strict["properties"] == {}
    assert strict["required"] == []


def test_strict_three_way_union_left_alone() -> None:
    """anyOf with 3+ branches isn't flattened (would require multi-type or extra logic)."""
    schema = {
        "type": "object",
        "properties": {
            "x": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
        },
    }
    strict = _make_strict_schema(schema)
    assert "anyOf" in strict["properties"]["x"]
    assert len(strict["properties"]["x"]["anyOf"]) == 3


def test_is_null_schema_tolerates_title() -> None:
    """Future pydantic versions may include a `title` on null schemas; still recognized as null."""
    assert _is_null_schema({"type": "null"})
    assert _is_null_schema({"type": "null", "title": "Foo"})
    assert _is_null_schema({"type": "null", "title": "Foo", "description": "x"})
    assert not _is_null_schema({"type": "null", "extra": 1})
    assert not _is_null_schema({"type": "string"})
