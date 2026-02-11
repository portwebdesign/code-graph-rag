from unittest.mock import Mock

import pytest

from codebase_rag.parsers.type_inference import (
    FunctionSignature,
    InferenceContext,
    JSTypeScriptInferenceEngine,
    PythonTypeInferenceEngine,
    TypeInferenceResult,
    TypeRegistry,
    TypeSource,
    VariableInfo,
)


class TestTypeInferenceResult:
    """Test TypeInferenceResult dataclass."""

    def test_creation(self):
        """Test creating a TypeInferenceResult."""
        result = TypeInferenceResult(
            type_string="int",
            confidence=1.0,
            source=TypeSource.ANNOTATION,
            language="python",
        )

        assert result.type_string == "int"
        assert result.confidence == 1.0
        assert result.source == TypeSource.ANNOTATION
        assert result.language == "python"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        result = TypeInferenceResult(
            type_string="str",
            confidence=0.9,
            source=TypeSource.INFERENCE,
            language="python",
            context={"key": "value"},
        )

        data = result.to_dict()
        assert data["type_string"] == "str"
        assert data["confidence"] == 0.9
        assert data["source"] == "inference"
        assert data["language"] == "python"
        assert data["context"] == {"key": "value"}

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "type_string": "list",
            "confidence": 0.8,
            "source": "inference",
            "language": "python",
            "context": {},
        }

        result = TypeInferenceResult.from_dict(data)
        assert result.type_string == "list"
        assert result.confidence == 0.8
        assert result.source == TypeSource.INFERENCE


class TestInferenceContext:
    """Test InferenceContext functionality."""

    def test_init(self):
        """Test context initialization."""
        ctx = InferenceContext(language="python", file_path="test.py")

        assert ctx.language == "python"
        assert ctx.current_file == "test.py"
        assert len(ctx.variables) == 0
        assert len(ctx.functions) == 0

    def test_scope_management(self):
        """Test entering and exiting scopes."""
        ctx = InferenceContext("python")

        ctx.enter_scope("class", "MyClass")
        assert ctx.current_class == "MyClass"
        assert len(ctx.scope_stack) == 1

        ctx.enter_scope("function", "my_func")
        assert ctx.current_function == "my_func"
        assert len(ctx.scope_stack) == 2

        ctx.exit_scope()
        assert ctx.current_function is None
        assert len(ctx.scope_stack) == 1

        ctx.exit_scope()
        assert ctx.current_class is None
        assert len(ctx.scope_stack) == 0

    def test_add_variable(self):
        """Test adding variable to context."""
        ctx = InferenceContext("python")

        var_info = VariableInfo(
            name="x",
            type_result=TypeInferenceResult(
                type_string="int",
                confidence=1.0,
                source=TypeSource.ANNOTATION,
            ),
            line=10,
        )

        ctx.add_variable(var_info)

        retrieved = ctx.get_variable("x")
        assert retrieved is not None
        assert retrieved.name == "x"
        assert retrieved.type_result is not None
        assert retrieved.type_result.type_string == "int"

    def test_add_function(self):
        """Test adding function signature to context."""
        ctx = InferenceContext("python")

        sig = FunctionSignature(
            name="my_func",
            return_type=TypeInferenceResult(
                type_string="str",
                confidence=0.9,
                source=TypeSource.INFERENCE,
            ),
        )

        ctx.add_function(sig)

        retrieved = ctx.get_function("my_func")
        assert retrieved is not None
        assert retrieved.name == "my_func"
        assert retrieved.return_type is not None
        assert retrieved.return_type.type_string == "str"

    def test_import_management(self):
        """Test import tracking."""
        ctx = InferenceContext("python")

        ctx.add_import("np", "numpy")
        ctx.add_from_import("os.path", ["join", "dirname"])

        assert ctx.resolve_import("np") == "numpy"
        assert ctx.resolve_import("join") == "os.path.join"
        assert ctx.resolve_import("dirname") == "os.path.dirname"
        assert ctx.resolve_import("unknown") is None

    def test_inference_cache(self):
        """Test inference result caching."""
        ctx = InferenceContext("python")

        result1 = TypeInferenceResult(
            type_string="int",
            confidence=1.0,
            source=TypeSource.ANNOTATION,
        )

        ctx.cache_inference("var_x", result1)
        result2 = ctx.get_cached_inference("var_x")

        assert result2 == result1

    def test_serialization(self):
        """Test context serialization to dictionary."""
        ctx = InferenceContext("python", "test.py")

        var_info = VariableInfo(
            name="x",
            type_result=TypeInferenceResult(
                type_string="int",
                confidence=1.0,
                source=TypeSource.ANNOTATION,
            ),
        )
        ctx.add_variable(var_info)

        data = ctx.to_dict()
        assert data["language"] == "python"
        assert data["file_path"] == "test.py"
        assert "variables" in data
        assert "functions" in data


class TestTypeRegistry:
    """Test TypeRegistry functionality."""

    def test_init(self):
        """Test registry initialization."""
        registry = TypeRegistry("python")

        assert registry.language == "python"
        stats = registry.stats()
        assert stats["types_registered"] > 0

    def test_register_and_lookup_type(self):
        """Test registering and looking up types."""
        registry = TypeRegistry("python")

        custom_type = TypeInferenceResult(
            type_string="CustomClass",
            confidence=1.0,
            source=TypeSource.REGISTRY,
        )

        registry.register_type("CustomClass", custom_type)
        result = registry.lookup_type("CustomClass")

        assert result is not None
        assert result.type_string == "CustomClass"

    def test_register_function(self):
        """Test registering function signatures."""
        registry = TypeRegistry("python")

        sig = FunctionSignature(
            name="custom_func",
            return_type=TypeInferenceResult(
                type_string="int",
                confidence=0.9,
                source=TypeSource.INFERENCE,
            ),
        )

        registry.register_function("custom_func", sig)
        result = registry.lookup_function("custom_func")

        assert result is not None
        assert result.name == "custom_func"

    def test_type_mapping(self):
        """Test type alias mapping."""
        registry = TypeRegistry("python")

        registry.add_type_mapping("MyList", "list[Any]")

        resolved = registry.resolve_type_mapping("MyList")
        assert resolved == "list[Any]"

    def test_builtin_types(self):
        """Test built-in types are loaded."""
        registry = TypeRegistry("python")

        assert registry.lookup_type("int") is not None
        assert registry.lookup_type("str") is not None
        assert registry.lookup_type("list") is not None
        assert registry.lookup_type("dict") is not None


class TestPythonTypeInferenceEngine:
    """Test Python type inference engine."""

    @pytest.fixture
    def engine(self):
        """Create Python type inference engine."""
        return PythonTypeInferenceEngine()

    @pytest.fixture
    def context(self):
        """Create inference context."""
        return InferenceContext("python")

    def test_init(self, engine):
        """Test engine initialization."""
        assert engine.language == "python"
        assert engine.registry is not None
        assert engine.annotation_strategy is not None
        assert engine.inference_strategy is not None
        assert engine.registry_strategy is not None

    def test_annotation_strategy(self, engine, context):
        """Test annotation strategy."""
        node = Mock()
        node.type = "typed_parameter"
        annotation_node = Mock()
        annotation_node.type = "identifier"
        annotation_node.text = b"int"
        node.child_by_field_name = Mock(return_value=annotation_node)

        result = engine.annotation_strategy.infer(node, context)

        assert result is not None
        assert result.type_string == "int"
        assert result.confidence == 1.0
        assert result.source == TypeSource.ANNOTATION

    def test_context_setting(self, engine, context):
        """Test setting inference context."""
        engine.set_context(context)

        assert engine.context == context
        assert engine.context.language == "python"

    def test_return_type_inference(self, engine, context):
        """Test function return type inference."""
        engine.set_context(context)

        func_node = Mock()
        func_node.child_by_field_name = Mock(return_value=None)

        result = engine.infer_return_type(func_node, context)

        assert result is not None
        assert result.type_string == "Any"


class TestJSTypeScriptInferenceEngine:
    """Test JavaScript/TypeScript type inference engine."""

    @pytest.fixture
    def ts_engine(self):
        """Create TypeScript engine."""
        return JSTypeScriptInferenceEngine("typescript")

    @pytest.fixture
    def js_engine(self):
        """Create JavaScript engine."""
        return JSTypeScriptInferenceEngine("javascript")

    @pytest.fixture
    def context(self):
        """Create inference context."""
        return InferenceContext("typescript")

    def test_ts_init(self, ts_engine):
        """Test TypeScript engine initialization."""
        assert ts_engine.language == "typescript"
        assert ts_engine.jsdoc_strategy is not None
        assert ts_engine.annotation_strategy is not None

    def test_js_init(self, js_engine):
        """Test JavaScript engine initialization."""
        assert js_engine.language == "javascript"

    def test_inference_number(self, ts_engine, context):
        """Test number literal inference."""
        ts_engine.set_context(context)

        node = Mock()
        node.type = "number"

        result = ts_engine.inference_strategy.infer(node, context)

        assert result is not None
        assert result.type_string == "number"
        assert result.source == TypeSource.INFERENCE

    def test_inference_string(self, ts_engine, context):
        """Test string literal inference."""
        ts_engine.set_context(context)

        node = Mock()
        node.type = "string"

        result = ts_engine.inference_strategy.infer(node, context)

        assert result is not None
        assert result.type_string == "string"

    def test_inference_boolean(self, ts_engine, context):
        """Test boolean literal inference."""
        ts_engine.set_context(context)

        node = Mock()
        node.type = "true"

        result = ts_engine.inference_strategy.infer(node, context)

        assert result is not None
        assert result.type_string == "boolean"


class TestPhase5Integration:
    """Test Phase 5 integration with existing phases."""

    def test_backward_compatibility(self):
        """Verify Phase 5 doesn't break Phase 1-4."""
        try:
            from codebase_rag.parsers.query_engine import QueryEngine

            engine = QueryEngine()
            assert engine is not None
        except Exception as e:
            pytest.fail(f"Phase 1 QueryEngine broken: {e}")

        try:
            from codebase_rag.parsers.process_manager import ParserProcessManager

            manager = ParserProcessManager()
            assert manager is not None
        except Exception as e:
            pytest.fail(f"Phase 4 ProcessManager broken: {e}")

    def test_type_inference_with_process_manager(self):
        """Test Phase 5 type inference with Phase 4 ProcessManager."""
        from codebase_rag.parsers.process_manager import ParserProcessManager

        engine = PythonTypeInferenceEngine()
        manager = ParserProcessManager()

        assert engine is not None
        assert manager is not None

    def test_multiple_engines(self):
        """Test using multiple type inference engines."""
        py_engine = PythonTypeInferenceEngine()
        ts_engine = JSTypeScriptInferenceEngine("typescript")
        js_engine = JSTypeScriptInferenceEngine("javascript")

        assert py_engine.language == "python"
        assert ts_engine.language == "typescript"
        assert js_engine.language == "javascript"

    def test_shared_registry(self):
        """Test sharing type registry across engines."""
        shared_registry = TypeRegistry("python")

        engine1 = PythonTypeInferenceEngine(shared_registry)
        engine2 = PythonTypeInferenceEngine(shared_registry)

        assert engine1.registry is engine2.registry


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
