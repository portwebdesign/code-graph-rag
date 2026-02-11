import importlib
import logging
import sys

import pytest

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def _load_deep_parser():
    try:
        module = importlib.import_module("ingestion.bundle.parsing.deep_parser")
        return getattr(module, "DeepParser", None)
    except Exception:
        return None


def _load_schema():
    try:
        return importlib.import_module("codec.schema_pb2")
    except Exception:
        return None


def test_react_detection():
    print("=== Testing React Lifecycle Hook Detection ===\n")

    deep_parser_cls = _load_deep_parser()
    schema_pb2 = _load_schema()
    if (
        deep_parser_cls is None
        or schema_pb2 is None
        or not hasattr(schema_pb2, "EntryPoint")
    ):
        pytest.skip("React detection dependencies not available")

    parser = deep_parser_cls()

    test_cases = [
        {
            "name": "React Class Component Lifecycle",
            "code": """
            function(module, exports, require) {
                class TodoList extends React.Component {
                    componentDidMount() {
                        this.fetchTodos();
                    }

                    componentWillUnmount() {
                        this.cleanup();
                    }

                    componentDidUpdate(prevProps) {
                        if (prevProps.id !== this.props.id) {
                            this.fetchTodos();
                        }
                    }
                }
            }
            """,
            "expected_patterns": [
                "componentDidMount",
                "componentWillUnmount",
                "componentDidUpdate",
            ],
            "expected_framework": "react",
            "expected_type": "LIFECYCLE_HOOK",
        },
        {
            "name": "React Hooks (useEffect)",
            "code": """
            function(module, exports, require) {
                function MyComponent() {
                    useEffect(() => {
                        fetchData();
                        return () => cleanup();
                    }, []);

                    useLayoutEffect(() => {
                        measureElement();
                    });

                    const memoized = useMemo(() => {
                        return expensiveCalculation();
                    }, [dep1, dep2]);
                }
            }
            """,
            "expected_patterns": ["useEffect", "useLayoutEffect", "useMemo"],
            "expected_framework": "react",
            "expected_type": "LIFECYCLE_HOOK",
        },
        {
            "name": "React Error Boundary",
            "code": """
            function(module, exports, require) {
                class ErrorBoundary extends React.Component {
                    componentDidCatch(error, errorInfo) {
                        logErrorToService(error, errorInfo);
                    }

                    static getDerivedStateFromProps(props, state) {
                        return null;
                    }
                }
            }
            """,
            "expected_patterns": ["componentDidCatch", "getDerivedStateFromProps"],
            "expected_framework": "react",
            "expected_type": "LIFECYCLE_HOOK",
        },
    ]

    total_detected = 0
    total_expected = 0

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
                total_expected += len(test["expected_patterns"])
                continue

            found_patterns = {
                getattr(getattr(ep, "entry_point", None), "pattern", "")
                for ep in entry_points
            }
            found_frameworks = {
                getattr(getattr(ep, "entry_point", None), "framework", "")
                for ep in entry_points
            }
            found_types = {
                schema_pb2.EntryPoint.EntryType.Name(
                    getattr(getattr(ep, "entry_point", None), "type", 0)
                )
                for ep in entry_points
            }

            print(f"Patterns: {found_patterns}")
            print(f"Frameworks: {found_frameworks}")
            print(f"Types: {found_types}")

            for pattern in test["expected_patterns"]:
                total_expected += 1
                if pattern in found_patterns:
                    print(f"✓ Found pattern {pattern}")
                    total_detected += 1
                else:
                    print(f"❌ Missing pattern {pattern}")

            if test["expected_framework"] in found_frameworks:
                print(f"✓ Found framework {test['expected_framework']}")
            else:
                print(f"❌ Missing framework {test['expected_framework']}")

            if test["expected_type"] in found_types:
                print(f"✓ Found type {test['expected_type']}")
            else:
                print(f"❌ Missing type {test['expected_type']}")

            for ep in entry_points:
                print("\nEntry Point:")
                entry_point = getattr(ep, "entry_point", None)
                print(f"  ID: {getattr(ep, 'id', '')}")
                print(f"  Framework: {getattr(entry_point, 'framework', '')}")
                print(f"  Pattern: {getattr(entry_point, 'pattern', '')}")
                print(
                    "  Type: "
                    + schema_pb2.EntryPoint.EntryType.Name(
                        getattr(entry_point, "type", 0)
                    )
                )
                print(f"  Trigger: {getattr(entry_point, 'trigger_description', '')}")
                arguments = getattr(entry_point, "arguments", None)
                if arguments:
                    print(f"  Arguments: {list(arguments)}")

        except Exception as e:
            print(f"❌ Test failed: {e}")
            import traceback

            traceback.print_exc()
            total_expected += len(test["expected_patterns"])

    print("\n=== Test Complete ===")
    print(
        f"Detection Rate: {total_detected}/{total_expected} ({100 * total_detected // total_expected if total_expected > 0 else 0}%)"
    )


if __name__ == "__main__":
    test_react_detection()
