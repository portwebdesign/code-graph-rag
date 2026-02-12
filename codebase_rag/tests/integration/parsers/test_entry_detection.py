import logging
import sys
from typing import Any, cast

import pytest

from codec import schema_pb2

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def test_entry_detection():
    print("=== Testing Entry Point Detection ===\n")
    DeepParser = pytest.importorskip("ingestion.bundle.parsing.deep_parser").DeepParser
    entry_point_enum = getattr(schema_pb2, "EntryPoint", None)
    if entry_point_enum is None:
        pytest.skip("Schema does not define EntryPoint")
    entry_point_enum_any = cast(Any, entry_point_enum)

    parser = DeepParser()

    test_cases = [
        {
            "name": "Leaflet Map Init",
            "code": """
            function(module, exports, require) {
                var map = L.map('map-container', {
                    center: [51.505, -0.09],
                    zoom: 13
                });
                map.on('click', function(e) {
                    console.log('Map clicked');
                });
            }
            """,
            "expected_patterns": ["L.map", "*.on"],
            "expected_frameworks": ["leaflet"],
        },
        {
            "name": "DOM Event Listener",
            "code": """
            function(module, exports, require) {
                document.getElementById('btn').addEventListener('click', function() {
                    console.log('Button clicked');
                });
                window.onload = function() {
                    console.log('Page loaded');
                };
            }
            """,
            "expected_patterns": ["*.addEventListener", "*.onload"],
            "expected_frameworks": ["dom"],
        },
        {
            "name": "Algolia Search",
            "code": """
            function(module, exports, require) {
                var client = require(123);
                var index = client.initIndex('products');
                index.search('query').then(function(results) {
                    console.log(results);
                });
            }
            """,
            "expected_patterns": ["*.initIndex", "*.search"],
            "expected_frameworks": ["algolia"],
        },
    ]

    for i, test in enumerate(test_cases):
        print(f"\n--- Test {i + 1}: {test['name']} ---")

        graph = schema_pb2.GraphCodeIndex()
        try:
            parser.parse(test["code"], "test_bundle", f"test_vm_{i}", graph)

            entry_points = [
                n for n in graph.nodes if getattr(n, "label", "") == "EntryPoint"
            ]
            print(f"Entry points found: {len(entry_points)}")

            if not entry_points:
                print("❌ No entry points detected!")
                continue

            found_patterns = set()
            found_frameworks = set()
            for ep in entry_points:
                ep_any = cast(Any, ep)
                entry_point = getattr(ep_any, "entry_point", None)
                if entry_point is None:
                    continue
                found_patterns.add(entry_point.pattern)
                found_frameworks.add(entry_point.framework)

            print(f"Patterns: {found_patterns}")
            print(f"Frameworks: {found_frameworks}")

            for pattern in test["expected_patterns"]:
                if pattern.startswith("*"):
                    suffix = pattern.split(".")[-1]
                    matches = [p for p in found_patterns if p.endswith(f".{suffix}")]
                    if matches:
                        print(f"✓ Found pattern matching {pattern}: {matches}")
                    else:
                        print(f"❌ Missing pattern {pattern}")
                elif pattern in found_patterns:
                    print(f"✓ Found pattern {pattern}")
                else:
                    print(f"❌ Missing pattern {pattern}")

            for framework in test["expected_frameworks"]:
                if framework in found_frameworks:
                    print(f"✓ Found framework {framework}")
                else:
                    print(f"❌ Missing framework {framework}")

            for ep in entry_points:
                ep_any = cast(Any, ep)
                entry_point = getattr(ep_any, "entry_point", None)
                if entry_point is None:
                    continue
                print("\nEntry Point:")
                print(f"  ID: {getattr(ep_any, 'id', '')}")
                print(f"  Framework: {entry_point.framework}")
                print(f"  Pattern: {entry_point.pattern}")
                print(
                    f"  Type: {entry_point_enum_any.EntryType.Name(entry_point.type)}"
                )
                print(f"  Trigger: {entry_point.trigger_description}")
                if entry_point.arguments:
                    print(f"  Arguments: {list(entry_point.arguments)}")

            is_entry_point = getattr(
                schema_pb2.Relationship.RelationshipType, "IS_ENTRY_POINT", None
            )
            if is_entry_point is None:
                print("\nIS_ENTRY_POINT relationships: 0")
            else:
                ep_rels = [r for r in graph.relationships if r.type == is_entry_point]
                print(f"\nIS_ENTRY_POINT relationships: {len(ep_rels)}")

        except Exception as e:
            print(f"❌ Test failed: {e}")
            import traceback

            traceback.print_exc()

    print("\n=== Test Complete ===")


if __name__ == "__main__":
    test_entry_detection()
