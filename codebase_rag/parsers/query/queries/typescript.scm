; =========================================================
; TYPESCRIPT – ADVANCED GRAPH RAG EDITION
; Production-Ready TypeScript.scm v3
; Type-System + Call Graph + Decorator Aware
; =========================================================



; =========================================================
; ROOT
; =========================================================

@query: program_root
(program) @program



; =========================================================
; FUNCTION DEFINITIONS
; =========================================================

@query: function_declaration
(function_declaration
  name: (identifier) @defined_function
  parameters: (formal_parameters)? @function_params
  return_type: (type_annotation)? @return_type
  body: (statement_block)? @function_body) @function_definition


@query: async_function
(function_declaration
  "async"
  name: (identifier) @defined_async_function
  parameters: (formal_parameters)? @function_params
  body: (statement_block)? @function_body) @async_function_definition


@query: generator_function
(generator_function_declaration
  name: (identifier) @defined_generator
  parameters: (formal_parameters)? @function_params
  body: (statement_block)? @function_body) @generator_definition


@query: arrow_function
(arrow_function
  parameters: (formal_parameters)? @function_params
  body: (_) @function_body) @arrow_function_definition



; =========================================================
; METHOD DEFINITIONS
; =========================================================

@query: method_definition
(method_definition
  name: (property_identifier) @defined_method
  parameters: (formal_parameters)? @method_params
  return_type: (type_annotation)? @return_type
  body: (statement_block)? @method_body) @method_definition



; =========================================================
; CLASS / INTERFACE / TYPE SYSTEM
; =========================================================

@query: class_definition
(class_declaration
  name: (type_identifier) @defined_class
  (type_parameters)? @generic_params
  (class_heritage)? @heritage
  (class_body)? @class_body) @class_node


@query: interface_definition
(interface_declaration
  name: (type_identifier) @defined_interface
  (type_parameters)? @generic_params
  (interface_body)? @interface_body) @interface_node


@query: enum_definition
(enum_declaration
  name: (identifier) @defined_enum
  (enum_body)? @enum_body) @enum_node


@query: type_alias
(type_alias_declaration
  name: (type_identifier) @defined_type
  (type_parameters)? @generic_params
  value: (_) @type_value) @type_alias_node



; =========================================================
; GENERICS USAGE
; =========================================================

@query: generic_type_usage
(generic_type
  (type_identifier) @generic_type
  (type_arguments) @generic_args) @generic_usage



; =========================================================
; DECORATORS
; =========================================================

@query: decorator_usage
(decorator
  (call_expression
    function: (identifier) @decorator_name)) @decorator_edge



; =========================================================
; IMPORT / EXPORT
; =========================================================

@query: import_statement
(import_statement
  source: (string) @import_source) @import_edge


@query: named_import
(import_statement
  (import_clause
    (named_imports
      (import_specifier
        name: (identifier) @imported_name
        alias: (identifier)? @import_alias)))) @named_import_edge


@query: export_statement
(export_statement) @export_edge



; =========================================================
; CALL GRAPH
; =========================================================

@query: call_expression
(call_expression
  function: (identifier) @callee
  arguments: (arguments)? @call_args) @call_edge


@query: method_call
(call_expression
  function:
    (member_expression
      object: (_) @caller_object
      property: (property_identifier) @callee_method)
  arguments: (arguments)? @call_args) @method_call_edge


@query: constructor_call
(new_expression
  constructor: (identifier) @constructor_name
  arguments: (arguments)? @call_args) @constructor_call_edge



; =========================================================
; ASYNC FLOW
; =========================================================

@query: await_expression
(await_expression
  (_) @awaited_value) @await_edge



; =========================================================
; VARIABLE & DATA FLOW
; =========================================================

@query: variable_declaration
(variable_declaration
  (variable_declarator
    name: (identifier) @defined_variable
    value: (_) @assigned_value)) @variable_edge


@query: assignment_expression
(assignment_expression
  left: (identifier) @defined_variable
  right: (_) @assigned_value) @assignment_edge


@query: property_assignment
(assignment_expression
  left:
    (member_expression
      object: (_) @object_ref
      property: (property_identifier) @defined_property)
  right: (_) @assigned_value) @property_assignment_edge



; =========================================================
; CONTROL FLOW (CFG)
; =========================================================

@query: if_statement
(if_statement
  condition: (_) @if_condition
  consequence: (_) @if_block
  alternative: (_)? @else_block) @if_cfg_node


@query: switch_statement
(switch_statement) @switch_cfg_node


@query: for_loop
(for_statement
  condition: (_)? @loop_condition
  body: (_) @loop_body) @for_cfg_node


@query: while_loop
(while_statement
  condition: (_) @while_condition
  body: (_) @while_body) @while_cfg_node


@query: try_catch
(try_statement) @try_cfg_node
(catch_clause) @catch_cfg_node
(finally_clause) @finally_cfg_node



; =========================================================
; TYPE ANNOTATIONS
; =========================================================

@query: type_annotation
(type_annotation) @type_annotation


@query: union_type
(union_type) @union_type


@query: intersection_type
(intersection_type) @intersection_type



; =========================================================
; NAMESPACE / MODULE
; =========================================================

; @query: namespace_declaration
; (namespace_declaration
;   name: (identifier) @namespace_name) @namespace_node
;
;
; @query: module_declaration
; (module_declaration
;   name: (_) @module_name) @module_node



; =========================================================
; UNIVERSAL FALLBACK
; =========================================================

@query: any_expression
(expression) @any_expression

@query: any_statement
(statement) @any_statement
