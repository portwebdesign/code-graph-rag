from codebase_rag.services.reasoning_capturer import ReasoningCapturer


def test_reasoning_extracts_think_tags(tmp_path) -> None:
    capturer = ReasoningCapturer(tmp_path)
    content = "<think>step one</think>Final answer"
    result = capturer.extract(content)
    assert result.thinking == "step one"
    assert result.response == "Final answer"


def test_reasoning_logs_to_file(tmp_path) -> None:
    capturer = ReasoningCapturer(tmp_path)
    path = capturer.log_reasoning("task", "thoughts", "response")
    assert path.exists()
    stored = path.read_text(encoding="utf-8")
    assert "## Thinking" in stored
    assert "thoughts" in stored
