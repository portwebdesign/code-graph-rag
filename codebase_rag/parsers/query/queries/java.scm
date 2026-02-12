; Java Declarative Query Engine
; Format: ; @query: name followed by tree-sitter query
;
; This file defines all declarative queries for Java AST extraction.

; @query: method_declarations
(method_declaration
  name: (identifier) @method_name
  parameters: (formal_parameters)? @params
  body: (block)? @body) @function

; @query: class_declarations
(class_declaration
  name: (identifier) @class_name
  (type_identifier)? @superclass
  (super_interfaces)? @interfaces
  body: (class_body)? @body) @class

; @query: interface_declarations
(interface_declaration
  name: (identifier) @interface_name
  (type_list)? @extends
  body: (interface_body)? @body) @class

; @query: enum_declarations
(enum_declaration
  name: (identifier) @enum_name
  body: (enum_body)? @body) @class

; @query: record_declarations
(record_declaration) @class

; @query: annotation_type_declarations
(annotation_type_declaration
  name: (identifier) @annotation_name
  body: (annotation_type_body)? @body) @class

; @query: constructor_declarations
(constructor_declaration
  name: (identifier) @constructor_name
  parameters: (formal_parameters)? @params
  body: (constructor_body)? @body) @function

; @query: field_declarations
(field_declaration
  type: (_) @type
  declarator: (variable_declarator
    name: (identifier) @field_name
    value: (_)? @field_value)) @field

; @query: variable_declarations
(local_variable_declaration
  type: (_) @type
  declarator: (variable_declarator
    name: (identifier) @var_name
    value: (_)? @var_value)) @var_decl

; @query: import_statements
(import_declaration) @import

; @query: static_imports
(import_declaration
  (asterisk) @static_import) @static_import_stmt

; @query: package_declarations
(package_declaration) @package

; @query: annotations
(annotation
  name: (identifier) @annotation_name
  arguments: (annotation_argument_list)? @annotation_args) @annotation

; @query: method_invocations
(method_invocation
  name: (identifier) @method_name
  arguments: (argument_list)? @args) @call

; @query: object_creation
(object_creation_expression
  type: (_) @type
  arguments: (argument_list)? @args
  (class_body)? @body) @new_expr

; @query: array_creation
(array_creation_expression) @array_creation

; @query: assignment_expressions
(assignment_expression
  left: (_) @left
  right: (_) @right) @assignment

; @query: binary_expressions
(binary_expression
  left: (_) @left
  operator: (_) @op
  right: (_) @right) @binop

; @query: unary_expressions
(unary_expression) @unary

; @query: cast_expressions
(cast_expression
  type: (_) @type
  operand: (_) @operand) @cast

; @query: instanceof_expressions
(instanceof_expression
  operand: (_) @operand
  type: (_) @type) @instanceof

; @query: ternary_expressions
(ternary_expression
  condition: (_) @condition
  consequence: (_) @consequence
  alternative: (_) @alternative) @ternary

; @query: member_access
(field_access
  object: (_) @object
  field: (identifier) @field) @member

; @query: array_access
(array_access
  array: (_) @array
  index: (_) @index) @array_access

; @query: if_statements
(if_statement
  condition: (parenthesized_expression) @condition
  consequence: (_) @consequence
  alternative: (_)? @alternative) @if

; @query: switch_statements
(switch_statement) @switch

; @query: case_labels
(switch_label) @switch_label

; @query: for_loops
(for_statement
  init: (_)? @init
  condition: (_)? @condition
  update: (_)? @update
  body: (_) @body) @for_loop

; @query: enhanced_for_loops
(enhanced_for_statement
  variable: (variable_declarator) @var
  iterable: (_) @iter
  body: (_) @body) @for_each

; @query: while_loops
(while_statement
  condition: (parenthesized_expression) @condition
  body: (_) @body) @while_loop

; @query: do_while_loops
(do_statement
  body: (_) @body
  condition: (parenthesized_expression) @condition) @do_while

; @query: try_statements
(try_statement) @try_stmt

; @query: catch_clauses
(catch_clause) @catch

; @query: finally_clauses
(finally_clause) @finally

; @query: try_with_resources
(try_with_resources_statement) @try_resource

; @query: throw_statements
(throw_statement) @throw

; @query: return_statements
(return_statement) @return

; @query: break_statements
(break_statement) @break

; @query: continue_statements
(continue_statement) @continue

; @query: yield_statements
(yield_statement) @yield

; @query: assert_statements
(assert_statement) @assert

; @query: lambda_expressions
(lambda_expression
  parameters: (formal_parameters)? @params
  body: (_) @body) @lambda

; @query: method_references
(method_reference) @method_ref

; @query: string_literals
(string_literal) @string

; @query: number_literals
; (decimal_integer_literal) @number_int
; (floating_point_literal) @number_float
; (hex_integer_literal) @number_hex

; @query: boolean_literals
(true) @bool_true
(false) @bool_false

; @query: null_literal
(null_literal) @null_literal

; @query: character_literals
(character_literal) @char_literal

; @query: array_initializers
(array_initializer) @array_init

; @query: generic_types
(type_parameters) @generics
(type_arguments) @type_args

; @query: wildcard_types
(wildcard) @wildcard

; @query: intersection_types
; (intersection_type) @intersection

; @query: union_types
; (union_type) @union

; @query: all_expressions
(_) @any_expr

; @query: all_statements
(_) @any_stmt
