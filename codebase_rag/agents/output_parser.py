from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any


class JSONOutputParser:
    def parse(self, payload: str) -> dict[str, Any]:
        content = payload.strip()
        if "```" in content:
            lines = [
                line
                for line in content.splitlines()
                if not line.strip().startswith("```")
            ]
            content = "\n".join(lines).strip()
        return json.loads(content) if content else {}


class XMLOutputParser:
    def parse(self, payload: str) -> dict[str, Any]:
        content = payload.strip()
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
