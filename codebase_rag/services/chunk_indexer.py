"""
This module provides a utility for splitting large text files, particularly source
code files, into smaller, manageable chunks.

This is often a prerequisite for semantic search and retrieval-augmented generation
(RAG), as it allows for more targeted searching and fits the context window
limitations of language models. The `ChunkIndexer` implements a sliding window
approach to create overlapping chunks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeChunk:
    """
    A data class representing a single chunk of a source code file.

    It is immutable (`frozen=True`) to ensure that chunks are treated as
    read-only data structures after creation.

    Attributes:
        content (str): The text content of the chunk.
        start_line (int): The 1-based starting line number of the chunk in the original file.
        end_line (int): The 1-based ending line number of the chunk in the original file.
    """

    content: str
    start_line: int
    end_line: int


class ChunkIndexer:
    """
    A service for splitting text into overlapping chunks.

    This class implements a sliding window algorithm to divide a given text or
    file into smaller `CodeChunk` objects of a specified maximum size, with a
    configurable overlap between consecutive chunks to maintain context.
    """

    def __init__(self, max_lines: int = 40, overlap: int = 8) -> None:
        """
        Initializes the ChunkIndexer.

        Args:
            max_lines (int): The maximum number of lines each chunk can have.
            overlap (int): The number of lines that should overlap between
                           consecutive chunks.
        """
        self.max_lines = max(5, max_lines)
        self.overlap = max(0, min(overlap, self.max_lines - 1))

    def create_chunks(self, text: str) -> list[CodeChunk]:
        """
        Creates a list of `CodeChunk` objects from a given string of text.

        Args:
            text (str): The input text to be chunked.

        Returns:
            A list of `CodeChunk` objects.
        """
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
        """
        Reads a file and splits its content into chunks.

        This is a convenience method that combines reading a file and calling
        `create_chunks`.

        Args:
            file_path (str | Path): The path to the file to be chunked.

        Returns:
            A list of `CodeChunk` objects from the file's content.
        """
        path = Path(file_path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        return self.create_chunks(text)
