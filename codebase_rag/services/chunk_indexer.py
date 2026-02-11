from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeChunk:
    content: str
    start_line: int
    end_line: int


class ChunkIndexer:
    def __init__(self, max_lines: int = 40, overlap: int = 8) -> None:
        self.max_lines = max(5, max_lines)
        self.overlap = max(0, min(overlap, self.max_lines - 1))

    def create_chunks(self, text: str) -> list[CodeChunk]:
        lines = text.splitlines()
        if not lines:
            return []

        chunks: list[CodeChunk] = []
        step = self.max_lines - self.overlap
        index = 0
        while index < len(lines):
            start = index
            end = min(len(lines), index + self.max_lines)
            chunk_lines = lines[start:end]
            if chunk_lines:
                chunks.append(
                    CodeChunk(
                        content="\n".join(chunk_lines),
                        start_line=start + 1,
                        end_line=end,
                    )
                )
            if end >= len(lines):
                break
            index += step
        return chunks

    def chunk_file(self, file_path: str | Path) -> list[CodeChunk]:
        path = Path(file_path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        return self.create_chunks(text)
