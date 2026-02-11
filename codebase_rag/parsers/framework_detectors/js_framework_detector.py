import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JsFrameworkType(Enum):
    """Supported JavaScript/TypeScript frameworks."""

    REACT = "react"
    EXPRESS = "express"
    NESTJS = "nestjs"
    VUE = "vue"
    ANGULAR = "angular"
    NEXT = "next"
    NUXT = "nuxt"
    GRAPHQL = "graphql"
    SVELTE = "svelte"
    NONE = "none"


@dataclass
class ReactComponent:
    """React component information."""

    component_name: str
    component_type: str
    props: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    exports: bool = True


@dataclass
class ExpressRoute:
    """Express route definition."""

    path: str
    method: str
    handler_name: str | None = None
    middleware: list[str] = field(default_factory=list)


@dataclass
class NestModule:
    """NestJS module information."""

    module_name: str
    controllers: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


@dataclass
class NestController:
    """NestJS controller endpoint."""

    path: str
    method: str
    handler_name: str
    decorators: list[str] = field(default_factory=list)


@dataclass
class VueComponent:
    """Vue component information."""

    component_name: str
    template_tag: str | None = None
    script_lang: str = "js"
    props: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    computed: list[str] = field(default_factory=list)
    lifecycle_hooks: list[str] = field(default_factory=list)


@dataclass
class AngularComponent:
    """Angular component information."""

    selector: str
    component_name: str
    template_url: str | None = None
    style_urls: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


class JsFrameworkDetector:
    """Detect JavaScript/TypeScript frameworks from source code.

    Detection:
        - React: import React, JSX syntax
        - Express: require('express'), app.get/post patterns
        - NestJS: @Controller, @Module decorators
        - Vue: <template>, <script>, <style> blocks
        - Angular: @Component, @NgModule decorators
        - Next.js: next/router, pages/ directory
        - Nuxt: nuxt.config.js, pages/ directory
        - Svelte: svelte files, reactive assignments

    Example:
        detector = JsFrameworkDetector()
        framework = detector.detect_from_source(js_code)
        if framework == JsFrameworkType.REACT:
            components = detector.extract_react_components(js_code)
    """

    REACT_INDICATORS = {
        "imports": ["import React", "from 'react'", 'from "react"', "react/"],
        "jsx": ["<", ">", "JSX"],
        "hooks": ["useState", "useEffect", "useContext", "useReducer"],
    }

    EXPRESS_INDICATORS = {
        "imports": ["require('express')", 'require("express")'],
        "patterns": ["app.get(", "app.post(", "app.use(", "router.get("],
    }

    NESTJS_INDICATORS = {
        "imports": ["@nestjs", "from '@nestjs"],
        "decorators": ["@Controller", "@Module", "@Injectable", "@Service"],
    }

    VUE_INDICATORS = {
        "syntax": ["<template>", "<script>", "<style>"],
        "imports": ["from 'vue'", 'from "vue"', "Vue"],
    }

    ANGULAR_INDICATORS = {
        "imports": ["@angular", "from '@angular"],
        "decorators": ["@Component", "@NgModule", "@Injectable", "@Directive"],
    }

    NEXT_INDICATORS = {
        "imports": ["next/router", "next/link", "next/image"],
        "files": ["pages/", "next.config.js"],
    }

    NUXT_INDICATORS = {
        "imports": ["nuxt", "#app", "#imports"],
        "patterns": ["defineNuxtConfig", "useNuxtApp", "useRuntimeConfig"],
    }

    GRAPHQL_INDICATORS = {
        "imports": ["graphql", "@apollo", "apollo-server", "relay-runtime"],
        "patterns": ["gql`", "graphql-tag", "type Query", "type Mutation"],
    }

    def __init__(self):
        """Initialize JavaScript/TypeScript framework detector."""
        pass

    def detect_from_source(self, source_code: str) -> JsFrameworkType:
        """Detect framework from JavaScript/TypeScript source code.

        Args:
            source_code: JavaScript/TypeScript source code as string

        Returns:
            JsFrameworkType detected framework

        Example:
            framework = detector.detect_from_source(js_source)
            print(f"Framework: {framework.value}")
        """
        if any(
            indicator in source_code for indicator in self.REACT_INDICATORS["imports"]
        ):
            if "React.createElement" in source_code or "<" in source_code:
                return JsFrameworkType.REACT

        if any(
            indicator in source_code for indicator in self.NESTJS_INDICATORS["imports"]
        ):
            return JsFrameworkType.NESTJS

        if any(
            indicator in source_code for indicator in self.ANGULAR_INDICATORS["imports"]
        ):
            return JsFrameworkType.ANGULAR

        if any(indicator in source_code for indicator in self.VUE_INDICATORS["syntax"]):
            return JsFrameworkType.VUE

        if any(
            indicator in source_code for indicator in self.VUE_INDICATORS["imports"]
        ):
            return JsFrameworkType.VUE

        if any(
            indicator in source_code for indicator in self.NEXT_INDICATORS["imports"]
        ):
            return JsFrameworkType.NEXT

        if any(
            indicator in source_code for indicator in self.NUXT_INDICATORS["patterns"]
        ):
            return JsFrameworkType.NUXT
        if any(
            indicator in source_code for indicator in self.NUXT_INDICATORS["imports"]
        ):
            return JsFrameworkType.NUXT

        if any(
            indicator in source_code
            for indicator in self.GRAPHQL_INDICATORS["patterns"]
        ):
            return JsFrameworkType.GRAPHQL
        if any(
            indicator in source_code for indicator in self.GRAPHQL_INDICATORS["imports"]
        ):
            return JsFrameworkType.GRAPHQL

        if any(
            indicator in source_code for indicator in self.EXPRESS_INDICATORS["imports"]
        ):
            return JsFrameworkType.EXPRESS

        if any(
            indicator in source_code
            for indicator in self.EXPRESS_INDICATORS["patterns"]
        ):
            return JsFrameworkType.EXPRESS

        return JsFrameworkType.NONE

    def extract_react_components(self, source_code: str) -> list[ReactComponent]:
        """Extract React component definitions.

        Args:
            source_code: JavaScript/TypeScript source code

        Returns:
            List of ReactComponent objects

        Example:
            components = detector.extract_react_components(source_code)
            for comp in components:
                print(f"Component: {comp.component_name}")
                print(f"Props: {', '.join(comp.props)}")
        """
        components = []

        functional_pattern = r"(?:const|function)\s+(\w+)\s*(?:=\s*)?(?:\((?:\s*{([^}]*)}|\s*(?:props|_)*\s*)\)|function\()"

        for match in re.finditer(functional_pattern, source_code):
            component_name = match.group(1)
            props_str = match.group(2) or ""

            props = [p.strip().split(":")[0] for p in props_str.split(",") if p.strip()]

            if component_name and component_name[0].isupper():
                hooks = self._extract_hooks(source_code, component_name)

                components.append(
                    ReactComponent(
                        component_name=component_name,
                        component_type="functional",
                        props=props,
                        hooks=hooks,
                    )
                )

        class_pattern = r"class\s+(\w+)\s+extends\s+(?:React\.)?(?:Pure)?Component"

        for match in re.finditer(class_pattern, source_code):
            component_name = match.group(1)

            components.append(
                ReactComponent(
                    component_name=component_name,
                    component_type="class",
                )
            )

        memo_pattern = r"React\.memo\((\w+)\)"

        for match in re.finditer(memo_pattern, source_code):
            component_name = match.group(1)

            components.append(
                ReactComponent(
                    component_name=component_name,
                    component_type="memo",
                )
            )

        return components

    def extract_express_routes(self, source_code: str) -> list[ExpressRoute]:
        """Extract Express route definitions.

        Args:
            source_code: JavaScript/TypeScript source code

        Returns:
            List of ExpressRoute objects

        Example:
            routes = detector.extract_express_routes(source_code)
            for route in routes:
                print(f"{route.method.upper()} {route.path}")
        """
        routes = []

        for method in ["get", "post", "put", "delete", "patch"]:
            pattern = (
                rf'app\.{method}\(["\']([^"\']+)["\'](?:\s*,\s*(?:function\s*)?(\w+))?'
            )

            for match in re.finditer(pattern, source_code):
                path = match.group(1)
                handler_name = match.group(2)

                routes.append(
                    ExpressRoute(
                        path=path,
                        method=method.upper(),
                        handler_name=handler_name,
                    )
                )

        for method in ["get", "post", "put", "delete", "patch"]:
            pattern = rf'router\.{method}\(["\']([^"\']+)["\']'

            for match in re.finditer(pattern, source_code):
                path = match.group(1)
                routes.append(
                    ExpressRoute(
                        path=path,
                        method=method.upper(),
                    )
                )

        return routes

    def extract_nest_modules(self, source_code: str) -> list[NestModule]:
        """Extract NestJS module definitions.

        Args:
            source_code: TypeScript source code

        Returns:
            List of NestModule objects

        Example:
            modules = detector.extract_nest_modules(source_code)
            for mod in modules:
                print(f"Module: {mod.module_name}")
                print(f"Controllers: {', '.join(mod.controllers)}")
        """
        modules = []

        module_pattern = r"@Module\(\s*{([^}]+)}\s*\)\s*export\s+class\s+(\w+)"

        for match in re.finditer(module_pattern, source_code, re.DOTALL):
            module_body = match.group(1)
            module_name = match.group(2)

            controllers_match = re.search(
                r"controllers\s*:\s*\[([^\]]+)\]", module_body
            )
            controllers = []
            if controllers_match:
                controllers = [c.strip() for c in controllers_match.group(1).split(",")]

            providers_match = re.search(r"providers\s*:\s*\[([^\]]+)\]", module_body)
            providers = []
            if providers_match:
                providers = [p.strip() for p in providers_match.group(1).split(",")]

            imports_match = re.search(r"imports\s*:\s*\[([^\]]+)\]", module_body)
            imports = []
            if imports_match:
                imports = [i.strip() for i in imports_match.group(1).split(",")]

            modules.append(
                NestModule(
                    module_name=module_name,
                    controllers=controllers,
                    providers=providers,
                    imports=imports,
                )
            )

        return modules

    def extract_nest_controllers(self, source_code: str) -> list[NestController]:
        """Extract NestJS controller endpoints.

        Args:
            source_code: TypeScript source code

        Returns:
            List of NestController objects

        Example:
            controllers = detector.extract_nest_controllers(source_code)
            for controller in controllers:
                print(f"{controller.method} {controller.path}")
        """
        controllers = []

        for method in ["Get", "Post", "Put", "Delete", "Patch"]:
            pattern = (
                rf'@{method}\(["\']([^"\']+)["\']?\s*\)\s*(?:public\s+)?(\w+)\s*\('
            )

            for match in re.finditer(pattern, source_code):
                path = match.group(1) or "/"
                handler = match.group(2)

                controllers.append(
                    NestController(
                        path=path,
                        method=method,
                        handler_name=handler,
                    )
                )

        return controllers

    def extract_vue_components(self, source_code: str) -> list[VueComponent]:
        """Extract Vue component information from .vue file.

        Args:
            source_code: Vue single-file component code

        Returns:
            List of VueComponent objects

        Example:
            components = detector.extract_vue_components(source_code)
            for comp in components:
                print(f"Component props: {', '.join(comp.props)}")
        """
        components = []

        script_pattern = r'<script(?:\s+lang=["\'](\w+)["\'])?>(.*?)</script>'
        script_match = re.search(script_pattern, source_code, re.DOTALL)

        if script_match:
            script_lang = script_match.group(1) or "js"
            script_code = script_match.group(2)

            export_pattern = r"export\s+default\s+(?:{([^}]+)}|[A-Za-z0-9_]+)"
            export_match = re.search(export_pattern, script_code, re.DOTALL)

            if export_match:
                component_body = export_match.group(1) or ""

                name_match = re.search(r"name\s*:\s*['\"](\w+)['\"]", component_body)
                component_name = (
                    name_match.group(1) if name_match else "UnnamedComponent"
                )

                props_match = re.search(
                    r"props\s*:\s*(\[.*?\]|\{.*?\})", component_body, re.DOTALL
                )
                props = []
                if props_match:
                    props_str = props_match.group(1)
                    props = re.findall(r"['\"](\w+)['\"]", props_str)

                methods_match = re.search(
                    r"methods\s*:\s*\{(.*?)\n\s*\}", component_body, re.DOTALL
                )
                methods = []
                if methods_match:
                    methods_body = methods_match.group(1)
                    methods = re.findall(r"(\w+)\s*\(", methods_body)

                computed_match = re.search(
                    r"computed\s*:\s*\{(.*?)\n\s*\}", component_body, re.DOTALL
                )
                computed = []
                if computed_match:
                    computed_body = computed_match.group(1)
                    computed = re.findall(r"(\w+)\s*\(", computed_body)

                lifecycle_hooks = []
                for hook in [
                    "mounted",
                    "created",
                    "updated",
                    "destroyed",
                    "beforeCreate",
                    "beforeUpdate",
                ]:
                    if rf"{hook}\s*(" in component_body:
                        lifecycle_hooks.append(hook)

                components.append(
                    VueComponent(
                        component_name=component_name,
                        script_lang=script_lang,
                        props=props,
                        methods=methods,
                        computed=computed,
                        lifecycle_hooks=lifecycle_hooks,
                    )
                )

        return components

    def extract_angular_components(self, source_code: str) -> list[AngularComponent]:
        """Extract Angular component information.

        Args:
            source_code: TypeScript component source code

        Returns:
            List of AngularComponent objects

        Example:
            components = detector.extract_angular_components(source_code)
            for comp in components:
                print(f"Selector: {comp.selector}")
                print(f"Inputs: {', '.join(comp.inputs)}")
        """
        components = []

        component_pattern = r"@Component\(\s*{([^}]+)}\s*\)\s*export\s+class\s+(\w+)"

        for match in re.finditer(component_pattern, source_code, re.DOTALL):
            component_body = match.group(1)
            component_name = match.group(2)

            selector_match = re.search(
                r"selector\s*:\s*['\"]([^'\"]+)['\"]", component_body
            )
            selector = selector_match.group(1) if selector_match else ""

            template_match = re.search(
                r"templateUrl\s*:\s*['\"]([^'\"]+)['\"]", component_body
            )
            template_url = template_match.group(1) if template_match else None

            styles_match = re.search(r"styleUrls\s*:\s*\[([^\]]+)\]", component_body)
            style_urls = []
            if styles_match:
                style_urls = re.findall(r"['\"]([^'\"]+)['\"]", styles_match.group(1))

            input_pattern = r"@Input\s+(?:readonly\s+)?(\w+)"
            inputs = [
                match.group(1) for match in re.finditer(input_pattern, source_code)
            ]

            output_pattern = r"@Output\s+(?:readonly\s+)?(\w+)"
            outputs = [
                match.group(1) for match in re.finditer(output_pattern, source_code)
            ]

            components.append(
                AngularComponent(
                    selector=selector,
                    component_name=component_name,
                    template_url=template_url,
                    style_urls=style_urls,
                    inputs=inputs,
                    outputs=outputs,
                )
            )

        return components

    def _extract_hooks(self, source_code: str, component_name: str) -> list[str]:
        """Extract React hooks used in component."""
        hooks = []

        hook_names = [
            "useState",
            "useEffect",
            "useContext",
            "useReducer",
            "useCallback",
            "useMemo",
            "useRef",
            "useLayoutEffect",
            "useDebugValue",
            "useImperativeHandle",
        ]

        for hook in hook_names:
            pattern = rf"{hook}\s*\("
            if re.search(pattern, source_code):
                hooks.append(hook)

        return hooks

    def get_framework_metadata(
        self, source_code: str, file_path: str | None = None
    ) -> dict[str, Any]:
        """Get all framework-specific metadata.

        Args:
            source_code: JavaScript/TypeScript source code
            file_path: Optional file path for extension detection

        Returns:
            Dictionary with framework metadata

        Example:
            metadata = detector.get_framework_metadata(source_code)
            print(f"Framework: {metadata['framework_type']}")
        """
        framework = self.detect_from_source(source_code)

        metadata = {
            "framework_type": framework.value,
            "detected": framework != JsFrameworkType.NONE,
        }

        if framework == JsFrameworkType.REACT:
            metadata["components"] = self.extract_react_components(source_code)
        elif framework == JsFrameworkType.EXPRESS:
            metadata["routes"] = self.extract_express_routes(source_code)
        elif framework == JsFrameworkType.NESTJS:
            metadata["modules"] = self.extract_nest_modules(source_code)
            metadata["controllers"] = self.extract_nest_controllers(source_code)
        elif framework == JsFrameworkType.VUE:
            metadata["components"] = self.extract_vue_components(source_code)
        elif framework == JsFrameworkType.ANGULAR:
            metadata["components"] = self.extract_angular_components(source_code)
        elif framework == JsFrameworkType.NUXT:
            metadata["nuxt_usage"] = self._extract_nuxt_usage(source_code)
        elif framework == JsFrameworkType.GRAPHQL:
            metadata["operations"] = self._extract_graphql_operations(source_code)

        return metadata

    def _extract_nuxt_usage(self, source_code: str) -> list[str]:
        usage: list[str] = []
        for pattern in self.NUXT_INDICATORS["patterns"]:
            if pattern in source_code:
                usage.append(pattern)
        return usage

    def _extract_graphql_operations(self, source_code: str) -> list[str]:
        operations: list[str] = []
        gql_pattern = r"gql`([^`]+)`"
        for match in re.finditer(gql_pattern, source_code, re.DOTALL):
            snippet = match.group(1).strip().split("\n", 1)[0]
            if snippet:
                operations.append(snippet[:120])
        if "type Query" in source_code:
            operations.append("schema: Query")
        if "type Mutation" in source_code:
            operations.append("schema: Mutation")
        return operations
