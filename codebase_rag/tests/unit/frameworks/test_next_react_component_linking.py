from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_nodes, get_relationships, run_updater


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _relationship_args(mock_ingestor: MagicMock, rel_type: str) -> list[tuple]:
    return [call.args for call in get_relationships(mock_ingestor, rel_type)]


def _node_props(mock_ingestor: MagicMock, label: str) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], call[0][1]) for call in get_nodes(mock_ingestor, label)
    ]


def _endpoint_qn_by_route(
    mock_ingestor: MagicMock,
    route_path: str,
    next_kind: str | None = None,
) -> str | None:
    for props in _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT):
        if props.get(cs.KEY_ROUTE_PATH) != route_path:
            continue
        if next_kind is not None and props.get("next_kind") != next_kind:
            continue
        return cast(str, props.get(cs.KEY_QUALIFIED_NAME))
    return None


def test_links_react_components_and_next_page_endpoint(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "next_component_graph"
    project.mkdir()

    _write(
        project / "app/page.tsx",
        """import Button from "../components/Button";
import * as Icons from "../components/Icons";
import { useState } from "react";
import { useFeature } from "../hooks/useFeature";

const LocalBadge = ({ value }: { value: string }) => <span>{value}</span>;

export default function Page({ title, count = 0 }: { title: string; count?: number }) {
    const [open, setOpen] = useState(false);
    const feature = useFeature();
    return (
        <main>
            <Button title={title} count={count} feature={feature} onClick={() => setOpen(!open)} />
            <LocalBadge value={title} />
            <Icons.Close />
        </main>
    );
}
""",
    )
    _write(
        project / "components/Button.tsx",
        """export default function Button() {
    return <button>save</button>;
}
""",
    )
    _write(
        project / "components/Icons.tsx",
        """export function Close() {
    return <svg />;
}
""",
    )
    _write(
        project / "hooks/useFeature.ts",
        """export function useFeature() {
    return "enabled";
}
""",
    )

    run_updater(project, mock_ingestor, skip_if_missing="typescript")

    component_qns = {
        cast(str, props[cs.KEY_QUALIFIED_NAME])
        for props in _node_props(mock_ingestor, cs.NodeLabel.COMPONENT)
    }
    assert "next_component_graph.app.page.Page" in component_qns
    assert "next_component_graph.app.page.LocalBadge" in component_qns
    assert "next_component_graph.components.Button.Button" in component_qns
    assert "next_component_graph.components.Icons.Close" in component_qns

    parameter_props = _node_props(mock_ingestor, cs.NodeLabel.PARAMETER)
    assert any(
        props.get("component_qn") == "next_component_graph.app.page.Page"
        and props.get("prop_path") == "title"
        for props in parameter_props
    )
    assert any(
        props.get("component_qn") == "next_component_graph.app.page.Page"
        and props.get("prop_path") == "count"
        for props in parameter_props
    )
    assert any(
        props.get("component_qn") == "next_component_graph.app.page.LocalBadge"
        and props.get("prop_path") == "value"
        for props in parameter_props
    )

    has_parameter = _relationship_args(mock_ingestor, cs.RelationshipType.HAS_PARAMETER)
    assert any(
        rel[0]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_component_graph.app.page.Page",
        )
        and cast(dict[str, object], rel[3]).get("parameter_name") == "title"
        for rel in has_parameter
    )

    uses_component = _relationship_args(
        mock_ingestor, cs.RelationshipType.USES_COMPONENT
    )
    source = (
        cs.NodeLabel.COMPONENT,
        cs.KEY_QUALIFIED_NAME,
        "next_component_graph.app.page.Page",
    )
    assert any(
        rel[0] == source
        and rel[2]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_component_graph.components.Button.Button",
        )
        and "title" in cast(dict[str, object], rel[3]).get("props_passed", [])
        and "feature" in cast(dict[str, object], rel[3]).get("props_passed", [])
        and "title:title" in cast(dict[str, object], rel[3]).get("prop_bindings", [])
        for rel in uses_component
    )
    assert any(
        rel[0] == source
        and rel[2]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_component_graph.app.page.LocalBadge",
        )
        and "value:title" in cast(dict[str, object], rel[3]).get("prop_bindings", [])
        for rel in uses_component
    )
    assert any(
        rel[0] == source
        and rel[2]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_component_graph.components.Icons.Close",
        )
        for rel in uses_component
    )

    calls = _relationship_args(mock_ingestor, cs.RelationshipType.CALLS)
    assert any(
        rel[0]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_component_graph.app.page.Page",
        )
        and cast(dict[str, object], rel[3]).get(cs.KEY_HOOK_NAME) == "useState"
        for rel in calls
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_component_graph.app.page.Page",
        )
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "next_component_graph.hooks.useFeature.useFeature",
        )
        and cast(dict[str, object], rel[3]).get(cs.KEY_HOOK_NAME) == "useFeature"
        for rel in calls
    )

    has_endpoint = _relationship_args(mock_ingestor, cs.RelationshipType.HAS_ENDPOINT)
    root_endpoint_qn = _endpoint_qn_by_route(mock_ingestor, "/", "page")
    assert root_endpoint_qn is not None
    assert any(
        rel[0]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_component_graph.app.page.Page",
        )
        and rel[2]
        == (
            cs.NodeLabel.ENDPOINT,
            cs.KEY_QUALIFIED_NAME,
            root_endpoint_qn,
        )
        for rel in has_endpoint
    )


def test_materializes_next_layout_and_route_endpoints(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "next_route_graph"
    project.mkdir()

    _write(
        project / "app/layout.tsx",
        """export default function RootLayout(props: { children: React.ReactNode }) {
    return <html><body>{props.children}</body></html>;
}
""",
    )
    _write(
        project / "app/dashboard/page.tsx",
        """export default function DashboardPage() {
    return <section>dashboard</section>;
}
""",
    )
    _write(
        project / "app/api/posts/[id]/route.ts",
        """export async function GET() {
    return Response.json({ ok: true });
}

export async function POST() {
    return Response.json({ ok: true });
}
""",
    )

    run_updater(project, mock_ingestor, skip_if_missing="typescript")

    endpoint_props = _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT)
    route_paths = {
        (
            cast(str, props.get(cs.KEY_HTTP_METHOD)),
            cast(str, props.get(cs.KEY_ROUTE_PATH)),
            cast(str, props.get(cs.KEY_FRAMEWORK)),
            cast(str, props.get("next_kind")),
        )
        for props in endpoint_props
    }
    assert ("GET", "/", "next", "layout") in route_paths
    assert ("GET", "/dashboard", "next", "page") in route_paths
    assert ("GET", "/api/posts/{param}", "next", "route") in route_paths
    assert ("POST", "/api/posts/{param}", "next", "route") in route_paths

    component_qns = {
        cast(str, props[cs.KEY_QUALIFIED_NAME])
        for props in _node_props(mock_ingestor, cs.NodeLabel.COMPONENT)
    }
    assert "next_route_graph.app.layout.RootLayout" in component_qns
    assert "next_route_graph.app.dashboard.page.DashboardPage" in component_qns

    has_endpoint = _relationship_args(mock_ingestor, cs.RelationshipType.HAS_ENDPOINT)
    layout_endpoint_qn = _endpoint_qn_by_route(mock_ingestor, "/", "layout")
    assert layout_endpoint_qn is not None
    assert any(
        rel[0]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_route_graph.app.layout.RootLayout",
        )
        and rel[2]
        == (
            cs.NodeLabel.ENDPOINT,
            cs.KEY_QUALIFIED_NAME,
            layout_endpoint_qn,
        )
        for rel in has_endpoint
    )


def test_keeps_distinct_next_endpoints_for_same_route_path(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "next_endpoint_ids"
    project.mkdir()

    _write(
        project / "app/layout.tsx",
        """export default function RootLayout(props: { children: React.ReactNode }) {
    return <html><body>{props.children}</body></html>;
}
""",
    )
    _write(
        project / "app/page.tsx",
        """export default function HomePage() {
    return <main>home</main>;
}
""",
    )

    run_updater(project, mock_ingestor, skip_if_missing="typescript")

    root_endpoints = [
        props
        for props in _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT)
        if props.get(cs.KEY_ROUTE_PATH) == "/"
    ]
    assert len(root_endpoints) == 2
    assert {cast(str, props.get("next_kind")) for props in root_endpoints} == {
        "layout",
        "page",
    }


def test_resolves_tsconfig_aliases_and_links_component_requests(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "next_alias_graph"
    project.mkdir()

    _write(
        project / "tsconfig.json",
        """{
  \"compilerOptions\": {
    \"baseUrl\": \\".\\",
    \"paths\": {
      \"@/*\": [\"./src/*\"]
    }
  }
}
""",
    )
    _write(
        project / "src/app/page.tsx",
        """import { Button } from \"@/components/ui/Button\";

export default function Page() {
    async function submit() {
        await fetch('/api/customers', { method: 'POST' });
    }

    return <Button onClick={submit} />;
}
""",
    )
    _write(
        project / "src/components/ui/Button.tsx",
        """export function Button() {
    return <button>save</button>;
}
""",
    )

    run_updater(project, mock_ingestor, skip_if_missing="typescript")

    component_qns = {
        cast(str, props[cs.KEY_QUALIFIED_NAME])
        for props in _node_props(mock_ingestor, cs.NodeLabel.COMPONENT)
    }
    assert "next_alias_graph.src.components.ui.Button.Button" in component_qns
    assert "next_alias_graph.src.app.page.component.Button" not in component_qns

    endpoint_qn = None
    for props in _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT):
        if (
            props.get(cs.KEY_ROUTE_PATH) == "/api/customers"
            and props.get(cs.KEY_HTTP_METHOD) == "POST"
        ):
            endpoint_qn = cast(str, props.get(cs.KEY_QUALIFIED_NAME))
            break
    assert endpoint_qn is not None

    requests_endpoint = _relationship_args(
        mock_ingestor, cs.RelationshipType.REQUESTS_ENDPOINT
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_alias_graph.src.app.page.Page",
        )
        and rel[2]
        == (
            cs.NodeLabel.ENDPOINT,
            cs.KEY_QUALIFIED_NAME,
            endpoint_qn,
        )
        and cast(dict[str, object], rel[3]).get(cs.KEY_ROUTE_PATH) == "/api/customers"
        for rel in requests_endpoint
    )


def test_links_tanstack_query_refs_and_generated_client_requests(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "next_query_client_graph"
    project.mkdir()

    _write(
        project / "src/app/customers/page.tsx",
        """import { useQuery } from '@tanstack/react-query';
import { listCustomers } from '@/lib/generated/client';

export default function CustomersPage() {
    const query = useQuery({
        queryKey: ['customers'],
        queryFn: listCustomers,
    });

    return <section>{query.data?.length ?? 0}</section>;
}
""",
    )
    _write(
        project / "src/lib/generated/client.ts",
        """const apiClient = {
    get: async (path: string) => fetch(path),
};

export async function listCustomers() {
    return apiClient.get('/api/customers');
}
""",
    )

    run_updater(project, mock_ingestor, skip_if_missing="typescript")

    component_props = _node_props(mock_ingestor, cs.NodeLabel.COMPONENT)
    page_component = next(
        props
        for props in component_props
        if props.get(cs.KEY_QUALIFIED_NAME)
        == "next_query_client_graph.src.app.customers.page.CustomersPage"
    )
    assert page_component.get("next_kind") == "page"
    assert page_component.get("next_route_path") == "/customers"

    endpoint_qn = None
    for props in _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT):
        if (
            props.get(cs.KEY_ROUTE_PATH) == "/api/customers"
            and props.get(cs.KEY_HTTP_METHOD) == "GET"
        ):
            endpoint_qn = cast(str, props.get(cs.KEY_QUALIFIED_NAME))
            break
    assert endpoint_qn is not None

    requests_endpoint = _relationship_args(
        mock_ingestor, cs.RelationshipType.REQUESTS_ENDPOINT
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "next_query_client_graph.src.lib.generated.client.listCustomers",
        )
        and rel[2]
        == (
            cs.NodeLabel.ENDPOINT,
            cs.KEY_QUALIFIED_NAME,
            endpoint_qn,
        )
        and cast(dict[str, object], rel[3]).get("client_kind") == "http_client_member"
        for rel in requests_endpoint
    )

    calls = _relationship_args(mock_ingestor, cs.RelationshipType.CALLS)
    assert any(
        rel[0]
        == (
            cs.NodeLabel.COMPONENT,
            cs.KEY_QUALIFIED_NAME,
            "next_query_client_graph.src.app.customers.page.CustomersPage",
        )
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "next_query_client_graph.src.lib.generated.client.listCustomers",
        )
        and cast(dict[str, object], rel[3]).get(cs.KEY_RELATION_TYPE)
        == "tanstack_query_fn_reference"
        for rel in calls
    )


def test_ignores_header_and_search_param_accessors_as_http_requests(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "next_request_accessor_guard"
    project.mkdir()

    _write(
        project / "src/lib/client.ts",
        """const apiClient = {
    get: async (path: string) => fetch(path),
};

export async function readRequestMetadata(url: string, response: Response) {
    const customers = await apiClient.get('/api/customers');
    const contentType = response.headers.get('Content-Type') || 'application/json';
    const parsed = new URL(url, 'https://example.test');
    const afterMessageId = parsed.searchParams.get('after_message_id');
    const templateVersionId = parsed.searchParams.get('template_version_id');
    return { customers, contentType, afterMessageId, templateVersionId };
}
""",
    )

    run_updater(project, mock_ingestor, skip_if_missing="typescript")

    endpoint_props = _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT)
    route_paths = {cast(str, props.get(cs.KEY_ROUTE_PATH)) for props in endpoint_props}

    assert "/api/customers" in route_paths
    assert "/Content-Type" not in route_paths
    assert "/after_message_id" not in route_paths
    assert "/template_version_id" not in route_paths

    requests_endpoint = _relationship_args(
        mock_ingestor, cs.RelationshipType.REQUESTS_ENDPOINT
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "next_request_accessor_guard.src.lib.client.readRequestMetadata",
        )
        and cast(dict[str, object], rel[3]).get(cs.KEY_ROUTE_PATH) == "/api/customers"
        for rel in requests_endpoint
    )
    assert not any(
        cast(dict[str, object], rel[3]).get(cs.KEY_ROUTE_PATH)
        in {"/Content-Type", "/after_message_id", "/template_version_id"}
        for rel in requests_endpoint
    )


def test_framework_linker_ignores_non_route_like_request_strings(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "framework_linker_request_guard"
    project.mkdir()

    _write(
        project / "src/lib/client.ts",
        """const apiBaseUrl = 'https://api.example.test';

export async function requestData() {
    await fetch('/api/customers', { method: 'POST' });
    await fetch('Content-Type');
    await axios.get('X-Request-ID');
    const apiClient = { get: (path: string) => fetch(path) };
    await apiClient.get('/api/orders');
    await apiClient.get('after_message_id');
}
""",
    )

    run_updater(project, mock_ingestor, skip_if_missing="typescript")

    route_paths = {
        cast(str, props.get(cs.KEY_ROUTE_PATH))
        for props in _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT)
    }

    assert "/api/customers" in route_paths
    assert "/api/orders" in route_paths
    assert "/Content-Type" not in route_paths
    assert "/X-Request-ID" not in route_paths
    assert "/after_message_id" not in route_paths
