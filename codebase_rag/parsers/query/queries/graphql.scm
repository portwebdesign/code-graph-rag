; GraphQL tree-sitter query file
; Standardized query names for SCM overrides

; @query: function_definitions
(field_definition) @function

; @query: class_definitions
(object_type_definition) @class

; @query: function_calls
(operation_definition) @call

; @query: import_statements
(schema_definition) @import

; Additional captures
(fragment_definition) @fragment
(interface_type_definition) @interface_type
(enum_type_definition) @enum_type
(scalar_type_definition) @scalar_type
(input_object_type_definition) @input_type
(directive_definition) @directive
