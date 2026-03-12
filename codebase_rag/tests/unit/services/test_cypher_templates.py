from codebase_rag.services.cypher_templates import CypherTemplateBank


class TestCypherTemplateBank:
    def test_module_inventory_template_returns_graph_properties(self) -> None:
        bank = CypherTemplateBank()

        match = bank.inspect("show main modules and entry points")

        assert match is not None
        assert match.name == "module_inventory"
        assert match.query is not None
        assert "pagerank" in match.query
        assert "community_id" in match.query
        assert "has_cycle" in match.query

    def test_function_inventory_template_returns_symbol_metadata(self) -> None:
        bank = CypherTemplateBank()

        match = bank.inspect("list functions")

        assert match is not None
        assert match.name == "function_inventory"
        assert match.query is not None
        assert "signature" in match.query
        assert "docstring" in match.query
        assert "in_call_count" in match.query
        assert "dead_code_score" in match.query
