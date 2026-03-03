"""
SQL Relationship Pass

Extracts structural relationships from SQL DDL files and writes them to the graph:

  - Column nodes (HAS_COLUMN): Each ``column_definition`` inside a CREATE TABLE
    becomes a ``Column`` node linked to its parent ``Class`` (table).

  - Foreign-key edges (FOREIGN_KEY): Inline and table-level REFERENCES clauses
    produce directed ``FOREIGN_KEY`` edges between two ``Class`` (table) nodes
    carrying the column_name and referenced_column as edge properties.

  - Constraint annotations (HAS_CONSTRAINT): CHECK / UNIQUE / EXCLUDE
    table-level constraints are attached to the table Class node.

  - Index-to-table edges (INDEXES_TABLE): Every ``create_index`` node that was
    already ingested as a ``Class`` node gets a directed ``INDEXES_TABLE`` edge
    pointing to the table it covers.

Controlled by the ``CODEGRAPH_SQL_RELATIONS`` environment variable
(enabled by default; set to ``0`` / ``false`` / ``no`` to disable).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure.language_spec import (
    _generic_file_to_module,
    _sql_get_name,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk(node: Node) -> Iterable[Node]:
    """Depth-first walk of the entire subtree rooted at *node*."""
    yield node
    for child in node.children:
        yield from _walk(child)


def _text(node: Node | None) -> str | None:
    """Decode node text to str, returning None for empty/None nodes."""
    if node is None or not node.text:
        return None
    return node.text.decode("utf-8", errors="replace")


def _build_sql_qn(
    name: str,
    file_path: Path,
    repo_path: Path,
    project_name: str,
) -> str:
    """Reproduce the FQN that ``resolve_fqn_from_ast`` would produce for a SQL entity."""
    parts = _generic_file_to_module(file_path, repo_path)
    return cs.SEPARATOR_DOT.join([project_name] + parts + [name])


def _get_object_reference_name(node: Node) -> str | None:
    """Return the first identifier text inside an object_reference child."""
    for child in node.children:
        if child.type == "object_reference" and child.children:
            return _text(child.children[0])
    return None


# Column-type node types emitted by tree-sitter-sql
_SQL_TYPE_NODE_TYPES = frozenset(
    {
        "predefined_type",
        "bigint",
        "int",
        "integer",
        "smallint",
        "text",
        "boolean",
        "bool",
        "uuid",
        "jsonb",
        "json",
        "timestamp",
        "time",
        "date",
        "numeric",
        "decimal",
        "character_varying",
        "character",
        "varchar",
        "char",
        "real",
        "float",
        "double_precision",
        "bytea",
        "serial",
        "bigserial",
        "smallserial",
        "money",
        "point",
        "line",
        "circle",
        "polygon",
        "inet",
        "cidr",
        "macaddr",
        "tsvector",
        "tsquery",
        "xml",
    }
)


def _is_type_node(node: Node) -> bool:
    """Heuristic: is this tree-sitter node a SQL data-type node?"""
    t = node.type.lower()
    return node.type in _SQL_TYPE_NODE_TYPES or "type" in t


def _get_column_type(col_node: Node, col_name: str) -> str | None:
    """
    Return a human-readable SQL type string from a column_definition node.

    We skip:
      - The first identifier (= column name)
      - Any ``constraint`` subtrees
    and take the text of the first remaining child that looks like a type.
    """
    skipped_name = False
    for child in col_node.children:
        if not skipped_name and child.type == "identifier" and _text(child) == col_name:
            skipped_name = True
            continue
        if child.type in ("constraint", ",", "(", ")"):
            continue
        if _is_type_node(child):
            return _text(child)
        # Some grammars nest the type inside a generic child; grab first text token
        found = _text(child)
        if found and found.upper() not in (
            "NOT",
            "NULL",
            "DEFAULT",
            "PRIMARY",
            "UNIQUE",
            "CHECK",
            "REFERENCES",
            "ON",
            "DELETE",
            "CASCADE",
            "SET",
        ):
            return found
    return None


def _has_keyword(col_node: Node, keyword: str) -> bool:
    """Return True if any descendant of col_node has text equal to keyword (uppercase)."""
    upper = keyword.upper()
    for node in _walk(col_node):
        txt = _text(node)
        if txt and txt.upper() == upper:
            return True
    return False


def _has_primary_key(col_node: Node) -> bool:
    return _has_keyword(col_node, "PRIMARY") or _has_keyword(col_node, "PRIMARY KEY")


def _has_not_null(col_node: Node) -> bool:
    # Also check for the combined text "NOT NULL"
    full = _text(col_node) or ""
    return "NOT NULL" in full.upper()


def _has_unique(col_node: Node) -> bool:
    return _has_keyword(col_node, "UNIQUE")


def _extract_references_target(col_node: Node) -> tuple[str | None, str | None]:
    """
    Scan a column_definition subtree for an inline FK reference.

    Returns (referenced_table_name, referenced_column_name).
    """
    found_ref = False
    for node in _walk(col_node):
        node_type_lower = node.type.lower()
        txt_upper = (_text(node) or "").upper()
        if not found_ref and (
            "references" in node_type_lower or txt_upper == "REFERENCES"
        ):
            found_ref = True
            continue
        if found_ref and node.type == "object_reference" and node.children:
            ref_table = _text(node.children[0])
            # The referenced column is in parentheses right after, often as a sibling
            # We do not need the column to build the edge, but return it for props
            return ref_table, None
    return None, None


def _extract_table_level_fk(
    constraint_node: Node,
) -> tuple[str | None, list[str], list[str]]:
    """
    Parse a table-level FOREIGN KEY constraint node.

    Returns (referenced_table, local_columns, referenced_columns).
    """
    # Pattern: FOREIGN KEY (col,..) REFERENCES table (col,..)
    local_cols: list[str] = []
    ref_cols: list[str] = []
    ref_table: str | None = None

    found_fk = False
    in_local = False
    found_refs = False
    in_ref_cols = False

    for node in _walk(constraint_node):
        txt_upper = (_text(node) or "").upper()
        node_type_lower = node.type.lower()

        if not found_fk and ("foreign" in node_type_lower or txt_upper == "FOREIGN"):
            found_fk = True
        elif found_fk and not in_local and node.type == "(":
            in_local = True
        elif in_local and node.type == "identifier":
            local_cols.append(_text(node) or "")
        elif in_local and node.type == ")":
            in_local = False
        elif not found_refs and (
            "references" in node_type_lower or txt_upper == "REFERENCES"
        ):
            found_refs = True
        elif found_refs and ref_table is None and node.type == "object_reference":
            ref_table = _text(node.children[0]) if node.children else None
        elif found_refs and ref_table and node.type == "(":
            in_ref_cols = True
        elif in_ref_cols and node.type == "identifier":
            ref_cols.append(_text(node) or "")
        elif in_ref_cols and node.type == ")":
            in_ref_cols = False

    return ref_table, local_cols, ref_cols


def _extract_index_table_name(index_node: Node) -> str | None:
    """
    Return the table name from a ``create_index`` node.

    The index name itself comes from the ``column`` field (a bare identifier).
    The first ``object_reference`` child is the ON-table.
    """
    for node in _walk(index_node):
        if node.type == "object_reference" and node.children:
            return _text(node.children[0])
    return None


def _looks_like_constraint(node: Node) -> bool:
    """True if *node* is a table-level constraint definition."""
    return node.type in ("constraint", "table_constraint")


def _constraint_kind(constraint_node: Node) -> str | None:
    """Return 'PRIMARY KEY', 'UNIQUE', 'CHECK', 'FOREIGN KEY', or None."""
    for node in _walk(constraint_node):
        txt = (_text(node) or "").upper()
        if txt in ("PRIMARY",):
            return "PRIMARY KEY"
        if txt == "UNIQUE":
            return "UNIQUE"
        if txt == "CHECK":
            return "CHECK"
        if txt == "FOREIGN":
            return "FOREIGN KEY"
    return None


# ---------------------------------------------------------------------------
# Main pass class
# ---------------------------------------------------------------------------


class SqlRelationPass:
    """
    Post-processing pass that adds structural SQL relationships to the graph.

    Must be run *after* the definition processor has already ingested all
    ``Class`` (table/index) and ``Function`` (view/trigger) nodes so that the
    node QNs are already known.
    """

    def __init__(
        self,
        ingestor,
        repo_path: Path,
        project_name: str,
        function_registry,
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.function_registry = function_registry

        self.enabled = os.getenv("CODEGRAPH_SQL_RELATIONS", "1").lower() not in {
            "0",
            "false",
            "no",
        }

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def process_ast_cache(
        self,
        ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
    ) -> None:
        """Iterate all cached ASTs; process SQL files only."""
        if not self.enabled:
            return

        sql_items: list[tuple[Path, Node]] = []
        for file_path, (root_node, language) in ast_items:
            if language == cs.SupportedLanguage.SQL:
                sql_items.append((file_path, root_node))

        if not sql_items:
            return

        # First pass: build table_name → qn lookup from every SQL file
        table_qn_lookup: dict[str, str] = {}
        for file_path, root_node in sql_items:
            for node in _walk(root_node):
                if node.type in (
                    "create_table",
                    "create_index",
                    "create_view",
                    "create_materialized_view",
                ):
                    name = _sql_get_name(node)
                    if name:
                        qn = _build_sql_qn(
                            name, file_path, self.repo_path, self.project_name
                        )
                        table_qn_lookup[name] = qn

        # Second pass: extract relationships
        for file_path, root_node in sql_items:
            try:
                self._process_file(file_path, root_node, table_qn_lookup)
            except Exception as exc:
                logger.warning("SqlRelationPass failed for {}: {}", file_path, exc)

        logger.info(
            "SqlRelationPass: processed {} SQL file(s), {} table names known",
            len(sql_items),
            len(table_qn_lookup),
        )

    # ------------------------------------------------------------------
    # Per-file dispatch
    # ------------------------------------------------------------------

    def _process_file(
        self,
        file_path: Path,
        root_node: Node,
        table_qn_lookup: dict[str, str],
    ) -> None:
        # tree-sitter-sql wraps every DDL statement in a ``statement`` node:
        #   program → statement → create_table / create_index / …
        # We unwrap one level; also handle files where DDL is a direct child.
        for child in root_node.children:
            # Unwrap the statement wrapper if present
            ddl_node = (
                child.children[0]
                if child.type == "statement" and child.children
                else child
            )
            if ddl_node.type == "create_table":
                self._process_create_table(ddl_node, file_path, table_qn_lookup)
            elif ddl_node.type == "create_index":
                self._process_create_index(ddl_node, file_path, table_qn_lookup)

    # ------------------------------------------------------------------
    # CREATE TABLE → columns + FK edges
    # ------------------------------------------------------------------

    def _process_create_table(
        self,
        table_node: Node,
        file_path: Path,
        table_qn_lookup: dict[str, str],
    ) -> None:
        table_name = _sql_get_name(table_node)
        if not table_name:
            return
        table_qn = _build_sql_qn(
            table_name, file_path, self.repo_path, self.project_name
        )

        rel_path = file_path.relative_to(self.repo_path).as_posix()
        abs_path = file_path.resolve().as_posix()

        for node in _walk(table_node):
            if node.type == "column_definition":
                self._process_column(
                    node,
                    table_name,
                    table_qn,
                    table_qn_lookup,
                    rel_path,
                    abs_path,
                )
            elif _looks_like_constraint(node):
                kind = _constraint_kind(node)
                if kind == "FOREIGN KEY":
                    self._process_table_level_fk(node, table_qn, table_qn_lookup)
                elif kind in ("UNIQUE", "CHECK"):
                    self._process_constraint(node, table_qn, kind)

    def _process_column(
        self,
        col_node: Node,
        table_name: str,
        table_qn: str,
        table_qn_lookup: dict[str, str],
        rel_path: str,
        abs_path: str,
    ) -> None:
        # Column name is the first identifier child
        col_name: str | None = None
        for child in col_node.children:
            if child.type == "identifier":
                col_name = _text(child)
                break
        if not col_name:
            return

        col_type = _get_column_type(col_node, col_name) or "unknown"
        is_pk = _has_primary_key(col_node)
        is_unique = is_pk or _has_unique(col_node)
        is_nullable = not (is_pk or _has_not_null(col_node))

        col_qn = f"{table_qn}.{col_name}"
        col_props: dict = {
            cs.KEY_QUALIFIED_NAME: col_qn,
            cs.KEY_NAME: col_name,
            "column_type": col_type,
            "is_primary_key": is_pk,
            "is_nullable": is_nullable,
            "is_unique": is_unique,
            cs.KEY_LANGUAGE: cs.SupportedLanguage.SQL.value,
            cs.KEY_PARENT_QN: table_qn,
            cs.KEY_PATH: rel_path,
            cs.KEY_REPO_REL_PATH: rel_path,
            cs.KEY_ABS_PATH: abs_path,
            cs.KEY_SYMBOL_KIND: "column",
        }
        self.ingestor.ensure_node_batch(cs.NodeLabel.COLUMN, col_props)
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, table_qn),
            cs.RelationshipType.HAS_COLUMN,
            (cs.NodeLabel.COLUMN, cs.KEY_QUALIFIED_NAME, col_qn),
            {"column_type": col_type, "is_primary_key": is_pk},
        )

        # Inline FK reference
        ref_table, _ = _extract_references_target(col_node)
        if ref_table:
            ref_qn = table_qn_lookup.get(ref_table)
            if ref_qn:
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, table_qn),
                    cs.RelationshipType.FOREIGN_KEY,
                    (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, ref_qn),
                    {
                        "column_name": col_name,
                        "column_type": col_type,
                        "referenced_table": ref_table,
                    },
                )

    def _process_table_level_fk(
        self,
        constraint_node: Node,
        table_qn: str,
        table_qn_lookup: dict[str, str],
    ) -> None:
        ref_table, local_cols, ref_cols = _extract_table_level_fk(constraint_node)
        if not ref_table:
            return
        ref_qn = table_qn_lookup.get(ref_table)
        if not ref_qn:
            return
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, table_qn),
            cs.RelationshipType.FOREIGN_KEY,
            (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, ref_qn),
            {
                "column_names": ",".join(local_cols),
                "referenced_columns": ",".join(ref_cols),
                "referenced_table": ref_table,
            },
        )

    def _process_constraint(
        self,
        constraint_node: Node,
        table_qn: str,
        kind: str,
    ) -> None:
        constraint_text = _text(constraint_node) or ""
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, table_qn),
            cs.RelationshipType.HAS_CONSTRAINT,
            (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, table_qn),
            {"constraint_kind": kind, "constraint_text": constraint_text[:200]},
        )

    # ------------------------------------------------------------------
    # CREATE INDEX → INDEXES_TABLE edge
    # ------------------------------------------------------------------

    def _process_create_index(
        self,
        index_node: Node,
        file_path: Path,
        table_qn_lookup: dict[str, str],
    ) -> None:
        index_name = _sql_get_name(index_node)
        if not index_name:
            return
        index_qn = _build_sql_qn(
            index_name, file_path, self.repo_path, self.project_name
        )

        table_name = _extract_index_table_name(index_node)
        if not table_name or table_name == index_name:
            return
        table_qn = table_qn_lookup.get(table_name)
        if not table_qn:
            return

        # Extract which columns are indexed
        indexed_cols: list[str] = []
        for node in _walk(index_node):
            if node.type == "index_fields":
                for child in _walk(node):
                    if child.type == "identifier":
                        col_name = _text(child)
                        if col_name:
                            indexed_cols.append(col_name)

        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, index_qn),
            cs.RelationshipType.INDEXES_TABLE,
            (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, table_qn),
            {
                "index_name": index_name,
                "indexed_columns": ",".join(indexed_cols),
            },
        )
