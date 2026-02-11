import pytest

from codebase_rag.formatters import MermaidProjectDiagramGenerator, ReportFormatter


@pytest.fixture
def sample_json_data():
    return {
        "summary": {
            "nodes_found": 26,
            "relationships_found": 15,
            "main_entities": [
                "manimproject.geometry-manim-platform.backend.app.services.manim_runner.ManimRunner",
                "manimproject.geometry-manim-platform.backend.app.api.generate.GenerateRequest",
                "manimproject.geometry-manim-platform.backend.app.services.skill_adapter.SkillAdapter",
            ],
        },
        "results": [
            {
                "tool_name": "query_codebase_knowledge_graph",
                "payload": {
                    "query_used": "MATCH (n:Function) WHERE n.qualified_name CONTAINS 'generate' RETURN n.qualified_name, n.name LIMIT 10",
                    "results": [
                        {
                            "qualified_name": "manimproject.api.generate.create_animation",
                            "name": "create_animation",
                        },
                        {
                            "qualified_name": "manimproject.services.generate_prompt",
                            "name": "generate_prompt",
                        },
                    ],
                    "summary": "Found 2 functions with 'generate' in their qualified names",
                },
            },
            {
                "tool_name": "show_schema_info",
                "payload": {
                    "nodes": {
                        "Function": {
                            "label": "Function",
                            "properties": "{qualified_name: string, name: string}",
                        },
                        "Class": {
                            "label": "Class",
                            "properties": "{qualified_name: string, name: string}",
                        },
                    },
                    "relationships": {
                        "CALLS": {"from": "Function", "to": "Function"},
                        "CONTAINS": {"from": "Module", "to": "Function"},
                    },
                },
            },
        ],
    }


def test_format_json_to_html(sample_json_data):
    html = ReportFormatter.format_json_to_html(sample_json_data)

    assert "<!DOCTYPE html>" in html
    assert "Graph-Code Analysis Report" in html
    assert "nodes_found" in html
    assert "query_codebase_knowledge_graph" in html
    assert "manimproject" in html


def test_format_invalid_json():
    html = ReportFormatter.format_json_to_html("invalid json {{{")

    assert "Error Generating Report" in html
    assert "Invalid JSON" in html


def test_save_html_report(sample_json_data, tmp_path):
    output_path = tmp_path / "test_report.html"

    result = ReportFormatter.save_html_report(
        sample_json_data,
        output_path,
        title="Test Report",
    )

    assert result.exists()
    assert result.suffix == ".html"

    content = result.read_text(encoding="utf-8")
    assert "Test Report" in content
    assert "nodes_found" in content


def test_generate_project_structure_diagram():
    graph_data = {
        "results": [
            {
                "labels": ["Project"],
                "name": "MyProject",
                "qualified_name": "my_project",
            },
            {
                "labels": ["Module"],
                "name": "utils",
                "qualified_name": "my_project.utils",
            },
            {
                "labels": ["Function"],
                "name": "helper",
                "qualified_name": "my_project.utils.helper",
            },
        ]
    }

    mermaid = MermaidProjectDiagramGenerator.generate_project_structure_diagram(
        graph_data
    )

    assert "graph TD" in mermaid
    assert "MyProject" in mermaid
    assert "utils" in mermaid


def test_generate_llm_integration_diagram():
    mermaid = MermaidProjectDiagramGenerator.generate_llm_integration_diagram()

    assert "graph TD" in mermaid
    assert "CypherGenerator" in mermaid
    assert "LLM" in mermaid
    assert "Retry with Feedback" in mermaid


def test_mermaid_clean_id():
    assert (
        MermaidProjectDiagramGenerator._clean_id("my.module.name") == "my_module_name"
    )
    assert (
        MermaidProjectDiagramGenerator._clean_id("path/to/file.py") == "path_to_file_py"
    )
    assert MermaidProjectDiagramGenerator._clean_id(
        "very.long.qualified.name.that.exceeds.fifty.characters.in.length"
    )[:50] == MermaidProjectDiagramGenerator._clean_id(
        "very.long.qualified.name.that.exceeds.fifty.characters.in.length"
    )
