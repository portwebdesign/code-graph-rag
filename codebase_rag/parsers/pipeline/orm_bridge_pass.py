"""
ORM Bridge Pass

Detects ORM model classes in non-SQL source files and creates
``MAPS_TO_TABLE`` edges from the ORM ``Class`` node to the corresponding
SQL ``Class`` (table) node.  Also creates ``QUERIES_TABLE`` edges for
functions that directly reference ORM models in query calls.

Supported frameworks:

  Python
    - **SQLAlchemy** – ``__tablename__ = "name"`` in a class body
    - **SQLAlchemy (mapped_column / DeclarativeBase)** – ``__tablename__``
    - **Django ORM** – ``class Meta: db_table = "name"``
    - **Peewee** – ``class Meta: table_name = "name"``
    - **Tortoise ORM** – ``class Meta: table = "name"``

  TypeScript / JavaScript
    - **TypeORM** – ``@Entity("name")`` or ``@Entity()`` (uses class name)
    - **MikroORM** – ``@Entity({ tableName: "name" })``
    - **Prisma** – ``@@map("name")`` in .prisma files (treated as TS)
    - **Sequelize** – ``tableName: "name"`` inside ``Model.init``

  Ruby
    - **ActiveRecord** – ``self.table_name = "name"`` in class body

Controlled by ``CODEGRAPH_ORM_BRIDGE`` (enabled by default; set to
``0`` / ``false`` / ``no`` to disable).
"""

from __future__ import annotations

import os
import re
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
# Regex patterns
# ---------------------------------------------------------------------------

# Python: __tablename__ / table_name / table / db_table
_PY_TABLENAME_RE = re.compile(
    r'__tablename__\s*=\s*["\'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["\']',
)
_PY_META_TABLE_RE = re.compile(
    r'(?:table_name|table|db_table)\s*=\s*["\'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["\']',
)
_PY_CLASS_RE = re.compile(
    r"^class\s+(?P<cls>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*:",
    re.MULTILINE,
)

# TypeScript / JavaScript: @Entity("name"), @Entity({ tableName: "name" })
_TS_ENTITY_STR_RE = re.compile(
    r'@Entity\s*\(\s*["\'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["\']',
)
_TS_ENTITY_OBJ_RE = re.compile(
    r'@Entity\s*\(\s*\{[^}]*tableName\s*:\s*["\'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["\']',
)
_TS_ENTITY_BARE_RE = re.compile(
    r"@Entity\s*\(\s*\)\s*\n\s*(?:export\s+)?(?:abstract\s+)?class\s+(?P<cls>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_TS_MAP_RE = re.compile(
    r'@@map\s*\(\s*["\'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["\']',
)
# Sequelize: ModelName.init({ ... }, { tableName: "name" })
_TS_SEQ_TABLE_RE = re.compile(
    r'tableName\s*:\s*["\'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["\']',
)
_TS_CLASS_RE = re.compile(
    r"(?:export\s+)?(?:abstract\s+)?class\s+(?P<cls>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)

# Ruby: self.table_name = "name"
_RB_TABLE_RE = re.compile(
    r'self\.table_name\s*=\s*["\'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["\']',
)
_RB_CLASS_RE = re.compile(
    r"^class\s+(?P<cls>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Raw SQL-in-string usage detection (for QUERIES_TABLE edges)
# ---------------------------------------------------------------------------

_SQL_FROM_RE = re.compile(
    r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_SQL_JOIN_RE = re.compile(
    r"\bJOIN\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_SQL_INSERT_RE = re.compile(
    r"\bINSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_SQL_UPDATE_RE = re.compile(
    r"\bUPDATE\s+([A-Za-z_][A-Za-z0-9_]*)\s",
    re.IGNORECASE,
)
_SQL_DELETE_RE = re.compile(
    r"\bDELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_SQL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (_SQL_FROM_RE, "SELECT"),
    (_SQL_JOIN_RE, "JOIN"),
    (_SQL_INSERT_RE, "INSERT"),
    (_SQL_UPDATE_RE, "UPDATE"),
    (_SQL_DELETE_RE, "DELETE"),
]

# Python / TS / JS function definitions (top-level + class methods)
_FUNC_DEF_RE = re.compile(
    r"^(?P<indent>\s{0,12})(?:async\s+)?(?:def|function)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|<)",
    re.MULTILINE,
)


SqlUsage = tuple[str, str, str]  # (func_name, table_name, operation)


def _detect_sql_usages(
    source: str,
    known_tables_lower: dict[str, str],  # lowercase_table → original_table
) -> list[SqlUsage]:
    """
    Scan Python / JS / TS source for embedded SQL string references.

    Returns a list of ``(func_name, original_table_name, sql_operation)``
    tuples for every unique (function, table) pair found.

    The scan is *text-level*: it looks for SQL-keyword patterns inside any
    string content within each function body.  False positives are possible but
    harmless (the edge simply won't resolve to a known table QN).
    """
    if not known_tables_lower:
        return []

    results: list[SqlUsage] = []
    func_matches = list(_FUNC_DEF_RE.finditer(source))

    for i, fm in enumerate(func_matches):
        func_name = fm.group("name")
        func_start = fm.start()
        func_end = (
            func_matches[i + 1].start() if i + 1 < len(func_matches) else len(source)
        )
        func_body = source[func_start:func_end]

        seen: set[tuple[str, str]] = set()
        for pattern, op in _SQL_PATTERNS:
            for m in pattern.finditer(func_body):
                tname_lower = m.group(1).lower()
                orig_tname = known_tables_lower.get(tname_lower)
                if orig_tname and (func_name, tname_lower) not in seen:
                    seen.add((func_name, tname_lower))
                    results.append((func_name, orig_tname, op))

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _build_sql_qn(
    name: str,
    file_path: Path,
    repo_path: Path,
    project_name: str,
) -> str:
    parts = _generic_file_to_module(file_path, repo_path)
    return cs.SEPARATOR_DOT.join([project_name] + parts + [name])


def _build_code_qn(
    class_name: str,
    file_path: Path,
    repo_path: Path,
    project_name: str,
) -> str:
    """Reproduce the QN for a non-SQL class node (same formula as FQN resolver)."""
    parts = _generic_file_to_module(file_path, repo_path)
    return cs.SEPARATOR_DOT.join([project_name] + parts + [class_name])


def _to_snake(name: str) -> str:
    """CamelCase → snake_case (for bare @Entity() class-name heuristic)."""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return s


def _to_plural_snake(name: str) -> str:
    """CamelCase → plural snake_case (UserProfile → user_profiles)."""
    snake = _to_snake(name)
    return snake + "s" if not snake.endswith("s") else snake


# ---------------------------------------------------------------------------
# Per-language detectors
# ---------------------------------------------------------------------------

OrmMapping = tuple[str, str]  # (class_name, table_name)


def _detect_python_mappings(source: str) -> list[OrmMapping]:
    """Return [(class_name, table_name)] for all ORM classes in Python source."""
    results: list[OrmMapping] = []
    # Split into class blocks
    class_matches = list(_PY_CLASS_RE.finditer(source))
    for i, cm in enumerate(class_matches):
        cls_name = cm.group("cls")
        cls_start = cm.start()
        cls_end = (
            class_matches[i + 1].start() if i + 1 < len(class_matches) else len(source)
        )
        cls_block = source[cls_start:cls_end]

        # __tablename__
        m = _PY_TABLENAME_RE.search(cls_block)
        if m:
            results.append((cls_name, m.group("name")))
            continue

        # Meta: table_name / table / db_table
        m = _PY_META_TABLE_RE.search(cls_block)
        if m:
            results.append((cls_name, m.group("name")))

    return results


def _detect_ts_mappings(source: str) -> list[OrmMapping]:
    """Return [(class_name, table_name)] for TypeORM / MikroORM / Prisma / Sequelize."""
    results: list[OrmMapping] = []

    # Explicit @Entity("name")
    for m in _TS_ENTITY_STR_RE.finditer(source):
        table_name = m.group("name")
        # find the first class after this decorator
        rest = source[m.end() :]
        cm = _TS_CLASS_RE.search(rest)
        if cm:
            results.append((cm.group("cls"), table_name))

    # @Entity({ tableName: "name" })
    for m in _TS_ENTITY_OBJ_RE.finditer(source):
        table_name = m.group("name")
        rest = source[m.end() :]
        cm = _TS_CLASS_RE.search(rest)
        if cm:
            results.append((cm.group("cls"), table_name))

    # Bare @Entity() → derive table name from class name
    for m in _TS_ENTITY_BARE_RE.finditer(source):
        cls_name = m.group("cls")
        # Use both snake and plural_snake as candidates
        results.append((cls_name, _to_snake(cls_name)))
        results.append((cls_name, _to_plural_snake(cls_name)))

    # @@map("name") – Prisma
    for m in _TS_MAP_RE.finditer(source):
        table_name = m.group("name")
        # find the nearest class before this directive
        text_before = source[: m.start()]
        candidates = list(_TS_CLASS_RE.finditer(text_before))
        if candidates:
            results.append((candidates[-1].group("cls"), table_name))

    # Sequelize tableName property
    for m in _TS_SEQ_TABLE_RE.finditer(source):
        table_name = m.group("name")
        text_before = source[: m.start()]
        candidates = list(_TS_CLASS_RE.finditer(text_before))
        if candidates:
            results.append((candidates[-1].group("cls"), table_name))

    return results


def _detect_ruby_mappings(source: str) -> list[OrmMapping]:
    """Return [(class_name, table_name)] for ActiveRecord classes."""
    results: list[OrmMapping] = []
    class_matches = list(_RB_CLASS_RE.finditer(source))
    for i, cm in enumerate(class_matches):
        cls_name = cm.group("cls")
        cls_start = cm.start()
        cls_end = (
            class_matches[i + 1].start() if i + 1 < len(class_matches) else len(source)
        )
        cls_block = source[cls_start:cls_end]
        m = _RB_TABLE_RE.search(cls_block)
        if m:
            results.append((cls_name, m.group("name")))
    return results


_LANGUAGE_DETECTORS = {
    cs.SupportedLanguage.PYTHON: _detect_python_mappings,
    cs.SupportedLanguage.JS: _detect_ts_mappings,
    cs.SupportedLanguage.TS: _detect_ts_mappings,
    cs.SupportedLanguage.RUBY: _detect_ruby_mappings,
}

_ORM_EXTENSIONS = frozenset({".py", ".ts", ".js", ".tsx", ".jsx", ".rb", ".prisma"})


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class OrmBridgePass:
    """
    Post-processing pass that creates MAPS_TO_TABLE relationships between
    ORM model classes (Python/TS/Ruby) and SQL table nodes.

    Must run *after* both the definition processor (so ORM class nodes exist)
    and the SQL relation pass (so SQL table nodes exist).
    """

    def __init__(
        self,
        ingestor,
        repo_path: Path,
        project_name: str,
        simple_name_lookup: dict[str, set[str]],
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.simple_name_lookup = simple_name_lookup

        self.enabled = os.getenv("CODEGRAPH_ORM_BRIDGE", "1").lower() not in {
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
        if not self.enabled:
            return

        # Separate SQL and non-SQL items; build sql_table_lookup first
        sql_items: list[tuple[Path, Node]] = []
        orm_items: list[tuple[Path, Node, cs.SupportedLanguage]] = []

        for file_path, (root_node, language) in ast_items:
            if language == cs.SupportedLanguage.SQL:
                sql_items.append((file_path, root_node))
            elif (
                language in _LANGUAGE_DETECTORS and file_path.suffix in _ORM_EXTENSIONS
            ):
                orm_items.append((file_path, root_node, language))

        if not sql_items:
            return
        # orm_items may be empty if there are no Python/TS/Ruby files;
        # we still need to run even then because the raw-SQL scanner (QUERIES_TABLE)
        # has nothing to scan, but callers shouldn't crash.

        # Build table_name → sql_qn lookup
        sql_table_qn: dict[str, str] = {}
        for file_path, root_node in sql_items:
            for node in _walk(root_node):
                if node.type == "create_table":
                    name = _sql_get_name(node)
                    if name:
                        qn = _build_sql_qn(
                            name, file_path, self.repo_path, self.project_name
                        )
                        sql_table_qn[name] = qn

        if not sql_table_qn:
            return

        # Pre-build lowercase lookup for fast QUERIES_TABLE matching
        tables_lower: dict[str, str] = {t.lower(): t for t in sql_table_qn}

        bridge_count = 0
        queries_count = 0
        for file_path, root_node, language in orm_items:
            try:
                n_maps, n_qry = self._process_file(
                    file_path, language, sql_table_qn, tables_lower
                )
                bridge_count += n_maps
                queries_count += n_qry
            except Exception as exc:
                logger.warning("OrmBridgePass failed for {}: {}", file_path, exc)

        logger.info(
            "OrmBridgePass: {} MAPS_TO_TABLE + {} QUERIES_TABLE edge(s) across {} file(s)",
            bridge_count,
            queries_count,
            len(orm_items),
        )

    # ------------------------------------------------------------------
    # Per-file processing
    # ------------------------------------------------------------------

    def _process_file(
        self,
        file_path: Path,
        language: cs.SupportedLanguage,
        sql_table_qn: dict[str, str],
        tables_lower: dict[str, str],
    ) -> tuple[int, int]:
        """Returns (maps_to_table_count, queries_table_count)."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return 0, 0

        detector = _LANGUAGE_DETECTORS.get(language)
        maps_count = 0

        if detector:
            mappings = detector(source)
            for class_name, table_name in mappings:
                table_qn = sql_table_qn.get(table_name)
                if not table_qn:
                    continue
                orm_qn = self._resolve_orm_class_qn(class_name, file_path)
                if not orm_qn:
                    continue
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, orm_qn),
                    cs.RelationshipType.MAPS_TO_TABLE,
                    (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, table_qn),
                    {"table_name": table_name, "orm_class": class_name},
                )
                maps_count += 1

        # Raw SQL-in-string usage (creates QUERIES_TABLE edges)
        queries_count = 0
        usages = _detect_sql_usages(source, tables_lower)
        for func_name, orig_tname, op in usages:
            table_qn = sql_table_qn.get(orig_tname)
            if not table_qn:
                continue
            func_qn = self._resolve_func_qn(func_name, file_path)
            if not func_qn:
                continue
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, func_qn),
                cs.RelationshipType.QUERIES_TABLE,
                (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, table_qn),
                {"operation": op, "table_name": orig_tname},
            )
            queries_count += 1

        return maps_count, queries_count

    def _resolve_func_qn(self, func_name: str, file_path: Path) -> str | None:
        """
        Resolve the qualified name of a function/method by name.

        Uses the same priority scheme as ``_resolve_orm_class_qn``:
        file-path-matched candidate from simple_name_lookup → any candidate
        → computed fallback.
        """
        try:
            rel_path_str = file_path.relative_to(self.repo_path).as_posix()
        except ValueError:
            rel_path_str = ""

        candidates = self.simple_name_lookup.get(func_name, set())

        for qn in candidates:
            if rel_path_str and any(
                part in qn
                for part in rel_path_str.replace("/", ".")
                .replace("\\", ".")
                .split(".")[:4]
            ):
                return qn

        if candidates:
            return next(iter(candidates))

        # Fall back to computed QN (node may not exist yet but edge is stored)
        parts = file_path.relative_to(self.repo_path).with_suffix("").parts
        return cs.SEPARATOR_DOT.join([self.project_name, *parts, func_name])

    def _resolve_orm_class_qn(self, class_name: str, file_path: Path) -> str | None:
        """
        Find the best-matching qualified name for an ORM class.

        Priority:
          1. Exact match in simple_name_lookup that is in this file's path
          2. Any match in simple_name_lookup
          3. Computed QN (project + file_parts + class_name)
        """
        try:
            rel_path_str = file_path.relative_to(self.repo_path).as_posix()
        except ValueError:
            rel_path_str = ""

        candidates = self.simple_name_lookup.get(class_name, set())

        # prefer candidate whose QN path matches this file
        for qn in candidates:
            # QN parts are dot-separated; file path uses "/"
            # Convert QN path part back and check suffix
            if rel_path_str and any(
                part in qn
                for part in rel_path_str.replace("/", ".")
                .replace("\\", ".")
                .split(".")[:4]
            ):
                return qn

        # Fall back to any candidate from the lookup
        if candidates:
            return next(iter(candidates))

        # Last resort: compute from path (node may not have been indexed yet)
        return _build_code_qn(class_name, file_path, self.repo_path, self.project_name)
