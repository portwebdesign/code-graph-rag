from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from codebase_rag.core import constants as cs
from codebase_rag.core.config_semantic_identity import (
    is_secret_like_name,
    normalize_env_name,
)

from .patterns.hardcoded_secrets import HARDCODED_SECRET_PATTERNS
from .patterns.sql_injection import SQL_INJECTION_PATTERNS
from .patterns.xss import XSS_PATTERNS

_ASSIGNMENT_NAME_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]")
_QUOTED_KEY_RE = re.compile(r"""['"](?P<name>[A-Za-z_][A-Za-z0-9_]*)['"]\s*:""")


@dataclass(frozen=True)
class SecurityFinding:
    path: str
    pattern: str
    line: int
    category: str = "generic"
    secret_name: str | None = None
    masked: bool = False

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": self.path,
            "pattern": self.pattern,
            "line": self.line,
            "category": self.category,
        }
        if self.secret_name:
            payload["secret_name"] = self.secret_name
        if self.masked:
            payload["masked"] = True
        return payload


class SecurityScanner:
    def __init__(self) -> None:
        self.patterns = (
            SQL_INJECTION_PATTERNS + XSS_PATTERNS + HARDCODED_SECRET_PATTERNS
        )

    def scan_text(self, text: str, path: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        lines = text.splitlines()
        for name, pattern in self.patterns:
            category = self._category_for_pattern(name)
            flags = re.IGNORECASE if category == "secret" else 0
            for match in re.finditer(pattern, text, flags):
                line_number = text[: match.start()].count("\n") + 1
                line_text = (
                    lines[line_number - 1] if 0 < line_number <= len(lines) else ""
                )
                findings.append(
                    self._build_finding(
                        path=path,
                        pattern_name=name,
                        line_number=line_number,
                        line_text=line_text,
                        match=match,
                    )
                )
        return findings

    def scan_secret_text(self, text: str, path: str) -> list[SecurityFinding]:
        return [
            finding
            for finding in self.scan_text(text, path)
            if finding.category == "secret"
        ]

    def scan_files(self, paths: Iterable[Path]) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for file_path in paths:
            if not file_path.exists() or file_path.stat().st_size > 1_000_000:
                continue
            try:
                content = file_path.read_text(
                    encoding=cs.ENCODING_UTF8, errors="ignore"
                )
            except Exception:
                continue
            findings.extend(self.scan_text(content, str(file_path)))
        return findings

    def scan_secret_files(self, paths: Iterable[Path]) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for file_path in paths:
            if not file_path.exists() or file_path.stat().st_size > 1_000_000:
                continue
            try:
                content = file_path.read_text(
                    encoding=cs.ENCODING_UTF8, errors="ignore"
                )
            except Exception:
                continue
            findings.extend(self.scan_secret_text(content, str(file_path)))
        return findings

    def _build_finding(
        self,
        *,
        path: str,
        pattern_name: str,
        line_number: int,
        line_text: str,
        match: re.Match[str],
    ) -> SecurityFinding:
        category = self._category_for_pattern(pattern_name)
        secret_name = None
        masked = False
        if category == "secret":
            secret_name = self._extract_secret_name(
                pattern_name=pattern_name,
                line_text=line_text,
                match=match,
            )
            masked = True
        return SecurityFinding(
            path=path,
            pattern=pattern_name,
            line=line_number,
            category=category,
            secret_name=secret_name,
            masked=masked,
        )

    @staticmethod
    def _category_for_pattern(pattern_name: str) -> str:
        if pattern_name.startswith("sql_"):
            return "sql"
        if pattern_name.startswith("xss_"):
            return "xss"
        return "secret"

    def _extract_secret_name(
        self,
        *,
        pattern_name: str,
        line_text: str,
        match: re.Match[str],
    ) -> str | None:
        for pattern in (_ASSIGNMENT_NAME_RE, _QUOTED_KEY_RE):
            name_match = pattern.search(line_text)
            if name_match:
                candidate = normalize_env_name(name_match.group("name"))
                if candidate and candidate != "UNKNOWN_ENV":
                    return candidate
        matched_text = match.group(0)
        if is_secret_like_name(matched_text):
            candidate = normalize_env_name(matched_text)
            if candidate != "UNKNOWN_ENV":
                return candidate
        pattern_candidate = pattern_name.replace("secret_assign_", "")
        pattern_candidate = pattern_candidate.replace("secret_env_", "")
        pattern_candidate = pattern_candidate.replace("secret_config_", "")
        candidate = normalize_env_name(pattern_candidate)
        if candidate and candidate != "UNKNOWN_ENV":
            return candidate
        return None
