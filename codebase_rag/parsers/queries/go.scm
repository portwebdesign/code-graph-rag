; Go tree-sitter query file
; Standardized query names for SCM overrides

; @query: function_definitions
(function_declaration) @function
(method_declaration) @function

; @query: class_definitions
(type_declaration) @class

; @query: function_calls
(call_expression) @call

; @query: goroutines
(go_statement) @goroutine

; @query: channel_send
(send_statement) @channel_send

; @query: channel_receive
(unary_expression
	operator: "<-"
	operand: (_) @channel_operand) @channel_receive

; @query: db_calls
(call_expression
	function: (selector_expression
		field: (field_identifier) @db_method)
	(#match? @db_method "^(Query|Exec|QueryRow|Prepare|Begin|Commit|Rollback)$")) @db_call

; @query: import_statements
(import_declaration) @import
