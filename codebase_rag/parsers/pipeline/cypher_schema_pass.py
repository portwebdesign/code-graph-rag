"""
Cypher Schema Pass  (v4)

Extracts a rich semantic model from Cypher DDL and query files
(Memgraph / Neo4j init scripts, seed data, application queries).

Source patterns handled
-----------------------
1. ``CREATE CONSTRAINT ON (alias:Label) ASSERT …`` (legacy)
   ``CREATE CONSTRAINT name FOR (n:Label) REQUIRE … IS UNIQUE`` (Cypher 5)
   ``CREATE CONSTRAINT name FOR ()-[r:REL]-() REQUIRE … IS NOT NULL`` (edge)
   → ``GraphConstraint`` node + ``HAS_GRAPH_CONSTRAINT`` edge

2. ``CREATE [TEXT|RANGE|BTREE] INDEX [name] [FOR …] ON :Label(props)``
   → persisted as properties on the ``GraphNodeLabel`` node

3. ``CREATE (:Label {prop: val, …})``  (seed / demo data)
   → property names stored on the ``GraphNodeLabel`` node

4. Relationship topology: ``(a:L1)-[:REL]->(b:L2)``
   Full v4 semantics:
   ▸ Multi-label nodes  ``(n:User:Admin)``
   ▸ Undirected edges   ``-[:R]-``
   ▸ OPTIONAL MATCH flag on each triple
   ▸ Relationship properties  ``[:R {weight: 1}]``
   ▸ Variable-length hops  ``[:R*1..5]``
   ▸ Query intent classification  (DDL/READ/WRITE/AGGREGATE/TRAVERSAL)
   ▸ Pattern grouping via ``pattern_id``
   ▸ Alias-only nodes fall back to ``"Unknown"`` (not dropped)
   → ``GraphRelType`` node + ``DEFINES_RELATIONSHIP`` + ``CONNECTS`` edges

5. WHERE clause predicate analysis
   → ``filter_properties`` stored on ``GraphNodeLabel``

Cross-DB / cross-language edges
--------------------------------
  ``Class``    (SQL table)      ─[SYNCS_TO]→     ``GraphNodeLabel``
  ``Function`` (Python/TS)      ─[QUERIES_LABEL]→ ``GraphNodeLabel``

Controlled by env var ``CODEGRAPH_CYPHER_SCHEMA``
  (enabled by default; set to ``0`` / ``false`` / ``no`` to disable).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
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


def _build_qn(name: str, file_path: Path, repo_path: Path, project_name: str) -> str:
    """Reproduce the FQN that ``resolve_fqn_from_ast`` would produce."""
    parts = _generic_file_to_module(file_path, repo_path)
    return cs.SEPARATOR_DOT.join([project_name] + parts + [name])


def _walk_nodes(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from _walk_nodes(child)


def _node_text(node: Node | None) -> str | None:
    if node is None or not node.text:
        return None
    return node.text.decode("utf-8", errors="replace")


def _strip_cypher_comments(source: str) -> str:
    """Remove single-line (``//``) and multi-line (``/* */``) Cypher comments."""
    # Multi-line first
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    # Single-line
    lines = [ln for ln in source.splitlines() if not ln.strip().startswith("//")]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Name normalisation for SYNCS_TO matching
# ---------------------------------------------------------------------------

# Suffixes that indicate the word is already singular (Latin / Greek endings)
_KEEP_TRAILING_S_RE = re.compile(
    r"(?:sis|ous|us|xis|ius|atus|eaus|eus|ess|ness|ress|lass|ass|ss)$"
)


def _normalise_for_sync(name: str) -> str:
    """
    Return a canonical lower-case form for SQL ↔ Cypher label matching.

    Rules applied (most-specific first):
      * Strip snake_case / kebab-case separators
      * Depluralize common English suffixes (``-ies`` → ``-y``, ``-branches`` → ``-branch`` …)
      * Strip trailing ``s`` **only** when the word does NOT end in a Latin/Greek
        singular marker (``-us``, ``-sis``, ``-ness``, etc.)

    Examples
    --------
    ``users``            → ``user``
    ``companies``        → ``company``
    ``nace_codes``       → ``nacecode``
    ``general_tables``   → ``generaltable``
    ``tax_declarations`` → ``taxdeclaration``
    ``status``           → ``status``   (NOT ``statu``)
    ``analysis``         → ``analysis`` (NOT ``analysi``)
    """
    n = name.lower().replace("_", "").replace("-", "")

    for suffix, replacement in (
        ("ies", "y"),  # companies → company
        ("branches", "branch"),
        ("churches", "church"),
        ("addresses", "address"),
        ("processes", "process"),
        ("statuses", "status"),
        ("ses", "s"),  # processes already covered above
        ("ches", "ch"),
        ("shes", "sh"),
        ("xes", "x"),
        ("zes", "z"),
    ):
        if n.endswith(suffix) and len(n) > len(suffix) + 2:
            n = n[: -len(suffix)] + replacement
            break
    else:
        # Generic: strip trailing 's' unless the word has a Latin/Greek singular ending
        if n.endswith("s") and len(n) > 3 and not _KEEP_TRAILING_S_RE.search(n):
            n = n[:-1]

    return n


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RelTriple:
    """
    A relationship triple extracted from Cypher source (v4).

    Enhancements over v3
    --------------------
    src_labels / tgt_labels : all labels on multi-label nodes  (gap 5)
    direction               : "outgoing" | "incoming" | "undirected"  (gap 4)
    is_optional             : True when inside OPTIONAL MATCH  (gap 3)
    rel_props               : property names on the relationship  (gap 6)
    pattern_id              : sequential statement index for pattern grouping  (gap 1)
    query_intent            : DDL | READ | WRITE | AGGREGATE | TRAVERSAL  (gap 10)
    """

    src_label: str
    rel_type: str
    tgt_label: str
    src_labels: list[str] = field(
        default_factory=list
    )  # multi-label: all labels on src
    tgt_labels: list[str] = field(
        default_factory=list
    )  # multi-label: all labels on tgt
    is_variable_length: bool = False
    hop_min: int = 1
    hop_max: int = 1  # -1 → unlimited (``*`` without upper bound)
    direction: str = "outgoing"  # "outgoing" | "incoming" | "undirected"
    is_optional: bool = False
    rel_props: list[str] = field(default_factory=list)
    pattern_id: int = 0
    query_intent: str = "READ"  # DDL | READ | WRITE | AGGREGATE | TRAVERSAL


@dataclass
class WhereFilter:
    """
    A predicate from a Cypher WHERE clause (gap 2).

    Example: ``WHERE u.age > 30``
      → alias="u", property="age", operator=">", value="30"
    """

    alias: str
    property: str
    operator: str
    value: str
    pattern_id: int = 0


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# ── Legacy (Cypher ≤ 3) constraints ──────────────────────────────────────────
# CREATE CONSTRAINT ON (alias:Label) ASSERT alias.prop IS UNIQUE|NOT NULL|…
_CONSTRAINT_LEGACY_RE = re.compile(
    r"CREATE\s+CONSTRAINT\s+ON\s+\(\w+:(\w+)\)\s+ASSERT\s+\w+\.(\w+)\s+IS\s+"
    r"(UNIQUE|NOT\s+NULL|NODE\s+KEY|TYPED\s+[\w\s]+?|EXISTS)",
    re.IGNORECASE,
)

# ── Modern (Cypher 5 / Memgraph ≥ 2.4) node constraints ─────────────────────
# CREATE CONSTRAINT name FOR (n:Label) REQUIRE n.prop IS UNIQUE
# CREATE CONSTRAINT name FOR (n:Label) REQUIRE (n.p1, n.p2) IS NODE KEY
_CONSTRAINT_MODERN_RE = re.compile(
    r"CREATE\s+CONSTRAINT\s+\w+\s+FOR\s+\(\w+:(\w+)\)\s+REQUIRE\s+"
    r"(?:\w+\.(\w+)|\([^)]+\))\s+IS\s+(UNIQUE|NOT\s+NULL|NODE\s+KEY|TYPED\s+[\w\s]+?|EXISTS)",
    re.IGNORECASE,
)

# ── Edge (relationship) constraints (gap 9) ───────────────────────────────────
# CREATE CONSTRAINT name FOR ()-[r:REL]->() REQUIRE r.prop IS NOT NULL
_EDGE_CONSTRAINT_RE = re.compile(
    r"CREATE\s+CONSTRAINT\s+\w+\s+FOR\s+\(\w*\)\s*-\[\w*:(\w+)\]-[>]?\s*\(\w*\)\s+REQUIRE\s+"
    r"\w+\.(\w+)\s+IS\s+(UNIQUE|NOT\s+NULL|EXISTS|TYPED\s+[\w\s]+?)",
    re.IGNORECASE,
)

# ── Regular property indexes (legacy) ────────────────────────────────────────
# CREATE INDEX ON :Label(prop1, prop2)
_INDEX_LEGACY_RE = re.compile(
    r"CREATE\s+INDEX\s+ON\s+:(\w+)\(([^)]+)\)",
    re.IGNORECASE,
)

# ── Named / Cypher-5 indexes ──────────────────────────────────────────────────
# CREATE [BTREE|RANGE|COMPOSITE] INDEX name FOR (n:Label) ON (n.prop1, n.prop2)
_INDEX_NAMED_RE = re.compile(
    r"CREATE\s+(?:BTREE\s+|RANGE\s+|COMPOSITE\s+|VECTOR\s+|FULLTEXT\s+)?"
    r"INDEX\s+\w+\s+FOR\s+\(\w+:(\w+)\)\s+ON\s+\(([^)]+)\)",
    re.IGNORECASE,
)

# ── Text (full-text search) indexes ──────────────────────────────────────────
# CREATE TEXT INDEX indexName ON :Label;
_TEXT_INDEX_RE = re.compile(
    r"CREATE\s+TEXT\s+INDEX\s+\w+\s+ON\s+:(\w+)",
    re.IGNORECASE,
)

# ── Node creation with properties ────────────────────────────────────────────
# CREATE (:Label {…}), MERGE (alias:Label {…})
_CREATE_NODE_RE = re.compile(
    r"CREATE\s+\((?:\w+)?:(\w+)\s*\{([^}]+)\}",
    re.DOTALL | re.IGNORECASE,
)

# ── QUERIES_LABEL: detect Cypher MATCH/MERGE/CREATE references in source code ─
_CY_LABEL_IN_CODE_RE = re.compile(
    r"(?:MATCH|MERGE|CREATE)\s+\([^)]*:(\w+)",
    re.IGNORECASE,
)
# Function / method definition (Python + TS/JS)
_CODE_FUNC_DEF_RE = re.compile(
    r"^(?P<indent>\s{0,12})(?:async\s+)?(?:def|function)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|<)",
    re.MULTILINE,
)
_CODE_LANGUAGES = frozenset({"py", "ts", "js", "tsx", "jsx"})

# ── Tokenizer patterns for relationship extraction ────────────────────────────

# Node token — captures alias + ALL colon-separated labels (gap 5: multi-label)
# group(1) = alias (may be empty), group(2) = labels string e.g. ":User:Admin"
_NODE_TOK_RE = re.compile(
    r"\((\w*)((?:\s*:\s*\w+)*)\s*(?:\{[^}]*\})?\s*\)",
    re.DOTALL,
)

# Relationship bracket with direction arrows:
#   <-[…]-   -[…]->   <-[…]->   -[…]-  (gap 4: undirected = no arrows at all)
_REL_TOK_RE = re.compile(
    r"(<?)--?\[([^\]]*)\]--?(>?)",
    re.DOTALL,
)

# Inside a relationship bracket: :TYPE1|TYPE2  *min..max  {prop: val}
# Groups: (1) type list (pipe-sep)  (2) min-or-exact after *  (3) upper bound after ..
_REL_CONTENT_RE = re.compile(
    r":?\s*([\w]+(?:\s*[|]\s*[\w]+)*)"  # type list:  TYPE_A  or  TYPE_A|TYPE_B
    r"(?:\*(\d*)(?:\.\.(\d*))?)?",  # optional var-len:  *  *3  *1..5  *1..  *..5
    re.IGNORECASE,
)

# Property block inside relationship bracket:  [:R {active: true}]  (gap 6)
_REL_PROPS_BLOCK_RE = re.compile(r"\{([^}]+)\}")

# OPTIONAL MATCH position scanner (gap 3)
_MATCH_KEYWORD_RE = re.compile(r"(OPTIONAL\s+)?MATCH\b", re.IGNORECASE)

# WHERE predicate extractor (gap 2)
# Captures: (alias, property, operator, value)
_WHERE_PREDICATE_RE = re.compile(
    r"(?:^|[\s,])(\w+)\.(\w+)\s*"
    r"(>=|<=|<>|>|<|=|CONTAINS|STARTS\s+WITH|ENDS\s+WITH|IN|IS\s+NOT\s+NULL|IS\s+NULL)\s*"
    r"([^\s,;)\n]+)?",
    re.IGNORECASE,
)

# Query intent classifiers (gap 10)
_DDL_INTENT_RE = re.compile(
    r"^\s*CREATE\s+(?:CONSTRAINT|INDEX|TEXT\s+INDEX)\b", re.IGNORECASE | re.MULTILINE
)
_WRITE_INTENT_RE = re.compile(
    r"\b(?:CREATE|MERGE|SET|DELETE|DETACH\s+DELETE|REMOVE)\b", re.IGNORECASE
)
_AGGR_INTENT_RE = re.compile(r"\b(?:COUNT|SUM|AVG|MAX|MIN|COLLECT)\s*\(", re.IGNORECASE)
_TRAVERSAL_INTENT_RE = re.compile(r"\*(?:\d|\d*\.\.\d*)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Schema-extraction helpers
# ---------------------------------------------------------------------------


def _extract_property_names(props_str: str) -> list[str]:
    """Return property names from a ``{ key: val, … }`` content string."""
    return [m.strip() for m in re.findall(r"(\w+)\s*:", props_str)]


def _classify_intent(stmt: str) -> str:
    """
    Classify the coarse intent of a single Cypher statement (gap 10).

    Returns one of: ``DDL`` | ``WRITE`` | ``AGGREGATE`` | ``TRAVERSAL`` | ``READ``
    """
    if _DDL_INTENT_RE.search(stmt):
        return "DDL"
    if _TRAVERSAL_INTENT_RE.search(stmt):
        return "TRAVERSAL"
    if _AGGR_INTENT_RE.search(stmt):
        return "AGGREGATE"
    if _WRITE_INTENT_RE.search(stmt):
        return "WRITE"
    return "READ"


def _parse_node_labels(labels_str: str) -> list[str]:
    """Parse ``:Label1:Label2`` string into ``['Label1', 'Label2']``."""
    return re.findall(r":(\w+)", labels_str or "")


def _extract_where_predicates(clean_source: str) -> dict[str, set[str]]:
    """
    Extract WHERE clause predicates and return a mapping of
    ``label → {properties used in WHERE filters}`` (gap 2).

    Uses the alias map from each statement to resolve aliases to labels.  Only
    predicates where the alias resolves to a known label are recorded.
    """
    label_filters: dict[str, set[str]] = {}

    for stmt in clean_source.split(";"):
        stmt = stmt.strip()
        if not stmt or "WHERE" not in stmt.upper():
            continue

        # Build alias→primary-label map for this statement
        alias_map: dict[str, str] = {}
        for m in _NODE_TOK_RE.finditer(stmt):
            alias = m.group(1)
            node_labels = _parse_node_labels(m.group(2))
            if alias and node_labels:
                alias_map[alias] = node_labels[0]

        # Find WHERE clause(s)
        where_start = stmt.upper().find("WHERE")
        if where_start == -1:
            continue
        where_section = stmt[where_start:]

        for pred in _WHERE_PREDICATE_RE.finditer(where_section):
            alias = pred.group(1)
            prop = pred.group(2)
            lbl = alias_map.get(alias)
            if lbl:
                label_filters.setdefault(lbl, set()).add(prop)

    return label_filters


def _extract_relationships(clean_source: str) -> list[RelTriple]:
    """
    Extract directed relationship triples from Cypher source (v4).

    Changes over v3
    ---------------
    - Multi-label nodes ``(n:User:Admin)`` → src_labels / tgt_labels  (gap 5)
    - Undirected edges ``-[]-`` → direction="undirected"  (gap 4)
    - OPTIONAL MATCH context → is_optional=True  (gap 3)
    - Relationship properties ``[:R {w:1}]`` → rel_props list  (gap 6)
    - Alias-only nodes (no label) → fallback ``"Unknown"``  (gap 8)
    - Query intent classification per statement  (gap 10)
    - Sequential ``pattern_id`` per statement  (gap 1)
    """
    results: list[RelTriple] = []
    seen: set[tuple[str, str, str, str]] = set()  # (src, rel, tgt, direction)

    for pattern_id, stmt in enumerate(clean_source.split(";")):
        stmt = stmt.strip()
        if not stmt:
            continue

        intent = _classify_intent(stmt)

        # ── Build alias → [labels] map for this statement ─────────────────
        alias_map: dict[str, list[str]] = {}
        for m in _NODE_TOK_RE.finditer(stmt):
            alias = m.group(1)
            node_labels = _parse_node_labels(m.group(2))
            if alias:
                if alias not in alias_map:
                    alias_map[alias] = node_labels if node_labels else []
                else:
                    # Merge: first definition wins for primary; union for all
                    for lbl in node_labels:
                        if lbl not in alias_map[alias]:
                            alias_map[alias].append(lbl)

        # ── Collect MATCH keyword positions for OPTIONAL detection ────────
        match_positions: list[tuple[int, bool]] = []  # (pos, is_optional)
        for m in _MATCH_KEYWORD_RE.finditer(stmt):
            match_positions.append((m.start(), bool(m.group(1))))

        # ── Collect node token positions ──────────────────────────────────
        node_positions: list[tuple[int, int, str]] = []  # (start, end, alias)
        for m in _NODE_TOK_RE.finditer(stmt):
            node_positions.append((m.start(), m.end(), m.group(1)))

        if not node_positions:
            continue

        # ── Process each relationship bracket ─────────────────────────────
        for rel_m in _REL_TOK_RE.finditer(stmt):
            left_arrow = rel_m.group(1) == "<"
            right_arrow = rel_m.group(3) == ">"
            rel_content = rel_m.group(2).strip()
            r_start, r_end = rel_m.start(), rel_m.end()

            # Gap 4: direction
            if left_arrow and not right_arrow:
                direction = "incoming"  # will swap src/tgt below
            elif right_arrow and not left_arrow:
                direction = "outgoing"
            elif left_arrow and right_arrow:
                direction = (
                    "outgoing"  # bi-directional written as <-[]->; treat as outgoing
                )
            else:
                direction = "undirected"

            # Gap 3: is_optional — nearest preceding MATCH keyword
            preceding_matches = [
                (pos, opt) for pos, opt in match_positions if pos < r_start
            ]
            is_optional = preceding_matches[-1][1] if preceding_matches else False

            # Resolve adjacent nodes by position
            preceding_nodes = [n for n in node_positions if n[1] <= r_start]
            following_nodes = [n for n in node_positions if n[0] >= r_end]
            if not preceding_nodes or not following_nodes:
                continue

            near_src_alias = preceding_nodes[-1][2]
            near_tgt_alias = following_nodes[0][2]
            src_lbls = alias_map.get(near_src_alias, [])
            tgt_lbls = alias_map.get(near_tgt_alias, [])

            # Gap 8: fall back to "Unknown" for unlabeled / unseen aliases
            src_primary = (
                src_lbls[0] if src_lbls else ("Unknown" if near_src_alias else None)
            )
            tgt_primary = (
                tgt_lbls[0] if tgt_lbls else ("Unknown" if near_tgt_alias else None)
            )
            if not src_primary or not tgt_primary:
                continue

            # Apply direction swap (incoming: source and target positions are logical)
            if direction == "incoming":
                src_primary, tgt_primary = tgt_primary, src_primary
                src_lbls, tgt_lbls = tgt_lbls, src_lbls
                direction = "outgoing"  # normalise to canonical direction

            # Gap 6: relationship properties
            props_m = _REL_PROPS_BLOCK_RE.search(rel_content)
            rel_props = _extract_property_names(props_m.group(1)) if props_m else []

            # Parse type list + variable-length
            # Strip property block before regex matching
            type_content = _REL_PROPS_BLOCK_RE.sub("", rel_content).strip()
            cm = _REL_CONTENT_RE.match(type_content)
            if not cm:
                continue

            types_raw = cm.group(1)
            has_var_len = cm.group(2) is not None
            hop_min_str = cm.group(2) or ""
            hop_max_str = cm.group(3) if cm.group(3) is not None else ""

            hop_min = int(hop_min_str) if hop_min_str.isdigit() else 1
            if not has_var_len:
                hop_max = 1
            elif cm.group(3) is None:
                hop_max = int(hop_min_str) if hop_min_str.isdigit() else -1
            elif not hop_max_str:
                hop_max = -1
            else:
                hop_max = int(hop_max_str)

            # Expand multi-type: TYPE_A|TYPE_B
            for rel_type in re.split(r"\s*\|\s*", types_raw):
                rel_type = rel_type.strip()
                if not rel_type:
                    continue
                key = (src_primary, rel_type, tgt_primary, direction)
                if key not in seen:
                    seen.add(key)
                    results.append(
                        RelTriple(
                            src_label=src_primary,
                            rel_type=rel_type,
                            tgt_label=tgt_primary,
                            src_labels=list(src_lbls),
                            tgt_labels=list(tgt_lbls),
                            is_variable_length=has_var_len,
                            hop_min=hop_min,
                            hop_max=hop_max,
                            direction=direction,
                            is_optional=is_optional,
                            rel_props=rel_props,
                            pattern_id=pattern_id,
                            query_intent=intent,
                        )
                    )

    return results


# ---------------------------------------------------------------------------
# Main pass class
# ---------------------------------------------------------------------------


class CypherSchemaPass:
    """
    Post-processing pass that extracts a rich schema model from Cypher DDL
    files and adds it to the knowledge graph.

    Must run *after* ``SqlRelationPass`` so that SQL table QNs are present for
    ``SYNCS_TO`` link generation.
    """

    def __init__(
        self,
        ingestor,
        repo_path: Path,
        project_name: str,
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name

        self.enabled = os.getenv("CODEGRAPH_CYPHER_SCHEMA", "1").lower() not in {
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
        """
        Iterate all cached ASTs.

        ``.cypher`` files are identified by extension (tree-sitter-sql is used
        as a fallback grammar but its parse tree does not reflect Cypher DDL
        structure); this pass re-reads the source text and applies regex-based
        extraction.

        SQL files in the same cache are scanned to build a table-name → QN
        lookup so that ``SYNCS_TO`` edges can be emitted without an extra pass.
        """
        if not self.enabled:
            return

        cypher_paths: list[Path] = []
        sql_items: list[tuple[Path, Node]] = []
        code_items: list[tuple[Path, cs.SupportedLanguage]] = []

        for file_path, (root_node, language) in ast_items:
            if file_path.suffix.lower() == ".cypher":
                cypher_paths.append(file_path)
            elif language == cs.SupportedLanguage.SQL:
                sql_items.append((file_path, root_node))
            elif file_path.suffix.lstrip(".").lower() in _CODE_LANGUAGES:
                code_items.append((file_path, language))

        if not cypher_paths:
            logger.debug("CypherSchemaPass: no .cypher files in cache — skipping")
            return

        # Build normalised_name → SQL Class QN lookup
        sql_qn_lookup: dict[str, str] = {}
        for file_path, root_node in sql_items:
            for node in _walk_nodes(root_node):
                if node.type in (
                    "create_table",
                    "create_view",
                    "create_materialized_view",
                ):
                    name = _sql_get_name(node)
                    if name:
                        sql_qn_lookup[_normalise_for_sync(name)] = _build_qn(
                            name, file_path, self.repo_path, self.project_name
                        )
        logger.debug("CypherSchemaPass: SQL lookup → {} entries", len(sql_qn_lookup))

        # Process each Cypher file; collect known label names for QUERIES_LABEL
        known_labels: set[str] = set()  # populated during file processing
        totals = [
            0
        ] * 8  # labels, constraints, idx, text_idx, rel_types, syncs, connects, q_label
        for fp in cypher_paths:
            try:
                counts = self._process_file(fp, sql_qn_lookup, known_labels)
                for i, v in enumerate(counts):
                    totals[i] += v
            except Exception as exc:
                logger.warning("CypherSchemaPass failed for {}: {}", fp, exc)

        # Emit QUERIES_LABEL edges for code files that reference Cypher labels
        ql_count = self._process_queries_label(code_items, known_labels)
        totals[7] += ql_count

        logger.info(
            "CypherSchemaPass: {} Cypher file(s) → "
            "{} labels, {} constraints, {} prop-idx, {} txt-idx, "
            "{} rel-types, {} SYNCS_TO, {} CONNECTS, {} QUERIES_LABEL",
            len(cypher_paths),
            *totals,
        )

    # ------------------------------------------------------------------
    # Per-file extraction
    # ------------------------------------------------------------------

    def _process_file(
        self,
        file_path: Path,
        sql_qn_lookup: dict[str, str],
        known_labels: set[str],
    ) -> tuple[int, int, int, int, int, int, int]:
        """
        Returns (label_count, constraint_count, index_count, text_index_count,
                 rel_type_count, syncs_to_count, connects_count).
        """
        source = file_path.read_text(encoding="utf-8", errors="replace")
        clean = _strip_cypher_comments(source)

        rel_path = file_path.relative_to(self.repo_path).as_posix()
        abs_path = file_path.resolve().as_posix()

        # ── Accumulate per-label information ──────────────────────────────
        # label → { constraints [(prop, kind)], index_props [str], text_index bool, node_props [str] }
        LabelInfo = dict  # type alias for readability
        labels: dict[str, LabelInfo] = {}

        def _get(lbl: str) -> LabelInfo:
            if lbl not in labels:
                labels[lbl] = {
                    "constraints": [],
                    "index_props": [],
                    "text_index": False,
                    "node_props": set(),
                    "filter_props": [],  # gap 2: WHERE-filtered properties
                }
            return labels[lbl]

        # ── CONSTRAINTS (legacy + modern Cypher5) ────────────────────────
        for m in _CONSTRAINT_LEGACY_RE.finditer(clean):
            lbl = m.group(1)
            prop = m.group(2) or ""
            kind = m.group(3).upper().replace("  ", " ")
            _get(lbl)["constraints"].append((prop, kind))
        for m in _CONSTRAINT_MODERN_RE.finditer(clean):
            lbl = m.group(1)
            prop = m.group(2) or ""
            kind = m.group(3).upper().replace("  ", " ")
            _get(lbl)["constraints"].append((prop, kind))

        # ── PROPERTY INDEXES (legacy + named) ────────────────────────────
        for m in _INDEX_LEGACY_RE.finditer(clean):
            lbl = m.group(1)
            props = [p.strip() for p in m.group(2).split(",") if p.strip()]
            _get(lbl)["index_props"].extend(props)
        for m in _INDEX_NAMED_RE.finditer(clean):
            lbl = m.group(1)
            raw = re.sub(r"\w+\.", "", m.group(2))  # strip alias. prefix
            props = [p.strip() for p in raw.split(",") if p.strip()]
            _get(lbl)["index_props"].extend(props)

        # ── TEXT INDEXES ──────────────────────────────────────────────────
        for m in _TEXT_INDEX_RE.finditer(clean):
            _get(m.group(1))["text_index"] = True

        # ── NODE PROPERTIES from seed / CREATE statements ─────────────────
        for m in _CREATE_NODE_RE.finditer(clean):
            lbl = m.group(1)
            prop_names = _extract_property_names(m.group(2))
            _get(lbl)["node_props"].update(prop_names)

        # ── Discover labels from ALL node tokens (handles multi-label, gap 5) ───
        # Covers: CREATE, MATCH, MERGE, OPTIONAL MATCH, seed data in one pass.
        for m in _NODE_TOK_RE.finditer(clean):
            for lbl in _parse_node_labels(m.group(2)):
                _get(lbl)  # register; no extra props needed

        # ── WHERE filter properties (gap 2) ───────────────────────────────
        where_filters = _extract_where_predicates(clean)
        for lbl, filter_props in where_filters.items():
            _get(lbl)["filter_props"] = sorted(filter_props)

        # ── Edge constraints (gap 9) ───────────────────────────────────
        # Stored separately; keyed by rel_type
        edge_constraints: dict[str, list[tuple[str, str]]] = {}
        for m in _EDGE_CONSTRAINT_RE.finditer(clean):
            rel_type_ec = m.group(1)
            prop_ec = m.group(2)
            kind_ec = m.group(3).upper().replace("  ", " ")
            edge_constraints.setdefault(rel_type_ec, []).append((prop_ec, kind_ec))

        if not labels:
            return 0, 0, 0, 0, 0, 0, 0

        # Register known labels for QUERIES_LABEL scanning
        known_labels.update(labels.keys())
        label_count = 0
        constraint_count = 0
        index_count = 0
        text_index_count = 0

        for lbl, info in labels.items():
            node_qn = f"{self.project_name}.cypher.{lbl}"
            unique_idx_props = sorted(set(info["index_props"]))
            sorted_node_props = sorted(info["node_props"])

            node_props: dict = {
                cs.KEY_QUALIFIED_NAME: node_qn,
                cs.KEY_NAME: lbl,
                cs.KEY_LANGUAGE: "cypher",
                cs.KEY_PATH: rel_path,
                cs.KEY_REPO_REL_PATH: rel_path,
                cs.KEY_ABS_PATH: abs_path,
                cs.KEY_SYMBOL_KIND: "graph_node_label",
                "cypher_label": lbl,
                "constraint_count": len(info["constraints"]),
                "index_count": len(unique_idx_props),
                "index_properties": ",".join(unique_idx_props),
                "has_text_index": info["text_index"],
                "node_properties": ",".join(sorted_node_props),
                "node_property_count": len(sorted_node_props),
                # gap 2: properties commonly used in WHERE clauses
                "filter_properties": ",".join(info.get("filter_props", [])),
            }
            self.ingestor.ensure_node_batch(cs.NodeLabel.GRAPH_NODE_LABEL, node_props)
            label_count += 1

            if info["text_index"]:
                text_index_count += 1
            index_count += len(unique_idx_props)

            # Write GraphConstraint nodes + HAS_GRAPH_CONSTRAINT edges
            for prop, kind in info["constraints"]:
                c_qn = f"{node_qn}.constraint.{prop}"
                c_props: dict = {
                    cs.KEY_QUALIFIED_NAME: c_qn,
                    cs.KEY_NAME: f"{lbl}.{prop}",
                    cs.KEY_LANGUAGE: "cypher",
                    cs.KEY_PATH: rel_path,
                    cs.KEY_REPO_REL_PATH: rel_path,
                    cs.KEY_ABS_PATH: abs_path,
                    cs.KEY_SYMBOL_KIND: "graph_constraint",
                    "cypher_label": lbl,
                    "constraint_property": prop,
                    "constraint_kind": kind,
                }
                self.ingestor.ensure_node_batch(cs.NodeLabel.GRAPH_CONSTRAINT, c_props)
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.GRAPH_NODE_LABEL, cs.KEY_QUALIFIED_NAME, node_qn),
                    cs.RelationshipType.HAS_GRAPH_CONSTRAINT,
                    (cs.NodeLabel.GRAPH_CONSTRAINT, cs.KEY_QUALIFIED_NAME, c_qn),
                    {"constraint_property": prop, "constraint_kind": kind},
                )
                constraint_count += 1

        # ── Extract and write relationship types ──────────────────────────
        rels = _extract_relationships(clean)
        rel_type_nodes_written: set[str] = set()
        connect_seen: set[tuple[str, str, str, str]] = set()  # (src, rt, tgt, dir)
        rel_type_count = 0
        connects_count = 0

        for rt in rels:
            src_lbl = rt.src_label
            tgt_lbl = rt.tgt_label
            rel_type = rt.rel_type
            # Skip Unknown placeholder labels in graph writes
            if src_lbl == "Unknown" or tgt_lbl == "Unknown":
                continue
            src_qn = f"{self.project_name}.cypher.{src_lbl}"
            tgt_qn = f"{self.project_name}.cypher.{tgt_lbl}"
            rt_qn = f"{self.project_name}.cypher.reltype.{rel_type}"

            # Ensure GraphRelType node exists
            if rt_qn not in rel_type_nodes_written:
                ec_list = edge_constraints.get(rel_type, [])
                self.ingestor.ensure_node_batch(
                    cs.NodeLabel.GRAPH_REL_TYPE,
                    {
                        cs.KEY_QUALIFIED_NAME: rt_qn,
                        cs.KEY_NAME: rel_type,
                        cs.KEY_LANGUAGE: "cypher",
                        cs.KEY_PATH: rel_path,
                        cs.KEY_REPO_REL_PATH: rel_path,
                        cs.KEY_ABS_PATH: abs_path,
                        cs.KEY_SYMBOL_KIND: "graph_rel_type",
                        "rel_type": rel_type,
                        # gap 9: edge-level constraints
                        "edge_constraint_count": len(ec_list),
                        "edge_constraints": ",".join(f"{p}:{k}" for p, k in ec_list),
                        # gap 10: query intent observed for this rel type
                        "query_intent": rt.query_intent,
                    },
                )
                rel_type_nodes_written.add(rt_qn)
                rel_type_count += 1

            # DEFINES_RELATIONSHIP: source label → rel type node
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.GRAPH_NODE_LABEL, cs.KEY_QUALIFIED_NAME, src_qn),
                cs.RelationshipType.DEFINES_RELATIONSHIP,
                (cs.NodeLabel.GRAPH_REL_TYPE, cs.KEY_QUALIFIED_NAME, rt_qn),
                {"rel_type": rel_type, "target_label": tgt_lbl},
            )

            # CONNECTS: source label → target label (direct shortcut for traversal)
            ck = (src_lbl, rel_type, tgt_lbl, rt.direction)
            if ck not in connect_seen:
                connect_seen.add(ck)
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.GRAPH_NODE_LABEL, cs.KEY_QUALIFIED_NAME, src_qn),
                    cs.RelationshipType.CONNECTS,
                    (cs.NodeLabel.GRAPH_NODE_LABEL, cs.KEY_QUALIFIED_NAME, tgt_qn),
                    {
                        "rel_type": rel_type,
                        "direction": rt.direction,  # gap 4
                        "is_variable_length": rt.is_variable_length,
                        "hop_min": rt.hop_min,
                        "hop_max": rt.hop_max,
                        "is_optional": rt.is_optional,  # gap 3
                        "edge_properties": ",".join(rt.rel_props),  # gap 6
                        "src_labels": ",".join(rt.src_labels),  # gap 5
                        "tgt_labels": ",".join(rt.tgt_labels),  # gap 5
                        "pattern_id": rt.pattern_id,  # gap 1
                        "query_intent": rt.query_intent,  # gap 10
                    },
                )
                connects_count += 1

        # ── SYNCS_TO: SQL Class → GraphNodeLabel ──────────────────────────
        syncs_count = 0
        if sql_qn_lookup:
            for lbl in labels:
                sql_qn = sql_qn_lookup.get(_normalise_for_sync(lbl))
                if sql_qn:
                    node_qn = f"{self.project_name}.cypher.{lbl}"
                    self.ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, sql_qn),
                        cs.RelationshipType.SYNCS_TO,
                        (cs.NodeLabel.GRAPH_NODE_LABEL, cs.KEY_QUALIFIED_NAME, node_qn),
                        {"sync_source": "cypher_schema_pass"},
                    )
                    syncs_count += 1

        return (
            label_count,
            constraint_count,
            index_count,
            text_index_count,
            rel_type_count,
            syncs_count,
            connects_count,
        )

    # ------------------------------------------------------------------
    # QUERIES_LABEL: detect Cypher label usage in source code
    # ------------------------------------------------------------------

    def _process_queries_label(
        self,
        code_items: list[tuple[Path, cs.SupportedLanguage]],
        known_labels: set[str],
    ) -> int:
        """
        Scan Python / TypeScript / JavaScript source files for
        ``MATCH (:Label)`` and ``MERGE (:Label)`` patterns and emit
        ``Function -[QUERIES_LABEL]-> GraphNodeLabel`` edges.

        Returns the total number of QUERIES_LABEL edges emitted.
        """
        if not known_labels:
            return 0

        total = 0
        for file_path, _lang in code_items:
            try:
                source = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            func_matches = list(_CODE_FUNC_DEF_RE.finditer(source))
            for i, fm in enumerate(func_matches):
                func_name = fm.group("name")
                func_start = fm.start()
                func_end = (
                    func_matches[i + 1].start()
                    if i + 1 < len(func_matches)
                    else len(source)
                )
                func_body = source[func_start:func_end]

                seen_labels: set[str] = set()
                for m in _CY_LABEL_IN_CODE_RE.finditer(func_body):
                    lbl = m.group(1)
                    if lbl in known_labels and lbl not in seen_labels:
                        seen_labels.add(lbl)
                        func_qn = self._resolve_func_qn(func_name, file_path)
                        node_qn = f"{self.project_name}.cypher.{lbl}"
                        self.ingestor.ensure_relationship_batch(
                            (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, func_qn),
                            cs.RelationshipType.QUERIES_LABEL,
                            (
                                cs.NodeLabel.GRAPH_NODE_LABEL,
                                cs.KEY_QUALIFIED_NAME,
                                node_qn,
                            ),
                            {"cypher_label": lbl},
                        )
                        total += 1

        return total

    def _resolve_func_qn(self, func_name: str, file_path: Path) -> str:
        """Compute a best-effort qualified name for a function."""
        try:
            parts = file_path.relative_to(self.repo_path).with_suffix("").parts
        except ValueError:
            parts = (file_path.stem,)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts, func_name])
