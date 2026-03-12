from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CypherTemplateMatch:
    name: str
    query: str | None
    confidence: float
    strategy: str
    reason: str
    prompt_hint: str = ""


@dataclass(frozen=True)
class CypherTemplate:
    name: str
    query: str | None
    phrases_any: tuple[str, ...]
    phrases_all: tuple[str, ...] = ()
    direct_threshold: float = 0.95
    prompt_hint: str = ""


class CypherTemplateBank:
    _TEMPLATES: tuple[CypherTemplate, ...] = (
        CypherTemplate(
            name="module_inventory",
            query=(
                "MATCH (m:Module {project_name: $project_name}) "
                "RETURN m.name AS module, m.qualified_name AS qualified_name, m.path AS path, "
                "coalesce(m.pagerank, 0.0) AS pagerank, "
                "coalesce(m.community_id, -1) AS community_id, "
                "coalesce(m.has_cycle, false) AS has_cycle "
                "ORDER BY pagerank DESC, module LIMIT 50"
            ),
            phrases_any=(
                "list modules",
                "show modules",
                "main modules",
                "entry points",
            ),
            direct_threshold=0.7,
            prompt_hint=(
                "For module inventory, prefer MATCH on Module with explicit "
                "project_name scope and return module/path plus pagerank, community_id, and has_cycle."
            ),
        ),
        CypherTemplate(
            name="class_inventory",
            query=(
                "MATCH (m:Module {project_name: $project_name})-[:DEFINES]->(c:Class) "
                "RETURN c.name AS class_name, c.qualified_name AS qualified_name, "
                "coalesce(c.path, m.path, '') AS path, m.path AS module_path, "
                "coalesce(c.visibility, '') AS visibility, "
                "coalesce(c.start_line, 0) AS start_line, coalesce(c.end_line, 0) AS end_line, "
                "coalesce(c.module_qn, m.qualified_name, '') AS module_qn, "
                "coalesce(c.signature, c.signature_lite, '') AS signature, "
                "coalesce(c.docstring, '') AS docstring, "
                "coalesce(c.pagerank, 0.0) AS pagerank, "
                "coalesce(c.community_id, -1) AS community_id, "
                "coalesce(c.has_cycle, false) AS has_cycle "
                "ORDER BY pagerank DESC, class_name LIMIT 80"
            ),
            phrases_any=(
                "list classes",
                "show classes",
                "main classes",
                "important classes",
            ),
            direct_threshold=0.7,
            prompt_hint=(
                "For class inventory, traverse Module-[:DEFINES]->Class and return "
                "qualified_name, path, visibility, line range, docstring, pagerank, community_id, and has_cycle."
            ),
        ),
        CypherTemplate(
            name="function_inventory",
            query=(
                "MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(f) "
                "WHERE f:Function OR f:Method "
                "RETURN f.name AS symbol, f.qualified_name AS qualified_name, labels(f) AS type, "
                "coalesce(f.path, m.path, '') AS path, m.path AS module_path, "
                "coalesce(f.signature, f.signature_lite, '') AS signature, "
                "coalesce(f.visibility, '') AS visibility, "
                "coalesce(f.start_line, 0) AS start_line, coalesce(f.end_line, 0) AS end_line, "
                "coalesce(f.module_qn, m.qualified_name, '') AS module_qn, "
                "coalesce(f.docstring, '') AS docstring, "
                "coalesce(f.pagerank, 0.0) AS pagerank, "
                "coalesce(f.community_id, -1) AS community_id, "
                "coalesce(f.has_cycle, false) AS has_cycle, "
                "coalesce(f.in_call_count, 0) AS in_call_count, "
                "coalesce(f.out_call_count, 0) AS out_call_count, "
                "coalesce(f.dead_code_score, 0.0) AS dead_code_score, "
                "coalesce(f.is_reachable, true) AS is_reachable "
                "ORDER BY pagerank DESC, in_call_count DESC, symbol LIMIT 100"
            ),
            phrases_any=(
                "list functions",
                "show functions",
                "list methods",
                "show methods",
            ),
            direct_threshold=0.7,
            prompt_hint=(
                "For symbol inventory, keep project_name scope and return symbol, "
                "qualified_name, path, signature, docstring, pagerank, call counts, dead_code_score, and reachability."
            ),
        ),
        CypherTemplate(
            name="dependency_hotspots",
            query=(
                "MATCH (m:Module {project_name: $project_name}) "
                "OPTIONAL MATCH (m)-[out_r:CALLS|IMPORTS]->() "
                "WITH m, count(DISTINCT out_r) AS outgoing_edges "
                "OPTIONAL MATCH ()-[in_r:CALLS|IMPORTS]->(m) "
                "RETURN m.name AS module, m.qualified_name AS qualified_name, m.path AS path, "
                "outgoing_edges, count(DISTINCT in_r) AS incoming_edges, "
                "coalesce(m.pagerank, 0.0) AS pagerank, "
                "coalesce(m.community_id, -1) AS community_id, "
                "coalesce(m.has_cycle, false) AS has_cycle "
                "ORDER BY outgoing_edges DESC, incoming_edges DESC, pagerank DESC, module LIMIT 25"
            ),
            phrases_any=(
                "dependency hotspots",
                "hotspots",
                "dependency hotspot",
                "most connected modules",
            ),
            direct_threshold=0.7,
            prompt_hint=(
                "For dependency hotspots, count CALLS/IMPORTS edges per Module and return outgoing_edges, incoming_edges, pagerank, community_id, and has_cycle."
            ),
        ),
        CypherTemplate(
            name="blast_radius",
            query=None,
            phrases_any=(
                "blast radius",
                "impact",
                "affected files",
                "affected symbols",
            ),
            direct_threshold=1.1,
            prompt_hint=(
                "For blast-radius questions, prefer multi-hop traversal over CALLS/IMPORTS/INHERITS/USES "
                "with strict project_name scoping."
            ),
        ),
        CypherTemplate(
            name="imports_overview",
            query=None,
            phrases_any=("imports", "dependencies", "dependency chain"),
            direct_threshold=1.1,
            prompt_hint=(
                "For dependency-chain questions, keep project_name scope and prefer Module-to-Module "
                "or Module-to-Symbol import/call traversal before broad RETURNs."
            ),
        ),
    )

    def inspect(self, natural_language_query: str) -> CypherTemplateMatch | None:
        lowered = natural_language_query.lower()
        best_match: CypherTemplateMatch | None = None

        for template in self._TEMPLATES:
            if template.phrases_all and not all(
                phrase in lowered for phrase in template.phrases_all
            ):
                continue

            match_count = sum(1 for phrase in template.phrases_any if phrase in lowered)
            if match_count <= 0:
                continue

            confidence = min(0.99, 0.55 + (0.18 * match_count))
            strategy = (
                "direct_template"
                if template.query is not None
                and confidence >= template.direct_threshold
                else "prompt_hint"
            )
            candidate = CypherTemplateMatch(
                name=template.name,
                query=template.query if strategy == "direct_template" else None,
                confidence=round(confidence, 3),
                strategy=strategy,
                reason=f"matched_phrases={match_count}",
                prompt_hint=template.prompt_hint,
            )
            if best_match is None or candidate.confidence > best_match.confidence:
                best_match = candidate

        return best_match

    def augment_prompt(self, natural_language_query: str) -> str:
        match = self.inspect(natural_language_query)
        if match is None or not match.prompt_hint:
            return natural_language_query
        return (
            f"{natural_language_query}\n\n"
            "CYPHER TEMPLATE HINT:\n"
            f"- Template: {match.name}\n"
            f"- Hint: {match.prompt_hint}\n"
            "- Keep project scoping explicit and prefer a single compact RETURN clause."
        )
