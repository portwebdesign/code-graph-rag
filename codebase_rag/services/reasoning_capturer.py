from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReasoningCapture:
    thinking: str
    response: str


class ReasoningCapturer:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, content: str) -> ReasoningCapture:
        match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
        if not match:
            return ReasoningCapture(thinking="", response=content)

        thinking = match.group(1).strip()
        response = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return ReasoningCapture(thinking=thinking, response=response)

    def log_reasoning(self, task: str, thinking: str, response: str) -> Path:
        safe_task = re.sub(r"[^a-zA-Z0-9_-]+", "_", task).strip("_") or "task"
        timestamp = int(time.time() * 1000)
        path = self.log_dir / f"{safe_task}_{timestamp}.md"
        content = "\n".join(
            [
                f"# {safe_task}",
                "",
                "## Thinking",
                thinking,
                "",
                "## Response",
                response,
                "",
            ]
        )
        path.write_text(content, encoding="utf-8")
        return path
