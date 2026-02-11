from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from codebase_rag.core import constants as cs

from .patterns.hardcoded_secrets import HARDCODED_SECRET_PATTERNS
from .patterns.sql_injection import SQL_INJECTION_PATTERNS
from .patterns.xss import XSS_PATTERNS


@dataclass(frozen=True)
class SecurityFinding:
    path: str
    pattern: str
    line: int


class SecurityScanner:
    def __init__(self) -> None:
        self.patterns = (
            SQL_INJECTION_PATTERNS + XSS_PATTERNS + HARDCODED_SECRET_PATTERNS
        )

    def scan_text(self, text: str, path: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for name, pattern in self.patterns:
            for match in re.finditer(pattern, text):
                line_number = text[: match.start()].count("\n") + 1
                findings.append(
                    SecurityFinding(path=path, pattern=name, line=line_number)
                )
        return findings

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
