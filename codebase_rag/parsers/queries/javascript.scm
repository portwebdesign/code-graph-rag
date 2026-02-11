; =========================================================
; JAVASCRIPT / TYPESCRIPT – GRAPH RAG PRODUCTION EDITION
; JS/TS.scm v3
; Noise-Controlled + Graph-Safe Edition
; =========================================================


; =========================================================
; ROOT
; =========================================================

@query: program_root
(program) @program



; =========================================================
; FUNCTION DEFINITIONS
; =========================================================

@query: function_definition
(function_declaration
  name: (identifier) @defined_function
  parameters: (formal_parameters)? @function_params
  body: (statement_block)? @function_body) @function_definition


@query: async_function_definition
(function_declaration
  ; (async) -- INVALID NODE TYPE
  name: (identifier) @defined_function
  parameters: (formal_parameters)? @function_params
  body: (statement_block)? @function_body) @async_function_definition


@query: generator_definition
(generator_function_declaration
  name: (identifier) @defined_function
  parameters: (formal_parameters)? @function_params
  body: (statement_block)? @function_body) @generator_definition


@query: function_expression_definition
(function_expression
  name: (identifier)? @defined_function
  parameters: (formal_parameters)? @function_params
  body: (statement_block)? @function_body) @function_expression_definition


@query: arrow_function_definition
(arrow_function
  parameters: (formal_parameters)? @function_params
  body: (_) @function_body) @arrow_function_definition



; =========================================================
; CLASS DEFINITIONS
; =========================================================

@query: class_definition
(class_declaration
  name: (identifier) @defined_class
  (class_heritage
    (identifier) @base_class)?
  body: (class_body)? @class_body) @class_definition


@query: class_expression
(class
  name: (identifier)? @defined_class
  body: (class_body)? @class_body) @class_expression


@query: method_definition
(method_definition
  name: (property_identifier) @defined_method
  parameters: (formal_parameters)? @method_params
  body: (statement_block)? @method_body) @method_definition



; =========================================================
; IMPORT / EXPORT
; =========================================================

@query: import_edge
(import_statement
  source: (string) @import_source) @import_edge


@query: named_import_edge
(import_statement
  (import_clause
    (named_imports
      (import_specifier
        name: (identifier) @imported_name
        alias: (identifier)? @import_alias)))) @named_import_edge


@query: export_edge
(export_statement) @export_edge



; =========================================================
; VARIABLES & DATA FLOW
; =========================================================

@query: variable_declaration_edge
(variable_declaration
  (variable_declarator
    name: (identifier) @defined_variable
    value: (_) @assigned_value)) @variable_declaration_edge


@query: assignment_edge
(assignment_expression
  left: (identifier) @defined_variable
  right: (_) @assigned_value) @assignment_edge


@query: property_assignment_edge
(assignment_expression
  left: (member_expression
    object: (_) @object_ref
    property: (property_identifier) @defined_property)
  right: (_) @assigned_value) @property_assignment_edge


@query: destructuring_object_edge
(variable_declarator
  name: (object_pattern) @destructured_object
  value: (_) @assigned_value) @destructuring_object_edge


@query: destructuring_array_edge
(variable_declarator
  name: (array_pattern) @destructured_array
  value: (_) @assigned_value) @destructuring_array_edge



; =========================================================
; CALL GRAPH
; =========================================================

@query: call_edge
(call_expression
  function: (identifier) @callee
  arguments: (arguments)? @call_args) @call_edge


@query: method_call_edge
(call_expression
  function: (member_expression
    object: (_) @caller_object
    property: (property_identifier) @callee_method)
  arguments: (arguments)? @call_args) @method_call_edge


@query: constructor_call_edge
(new_expression
  constructor: (identifier) @constructor_name
  arguments: (arguments)? @call_args) @constructor_call_edge



; =========================================================
; MEMBER USAGE
; =========================================================

@query: property_usage_edge
(member_expression
  object: (_) @object_ref
  property: (property_identifier) @used_property) @property_usage_edge



; =========================================================
; CONTROL FLOW
; =========================================================

@query: if_cfg_node
(if_statement
  condition: (_) @if_condition
  consequence: (_) @if_block
  alternative: (_)? @else_block) @if_cfg_node


@query: switch_cfg_node
(switch_statement) @switch_cfg_node

@query: switch_case_cfg_node
(switch_case) @switch_case_cfg_node


@query: for_cfg_node
(for_statement
  initializer: (_)? @init
  condition: (_)? @loop_condition
  increment: (_)? @loop_update
  body: (_) @loop_body) @for_cfg_node


@query: for_in_cfg_node
(for_in_statement) @for_in_cfg_node

; @query: for_of_cfg_node
; (for_of_statement) @for_of_cfg_node


@query: while_cfg_node
(while_statement
  condition: (_) @while_condition
  body: (_) @while_body) @while_cfg_node


@query: do_while_cfg_node
(do_statement) @do_while_cfg_node


@query: try_cfg_node
(try_statement) @try_cfg_node

@query: catch_cfg_node
(catch_clause) @catch_cfg_node

@query: finally_cfg_node
(finally_clause) @finally_cfg_node


@query: break_cfg_node
(break_statement) @break_cfg_node

@query: continue_cfg_node
(continue_statement) @continue_cfg_node

@query: return_edge
(return_statement (_) @return_value) @return_edge

@query: throw_edge
(throw_statement (_) @thrown_value) @throw_edge



; =========================================================
; EXPRESSIONS
; =========================================================

@query: binary_operation
(binary_expression
  left: (_) @left_operand
  operator: (_) @operator_symbol
  right: (_) @right_operand) @binary_operation


; @query: logical_operation
; (logical_expression
;   left: (_) @left_operand
;   operator: (_) @operator_symbol
;   right: (_) @right_operand) @logical_operation


@query: unary_operation
(unary_expression
  argument: (_) @operand) @unary_operation


@query: ternary_operation
(ternary_expression
  condition: (_) @condition
  consequence: (_) @consequence
  alternative: (_) @alternative) @ternary_operation



; =========================================================
; OBJECT / ARRAY LITERALS
; =========================================================

@query: object_literal
(object) @object_literal


@query: object_pair
(pair
  key: (_) @object_key
  value: (_) @object_value) @object_pair


@query: array_literal
(array
  (_) @array_element) @array_literal


@query: spread_element
(spread_element) @spread_element

; @query: rest_element
; (rest_element) @rest_element



; =========================================================
; LITERALS
; =========================================================

@query: string_literal
(string) @string_literal

@query: number_literal
(number) @number_literal

@query: true_literal
(true) @true_literal

@query: false_literal
(false) @false_literal

@query: null_literal
(null) @null_literal

@query: undefined_literal
(undefined) @undefined_literal

@query: regex_literal
(regex) @regex_literal

@query: template_literal
(template_string) @template_literal



; =========================================================
; TYPESCRIPT
; =========================================================

; @query: type_annotation
; (type_annotation) @type_annotation

; @query: generic_type_parameters
; (type_parameters) @generic_type_parameters

; @query: interface_definition
; (interface_declaration) @interface_definition

; @query: type_alias_definition
; (type_alias_declaration) @type_alias_definition

; @query: enum_definition
; (enum_declaration) @enum_definition

; @query: namespace_definition
; (namespace_declaration) @namespace_definition

; @query: module_definition
; (module_declaration) @module_definition



; =========================================================
; JSX
; =========================================================

@query: jsx_element
(jsx_element) @jsx_element

@query: jsx_self_closing
(jsx_self_closing_element) @jsx_self_closing

; @query: jsx_expression
; (jsx_expression_container) @jsx_expression


; @query: css_class_edge
; (
;   jsx_attribute
;     name: (property_identifier) @class_attr
;     value: (_) @class_value
;   (#match? @class_attr "^(class|className)$")
; ) @css_class_edge



; =========================================================
; CONTEXTUAL KEYWORDS
; =========================================================

@query: this_reference
(this) @this_reference

@query: super_reference
(super) @super_reference

@query: decorator
(decorator) @decorator

@query: comment
(comment) @comment
