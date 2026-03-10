from codebase_rag.agents.output_parser import (
    JSONOutputParser,
    XMLOutputParser,
    decode_escaped_text,
    extract_code_block,
)


def test_json_output_parser_with_markdown() -> None:
    parser = JSONOutputParser()
    payload = '```json\n{"summary": "ok"}\n```'
    assert parser.parse(payload)["summary"] == "ok"


def test_json_output_parser_extracts_json_from_wrapped_text() -> None:
    parser = JSONOutputParser()
    payload = 'Plan:\n```json\n{"summary":"ok","steps":["a"]}\n```\nUse it.'
    assert parser.parse(payload)["steps"] == ["a"]


def test_decode_escaped_text_restores_newlines() -> None:
    payload = "line1\\nline2\\nline3"
    assert decode_escaped_text(payload) == "line1\nline2\nline3"


def test_decode_escaped_text_preserves_windows_paths() -> None:
    payload = r"C:\new\temp\foo.py"
    assert decode_escaped_text(payload) == payload


def test_decode_escaped_text_preserves_json_objects() -> None:
    payload = r'{"path":"C:\\new\\temp\\foo.py"}'
    assert decode_escaped_text(payload) == payload


def test_extract_code_block_prefers_requested_language() -> None:
    payload = '```json\n{"summary":"ok"}\n```\n```python\nprint("ok")\n```'
    language, content = extract_code_block(payload, preferred_languages={"python"})
    assert language == "python"
    assert content == 'print("ok")'


def test_xml_output_parser_basic() -> None:
    parser = XMLOutputParser()
    payload = "<plan><summary>ok</summary><step>a</step><step>b</step></plan>"
    result = parser.parse(payload)
    assert result["summary"] == "ok"
    assert result["step"] == ["a", "b"]
