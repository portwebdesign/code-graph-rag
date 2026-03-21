from __future__ import annotations

from codebase_rag.core import constants as cs


def test_sql_function_and_class_type_sets_are_disjoint() -> None:
    assert set(cs.SPEC_SQL_FUNCTION_TYPES).isdisjoint(set(cs.SPEC_SQL_CLASS_TYPES))


def test_sql_policy_treats_view_as_schema_artifact() -> None:
    assert "create_view" in cs.SPEC_SQL_CLASS_TYPES
    assert "create_view" not in cs.SPEC_SQL_FUNCTION_TYPES


def test_sql_policy_supports_procedure_as_callable_definition() -> None:
    assert "create_procedure" in cs.SPEC_SQL_FUNCTION_TYPES
