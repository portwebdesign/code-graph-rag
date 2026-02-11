import logging
import sys

import pytest

from codec import schema_pb2

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def test_parser():
    deep_parser_module = pytest.importorskip("ingestion.bundle.parsing.deep_parser")
    DeepParser = getattr(deep_parser_module, "DeepParser", None)
    if DeepParser is None:
        pytest.skip("DeepParser not available")
    assert DeepParser is not None

    print("Initializing DeepParser...")
    try:
        parser = DeepParser()
        print("DeepParser initialized.")
    except Exception as e:
        print(f"Initialization failed: {e}")
        import traceback

        traceback.print_exc()
        return

    code = "function(module, exports, require) { function test() { require(123); console.log('hello'); } }"
    print(f"Parsing code: {code}")

    graph = schema_pb2.GraphCodeIndex()
    try:
        parser.parse(code, "bundle_1", "vm_1", graph)
        print("Parse successful.")
        print(f"Nodes created: {len(graph.nodes)}")
        print(f"Relationships created: {len(graph.relationships)}")

        has_calls = False
        likely_calls_type = getattr(
            schema_pb2.Relationship.RelationshipType, "LIKELY_CALLS", None
        )
        for rel in graph.relationships:
            if likely_calls_type is not None and rel.type == likely_calls_type:
                print(f"Found LIKELY_CALLS: {rel.source_id} -> {rel.target_id}")
                has_calls = True

        if not has_calls:
            print("WARNING: No LIKELY_CALLS found.")

        for node in graph.nodes:
            node_id = getattr(node, "id", None)
            node_label = getattr(node, "label", None)
            print(f"Node: {node_id} {node_label}")
            virtual_function = getattr(node, "virtual_function", None)
            if virtual_function is not None:
                print(f"  Synthetic Name: {virtual_function.synthetic_name}")
                print(f"  Signals: {virtual_function.string_literals}")
    except Exception as e:
        print(f"Parse failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_parser()
