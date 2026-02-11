; =========================================================
; PYTHON – ADVANCED GRAPH RAG EDITION
; Production-Ready Advanced python.scm v2
; Graph-Aware Semantic Edition
; Call + Data Flow Optimized Edition
; =========================================================



; =========================================================
; STRUCTURE LAYER
; =========================================================

@query: function_definition
(function_definition
  name: (identifier) @defined_function
  parameters: (parameters)? @function_params
  body: (block)? @function_body) @function_definition


@query: async_function_definition
(function_definition
  "async"
  name: (identifier) @defined_async_function
  parameters: (parameters)? @function_params
  body: (block)? @function_body) @async_function_definition


@query: method_definition
(class_definition
  name: (identifier) @class_name
  body: (block
    (function_definition
      name: (identifier) @defined_method
      parameters: (parameters)? @method_params
      body: (block)? @method_body) @method_definition))


@query: class_definition
(class_definition
  name: (identifier) @defined_class
  (argument_list)? @base_list
  body: (block)? @class_body) @class_definition


@query: inheritance_edge
(class_definition
  name: (identifier) @derived_class
  (argument_list
    (identifier) @base_class)) @inheritance_edge


@query: metaclass_definition
(class_definition
  name: (identifier) @class_name
  (argument_list
    (keyword_argument
      name: (identifier) @kw
      value: (identifier) @metaclass_name
      (#eq? @kw "metaclass")))) @metaclass_definition



; =========================================================
; IMPORTS (Alias Aware)
; =========================================================

@query: import_edge
(import_statement
  (aliased_import
    name: (dotted_name) @imported_module
    alias: (identifier)? @import_alias)) @import_edge


; (import_from_statement) @import_from_edge -- BROKEN PATTERN
; (import_from_statement
;   module_name: (dotted_name) @from_module
;   (aliased_import
;     name: (identifier) @imported_name
;     alias: (identifier)? @import_alias)) @import_from_edge



; =========================================================
; DOCSTRINGS
; =========================================================

@query: module_docstring
(module
  (expression_statement
    (string) @module_docstring))


@query: function_docstring
(function_definition
  body: (block
    (expression_statement
      (string) @function_docstring)))


@query: class_docstring
(class_definition
  body: (block
    (expression_statement
      (string) @class_docstring)))



; =========================================================
; ASSIGNMENTS (Data Flow Aware)
; =========================================================

@query: assignment_edge
(assignment
  left: (identifier) @defined_variable
  right: (_) @assigned_value) @assignment_edge


@query: attribute_assignment_edge
(assignment
  left: (attribute
    object: (_) @object_ref
    attribute: (identifier) @defined_attribute)
  right: (_) @assigned_value) @attribute_assignment_edge


@query: subscript_assignment_edge
(assignment
  left: (subscript
    value: (_) @container_ref
    subscript: (_) @index_key)
  right: (_) @assigned_value) @subscript_assignment_edge



@query: destructuring_assignment_edge
(assignment
  left: (tuple_pattern) @defined_variable
  right: (_) @assigned_value) @destructuring_assignment_edge


@query: typed_assignment_edge
(assignment
  left: (identifier) @defined_variable
  type: (_) @type_annotation
  right: (_) @assigned_value) @typed_assignment_edge



; =========================================================
; PARAMETERS
; =========================================================

@query: param_name
(parameters
  (identifier) @param_name)


@query: default_param
(parameters
  (default_parameter
    name: (identifier) @param_name
    value: (_) @default_value))


@query: typed_param
(parameters
  (typed_parameter
    (identifier) @param_name
    (_) @param_type))


@query: varargs_param
(parameters
  (list_splat_pattern (identifier) @varargs))


@query: kwargs_param
(parameters
  (dictionary_splat_pattern (identifier) @kwargs))



; =========================================================
; CALL GRAPH LAYER
; =========================================================

@query: call_edge
(call
  function: (identifier) @callee
  arguments: (argument_list)? @call_args) @call_edge


@query: method_call_edge
(call
  function:
    (attribute
      object: (_) @caller_object
      attribute: (identifier) @callee_method)
  arguments: (argument_list)? @call_args) @method_call_edge


@query: chained_call_edge
(call
  function:
    (attribute
      object: (attribute) @chained_object
      attribute: (identifier) @callee_method)
  arguments: (argument_list)? @call_args) @chained_call_edge



; =========================================================
; ATTRIBUTE USAGE
; =========================================================

@query: attribute_usage_edge
(attribute
  object: (_) @object_ref
  attribute: (identifier) @used_attribute) @attribute_usage_edge



; =========================================================
; CONTROL FLOW GRAPH
; =========================================================

@query: if_cfg_node
(if_statement
  condition: (_) @if_condition
  consequence: (block) @if_block
  alternative: (block)? @else_block) @if_cfg_node


@query: for_cfg_node
(for_statement
  left: (_) @loop_variable
  right: (_) @loop_iterable
  body: (block) @loop_body) @for_cfg_node


@query: while_cfg_node
(while_statement
  condition: (_) @while_condition
  body: (block) @while_body) @while_cfg_node


@query: try_cfg_node
(try_statement
  body: (block) @try_block) @try_cfg_node


@query: except_cfg_node
(except_clause
  (_) @exception_type
  (block) @handler_block) @except_cfg_node


@query: finally_block
(try_statement
  (finally_clause
    (block) @finally_block))



; =========================================================
; COMPREHENSIONS
; =========================================================

@query: list_comprehension_edge
(list_comprehension
  body: (_) @comp_body
  (for_in_clause
    left: (_) @defined_variable
    right: (_) @iterable_source)) @list_comprehension_edge


@query: set_comprehension
(set_comprehension) @set_comprehension

@query: dict_comprehension
(dictionary_comprehension) @dict_comprehension

@query: generator_expression
(generator_expression) @generator_expression



; =========================================================
; PATTERN MATCHING
; =========================================================

@query: match_node
(match_statement
  subject: (_) @match_subject) @match_node


@query: case_node
(case_clause
  (_) @case_pattern
  (block) @case_body) @case_node



; =========================================================
; OPERATORS
; =========================================================

@query: binary_operation
(binary_operator
  left: (_) @left_operand
  operator: (_) @operator_symbol
  right: (_) @right_operand) @binary_operation


@query: boolean_operation
(boolean_operator) @boolean_operation


@query: comparison_operation
(comparison_operator) @comparison_operation


@query: unary_operation
(unary_operator
  argument: (_) @operand) @unary_operation



; =========================================================
; GENERICS
; =========================================================

@query: generic_type_usage
(subscript
  value: (identifier) @generic_type
  subscript: (_) @generic_parameter) @generic_type_usage



; =========================================================
; LITERALS
; =========================================================

@query: string_literal
(string) @string_literal

@query: int_literal
(integer) @int_literal

@query: float_literal
(float) @float_literal

@query: true_literal
(true) @true_literal

@query: false_literal
(false) @false_literal

@query: none_literal
(none) @none_literal

@query: list_literal
(list) @list_literal

@query: dict_literal
(dictionary) @dict_literal

@query: set_literal
(set) @set_literal

; @query: tuple_literal
; (tuple) @tuple_literal

; @query: f_string_literal
; (f_string) @f_string_literal



; =========================================================
; DATA FLOW USAGE
; =========================================================

@query: variable_usage
(identifier) @variable_usage


@query: walrus_assignment_edge
(named_expression
  name: (identifier) @defined_variable
  value: (_) @assigned_value) @walrus_assignment_edge



; =========================================================
; RETURNS & RAISE
; =========================================================

@query: return_edge
(return_statement
  (_) @return_value) @return_edge


@query: raise_edge
(raise_statement
  (_) @raised_expression) @raise_edge



; =========================================================
; CONTEXT STATEMENTS
; =========================================================

@query: with_cfg_node
(with_statement
  body: (block) @with_body) @with_cfg_node


@query: global_statement
(global_statement) @global_statement

@query: nonlocal_statement
(nonlocal_statement) @nonlocal_statement

@query: break_statement
(break_statement) @break_statement

@query: continue_statement
(continue_statement) @continue_statement

@query: pass_statement
(pass_statement) @pass_statement

@query: delete_statement
(delete_statement) @delete_statement



; =========================================================
; UNIVERSAL CAPTURE
; =========================================================

@query: any_expression
(expression) @any_expression

; @query: any_statement
; (statement) @any_statement
