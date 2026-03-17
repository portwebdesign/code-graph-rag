from __future__ import annotations

from dataclasses import dataclass

from codebase_rag.parsers.pipeline.ts_contracts import (
    _ARROW_FUNCTION_DECL_RE,
    _FUNCTION_DECL_RE,
    _extract_brace_block,
    _line_number_for_offset,
)


@dataclass(frozen=True)
class TypeScriptSymbolBlock:
    symbol_name: str
    body: str
    line_start: int | None = None
    line_end: int | None = None


def extract_typescript_symbol_blocks(source: str) -> list[TypeScriptSymbolBlock]:
    """Extracts top-level TS/TSX function-like symbol bodies."""

    blocks: list[TypeScriptSymbolBlock] = []

    for match in _FUNCTION_DECL_RE.finditer(source):
        open_brace_index = source.find("{", match.end() - 1)
        if open_brace_index < 0:
            continue
        block = _extract_brace_block(source, open_brace_index)
        if block is None:
            continue
        body, end_index = block
        blocks.append(
            TypeScriptSymbolBlock(
                symbol_name=match.group("name"),
                body=body,
                line_start=_line_number_for_offset(source, match.start()),
                line_end=_line_number_for_offset(source, end_index),
            )
        )

    for match in _ARROW_FUNCTION_DECL_RE.finditer(source):
        arrow_index = source.find("=>", match.end() - 2)
        if arrow_index < 0:
            continue
        body_start = arrow_index + 2
        while body_start < len(source) and source[body_start].isspace():
            body_start += 1
        if body_start >= len(source):
            continue
        if source[body_start] == "{":
            block = _extract_brace_block(source, body_start)
            if block is None:
                continue
            body, end_index = block
        else:
            end_index = source.find(";", body_start)
            if end_index < 0:
                end_index = len(source)
            body = source[body_start:end_index]
        blocks.append(
            TypeScriptSymbolBlock(
                symbol_name=match.group("name"),
                body=body,
                line_start=_line_number_for_offset(source, match.start()),
                line_end=_line_number_for_offset(source, end_index),
            )
        )

    unique: dict[tuple[str, int | None, int | None], TypeScriptSymbolBlock] = {}
    for block in blocks:
        key = (block.symbol_name, block.line_start, block.line_end)
        unique.setdefault(key, block)
    return list(unique.values())
