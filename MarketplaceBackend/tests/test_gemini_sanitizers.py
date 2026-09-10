"""
Tests for the pure sanitisation helpers in routers/gemini.py.

These guard the boundary between provider-submitted tool definitions and the
Gemini function-declaration API: bad names, unsafe types and junk fields must
never reach the model.
"""

from routers.gemini import TOOL_NAME_RE, _sanitize_parameters, _sanitize_tools


def test_parameters_default_to_empty_object():
    assert _sanitize_parameters(None) == {"type": "object", "properties": {}}
    assert _sanitize_parameters("nonsense") == {"type": "object", "properties": {}}


def test_unsafe_property_types_fall_back_to_string():
    out = _sanitize_parameters({"properties": {"q": {"type": "function"}, "n": {"type": "integer"}}})
    assert out["properties"]["q"] == {"type": "string"}
    assert out["properties"]["n"] == {"type": "integer"}


def test_required_only_keeps_known_properties():
    out = _sanitize_parameters({"properties": {"a": {"type": "string"}}, "required": ["a", "ghost", 42]})
    assert out["required"] == ["a"]


def test_enum_kept_only_when_scalar():
    ok = _sanitize_parameters({"properties": {"u": {"type": "string", "enum": ["c", "f"]}}})
    bad = _sanitize_parameters({"properties": {"u": {"type": "string", "enum": [{"x": 1}]}}})
    assert ok["properties"]["u"]["enum"] == ["c", "f"]
    assert "enum" not in bad["properties"]["u"]


def test_tools_with_invalid_names_are_dropped():
    tools = [
        {"name": "good_tool", "description": "ok"},
        {"name": "has space", "description": "bad"},
        {"name": "1starts_with_digit"},
        {"name": "x" * 65},
        {"name": None},
        "not a dict",
    ]
    out = _sanitize_tools(tools)
    assert [t["name"] for t in out] == ["good_tool"]


def test_missing_description_gets_placeholder():
    [t] = _sanitize_tools([{"name": "t1", "description": "   "}])
    assert t["description"] == "Tool t1"
    assert t["parameters"] == {"type": "object", "properties": {}}


def test_tool_name_regex_matches_gemini_constraints():
    assert TOOL_NAME_RE.match("weather_lookup_v2")
    assert not TOOL_NAME_RE.match("weather-lookup")
    assert not TOOL_NAME_RE.match("")
