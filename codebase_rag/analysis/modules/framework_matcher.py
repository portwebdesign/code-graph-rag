from __future__ import annotations

from typing import Any, cast

from ...services.protocols import QueryProtocol
from .base_module import AnalysisContext, AnalysisModule


class FrameworkMatcherModule(AnalysisModule):
    def get_name(self) -> str:
        return "framework_metadata"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if not hasattr(context.runner.ingestor, "fetch_all"):
            return {}

        self.ingestor = cast(QueryProtocol, context.runner.ingestor)

        django = self._match_django(context)
        flask = self._match_flask(context)
        fastapi = self._match_fastapi(context)
        laravel = self._match_endpoint_framework("laravel")
        spring = self._match_endpoint_framework("spring")
        aspnet = self._match_endpoint_framework("aspnet")
        express = self._match_endpoint_framework("express")
        nestjs = self._match_endpoint_framework("nestjs")
        rails = self._match_endpoint_framework("rails")
        go_web = self._match_endpoint_framework("go_web")

        return {
            "django": django,
            "flask": flask,
            "fastapi": fastapi,
            "laravel": laravel,
            "spring": spring,
            "aspnet": aspnet,
            "express": express,
            "nestjs": nestjs,
            "rails": rails,
            "go_web": go_web,
            "total_components": (
                django.get("total_components", 0)
                + flask.get("total_components", 0)
                + fastapi.get("total_components", 0)
                + laravel.get("total_components", 0)
                + spring.get("total_components", 0)
                + aspnet.get("total_components", 0)
                + express.get("total_components", 0)
                + nestjs.get("total_components", 0)
                + rails.get("total_components", 0)
                + go_web.get("total_components", 0)
            ),
        }

    def _match_django(self, context: AnalysisContext) -> dict[str, Any]:
        models_query = """
        MATCH (c:Class)
        WHERE toLower(c.path) ENDS WITH 'models.py'
           OR toLower(c.qualified_name) CONTAINS '.models.'
        RETURN c.qualified_name AS qualified_name, c.path AS path
        """

        views_query = """
        MATCH (f)
        WHERE (f:Function OR f:Method)
          AND (
                toLower(f.path) ENDS WITH 'views.py'
             OR toLower(f.qualified_name) CONTAINS '.views.'
          )
        RETURN f.qualified_name AS qualified_name, f.path AS path
        """

        urls_query = """
        MATCH (f)
        WHERE toLower(f.path) ENDS WITH 'urls.py'
           OR toLower(f.qualified_name) CONTAINS 'urlpatterns'
        RETURN f.qualified_name AS qualified_name, f.path AS path
        """

        middleware_query = """
        MATCH (c:Class)
        WHERE toLower(c.name) CONTAINS 'middleware'
        RETURN c.qualified_name AS qualified_name, c.path AS path
        """

        models = self.ingestor.fetch_all(models_query, {})
        views = self.ingestor.fetch_all(views_query, {})
        urls = self.ingestor.fetch_all(urls_query, {})
        middleware = self.ingestor.fetch_all(middleware_query, {})

        return {
            "models": models,
            "views": views,
            "urls": urls,
            "middleware": middleware,
            "total_components": len(models) + len(views) + len(urls) + len(middleware),
        }

    def _match_flask(self, context: AnalysisContext) -> dict[str, Any]:
        routes_query = """
        MATCH (f)
        WHERE (f:Function OR f:Method)
          AND any(d IN f.decorators
                WHERE toLower(d) CONTAINS '@app.route'
                   OR toLower(d) CONTAINS '@bp.route'
          )
        RETURN f.qualified_name AS qualified_name,
               f.path AS path,
               f.decorators AS decorators
        """

        blueprints_query = """
        MATCH (v)
        WHERE toLower(v.name) CONTAINS 'blueprint'
        RETURN v.qualified_name AS qualified_name, v.path AS path
        """

        hooks_query = """
        MATCH (f)
        WHERE (f:Function OR f:Method)
          AND f.name IN ['before_request', 'after_request', 'teardown_request']
        RETURN f.qualified_name AS qualified_name, f.path AS path
        """

        routes = self.ingestor.fetch_all(routes_query, {})
        blueprints = self.ingestor.fetch_all(blueprints_query, {})
        hooks = self.ingestor.fetch_all(hooks_query, {})

        return {
            "routes": routes,
            "blueprints": blueprints,
            "hooks": hooks,
            "total_components": len(routes) + len(blueprints) + len(hooks),
        }

    def _match_fastapi(self, context: AnalysisContext) -> dict[str, Any]:
        routes_query = """
        MATCH (f)
        WHERE (f:Function OR f:Method)
          AND any(d IN f.decorators
                WHERE toLower(d) CONTAINS '@app.get'
                   OR toLower(d) CONTAINS '@app.post'
                   OR toLower(d) CONTAINS '@app.put'
                   OR toLower(d) CONTAINS '@app.delete'
                   OR toLower(d) CONTAINS '@router.get'
                   OR toLower(d) CONTAINS '@router.post'
                   OR toLower(d) CONTAINS '@router.put'
                   OR toLower(d) CONTAINS '@router.delete'
          )
        RETURN f.qualified_name AS qualified_name,
               f.path AS path,
               f.decorators AS decorators
        """

        dependencies_query = """
        MATCH (f)
        WHERE (f:Function OR f:Method)
          AND any(d IN f.decorators WHERE toLower(d) CONTAINS 'depends')
        RETURN f.qualified_name AS qualified_name, f.path AS path
        """

        middleware_query = """
        MATCH (f)
        WHERE (f:Function OR f:Method)
          AND any(d IN f.decorators WHERE toLower(d) CONTAINS '@app.middleware')
        RETURN f.qualified_name AS qualified_name, f.path AS path
        """

        routes = self.ingestor.fetch_all(routes_query, {})
        dependencies = self.ingestor.fetch_all(dependencies_query, {})
        middleware = self.ingestor.fetch_all(middleware_query, {})

        return {
            "routes": routes,
            "dependencies": dependencies,
            "middleware": middleware,
            "total_components": len(routes) + len(dependencies) + len(middleware),
        }

    def _match_endpoint_framework(self, framework_name: str) -> dict[str, Any]:
        endpoints_query = """
        MATCH (e:Endpoint)
        WHERE toLower(coalesce(e.framework, '')) = $framework
        RETURN e.qualified_name AS qualified_name,
               e.path AS path,
               e.http_method AS method,
               e.route_path AS route
        """
        endpoints = self.ingestor.fetch_all(
            endpoints_query, {"framework": framework_name}
        )
        return {
            "endpoints": endpoints,
            "total_components": len(endpoints),
        }
