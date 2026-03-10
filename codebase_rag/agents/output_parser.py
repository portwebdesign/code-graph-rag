from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

_WINDOWS_PATH_RE = re.compile(
    r"""(?ix)
    (^|[\s\[{(:,="'`])
    (
        [a-z]:\\
        |\\\\
    )
    """
)


def decode_escaped_text(payload: str) -> str:
    content = payload.strip()
    if not content or "\n" in content or "\r" in content:
        return content
    if content.startswith(("'", '"')) and content.endswith(("'", '"')):
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, str):
            return decoded
    if content.startswith(("{", "[")) and content.endswith(("}", "]")):
        return content
    if "\\n" not in content and "\\r" not in content:
        return content
    if _WINDOWS_PATH_RE.search(content):
        return content

    decoded = content.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\r")
    if '\\"' in decoded and ("\n" in decoded or decoded.startswith("```")):
        decoded = decoded.replace('\\"', '"')
    return decoded


def extract_code_block(
    payload: str,
    preferred_languages: set[str] | None = None,
) -> tuple[str | None, str | None]:
    fence_pattern = re.compile(
        r"```(?P<lang>[a-zA-Z0-9_+-]*)\s*\n(?P<body>.*?)```",
        flags=re.DOTALL,
    )
    matches = list(fence_pattern.finditer(payload))
    if not matches:
        return None, None

    if preferred_languages:
        normalized = {item.lower() for item in preferred_languages}
        for match in matches:
            language = match.group("lang").strip().lower() or None
            if language in normalized:
                return language, match.group("body").strip()

    first = matches[0]
    language = first.group("lang").strip().lower() or None
    return language, first.group("body").strip()


def extract_json_object(payload: str) -> str:
    content = decode_escaped_text(payload).strip()
    if not content:
        return "{}"

    json_lang, json_block = extract_code_block(content, preferred_languages={"json"})
    if json_lang == "json" and json_block:
        return json_block

    _, any_block = extract_code_block(content)
    if any_block and any_block.lstrip().startswith(("{", "[")):
        return any_block

    if content.startswith(("'", '"')) and content.endswith(("'", '"')):
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, str):
            return extract_json_object(decoded)

    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end >= start:
        candidate = content[start : end + 1].strip()
        if candidate:
            return candidate

    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1 and end >= start:
        candidate = content[start : end + 1].strip()
        if candidate:
            return candidate

    return content


class JSONOutputParser:
    def parse(self, payload: str) -> dict[str, Any]:
        content = extract_json_object(payload)
        parsed = json.loads(content) if content else {}
        return parsed if isinstance(parsed, dict) else {}


class XMLOutputParser:
    def parse(self, payload: str) -> dict[str, Any]:
        content = decode_escaped_text(payload).strip()
        if not content:
            return {}
        root = ET.fromstring(content)
        result: dict[str, Any] = {}
        for child in root:
            text = (child.text or "").strip()
            if child.tag in result:
                existing = result[child.tag]
                if isinstance(existing, list):
                    existing.append(text)
                else:
                    result[child.tag] = [existing, text]
            else:
                result[child.tag] = text
        return result


def get_output_parser(format_name: str) -> JSONOutputParser | XMLOutputParser:
    normalized = re.sub(r"\s+", "", format_name).lower()
    if normalized == "xml":
        return XMLOutputParser()
    return JSONOutputParser()
