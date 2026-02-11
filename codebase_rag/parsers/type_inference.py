from pathlib import Path
from typing import TYPE_CHECKING, cast

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import (
    ASTCacheProtocol,
    ASTNode,
    FunctionRegistryTrieProtocol,
    LanguageQueries,
    SimpleNameLookup,
    TreeSitterNodeProtocol,
)
from codebase_rag.parsers.csharp import CSharpTypeInferenceEngine
from codebase_rag.parsers.go import GoTypeInferenceEngine
from codebase_rag.parsers.import_processor import ImportProcessor
from codebase_rag.parsers.java import JavaTypeInferenceEngine
from codebase_rag.parsers.js_ts import JsTypeInferenceEngine
from codebase_rag.parsers.kotlin import KotlinTypeInferenceEngine
from codebase_rag.parsers.lua import LuaTypeInferenceEngine
from codebase_rag.parsers.noop_type_inference import NoopTypeInferenceEngine
from codebase_rag.parsers.php import PhpTypeInferenceEngine
from codebase_rag.parsers.py import PythonTypeInferenceEngine, resolve_class_name
from codebase_rag.parsers.ruby import RubyTypeInferenceEngine
from codebase_rag.parsers.scala import ScalaTypeInferenceEngine

if TYPE_CHECKING:
    pass


class TypeInferenceEngine:
    """
    Coordinator for language-specific type inference.

    This class initializes and manages instances of language-specific type inference
    engines (e.g., `PythonTypeInferenceEngine`, `JavaTypeInferenceEngine`) and
    routes type inference requests to the correct engine.
    """

    def __init__(
        self,
        import_processor: ImportProcessor,
        function_registry: FunctionRegistryTrieProtocol,
        repo_path: Path,
        project_name: str,
        ast_cache: "ASTCacheProtocol",
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        module_qn_to_file_path: dict[str, Path],
        class_inheritance: dict[str, list[str]],
        simple_name_lookup: SimpleNameLookup,
    ):
        self.import_processor = import_processor
        self.function_registry = function_registry
        self.repo_path = repo_path
        self.project_name = project_name
        self.ast_cache = ast_cache
        self.queries = queries
        self.module_qn_to_file_path = module_qn_to_file_path
        self.class_inheritance = class_inheritance
        self.simple_name_lookup = simple_name_lookup

        self._java_type_inference: JavaTypeInferenceEngine | None = None
        self._lua_type_inference: LuaTypeInferenceEngine | None = None
        self._js_type_inference: JsTypeInferenceEngine | None = None
        self._python_type_inference: PythonTypeInferenceEngine | None = None
        self._go_type_inference: GoTypeInferenceEngine | None = None
        self._scala_type_inference: ScalaTypeInferenceEngine | None = None
        self._csharp_type_inference: CSharpTypeInferenceEngine | None = None
        self._php_type_inference: PhpTypeInferenceEngine | None = None
        self._ruby_type_inference: RubyTypeInferenceEngine | None = None
        self._kotlin_type_inference: KotlinTypeInferenceEngine | None = None
        self._noop_type_inference: NoopTypeInferenceEngine | None = None

    @property
    def java_type_inference(self) -> JavaTypeInferenceEngine:
        """
        Lazily initializes and returns the Java type inference engine.

        Returns:
             JavaTypeInferenceEngine: The Java type inference engine instance.
        """
        if self._java_type_inference is None:
            self._java_type_inference = JavaTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                repo_path=self.repo_path,
                project_name=self.project_name,
                ast_cache=self.ast_cache,
                queries=self.queries,
                module_qn_to_file_path=self.module_qn_to_file_path,
                class_inheritance=self.class_inheritance,
                simple_name_lookup=self.simple_name_lookup,
            )
        return self._java_type_inference

    @property
    def lua_type_inference(self) -> LuaTypeInferenceEngine:
        """
        Lazily initializes and returns the Lua type inference engine.

        Returns:
             LuaTypeInferenceEngine: The Lua type inference engine instance.
        """
        if self._lua_type_inference is None:
            self._lua_type_inference = LuaTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                project_name=self.project_name,
            )
        return self._lua_type_inference

    @property
    def js_type_inference(self) -> JsTypeInferenceEngine:
        """
        Lazily initializes and returns the JavaScript/TypeScript type inference engine.

        Returns:
             JsTypeInferenceEngine: The JS/TS type inference engine instance.
        """
        if self._js_type_inference is None:
            self._js_type_inference = JsTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                project_name=self.project_name,
                find_method_ast_node_func=self.python_type_inference._find_method_ast_node,
            )
        return self._js_type_inference

    @property
    def python_type_inference(self) -> PythonTypeInferenceEngine:
        """
        Lazily initializes and returns the Python type inference engine.

        Returns:
             PythonTypeInferenceEngine: The Python type inference engine instance.
        """
        if self._python_type_inference is None:
            self._python_type_inference = PythonTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                repo_path=self.repo_path,
                project_name=self.project_name,
                ast_cache=self.ast_cache,
                queries=self.queries,
                module_qn_to_file_path=self.module_qn_to_file_path,
                class_inheritance=self.class_inheritance,
                simple_name_lookup=self.simple_name_lookup,
                js_type_inference_getter=lambda: self.js_type_inference,
            )
        return self._python_type_inference

    @property
    def go_type_inference(self) -> GoTypeInferenceEngine:
        """
        Lazily initializes and returns the Go type inference engine.

        Returns:
             GoTypeInferenceEngine: The Go type inference engine instance.
        """
        if self._go_type_inference is None:
            self._go_type_inference = GoTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                project_name=self.project_name,
            )
        return self._go_type_inference

    @property
    def scala_type_inference(self) -> ScalaTypeInferenceEngine:
        """
        Lazily initializes and returns the Scala type inference engine.

        Returns:
             ScalaTypeInferenceEngine: The Scala type inference engine instance.
        """
        if self._scala_type_inference is None:
            self._scala_type_inference = ScalaTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                project_name=self.project_name,
            )
        return self._scala_type_inference

    @property
    def csharp_type_inference(self) -> CSharpTypeInferenceEngine:
        """
        Lazily initializes and returns the C# type inference engine.

        Returns:
             CSharpTypeInferenceEngine: The C# type inference engine instance.
        """
        if self._csharp_type_inference is None:
            self._csharp_type_inference = CSharpTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                project_name=self.project_name,
            )
        return self._csharp_type_inference

    @property
    def php_type_inference(self) -> PhpTypeInferenceEngine:
        """
        Lazily initializes and returns the PHP type inference engine.

        Returns:
             PhpTypeInferenceEngine: The PHP type inference engine instance.
        """
        if self._php_type_inference is None:
            self._php_type_inference = PhpTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                project_name=self.project_name,
            )
        return self._php_type_inference

    @property
    def ruby_type_inference(self) -> RubyTypeInferenceEngine:
        """
        Lazily initializes and returns the Ruby type inference engine.

        Returns:
             RubyTypeInferenceEngine: The Ruby type inference engine instance.
        """
        if self._ruby_type_inference is None:
            self._ruby_type_inference = RubyTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                project_name=self.project_name,
            )
        return self._ruby_type_inference

    @property
    def kotlin_type_inference(self) -> KotlinTypeInferenceEngine:
        """
        Lazily initializes and returns the Kotlin type inference engine.

        Returns:
             KotlinTypeInferenceEngine: The Kotlin type inference engine instance.
        """
        if self._kotlin_type_inference is None:
            self._kotlin_type_inference = KotlinTypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                project_name=self.project_name,
            )
        return self._kotlin_type_inference

    @property
    def noop_type_inference(self) -> NoopTypeInferenceEngine:
        """
        Lazily initializes and returns the No-op type inference engine.

        Returns:
             NoopTypeInferenceEngine: The No-op type inference engine instance.
        """
        if self._noop_type_inference is None:
            self._noop_type_inference = NoopTypeInferenceEngine()
        return self._noop_type_inference

    def build_local_variable_type_map(
        self, caller_node: ASTNode, module_qn: str, language: cs.SupportedLanguage
    ) -> dict[str, str]:
        """
        Build a map of local variable types for the given scope.

        Delegates to the appropriate language-specific inference engine.

        Args:
            caller_node (ASTNode): The AST node representing the function or scope.
            module_qn (str): The qualified name of the module containing the code.
            language (cs.SupportedLanguage): The programming language.

        Returns:
            dict[str, str]: A dictionary mapping variable names to their inferred types (QNs).
        """
        match language:
            case cs.SupportedLanguage.PYTHON:
                return self.python_type_inference.build_local_variable_type_map(
                    caller_node, module_qn
                )
            case cs.SupportedLanguage.JS | cs.SupportedLanguage.TS:
                return self.js_type_inference.build_local_variable_type_map(
                    caller_node, module_qn
                )
            case cs.SupportedLanguage.JAVA:
                return self.java_type_inference.build_variable_type_map(
                    caller_node, module_qn
                )
            case cs.SupportedLanguage.LUA:
                return self.lua_type_inference.build_local_variable_type_map(
                    cast("TreeSitterNodeProtocol", caller_node), module_qn
                )
            case cs.SupportedLanguage.GO:
                return self.go_type_inference.build_local_variable_type_map(
                    cast("TreeSitterNodeProtocol", caller_node), module_qn
                )
            case cs.SupportedLanguage.SCALA:
                return self.scala_type_inference.build_local_variable_type_map(
                    cast("TreeSitterNodeProtocol", caller_node), module_qn
                )
            case cs.SupportedLanguage.CSHARP:
                return self.csharp_type_inference.build_local_variable_type_map(
                    cast("TreeSitterNodeProtocol", caller_node), module_qn
                )
            case cs.SupportedLanguage.PHP:
                return self.php_type_inference.build_local_variable_type_map(
                    cast("TreeSitterNodeProtocol", caller_node), module_qn
                )
            case cs.SupportedLanguage.RUBY:
                return self.ruby_type_inference.build_local_variable_type_map(
                    cast("TreeSitterNodeProtocol", caller_node), module_qn
                )
            case cs.SupportedLanguage.KOTLIN:
                return self.kotlin_type_inference.build_local_variable_type_map(
                    cast("TreeSitterNodeProtocol", caller_node), module_qn
                )
            case (
                cs.SupportedLanguage.YAML
                | cs.SupportedLanguage.JSON
                | cs.SupportedLanguage.HTML
                | cs.SupportedLanguage.CSS
                | cs.SupportedLanguage.SCSS
                | cs.SupportedLanguage.GRAPHQL
                | cs.SupportedLanguage.DOCKERFILE
                | cs.SupportedLanguage.SQL
                | cs.SupportedLanguage.VUE
                | cs.SupportedLanguage.SVELTE
            ):
                return self.noop_type_inference.build_local_variable_type_map(
                    cast("TreeSitterNodeProtocol", caller_node), module_qn
                )
            case _:
                return {}

    def _resolve_class_name(self, class_name: str, module_qn: str) -> str | None:
        return resolve_class_name(
            class_name, module_qn, self.import_processor, self.function_registry
        )

    def _build_java_variable_type_map(
        self, caller_node: ASTNode, module_qn: str
    ) -> dict[str, str]:
        return self.java_type_inference.build_variable_type_map(caller_node, module_qn)
