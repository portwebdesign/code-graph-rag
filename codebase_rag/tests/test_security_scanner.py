from __future__ import annotations

from codebase_rag.security.security_scanner import SecurityScanner


def test_scan_text_detects_sql_pattern() -> None:
    scanner = SecurityScanner()
    text = 'query = "SELECT * FROM users" + user_input\n'

    findings = scanner.scan_text(text, "sample.py")

    assert any(finding.pattern.startswith("sql_") for finding in findings)


def test_scan_text_detects_xss_pattern() -> None:
    scanner = SecurityScanner()
    text = "element.innerHTML = userInput"

    findings = scanner.scan_text(text, "sample.js")

    assert any(finding.pattern.startswith("xss_") for finding in findings)
