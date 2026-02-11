import logging
import sys
from typing import Any, cast

import pytest

from codec import schema_pb2

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def test_flow_analyzer():
    print("=== Testing FlowAnalyzer ===\n")

    FlowAnalyzer = pytest.importorskip(
        "ingestion.bundle.analysis.flow_analyzer"
    ).FlowAnalyzer

    graph = schema_pb2.GraphCodeIndex()

    likely_calls = getattr(schema_pb2.Relationship, "LIKELY_CALLS", None)
    if likely_calls is None:
        pytest.skip("Schema does not define LIKELY_CALLS")

    entry_point_enum = getattr(schema_pb2, "EntryPoint", None)
    if entry_point_enum is None:
        pytest.skip("Schema does not define EntryPoint")
    entry_point_enum_any = cast(Any, entry_point_enum)

    node_probe = schema_pb2.Node()
    if not all(
        hasattr(node_probe, field)
        for field in (
            "id",
            "label",
            "virtual_module",
            "virtual_function",
            "entry_point",
        )
    ):
        pytest.skip("Schema does not support flow analyzer node fields")

    vm1 = cast(Any, graph.nodes.add())
    vm1.id = "vm:test:1"
    vm1.label = "VirtualModule"
    vm1.virtual_module.bundle_id = "test"
    vm1.virtual_module.internal_id = "1"

    vf1 = cast(Any, graph.nodes.add())
    vf1.id = "vf:test:1:0"
    vf1.label = "VirtualFunction"
    vf1.virtual_function.bundle_id = "test"
    vf1.virtual_function.virtual_module_id = "vm:test:1"

    ep1 = cast(Any, graph.nodes.add())
    ep1.id = "ep:vf:test:1:0:event_handler:onclick"
    ep1.label = "EntryPoint"
    ep1.entry_point.virtual_function_id = "vf:test:1:0"
    ep1.entry_point.type = entry_point_enum_any.EVENT_HANDLER
    ep1.entry_point.framework = "dom"
    ep1.entry_point.pattern = "onclick"
    ep1.entry_point.trigger_description = "Click handler"

    vm2 = cast(Any, graph.nodes.add())
    vm2.id = "vm:test:2"
    vm2.label = "VirtualModule"
    vm2.virtual_module.bundle_id = "test"
    vm2.virtual_module.internal_id = "2"

    rel = cast(Any, graph.relationships.add())
    rel.type = likely_calls
    rel.source_id = "vm:test:1"
    rel.target_id = "vm:test:2"

    print(f"Graph has {len(graph.nodes)} nodes")
    print(f"Graph has {len(graph.relationships)} relationships")
    entry_points = [n for n in graph.nodes if getattr(n, "label", "") == "EntryPoint"]
    print(f"Entry points: {[getattr(n, 'id', '') for n in entry_points]}")
    print(
        f"LIKELY_CALLS: {[(r.source_id, r.target_id) for r in graph.relationships if r.type == likely_calls]}"
    )

    analyzer = FlowAnalyzer(max_depth=5)
    flows = analyzer.analyze_flows(graph)

    print(f"\nFlows created: {len(flows)}")
    for flow in flows:
        print(f"\nFlow {flow.id}:")
        print(f"  Entry point: {flow.entry_point_id}")
        print(f"  Depth: {flow.depth}")
        print(f"  Modules: {flow.total_modules}")
        print(f"  Functions: {flow.total_functions}")
        print(f"  Critical: {flow.is_critical}")
        print(f"  Complexity: {flow.complexity_score:.2f}")


if __name__ == "__main__":
    test_flow_analyzer()
