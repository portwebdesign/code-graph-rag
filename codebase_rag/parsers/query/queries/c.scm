; C Declarative Query Engine
; Format: ; @query: name followed by tree-sitter query

; @query: function_declarations
(function_definition) @function
(declaration
  declarator: (function_declarator
    declarator: (identifier) @name)) @function

; @query: function_definitions
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name)
  body: (compound_statement) @body) @function_def

; @query: struct_definitions
(struct_specifier
  name: (type_identifier) @struct_name) @class

; @query: union_definitions
(union_specifier
  name: (type_identifier) @union_name) @class

; @query: enum_definitions
(enum_specifier
  name: (type_identifier)? @enum_name) @class

; @query: include_directives
(preproc_include) @import

; @query: function_calls
(call_expression) @call
