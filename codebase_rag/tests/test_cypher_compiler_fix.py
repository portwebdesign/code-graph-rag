import sys

import pytest


def _load_compiler():
    module = pytest.importorskip("codebase_rag.services.cypher_compiler")
    compiler_cls = getattr(module, "CypherCompiler", None)
    error_cls = getattr(module, "CypherCompilerError", None)
    if compiler_cls is None or error_cls is None:
        pytest.skip("Cypher compiler not available")
    return compiler_cls, error_cls


def test_compiler_allows_valid_order_by():
    print("Testing valid ORDER BY...")
    compiler_cls, _ = _load_compiler()
    compiler = compiler_cls()
    query = "MATCH (n) RETURN n.name ORDER BY n.name;"
    compiled = compiler.compile(query)
    if "ORDER BY n.name" not in compiled:
        raise Exception("Failed to compile valid ORDER BY")
    print("PASS")


def test_compiler_allows_head_labels():
    print("Testing ALLOWED head(labels)...")
    compiler_cls, _ = _load_compiler()
    compiler = compiler_cls()
    query = "MATCH (n) RETURN labels(n) AS type ORDER BY head(labels(n));"
    compiled = compiler.compile(query)
    if "ORDER BY head(labels(n))" not in compiled:
        raise Exception("Failed to compile valid head(labels)")
    print("PASS")


def test_compiler_rejects_direct_labels_sort():
    print("Testing REJECT labels(n)...")
    compiler_cls, error_cls = _load_compiler()
    compiler = compiler_cls()
    query = "MATCH (n) RETURN n.name ORDER BY labels(n);"
    try:
        compiler.compile(query)
        raise Exception("Should have rejected 'ORDER BY labels(n)'")
    except error_cls as e:
        if "Cannot order by list" not in str(e):
            raise Exception(f"Wrong error message: {e}")
    print("PASS")


def test_compiler_rejects_alias_labels_sort():
    print("Testing REJECT alias sort...")
    compiler_cls, error_cls = _load_compiler()
    compiler = compiler_cls()
    query = "MATCH (n) RETURN labels(n) as type, n.name as name ORDER BY type, name;"
    try:
        compiler.compile(query)
        raise Exception("Should have rejected 'ORDER BY type'")
    except error_cls as e:
        if "Cannot order by list" not in str(e):
            raise Exception(f"Wrong error message: {e}")
    print("PASS")


if __name__ == "__main__":
    try:
        test_compiler_allows_valid_order_by()
        test_compiler_allows_head_labels()
        test_compiler_rejects_direct_labels_sort()
        test_compiler_rejects_alias_labels_sort()
        print("\nALL VERIFICATION TESTS PASSED SUCCESSFULLY")
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        sys.exit(1)
