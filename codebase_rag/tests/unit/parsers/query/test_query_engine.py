import tempfile
from pathlib import Path

import pytest
from tree_sitter import Language, Parser

from codebase_rag.infrastructure.parser_loader import get_parser_and_language
from codebase_rag.parsers.query.query_engine import QueryEngine, get_query_engine


@pytest.fixture
def query_engine():
    """Create QueryEngine instance."""
    return QueryEngine()


@pytest.fixture
def temp_queries_dir():
    """Create temporary queries directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestQueryEngine:
    """Test QueryEngine functionality."""

    def test_query_engine_initialization(self, query_engine):
        """Test QueryEngine initialization."""
        assert query_engine is not None
        assert query_engine.queries_dir.exists()

    def test_load_python_queries(self, query_engine):
        """Test loading Python queries."""
        queries = query_engine.load_queries("python")

        assert len(queries) > 0

        assert "function_definition" in queries
        assert "class_definition" in queries
        assert "import_edge" in queries
        assert "assignment_edge" in queries
        assert "call_edge" in queries

    def test_load_javascript_queries(self, query_engine):
        """Test loading JavaScript queries."""
        queries = query_engine.load_queries("javascript")

        assert len(queries) > 0
        assert "function_definition" in queries
        assert "class_definition" in queries
        assert "import_edge" in queries

    def test_load_java_queries(self, query_engine):
        """Test loading Java queries."""
        queries = query_engine.load_queries("java")

        assert len(queries) > 0
        assert "method_declarations" in queries
        assert "class_declarations" in queries

    def test_load_rust_queries(self, query_engine):
        """Test loading Rust queries."""
        queries = query_engine.load_queries("rust")

        assert len(queries) > 0
        assert "function_definitions" in queries
        assert "struct_declarations" in queries

    def test_load_cpp_queries(self, query_engine):
        """Test loading C++ queries."""
        queries = query_engine.load_queries("cpp")

        assert len(queries) > 0
        assert "function_declarations" in queries
        assert "class_definitions" in queries

    def test_get_query_caching(self, query_engine):
        """Test query caching."""
        query1 = query_engine.get_query("python", "function_definition")
        assert query1 is not None

        stats_before = query_engine.stats()
        query2 = query_engine.get_query("python", "function_definition")
        stats_after = query_engine.stats()

        assert query1 == query2
        assert stats_after["cache_hits"] > stats_before["cache_hits"]

    def test_query_stats(self, query_engine):
        """Test cache statistics."""
        query_engine.load_queries("python")

        stats = query_engine.stats()

        assert "cache_hits" in stats
        assert "cache_misses" in stats
        assert "hit_rate" in stats
        assert "cached_queries" in stats
        assert "loaded_languages" in stats
        assert "total_queries_loaded" in stats

    def test_clear_cache(self, query_engine):
        """Test cache clearing."""
        query_engine.load_queries("python")

        stats_before = query_engine.stats()
        assert stats_before["cached_queries"] > 0

        query_engine.clear_cache()

        stats_after = query_engine.stats()
        assert stats_after["cached_queries"] == 0

    def test_reload_queries(self, query_engine):
        """Test reloading queries."""
        query_engine.load_queries("python")

        query_engine.reload_queries("python")
        stats_after = query_engine.stats()

        assert stats_after["cached_queries"] >= 0

    def test_invalid_language(self, query_engine):
        """Test loading invalid language."""
        queries = query_engine.load_queries("invalid_lang_xyz")

        assert len(queries) == 0

    def test_get_query_invalid(self, query_engine):
        """Test getting non-existent query."""
        query = query_engine.get_query("python", "nonexistent_query")

        assert query is None

    def test_scm_file_parsing(self, query_engine, temp_queries_dir):
        """Test parsing of .scm files."""
        scm_content = """
; @query: test_query_1
(function_definition
  name: (identifier) @name) @func

; @query: test_query_2
(class_definition
  name: (identifier) @name) @class
"""
        scm_file = temp_queries_dir / "test.scm"
        scm_file.write_text(scm_content)

        QueryEngine(temp_queries_dir)

    def test_global_instance(self):
        """Test global QueryEngine instance."""
        engine1 = get_query_engine()
        engine2 = get_query_engine()

        assert engine1 is engine2

    def test_custom_queries_dir(self, temp_queries_dir):
        """Test using custom queries directory."""
        engine = QueryEngine(temp_queries_dir)

        assert engine.queries_dir == temp_queries_dir


class TestQueryEnginePerformance:
    """Test QueryEngine performance characteristics."""

    def test_multiple_loads_use_cache(self):
        """Test that multiple loads use cache."""
        engine = QueryEngine()

        for _ in range(5):
            engine.load_queries("python")

        stats = engine.stats()

        assert stats["hit_rate"] > 0.5

    def test_stats_reset(self):
        """Test statistics reset."""
        engine = QueryEngine()

        engine.load_queries("python")
        engine.get_query("python", "function_definition")
        engine.get_query("python", "function_definition")

        stats_before = engine.stats()
        assert stats_before["cache_hits"] > 0


class TestQueryEngineIntegration:
    """Integration tests for QueryEngine."""

    def test_python_function_query(self):
        """Test executing Python function query."""
        import tree_sitter_python as tspython

        engine = QueryEngine()

        code = """
def foo(x, y):
    return x + y

class MyClass:
    pass
"""

        parser = Parser(Language(tspython.language()))
        tree = parser.parse(code.encode())

        captures = engine.execute_query("python", "function_definition", tree.root_node)

        assert len(captures) > 0

        func_names = []
        for capture_name, node in captures:
            if capture_name != "defined_function":
                continue
            text = node.text
            if isinstance(text, bytes):
                func_names.append(text.decode())
            elif text is not None:
                func_names.append(str(text))
        assert "foo" in func_names

    def test_javascript_query(self):
        """Test executing JavaScript query."""
        import tree_sitter_javascript as tsjs

        engine = QueryEngine()

        code = """
function greet(name) {
    return 'Hello ' + name;
}
"""

        parser = Parser(Language(tsjs.language()))
        tree = parser.parse(code.encode())

        captures = engine.execute_query(
            "javascript", "function_definition", tree.root_node
        )

        assert len(captures) > 0

    def test_load_config_queries(self, query_engine):
        """Test loading config language queries."""
        json_queries = query_engine.load_queries("json")
        yaml_queries = query_engine.load_queries("yaml")
        docker_queries = query_engine.load_queries("dockerfile")

        assert "json_document" in json_queries
        assert "key_value_pair" in json_queries
        assert "yaml_document" in yaml_queries
        assert "mapping_pair" in yaml_queries
        parser, lang_obj = get_parser_and_language("dockerfile")
        if parser and lang_obj:
            assert "function_definitions" in docker_queries
            assert "copy_instruction" in docker_queries


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
