from codebase_rag.utils.llm_utils import safe_parse_json


def test_safe_parse_json_with_dict() -> None:
    payload = {"key": "value"}
    assert safe_parse_json(payload) == payload


def test_safe_parse_json_with_markdown() -> None:
    text = '```json\n{"key": "value"}\n```'
    assert safe_parse_json(text)["key"] == "value"


def test_safe_parse_json_with_verbose_text() -> None:
    text = 'Plan:\n{"steps": ["a", "b"]}\nDone'
    assert safe_parse_json(text)["steps"] == ["a", "b"]


def test_safe_parse_json_with_trailing_commas() -> None:
    text = '{"items": [1, 2,], "last": 3,}'
    assert safe_parse_json(text)["items"] == [1, 2]


def test_safe_parse_json_with_invalid_input() -> None:
    defaults = {"error": True}
    assert safe_parse_json("invalid", defaults=defaults) == defaults
