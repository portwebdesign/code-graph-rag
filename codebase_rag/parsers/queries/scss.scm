; =========================================================
; SCSS – ADVANCED GRAPH RAG EDITION
; Production-Ready SCSS.scm v3
; Design-System + Dependency Aware
; =========================================================



; =========================================================
; ROOT
; =========================================================

@query: stylesheet_root
(stylesheet) @scss_root



; =========================================================
; IMPORT SYSTEM
; =========================================================

@query: import_statements
(import_statement) @import_edge



; =========================================================
; VARIABLES
; =========================================================

@query: variable_declaration
(variable) @variable_definition


@query: variable_usage
(variable) @variable_reference



; =========================================================
; MIXINS
; =========================================================

@query: mixin_declaration
(mixin_statement) @mixin_node


@query: mixin_include
(include_statement) @mixin_call_edge



; =========================================================
; FUNCTIONS
; =========================================================

@query: function_definition
(function_statement) @function_node


@query: function_call
(call_expression) @function_call_edge



; =========================================================
; SELECTOR GRAPH
; =========================================================

@query: class_selector
(class_selector) @class_node

@query: id_selector
(id_selector) @id_node

@query: nested_rule
(rule_set
  selector: (_) @parent_selector
  body:
    (block
      (rule_set
        selector: (_) @child_selector))) @selector_hierarchy_edge



; =========================================================
; EXTEND GRAPH
; =========================================================

@query: extend_statement
(extend_statement
  (selector) @extended_selector) @extend_edge



; =========================================================
; MEDIA QUERIES
; =========================================================

@query: media_query
(at_rule
  name: (at_keyword) @at_name
  prelude: (_) @media_condition
  (#eq? @at_name "@media")) @media_query_node



; =========================================================
; DESIGN TOKENS
; =========================================================

@query: color_token
(declaration
  property: (property_name) @prop
  value: (color_value) @color_value) @color_token_node


@query: spacing_token
(declaration
  property: (property_name) @spacing_prop
  value: (dimension) @spacing_value) @spacing_token_node



; =========================================================
; TAILWIND INTELLIGENCE
; =========================================================

@query: tailwind_at_rules
(
  at_rule
    name: (at_keyword) @tailwind_at_name
  (#match? @tailwind_at_name "^@(tailwind|apply|layer|source)$")
) @tailwind_at_node



; =========================================================
; RULE BLOCK
; =========================================================

@query: rule_set
(rule_set
  selector: (_) @selector
  body: (block) @rule_body) @rule_node



; =========================================================
; UNIVERSAL FALLBACK
; =========================================================

@query: any_declaration
(declaration) @any_declaration

@query: any_rule
(rule_set) @any_rule

@query: any_at_rule
(at_rule) @any_at_rule
