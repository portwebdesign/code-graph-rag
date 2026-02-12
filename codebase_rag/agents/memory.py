from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MemoryEntry:
    text: str
    tags: list[str]
    timestamp: float


class MemoryAgent:
    def __init__(self, project_root: str) -> None:
        self.root = Path(project_root).resolve()
        self.path = self.root / "output" / "memory" / "decisions.jsonl"

    def add_entry(self, text: str, tags: list[str] | None = None) -> MemoryEntry:
        clean_tags = [tag.strip() for tag in (tags or []) if tag.strip()]
        entry = MemoryEntry(text=text, tags=clean_tags, timestamp=time.time())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.__dict__, ensure_ascii=False) + "\n")
        return entry

    def list_entries(self, limit: int = 50) -> list[MemoryEntry]:
        if not self.path.exists():
            return []
        entries: list[MemoryEntry] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line.strip())
                    entries.append(
                        MemoryEntry(
                            text=str(payload.get("text", "")),
                            tags=list(payload.get("tags", [])),
                            timestamp=float(payload.get("timestamp", 0)),
                        )
                    )
                except Exception:
                    continue
        return list(reversed(entries))[: max(1, limit)]
