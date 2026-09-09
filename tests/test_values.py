"""Tests for Godot variant literal parsing and serialization."""

from __future__ import annotations

import math

import pytest

from godot_agent.gdformat.values import (
    GDCall,
    GDNodePath,
    GDParseError,
    GDStringName,
    dumps,
    parse_value,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('"hello"', "hello"),
        ('"with \\"quotes\\""', 'with "quotes"'),
        ('"line\\nbreak"', "line\nbreak"),
        ("12", 12),
        ("-7", -7),
        ("1.5", 1.5),
        ("-0.25", -0.25),
        ("1e-05", 1e-05),
        ("true", True),
        ("false", False),
        ("null", None),
        ('&"my_name"', GDStringName("my_name")),
        ('^"Player/Sprite"', GDNodePath("Player/Sprite")),
        ("[]", []),
        ("[1, 2, 3]", [1, 2, 3]),
        ('["a", "b"]', ["a", "b"]),
        ("{}", {}),
        ('{"key": 1}', {"key": 1}),
    ],
)
def test_parses_primitives(text: str, expected: object) -> None:
    assert parse_value(text) == expected


def test_parses_infinity_and_nan() -> None:
    assert parse_value("inf") == math.inf
    assert parse_value("-inf") == -math.inf
    assert math.isnan(parse_value("nan"))


def test_parses_constructor_calls() -> None:
    assert parse_value("Vector2(0, 1)") == GDCall("Vector2", (0, 1))
    assert parse_value("Color(1, 1, 1, 1)") == GDCall("Color", (1, 1, 1, 1))
    assert parse_value('SubResource("Rect_a1")') == GDCall("SubResource", ("Rect_a1",))
    assert parse_value('PackedStringArray("a", "b")') == GDCall(
        "PackedStringArray", ("a", "b")
    )


def test_parses_empty_constructor_call() -> None:
    assert parse_value("PackedVector2Array()") == GDCall("PackedVector2Array")


def test_parses_object_with_keyed_arguments() -> None:
    """``Object(...)`` mixes a positional type name with keyed properties.

    This is the shape every entry in ``project.godot``'s input map takes, so
    getting it wrong would make the input map unreadable.
    """
    value = parse_value(
        'Object(InputEventKey, "resource_local_to_scene": false, "keycode": 32, "pressed": true)'
    )
    assert isinstance(value, GDCall)
    assert value.name == "Object"
    assert value.args == (GDCall("InputEventKey"),)
    assert dict(value.kwargs) == {
        "resource_local_to_scene": False,
        "keycode": 32,
        "pressed": True,
    }


def test_parses_nested_structures() -> None:
    value = parse_value('{"events": [Object(InputEventKey, "keycode": 32)], "deadzone": 0.5}')
    assert value["deadzone"] == 0.5
    assert isinstance(value["events"][0], GDCall)


def test_parses_multiline_value() -> None:
    value = parse_value('{\n"deadzone": 0.5,\n"events": []\n}')
    assert value == {"deadzone": 0.5, "events": []}


def test_tolerates_trailing_comma() -> None:
    assert parse_value("[1, 2,]") == [1, 2]
    assert parse_value('{"a": 1,}') == {"a": 1}


@pytest.mark.parametrize("text", ["", "@", '"unterminated', "[1, 2", "Vector2(1"])
def test_rejects_malformed_input(text: str) -> None:
    with pytest.raises(GDParseError):
        parse_value(text)


@pytest.mark.parametrize(
    "text",
    [
        '"hello"',
        "12",
        "-7",
        "1.5",
        "true",
        "null",
        '&"name"',
        '^"a/b"',
        "[1, 2, 3]",
        '{"key": 1}',
        "Vector2(0, 1)",
        'SubResource("Rect_a1")',
        'Object(InputEventKey, "keycode": 32)',
    ],
)
def test_round_trips(text: str) -> None:
    """Parsing then dumping must reproduce the canonical text exactly."""
    assert dumps(parse_value(text)) == text


def test_dump_preserves_float_marker() -> None:
    """A whole float must not collapse to an int, or Godot changes the type."""
    assert dumps(1.0) == "1.0"
    assert dumps(1) == "1"


def test_dump_escapes_strings() -> None:
    assert dumps('say "hi"\n') == '"say \\"hi\\"\\n"'


def test_dump_rejects_unknown_type() -> None:
    with pytest.raises(TypeError):
        dumps(object())
