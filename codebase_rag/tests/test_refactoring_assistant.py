from __future__ import annotations

from codebase_rag.refactoring.refactoring_assistant import RefactoringAssistant


def test_suggest_refactorings_extract_method(monkeypatch) -> None:
    assistant = RefactoringAssistant()

    monkeypatch.setattr(
        assistant,
        "_fetch_source",
        lambda *_: "line\n" * 60,
    )

    suggestions = assistant.suggest_refactorings(1)

    assert any(suggestion.name == "extract_method" for suggestion in suggestions)
