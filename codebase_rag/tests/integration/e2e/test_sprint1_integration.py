from __future__ import annotations

from pathlib import Path

from codebase_rag.agents.output_parser import JSONOutputParser
from codebase_rag.services.reasoning_capturer import ReasoningCapturer
from codebase_rag.utils.llm_utils import safe_parse_json


class DummyRegistry:
    def list_projects(self):
        return []

    def delete_project(self, project_name: str):
        return {"project": project_name}

    def wipe_database(self, confirm: bool):
        return "ok"


def test_sprint1_components_work_together(tmp_path: Path) -> None:
    __import__("codebase_rag.mcp.tools")
    parser = JSONOutputParser()
    parsed = parser.parse('{"summary": "ok"}')
    assert parsed["summary"] == "ok"

    json_payload = safe_parse_json('```json\n{"steps": ["a"]}\n```')
    assert json_payload["steps"] == ["a"]

    capturer = ReasoningCapturer(tmp_path)
    capture = capturer.extract("<think>line</think>response")
    assert capture.thinking == "line"
    assert capture.response == "response"

    # tools = ToolRegistry.build(DummyRegistry())
    # assert tools
    # assert "list_projects" in tools
