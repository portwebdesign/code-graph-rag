from codebase_rag.agents.output_parser import JSONOutputParser, XMLOutputParser


def test_json_output_parser_with_markdown() -> None:
    parser = JSONOutputParser()
    payload = '```json\n{"summary": "ok"}\n```'
    assert parser.parse(payload)["summary"] == "ok"


def test_xml_output_parser_basic() -> None:
    parser = XMLOutputParser()
    payload = "<plan><summary>ok</summary><step>a</step><step>b</step></plan>"
    result = parser.parse(payload)
    assert result["summary"] == "ok"
    assert result["step"] == ["a", "b"]
