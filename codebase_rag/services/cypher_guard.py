from __future__ import annotations

import re
from dataclasses import dataclass, field

from codebase_rag.core import constants as cs


@dataclass(frozen=True)
class CypherGuardResult:
    query: str
    valid: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class CypherGuard:
    _WRITE_PATTERN = re.compile(
        r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b",
        re.IGNORECASE,
    )
    _READ_PREFIX = re.compile(
        r"^\s*(MATCH|OPTIONAL MATCH|WITH|CALL|UNWIND|RETURN|EXPLAIN|PROFILE)\b",
        re.IGNORECASE,
    )
    _RETURN_PATTERN = re.compile(r"\bRETURN\b", re.IGNORECASE)
    _LIMIT_PATTERN = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)
    _PROJECT_SCOPE_PATTERN = re.compile(r"\bproject_name\b", re.IGNORECASE)
    _SCOPABLE_LABEL_PATTERN = re.compile(
        r"\((?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*:(?P<label>Module|Class|Function|Method|File|Endpoint|Component)\s*\)",
        re.IGNORECASE,
    )

    def rewrite_and_validate(
        self,
        query: str,
        *,
        require_project_scope: bool = True,
        default_limit: int = 200,
    ) -> CypherGuardResult:
        cleaned = " ".join(str(query or "").strip().split())
        warnings: list[str] = []
        errors: list[str] = []

        if not cleaned:
            return CypherGuardResult(
                query="",
                valid=False,
                errors=["empty_query"],
                metadata={"project_scope_present": False, "limit_present": False},
            )

        cleaned = cleaned.replace(cs.CYPHER_BACKTICK, "").strip()
        if cleaned.lower().startswith(cs.CYPHER_PREFIX):
            cleaned = cleaned[len(cs.CYPHER_PREFIX) :].strip()

        if not self._READ_PREFIX.match(cleaned):
            errors.append("query_must_start_with_read_clause")

        if self._WRITE_PATTERN.search(cleaned):
            errors.append("write_keywords_not_allowed")

        if not self._RETURN_PATTERN.search(cleaned):
            errors.append("return_clause_missing")

        if require_project_scope and self._SCOPABLE_LABEL_PATTERN.search(cleaned):
            if not self._PROJECT_SCOPE_PATTERN.search(cleaned):
                rewritten = self._inject_project_scope(cleaned)
                if rewritten != cleaned:
                    cleaned = rewritten
                    warnings.append("project_scope_injected")
                else:
                    warnings.append("project_scope_missing")

        if self._RETURN_PATTERN.search(cleaned) and not self._LIMIT_PATTERN.search(
            cleaned
        ):
            upper = cleaned.upper()
            if "COUNT(" not in upper and " COLLECT(" not in upper:
                cleaned = cleaned.rstrip(";")
                cleaned += f" LIMIT {max(1, int(default_limit))}"
                warnings.append("limit_added")

        if not cleaned.endswith(cs.CYPHER_SEMICOLON):
            cleaned += cs.CYPHER_SEMICOLON

        metadata = {
            "project_scope_present": bool(self._PROJECT_SCOPE_PATTERN.search(cleaned)),
            "limit_present": bool(self._LIMIT_PATTERN.search(cleaned)),
            "read_only": not bool(self._WRITE_PATTERN.search(cleaned)),
        }
        return CypherGuardResult(
            query=cleaned,
            valid=not errors,
            warnings=warnings,
            errors=errors,
            metadata=metadata,
        )

    def _inject_project_scope(self, query: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            alias = match.group("alias")
            label = match.group("label")
            return f"({alias}:{label} {{project_name: $project_name}})"

        return self._SCOPABLE_LABEL_PATTERN.sub(_replace, query, count=1)
