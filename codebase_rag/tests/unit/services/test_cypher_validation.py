import pytest


def _get_validator():
    module = pytest.importorskip("codebase_rag.services.llm")
    validator = getattr(module, "_validate_cypher_syntax", None)
    if validator is None:
        pytest.skip("Cypher validator not available")
    return validator


def test_validate_valid_single_label():
    query = "MATCH (n:Function) RETURN n.name"
    validator = _get_validator()
    is_valid, error = validator(query)

    assert is_valid is True
    assert error is None


def test_validate_valid_multi_label_single_var():
    query = "MATCH (n:Function|Method) RETURN n.name"
    validator = _get_validator()
    is_valid, error = validator(query)

    assert is_valid is True
    assert error is None


def test_validate_valid_where_clause():
    query = "MATCH (n) WHERE n:Function OR n:Method RETURN n.name"
    validator = _get_validator()
    is_valid, error = validator(query)

    assert is_valid is True
    assert error is None


def test_validate_invalid_multi_label_different_vars():
    query = "MATCH (f:Function|m:Method) RETURN f.name, m.name"
    validator = _get_validator()
    is_valid, error = validator(query)

    assert is_valid is False
    assert "Invalid multi-label syntax" in error
    assert "MATCH (f:Function|m:Method)" in error


def test_validate_invalid_pipe_with_different_vars():
    query = """
    MATCH (f:Function|m:Method)
    WHERE f.qualified_name CONTAINS 'generate'
    RETURN f.qualified_name, m.qualified_name
    """
    validator = _get_validator()
    is_valid, error = validator(query)

    assert is_valid is False
    assert "single variable" in error


def test_validate_case_insensitive():
    query = "match (f:Function|m:Method) return f.name"
    validator = _get_validator()
    is_valid, error = validator(query)

    assert is_valid is False


def test_validate_whitespace_variations():
    query1 = "MATCH(f:Function|m:Method)RETURN f"
    validator = _get_validator()
    is_valid1, _ = validator(query1)

    query2 = "MATCH ( f : Function | m : Method ) RETURN f"
    validator = _get_validator()
    is_valid2, _ = validator(query2)

    assert is_valid1 is False
    assert is_valid2 is False
