; =========================================================
; SCALA – ADVANCED GRAPH RAG EDITION
; Production-Ready scala.scm v3
; Semantic + Type + Flow Aware
; =========================================================



; =========================================================
; ROOT
; =========================================================

@query: compilation_unit
(compilation_unit) @scala_root



; =========================================================
; PACKAGE GRAPH
; =========================================================

@query: package_declaration
(package_clause
  (identifier) @package_name) @package_node



; =========================================================
; IMPORT SYSTEM
; =========================================================

@query: import_statements
(import_declaration) @import_edge



; =========================================================
; CLASS / TRAIT / OBJECT
; =========================================================

@query: class_definitions
(class_definition
  (identifier) @class_name) @class_node


(object_definition
  (identifier) @object_name) @object_node


(trait_definition
  (identifier) @trait_name) @trait_node



; =========================================================
; CASE CLASS
; =========================================================

@query: case_class
(class_definition
  (identifier) @case_class_name) @case_class_node



; =========================================================
; COMPANION OBJECT LINK
; =========================================================

@query: companion_object
(object_definition
  name: (identifier) @companion_name) @companion_node



; =========================================================
; INHERITANCE / MIXINS
; =========================================================

@query: inheritance
(class_definition
  name: (identifier) @derived
  extends_clause
    (template
      (type_identifier) @base)) @inheritance_edge


@query: trait_mixin
(template
  (type_identifier) @mixed_trait) @mixin_edge



; =========================================================
; FUNCTIONS
; =========================================================

@query: function_definitions
(function_definition
  name: (identifier) @function_name
  parameters: (parameters)? @params
  return_type: (_)?
  body: (_)?) @function_node


@query: anonymous_functions
(lambda_expression
  parameters: (_)?
  body: (_)?) @lambda_node



; =========================================================
; IMPLICIT / GIVEN (Scala 3)
; =========================================================

@query: implicit_definition
(function_definition
  modifiers: (modifiers (modifier) @mod (#eq? @mod "implicit"))
  name: (identifier) @implicit_name) @implicit_node


@query: given_definition
(given_definition
  name: (identifier)? @given_name
  body: (_)?) @given_node



; =========================================================
; PARAMETERS
; =========================================================

@query: parameter_definitions
(parameter
  name: (identifier) @param_name
  type: (_)?) @param_node



; =========================================================
; CALL GRAPH
; =========================================================

@query: call_expressions
(call_expression
  function: (_) @callee
  arguments: (arguments)? @args) @call_edge


@query: infix_calls
(infix_expression
  left: (_) @left
  operator: (identifier) @operator
  right: (_) @right) @infix_call_edge


@query: apply_calls
(call_expression
  function: (field_expression
              field: (identifier) @apply_target)) @apply_call_edge



; =========================================================
; FIELD ACCESS
; =========================================================

@query: field_access
(field_expression
  value: (_) @object_ref
  field: (identifier) @field_name) @field_access_edge



; =========================================================
; PATTERN MATCHING
; =========================================================

@query: match_expression
(match_expression
  value: (_) @match_value) @match_node


@query: case_clause
(case_clause
  pattern: (_) @case_pattern
  body: (_) @case_body) @case_node



; =========================================================
; FOR-COMPREHENSION
; =========================================================

@query: for_comprehension
(for_expression
  enumerators: (_) @enumerators
  body: (_) @for_body) @for_node



; =========================================================
; VAL / VAR DEFINITIONS
; =========================================================

@query: value_definition
(value_definition
  pattern: (identifier) @val_name
  value: (_) @val_value) @val_node


@query: variable_definition
(variable_definition
  pattern: (identifier) @var_name
  value: (_) @var_value) @var_node



; =========================================================
; TYPE DEFINITIONS
; =========================================================

@query: type_alias
(type_definition
  name: (identifier) @type_name
  value: (_) @type_value) @type_node



; =========================================================
; GENERICS
; =========================================================

@query: type_usage
(type_arguments
  (type_identifier) @generic_type) @type_usage_node



; =========================================================
; RETURN
; =========================================================

@query: return_statement
(return_expression
  (_) @return_value) @return_edge



; =========================================================
; UNIVERSAL FALLBACK
; =========================================================

@query: any_expression
(expression) @any_expression

@query: any_definition
(top_level_definition) @any_definition
