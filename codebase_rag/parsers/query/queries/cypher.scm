; =========================================================
; Cypher (.cypher) – Tree-sitter query stubs
; =========================================================
; NOTE: Cypher language support uses tree-sitter-sql as a grammar fallback
; because there is no dedicated tree-sitter-cypher grammar.
; Structural extraction (node labels, constraints, indexes, relationships)
; is handled entirely by CypherSchemaPass via regex-based text parsing,
; NOT via tree-sitter SCM captures.
;
; The arrays in _QUERY_NAME_MAP for SupportedLanguage.CYPHER are intentionally
; empty, so no tree-sitter captures are compiled for this language.
; This file exists only so that _apply_language_override() does not exit early
; on the missing-file check, and so the log line confirms CYPHER was visited.
;
; If a proper tree-sitter-cypher grammar becomes available in the future,
; the captures below should be filled in with the correct Cypher node types.
; =========================================================

; (no captures — extraction is performed by CypherSchemaPass via regex)
