from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol, cast

from codebase_rag.core import constants as cs
from codebase_rag.core.event_flow_identity import (
    build_event_flow_canonical_key,
    normalize_channel_name,
    normalize_event_name,
)
from codebase_rag.utils.path_utils import (
    build_runtime_event_path_fields,
    iter_repo_files,
)


class RuntimeGraphIngestorProtocol(Protocol):
    def ensure_node_batch(self, label: str, payload: dict[str, object]) -> None: ...

    def ensure_relationship_batch(
        self,
        source: tuple[str, str, str],
        relationship_type: str,
        target: tuple[str, str, str],
        payload: dict[str, object] | None = None,
    ) -> None: ...

    def flush_all(self) -> None: ...

    def fetch_all(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> list[object]: ...


class RuntimeEvidenceIngestor:
    _RUNTIME_DIRS = (
        "output/runtime",
        "output/dynamic",
        "output/profiler",
        "coverage",
        "logs",
    )
    _MAX_FILES = 80
    _MAX_EVENTS_PER_FILE = 40

    def __init__(self, repo_path: Path, project_name: str, ingestor: object) -> None:
        self.repo_path = repo_path.resolve()
        self.project_name = project_name
        self.ingestor = ingestor

    def ingest_available(self) -> dict[str, object]:
        if not all(
            hasattr(self.ingestor, attr)
            for attr in ("ensure_node_batch", "ensure_relationship_batch")
        ):
            return {"status": "skipped", "reason": "ingestor_missing_batch_api"}
        ingestor = self._ingestor_api()
        if ingestor is None:
            return {"status": "skipped", "reason": "ingestor_missing_batch_api"}

        runtime_files = self._discover_runtime_files()
        if not runtime_files:
            return {"status": "ok", "artifacts": 0, "events": 0}

        project_spec = (cs.NodeLabel.PROJECT, cs.KEY_NAME, self.project_name)
        event_count = 0

        for runtime_file in runtime_files:
            artifact_qn = self._artifact_qn(runtime_file)
            relative_path = runtime_file.relative_to(self.repo_path).as_posix()
            artifact_payload = {
                cs.KEY_QUALIFIED_NAME: artifact_qn,
                cs.KEY_NAME: relative_path,
                cs.KEY_PATH: relative_path,
                cs.KEY_REPO_REL_PATH: relative_path,
                cs.KEY_ABS_PATH: runtime_file.resolve().as_posix(),
                cs.KEY_PROJECT_NAME: self.project_name,
                "kind": self._artifact_kind(runtime_file),
                "source_parser": "runtime_evidence",
            }
            ingestor.ensure_node_batch(cs.NodeLabel.RUNTIME_ARTIFACT, artifact_payload)
            ingestor.ensure_relationship_batch(
                project_spec,
                cs.RelationshipType.CONTAINS,
                (
                    cs.NodeLabel.RUNTIME_ARTIFACT,
                    cs.KEY_QUALIFIED_NAME,
                    artifact_qn,
                ),
            )

            for event in self._extract_events(runtime_file):
                event_count += 1
                event_qn = f"{artifact_qn}.event.{event_count}"
                event_payload_data = dict(event)
                if not any(
                    str(event_payload_data.get(key, "")).strip()
                    for key in (cs.KEY_PATH, cs.KEY_REPO_REL_PATH, "file_path")
                ):
                    event_payload_data.update(
                        build_runtime_event_path_fields(relative_path, self.repo_path)
                    )
                event_payload = {
                    cs.KEY_QUALIFIED_NAME: event_qn,
                    cs.KEY_NAME: str(event.get("kind", "runtime_event")),
                    cs.KEY_PROJECT_NAME: self.project_name,
                    "source_parser": "runtime_evidence",
                    **event_payload_data,
                }
                ingestor.ensure_node_batch(cs.NodeLabel.RUNTIME_EVENT, event_payload)
                ingestor.ensure_relationship_batch(
                    (
                        cs.NodeLabel.RUNTIME_ARTIFACT,
                        cs.KEY_QUALIFIED_NAME,
                        artifact_qn,
                    ),
                    cs.RelationshipType.CONTAINS,
                    (
                        cs.NodeLabel.RUNTIME_EVENT,
                        cs.KEY_QUALIFIED_NAME,
                        event_qn,
                    ),
                )
                self._link_runtime_event(event_qn, event_payload_data)

        if hasattr(ingestor, "flush_all"):
            ingestor.flush_all()

        return {
            "status": "ok",
            "artifacts": len(runtime_files),
            "events": event_count,
        }

    def _discover_runtime_files(self) -> list[Path]:
        files: list[Path] = []
        for relative_dir in self._RUNTIME_DIRS:
            root = self.repo_path / relative_dir
            if not root.exists():
                continue
            for path in iter_repo_files(root):
                if len(files) >= self._MAX_FILES:
                    return files
                files.append(path)
        return files

    def _extract_events(self, runtime_file: Path) -> list[dict[str, object]]:
        lowered_name = runtime_file.name.lower()
        try:
            content = runtime_file.read_text(encoding=cs.ENCODING_UTF8, errors="ignore")
        except Exception:
            return []

        if lowered_name.endswith(".json"):
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                payload = {"raw_text": content}
            return self._events_from_json(payload, runtime_file)
        if lowered_name.endswith(".ndjson"):
            events: list[dict[str, object]] = []
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.extend(
                        self._events_from_json(json.loads(line), runtime_file)
                    )
                except json.JSONDecodeError:
                    continue
            return events[: self._MAX_EVENTS_PER_FILE]
        if lowered_name == "lcov.info":
            return self._events_from_lcov(content)
        return self._events_from_log(content, runtime_file)

    def _events_from_json(
        self,
        payload: object,
        runtime_file: Path,
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        if isinstance(payload, list):
            for item in cast(list[object], payload)[: self._MAX_EVENTS_PER_FILE]:
                if isinstance(item, dict):
                    normalized = self._normalize_event(cast(dict[str, object], item))
                    if normalized:
                        events.append(normalized)
            return events
        if isinstance(payload, dict):
            normalized = self._normalize_event(cast(dict[str, object], payload))
            if normalized:
                events.append(normalized)
            for key in ("events", "spans", "requests", "queries", "exceptions"):
                nested = cast(dict[str, object], payload).get(key)
                if isinstance(nested, list):
                    for item in cast(list[object], nested)[: self._MAX_EVENTS_PER_FILE]:
                        if isinstance(item, dict):
                            normalized_nested = self._normalize_event(
                                cast(dict[str, object], item)
                            )
                            if normalized_nested:
                                events.append(normalized_nested)
            return events[: self._MAX_EVENTS_PER_FILE]
        return [
            {
                "kind": self._artifact_kind(runtime_file),
                "path": runtime_file.relative_to(self.repo_path).as_posix(),
                "raw_text": str(payload)[:500],
            }
        ]

    def _events_from_lcov(self, content: str) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        current_file = ""
        covered = 0
        total = 0
        for line in content.splitlines():
            if line.startswith("SF:"):
                if current_file:
                    events.append(
                        {
                            "kind": "coverage",
                            "file_path": current_file,
                            "covered_lines": covered,
                            "total_lines": total,
                        }
                    )
                current_file = line.removeprefix("SF:").strip().replace("\\", "/")
                covered = 0
                total = 0
            elif line.startswith("DA:"):
                total += 1
                parts = line.removeprefix("DA:").split(",")
                if len(parts) >= 2 and parts[1].strip() != "0":
                    covered += 1
        if current_file:
            events.append(
                {
                    "kind": "coverage",
                    "file_path": current_file,
                    "covered_lines": covered,
                    "total_lines": total,
                }
            )
        return events[: self._MAX_EVENTS_PER_FILE]

    def _events_from_log(
        self, content: str, runtime_file: Path
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        http_pattern = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\s+(/[\w\-/{}:.?=&]+)")
        sql_pattern = re.compile(r"\b(select|insert|update|delete)\b", re.IGNORECASE)
        redis_pattern = re.compile(
            r"\b(redis|get|set|del|publish|subscribe)\b", re.IGNORECASE
        )
        event_name_pattern = re.compile(
            r"\b(?:event|event_name|message_type|subject)=([A-Za-z0-9_.:\-]+)",
            re.IGNORECASE,
        )
        channel_pattern = re.compile(
            r"\b(?:queue|queue_name|stream|stream_name|topic|topic_name|channel|channel_name)=([A-Za-z0-9_.:\-]+)",
            re.IGNORECASE,
        )
        dlq_pattern = re.compile(
            r"\b(?:dlq|dead_letter_queue|dead_letter_topic|retry_queue)=([A-Za-z0-9_.:\-]+)",
            re.IGNORECASE,
        )
        handler_pattern = re.compile(
            r"\b(?:handler|consumer|worker)=([A-Za-z0-9_.:\-]+)",
            re.IGNORECASE,
        )
        gql_pattern = re.compile(r"\b(query|mutation|subscription)\b", re.IGNORECASE)
        exception_pattern = re.compile(
            r"\b(exception|traceback|error)\b", re.IGNORECASE
        )

        for line in content.splitlines()[: self._MAX_EVENTS_PER_FILE]:
            normalized_line = line.strip()
            if not normalized_line:
                continue
            event: dict[str, object] | None = None
            if match := http_pattern.search(normalized_line):
                event = {
                    "kind": "http",
                    "method": match.group(1),
                    "route_path": match.group(2),
                }
            else:
                event_name_match = event_name_pattern.search(normalized_line)
                channel_match = channel_pattern.search(normalized_line)
                dlq_match = dlq_pattern.search(normalized_line)
                handler_match = handler_pattern.search(normalized_line)
                if event_name_match or channel_match:
                    event = {
                        "kind": "event_runtime",
                        "event_name": (
                            event_name_match.group(1) if event_name_match else ""
                        ),
                        "queue_name": channel_match.group(1) if channel_match else "",
                        "dlq_name": dlq_match.group(1) if dlq_match else "",
                        "handler_name": handler_match.group(1) if handler_match else "",
                        "stage": self._infer_runtime_stage(normalized_line),
                    }
            if event is None and sql_pattern.search(normalized_line):
                event = {"kind": "sql", "statement": normalized_line[:240]}
            elif event is None and redis_pattern.search(normalized_line):
                event = {"kind": "redis", "statement": normalized_line[:240]}
            elif gql_pattern.search(normalized_line):
                event = {"kind": "graphql", "statement": normalized_line[:240]}
            elif exception_pattern.search(normalized_line):
                event = {"kind": "exception", "message": normalized_line[:240]}
            if event is not None:
                event["path"] = runtime_file.relative_to(self.repo_path).as_posix()
                events.append(event)
        return events

    def _normalize_event(self, payload: dict[str, object]) -> dict[str, object] | None:
        kind = (
            str(
                payload.get("kind")
                or payload.get("type")
                or payload.get("event_type")
                or ""
            )
            .strip()
            .lower()
        )
        route_path = str(payload.get("route_path") or payload.get("url") or "").strip()
        sql = str(payload.get("sql") or payload.get("statement") or "").strip()
        redis = str(payload.get("redis") or payload.get("command") or "").strip()
        graphql = str(payload.get("graphql") or payload.get("operation") or "").strip()
        file_path = str(payload.get("file_path") or payload.get("path") or "").strip()
        event_name = str(
            payload.get("event_name")
            or payload.get("event")
            or payload.get("message_type")
            or payload.get("subject")
            or ""
        ).strip()
        channel_name = str(
            payload.get("queue_name")
            or payload.get("queue")
            or payload.get("stream_name")
            or payload.get("stream")
            or payload.get("topic_name")
            or payload.get("topic")
            or payload.get("channel_name")
            or payload.get("channel")
            or ""
        ).strip()
        dlq_name = str(
            payload.get("dlq_name")
            or payload.get("dlq")
            or payload.get("dead_letter_queue")
            or payload.get("dead_letter_topic")
            or payload.get("retry_queue")
            or ""
        ).strip()
        handler_name = str(
            payload.get("handler_name")
            or payload.get("handler")
            or payload.get("consumer")
            or payload.get("worker")
            or payload.get("symbol_name")
            or ""
        ).strip()
        stage = str(
            payload.get("stage")
            or payload.get("action")
            or payload.get("operation")
            or ""
        ).strip()
        retry_count = payload.get("retry_count") or payload.get("retries")
        if retry_count is None:
            retry_count = payload.get("attempt")

        if not kind:
            if route_path:
                kind = "http"
            elif sql:
                kind = "sql"
            elif redis:
                kind = "redis"
            elif graphql:
                kind = "graphql"
            elif event_name or channel_name or dlq_name:
                kind = "event_runtime"
            elif "exception" in payload or "error" in payload:
                kind = "exception"

        if not kind:
            return None

        normalized: dict[str, object] = {"kind": kind}
        if route_path:
            normalized["route_path"] = route_path
        if sql:
            normalized["statement"] = sql[:240]
        if redis:
            normalized["statement"] = redis[:240]
        if graphql:
            normalized["operation"] = graphql[:240]
        if file_path:
            normalized.update(
                build_runtime_event_path_fields(file_path, self.repo_path)
            )
        if event_name:
            normalized["event_name"] = event_name
            normalized["normalized_event_name"] = normalize_event_name(event_name)
        if channel_name:
            normalized["channel_name"] = channel_name
            normalized["normalized_channel_name"] = normalize_channel_name(channel_name)
        if dlq_name:
            normalized["dlq_name"] = dlq_name
            normalized["normalized_dlq_name"] = normalize_channel_name(dlq_name)
        if event_name or channel_name:
            normalized["canonical_key"] = build_event_flow_canonical_key(
                event_name=event_name,
                channel_name=channel_name,
                fallback_name=event_name or channel_name,
            )
        if handler_name:
            normalized["handler_name"] = handler_name
        if stage:
            normalized["stage"] = stage.lower()
        if isinstance(retry_count, int | float | str) and str(retry_count).strip():
            normalized["retry_count"] = retry_count
            normalized["has_retry"] = True
        duration = payload.get("duration_ms") or payload.get("duration")
        if isinstance(duration, int | float | str):
            normalized["duration_ms"] = duration
        message = (
            payload.get("message") or payload.get("error") or payload.get("exception")
        )
        if isinstance(message, str) and message.strip():
            normalized["message"] = message[:240]
        return normalized

    def _link_runtime_event(self, event_qn: str, event: dict[str, object]) -> None:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return
        kind = str(event.get("kind", "")).strip().lower()
        self._link_runtime_event_semantics(event_qn, event)
        canonical_file_path = str(event.get(cs.KEY_PATH) or "").strip()
        if canonical_file_path:
            ingestor.ensure_relationship_batch(
                (
                    cs.NodeLabel.RUNTIME_EVENT,
                    cs.KEY_QUALIFIED_NAME,
                    event_qn,
                ),
                cs.RelationshipType.OBSERVED_IN_RUNTIME,
                (cs.NodeLabel.FILE, cs.KEY_PATH, canonical_file_path),
                {"observation_kind": kind or "file"},
            )
        if kind == "coverage":
            if canonical_file_path:
                ingestor.ensure_relationship_batch(
                    (
                        cs.NodeLabel.RUNTIME_EVENT,
                        cs.KEY_QUALIFIED_NAME,
                        event_qn,
                    ),
                    cs.RelationshipType.COVERS_MODULE,
                    (cs.NodeLabel.FILE, cs.KEY_PATH, canonical_file_path),
                )
            return

        if kind == "http":
            route_path = str(event.get("route_path", "")).strip()
            if route_path:
                endpoint_qn = self._find_endpoint_qn(route_path)
                if endpoint_qn:
                    ingestor.ensure_relationship_batch(
                        (
                            cs.NodeLabel.RUNTIME_EVENT,
                            cs.KEY_QUALIFIED_NAME,
                            event_qn,
                        ),
                        cs.RelationshipType.OBSERVED_IN_RUNTIME,
                        (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                        {"observation_kind": "http"},
                    )
                    self._ensure_reverse_runtime_link(
                        (
                            cs.NodeLabel.ENDPOINT,
                            cs.KEY_QUALIFIED_NAME,
                            endpoint_qn,
                        ),
                        event_qn,
                        {"observation_kind": "http"},
                    )
            return

        if kind == "sql":
            datastore_qn = self._find_named_system_qn(
                cs.NodeLabel.DATA_STORE,
                preferred_engines=(
                    "postgres",
                    "mysql",
                    "sqlite",
                    "mongodb",
                    "memgraph",
                ),
            )
            if datastore_qn:
                ingestor.ensure_relationship_batch(
                    (
                        cs.NodeLabel.RUNTIME_EVENT,
                        cs.KEY_QUALIFIED_NAME,
                        event_qn,
                    ),
                    cs.RelationshipType.OBSERVED_IN_RUNTIME,
                    (cs.NodeLabel.DATA_STORE, cs.KEY_QUALIFIED_NAME, datastore_qn),
                )
                self._ensure_reverse_runtime_link(
                    (
                        cs.NodeLabel.DATA_STORE,
                        cs.KEY_QUALIFIED_NAME,
                        datastore_qn,
                    ),
                    event_qn,
                    {"observation_kind": "sql"},
                )
            return

        if kind == "redis":
            cache_qn = self._find_named_system_qn(
                cs.NodeLabel.CACHE_STORE,
                preferred_engines=("redis", "memcached"),
            )
            if cache_qn:
                ingestor.ensure_relationship_batch(
                    (
                        cs.NodeLabel.RUNTIME_EVENT,
                        cs.KEY_QUALIFIED_NAME,
                        event_qn,
                    ),
                    cs.RelationshipType.OBSERVED_IN_RUNTIME,
                    (cs.NodeLabel.CACHE_STORE, cs.KEY_QUALIFIED_NAME, cache_qn),
                )
                self._ensure_reverse_runtime_link(
                    (
                        cs.NodeLabel.CACHE_STORE,
                        cs.KEY_QUALIFIED_NAME,
                        cache_qn,
                    ),
                    event_qn,
                    {"observation_kind": "redis"},
                )
            return

        if kind == "graphql":
            graphql_qn = self._find_graphql_operation_qn(
                str(event.get("operation", "")).strip()
            )
            if graphql_qn:
                ingestor.ensure_relationship_batch(
                    (
                        cs.NodeLabel.RUNTIME_EVENT,
                        cs.KEY_QUALIFIED_NAME,
                        event_qn,
                    ),
                    cs.RelationshipType.OBSERVED_IN_RUNTIME,
                    (
                        cs.NodeLabel.GRAPHQL_OPERATION,
                        cs.KEY_QUALIFIED_NAME,
                        graphql_qn,
                    ),
                )
                self._ensure_reverse_runtime_link(
                    (
                        cs.NodeLabel.GRAPHQL_OPERATION,
                        cs.KEY_QUALIFIED_NAME,
                        graphql_qn,
                    ),
                    event_qn,
                    {"observation_kind": "graphql"},
                )
            return

        if kind == "exception":
            target_qn = self._find_service_qn()
            if target_qn:
                ingestor.ensure_relationship_batch(
                    (
                        cs.NodeLabel.RUNTIME_EVENT,
                        cs.KEY_QUALIFIED_NAME,
                        event_qn,
                    ),
                    cs.RelationshipType.RAISES_EXCEPTION,
                    (cs.NodeLabel.SERVICE, cs.KEY_QUALIFIED_NAME, target_qn),
                )

    def _find_endpoint_qn(self, route_path: str) -> str | None:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return None
        rows = ingestor.fetch_all(
            """
            MATCH (e:Endpoint {project_name: $project_name})
            WHERE coalesce(e.route_path, '') = $route_path
            RETURN coalesce(e.qualified_name, '') AS qualified_name
            LIMIT 1
            """,
            {
                cs.KEY_PROJECT_NAME: self.project_name,
                "route_path": route_path,
            },
        )
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            return None
        candidate = str(row.get("qualified_name", "")).strip()
        return candidate or None

    def _find_named_system_qn(
        self,
        label: str,
        *,
        preferred_engines: tuple[str, ...],
    ) -> str | None:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return None
        rows = ingestor.fetch_all(
            f"""
            MATCH (n:{label} {{project_name: $project_name}})
            RETURN
              coalesce(n.qualified_name, '') AS qualified_name,
              coalesce(n.engine, '') AS engine
            LIMIT 20
            """,
            {cs.KEY_PROJECT_NAME: self.project_name},
        )
        for preferred_engine in preferred_engines:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_dict = cast(dict[str, object], row)
                engine = str(row_dict.get("engine", "")).strip().lower()
                candidate = str(row_dict.get("qualified_name", "")).strip()
                if candidate and preferred_engine in engine:
                    return candidate
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_dict = cast(dict[str, object], row)
            candidate = str(row_dict.get("qualified_name", "")).strip()
            if candidate:
                return candidate
        return None

    def _find_graphql_operation_qn(self, operation_name: str) -> str | None:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return None
        normalized_name = operation_name.strip()
        rows = ingestor.fetch_all(
            """
            MATCH (g:GraphQLOperation {project_name: $project_name})
            WHERE $operation_name = ''
               OR toLower(coalesce(g.name, '')) = toLower($operation_name)
               OR toLower(coalesce(g.qualified_name, '')) CONTAINS toLower($operation_name)
            RETURN coalesce(g.qualified_name, '') AS qualified_name
            LIMIT 1
            """,
            {
                cs.KEY_PROJECT_NAME: self.project_name,
                "operation_name": normalized_name,
            },
        )
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            return None
        candidate = str(row.get("qualified_name", "")).strip()
        return candidate or None

    def _link_runtime_event_semantics(
        self, event_qn: str, event: dict[str, object]
    ) -> None:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return

        event_name = str(event.get("event_name", "")).strip()
        channel_name = str(
            event.get("channel_name") or event.get("queue_name") or ""
        ).strip()
        dlq_name = str(event.get("dlq_name", "")).strip()
        handler_name = str(event.get("handler_name", "")).strip()
        stage = str(event.get("stage", "")).strip().lower()

        event_flow_qn = self._find_event_flow_qn(
            event_name=event_name,
            channel_name=channel_name,
        )
        if event_flow_qn:
            payload = {
                "observation_kind": stage or "event_runtime",
                "event_name": event_name or None,
                "channel_name": channel_name or None,
                "canonical_key": build_event_flow_canonical_key(
                    event_name=event_name,
                    channel_name=channel_name,
                    fallback_name=event_name or channel_name or event_qn,
                ),
            }
            ingestor.ensure_relationship_batch(
                (
                    cs.NodeLabel.RUNTIME_EVENT,
                    cs.KEY_QUALIFIED_NAME,
                    event_qn,
                ),
                cs.RelationshipType.OBSERVED_IN_RUNTIME,
                (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, event_flow_qn),
                payload,
            )
            self._ensure_reverse_runtime_link(
                (
                    cs.NodeLabel.EVENT_FLOW,
                    cs.KEY_QUALIFIED_NAME,
                    event_flow_qn,
                ),
                event_qn,
                payload,
            )

        for queue_name, role in ((channel_name, "primary"), (dlq_name, "dlq")):
            if not queue_name:
                continue
            queue_qn = self._find_queue_qn(queue_name)
            if not queue_qn:
                continue
            payload = {
                "observation_kind": stage or "event_runtime",
                "queue_name": queue_name,
                "queue_role": role,
            }
            ingestor.ensure_relationship_batch(
                (
                    cs.NodeLabel.RUNTIME_EVENT,
                    cs.KEY_QUALIFIED_NAME,
                    event_qn,
                ),
                cs.RelationshipType.OBSERVED_IN_RUNTIME,
                (cs.NodeLabel.QUEUE, cs.KEY_QUALIFIED_NAME, queue_qn),
                payload,
            )
            self._ensure_reverse_runtime_link(
                (
                    cs.NodeLabel.QUEUE,
                    cs.KEY_QUALIFIED_NAME,
                    queue_qn,
                ),
                event_qn,
                payload,
            )

        handler_spec = self._find_handler_spec(handler_name)
        if handler_spec is not None:
            payload = {
                "observation_kind": stage or "event_runtime",
                "handler_name": handler_name,
            }
            ingestor.ensure_relationship_batch(
                (
                    cs.NodeLabel.RUNTIME_EVENT,
                    cs.KEY_QUALIFIED_NAME,
                    event_qn,
                ),
                cs.RelationshipType.OBSERVED_IN_RUNTIME,
                handler_spec,
                payload,
            )
            self._ensure_reverse_runtime_link(handler_spec, event_qn, payload)

    def _ensure_reverse_runtime_link(
        self,
        source_spec: tuple[str, str, str],
        event_qn: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return
        ingestor.ensure_relationship_batch(
            source_spec,
            cs.RelationshipType.OBSERVED_IN_RUNTIME,
            (
                cs.NodeLabel.RUNTIME_EVENT,
                cs.KEY_QUALIFIED_NAME,
                event_qn,
            ),
            payload,
        )

    def _find_event_flow_qn(
        self,
        *,
        event_name: str,
        channel_name: str,
    ) -> str | None:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return None
        rows = ingestor.fetch_all(
            """
            MATCH (e:EventFlow {project_name: $project_name})
            RETURN
              coalesce(e.qualified_name, '') AS qualified_name,
              coalesce(e.canonical_key, '') AS canonical_key,
              coalesce(e.event_name, '') AS event_name,
              coalesce(e.channel_name, '') AS channel_name
            LIMIT 100
            """,
            {cs.KEY_PROJECT_NAME: self.project_name},
        )
        expected_key = build_event_flow_canonical_key(
            event_name=event_name,
            channel_name=channel_name,
            fallback_name=event_name or channel_name,
        )
        normalized_event = normalize_event_name(event_name)
        normalized_channel = normalize_channel_name(channel_name)
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_dict = cast(dict[str, object], row)
            candidate_qn = str(row_dict.get("qualified_name", "")).strip()
            if not candidate_qn:
                continue
            candidate_key = str(row_dict.get("canonical_key", "")).strip().lower()
            candidate_event = normalize_event_name(str(row_dict.get("event_name", "")))
            candidate_channel = normalize_channel_name(
                str(row_dict.get("channel_name", ""))
            )
            if expected_key and candidate_key == expected_key:
                return candidate_qn
            if normalized_event and candidate_event == normalized_event:
                if not normalized_channel or candidate_channel == normalized_channel:
                    return candidate_qn
            if normalized_channel and not normalized_event:
                if candidate_channel == normalized_channel:
                    return candidate_qn
        return None

    def _find_queue_qn(self, queue_name: str) -> str | None:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return None
        rows = ingestor.fetch_all(
            """
            MATCH (q:Queue {project_name: $project_name})
            RETURN
              coalesce(q.qualified_name, '') AS qualified_name,
              coalesce(q.queue_name, q.name, '') AS queue_name
            LIMIT 100
            """,
            {cs.KEY_PROJECT_NAME: self.project_name},
        )
        expected = normalize_channel_name(queue_name)
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_dict = cast(dict[str, object], row)
            candidate_qn = str(row_dict.get("qualified_name", "")).strip()
            candidate_name = normalize_channel_name(str(row_dict.get("queue_name", "")))
            if candidate_qn and candidate_name == expected:
                return candidate_qn
        return None

    def _find_handler_spec(self, handler_name: str) -> tuple[str, str, str] | None:
        normalized = handler_name.strip()
        if not normalized:
            return None
        simple_name = normalized.split(".")[-1].lower()
        ingestor = self._ingestor_api()
        if ingestor is None:
            return None
        rows = ingestor.fetch_all(
            """
            MATCH (n)
            WHERE coalesce(n.project_name, $project_name) = $project_name
              AND (n:Function OR n:Method)
            RETURN
              labels(n) AS labels,
              coalesce(n.qualified_name, '') AS qualified_name,
              coalesce(n.name, '') AS name
            LIMIT 500
            """,
            {cs.KEY_PROJECT_NAME: self.project_name},
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_dict = cast(dict[str, object], row)
            qualified_name = str(row_dict.get("qualified_name", "")).strip()
            name = str(row_dict.get("name", "")).strip().lower()
            labels = cast(list[str], row_dict.get("labels", []))
            if not qualified_name or not labels:
                continue
            if (
                qualified_name.lower().endswith(normalized.lower())
                or name == simple_name
            ):
                return (labels[0], cs.KEY_QUALIFIED_NAME, qualified_name)
        return None

    def _find_service_qn(self) -> str | None:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return None
        rows = ingestor.fetch_all(
            """
            MATCH (s:Service {project_name: $project_name})
            RETURN coalesce(s.qualified_name, '') AS qualified_name
            LIMIT 1
            """,
            {cs.KEY_PROJECT_NAME: self.project_name},
        )
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            return None
        candidate = str(row.get("qualified_name", "")).strip()
        return candidate or None

    def _ingestor_api(self) -> RuntimeGraphIngestorProtocol | None:
        required = ("ensure_node_batch", "ensure_relationship_batch", "fetch_all")
        if not all(hasattr(self.ingestor, attr) for attr in required):
            return None
        return cast(RuntimeGraphIngestorProtocol, self.ingestor)

    def _artifact_qn(self, runtime_file: Path) -> str:
        relative = runtime_file.relative_to(self.repo_path).as_posix()
        normalized = relative.replace("/", ".").replace(":", ".")
        return f"{self.project_name}.runtime.{normalized}"

    def _artifact_kind(self, runtime_file: Path) -> str:
        lowered = runtime_file.name.lower()
        if "coverage" in lowered or lowered == "lcov.info":
            return "coverage"
        if "profile" in lowered:
            return "profile"
        if "trace" in lowered or "span" in lowered:
            return "trace"
        if "log" in lowered:
            return "log"
        return runtime_file.suffix.lower().removeprefix(".") or "runtime"

    @staticmethod
    def _infer_runtime_stage(text: str) -> str:
        lowered = text.lower()
        if any(token in lowered for token in ("replay", "redrive", "requeue")):
            return "replay"
        if "dlq" in lowered or "dead-letter" in lowered or "dead_letter" in lowered:
            return "dlq"
        if any(token in lowered for token in ("consume", "consumer", "subscribe")):
            return "consume"
        if any(
            token in lowered for token in ("publish", "emit", "dispatch", "enqueue")
        ):
            return "publish"
        return "event_runtime"
