from __future__ import annotations

from codebase_rag.services.cypher_guard import CypherGuard


def test_cypher_guard_injects_project_scope_and_limit() -> None:
    guard = CypherGuard()

    result = guard.rewrite_and_validate("MATCH (m:Module) RETURN m.name")

    assert result.valid is True
    assert "{project_name: $project_name}" in result.query
    assert "LIMIT 200" in result.query
    assert "project_scope_injected" in result.warnings
    assert "limit_added" in result.warnings


def test_cypher_guard_rejects_write_queries() -> None:
    guard = CypherGuard()

    result = guard.rewrite_and_validate("MATCH (n) DELETE n RETURN count(n)")

    assert result.valid is False
    assert "write_keywords_not_allowed" in result.errors
