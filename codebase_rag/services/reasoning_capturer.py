"""
This module provides a utility for capturing and logging the "reasoning" or
"thinking" process of an AI agent.

It is designed to parse a specific format where an agent's internal monologue is
enclosed in `<think>` tags. The `ReasoningCapturer` can extract this thinking
process from the final response and log it to a markdown file for later analysis
and debugging. This is useful for understanding the agent's decision-making process.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReasoningCapture:
    """
    A data class to hold the extracted thinking process and the final response.

    Attributes:
        thinking (str): The content found within the `<think>` tags.
        response (str): The rest of the content, with the thinking block removed.
    """

    thinking: str
    response: str


class ReasoningCapturer:
    """
    A service to extract and log an agent's reasoning process.
    """

    def __init__(self, log_dir: Path) -> None:
        """
        Initializes the ReasoningCapturer.

        Args:
            log_dir (Path): The directory where reasoning logs will be saved.
        """
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, content: str) -> ReasoningCapture:
        """
        Extracts the thinking block and the final response from a string.

        It looks for content enclosed in `<think>...</think>` tags.

        Args:
            content (str): The raw string output from the AI agent.

        Returns:
            A `ReasoningCapture` object containing the separated thinking
            and response parts.
        """
        match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
        if not match:
            return ReasoningCapture(thinking="", response=content)

        thinking = match.group(1).strip()
        response = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return ReasoningCapture(thinking=thinking, response=response)

    def log_reasoning(self, task: str, thinking: str, response: str) -> Path:
        """
        Logs the thinking and response to a timestamped markdown file.

        Args:
            task (str): A name for the task, used to create a safe filename.
            thinking (str): The extracted thinking content.
            response (str): The final response content.

        Returns:
            The path to the newly created log file.
        """
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
