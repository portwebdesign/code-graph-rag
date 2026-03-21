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

    def test_frontend_component_tree_template_matches_react_queries(self) -> None:
        bank = CypherTemplateBank()

        match = bank.inspect("show the component tree for the frontend")

        assert match is not None
        assert match.name == "frontend_component_tree"
        assert match.query is not None
        assert "USES_COMPONENT" in match.query
        assert "hooks_used" in match.query

    def test_frontend_prop_flow_template_matches_prop_queries(self) -> None:
        bank = CypherTemplateBank()

        match = bank.inspect("show prop flow between components")

        assert match is not None
        assert match.name == "frontend_prop_flow"
        assert match.query is not None
        assert "props_passed" in match.query
        assert "prop_bindings" in match.query

    def test_next_route_component_template_matches_next_queries(self) -> None:
        bank = CypherTemplateBank()

        match = bank.inspect("map next route to component")

        assert match is not None
        assert match.name == "next_route_component_map"
        assert match.query is not None
        assert "HAS_ENDPOINT" in match.query
        assert "next_kind" in match.query
        assert "coalesce(e.route_path, e.route, e.name)" in match.query
        assert "coalesce(e.http_method, e.method, 'ANY')" in match.query
