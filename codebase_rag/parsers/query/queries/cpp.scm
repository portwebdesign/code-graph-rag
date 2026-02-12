; C++ Declarative Query Engine
; Format: ; @query: name followed by tree-sitter query
;
; This file defines all declarative queries for C++ AST extraction.

; @query: function_declarations
(function_definition) @function
(declaration) @function
(field_declaration) @function
(template_declaration) @function
(lambda_expression) @function

; @query: function_definitions
(function_definition
  body: (compound_statement) @body) @function_def

; @query: class_definitions
(class_specifier
  name: (type_identifier) @class_name
  body: (field_declaration_list)? @body) @class

(class_specifier
  name: (template_type) @class_name
  body: (field_declaration_list)? @body) @class

; @query: struct_definitions
(struct_specifier
  name: (type_identifier) @struct_name
  body: (field_declaration_list)? @body) @class

(struct_specifier
  name: (template_type) @struct_name
  body: (field_declaration_list)? @body) @class

; @query: union_definitions
(union_specifier
  name: (type_identifier) @union_name
  body: (field_declaration_list)? @body) @class

; @query: enum_definitions
(enum_specifier
  name: (type_identifier)? @enum_name
  body: (enumerator_list)? @body) @class

; @query: namespace_definitions
(namespace_definition
  name: (namespace_identifier) @namespace_name
  body: (declaration_list) @body) @namespace

; @query: template_declarations
(template_declaration
  parameters: (template_parameter_list)? @params
  (_)? @decl) @template

; @query: typedef_declarations
(type_definition) @typedef

; @query: using_declarations
(using_declaration) @using_decl

; @query: include_directives
(preproc_include) @import

; @query: module_imports
(preproc_include) @import

; @query: define_directives
(preproc_def) @define

; @query: constructor_declarations
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @constructor_name)) @constructor

; @query: destructor_declarations
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @destructor_name)) @destructor

; @query: member_access
(field_expression
  argument: (_) @object
  field: (field_identifier) @field) @member

; @query: pointer_member_access
(pointer_expression
  argument: (field_expression
    argument: (_) @object
    field: (field_identifier) @field)) @pointer_member

; @query: method_invocations
(call_expression
  function: (field_expression
    argument: (_) @object
    field: (field_identifier) @method)
  arguments: (argument_list)? @args) @method_call

(call_expression
  function: (field_expression
    argument: (_) @object
    field: (qualified_identifier) @method)
  arguments: (argument_list)? @args) @method_call

; @query: function_calls
(call_expression) @call

; @query: new_expressions
(new_expression
  type: (_) @type) @new_expr

; @query: delete_expressions
(delete_expression
  argument: (_) @expr) @delete_expr

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
(unary_expression
  operator: (_) @op
  argument: (_) @arg) @unary

; @query: update_expressions
(update_expression) @update

; @query: cast_expressions
(cast_expression
  type: (_) @type
  value: (_) @value) @cast

; @query: conditional_expressions
(conditional_expression
  condition: (_) @condition
  consequence: (_) @consequence
  alternative: (_) @alternative) @ternary

; @query: sizeof_expressions
(sizeof_expression) @sizeof

; @query: alignof_expressions
(alignof_expression) @alignof

; @query: lambda_expressions
(lambda_expression
  captures: (lambda_capture_specifier)? @captures
  parameters: (parameter_list)? @params
  body: (compound_statement) @body) @lambda

; @query: if_statements
(if_statement
  condition: (parenthesized_expression) @condition
  consequence: (_) @consequence
  alternative: (_)? @alternative) @if

; @query: switch_statements
(switch_statement
  condition: (parenthesized_expression) @condition
  body: (compound_statement) @body) @switch

; @query: case_labels
(case_statement) @case_label

; @query: default_labels
(default_statement) @default_label

; @query: while_loops
(while_statement
  condition: (parenthesized_expression) @condition
  body: (_) @body) @while_loop

; @query: do_while_loops
(do_statement
  body: (_) @body
  condition: (parenthesized_expression) @condition) @do_while

; @query: for_loops
(for_statement
  init: (_)? @init
  condition: (_)? @condition
  update: (_)? @update
  body: (_) @body) @for_loop

; @query: range_for_loops
(for_range_loop
  declarations: (_) @var
  right: (_) @iter
  body: (_) @body) @for_range

; @query: try_statements
(try_statement) @try_stmt

; @query: catch_clauses
(catch_clause) @catch

; @query: throw_statements
(throw_statement) @throw

; @query: return_statements
(return_statement) @return

; @query: break_statements
(break_statement) @break

; @query: continue_statements
(continue_statement) @continue

; @query: goto_statements
(goto_statement) @goto

; @query: goto_labels
(labeled_statement) @label

; @query: variable_declarations
(declaration
  declarator: (init_declarator
    declarator: (identifier) @var_name
    value: (_)? @var_value)) @var_decl

; @query: pointer_declarators
(pointer_declarator) @pointer

; @query: reference_declarators
(reference_declarator) @reference

; @query: array_declarators
(array_declarator) @array

; @query: function_declarators
(function_declarator) @function_declarator

; @query: parameter_declarations
(parameter_declaration) @parameter

; @query: string_literals
(string_literal) @string
(raw_string_literal) @raw_string
(concatenated_string) @concat_string

; @query: number_literals
(number_literal) @number

; @query: character_literals
(char_literal) @char

; @query: boolean_literals
(true) @bool_true
(false) @bool_false

; @query: nullptr_literal
(nullptr) @nullptr

; @query: type_identifiers
(type_identifier) @type_id

; @query: identifiers
(identifier) @identifier

; @query: template_type_parameters
(template_type_parameter) @template_type_param

; @query: template_non_type_parameters
(template_non_type_parameter) @template_non_type_param

; @query: template_template_parameters
(template_template_parameter) @template_template_param

; @query: specialization
(template_specialization) @template_spec

; @query: access_specifiers
(access_specifier) @access_specifier

; @query: virtual_function_specifier
(virtual) @virtual

; @query: const_qualifier
(const_qualifier) @const

; @query: volatile_qualifier
(volatile_qualifier) @volatile

; @query: mutable_specifier
(mutable_specifier) @mutable

; @query: static_specifier
(static_specifier) @static

; @query: all_expressions
(_) @any_expr

; @query: all_statements
(_) @any_stmt
