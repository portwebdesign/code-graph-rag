; =========================================================
; SQL – GRAPH RAG COMPAT EDITION
; =========================================================

; @query: create_database
(create_database) @create_database

; @query: create_schema
(create_schema) @create_schema

; @query: set_schema
(set_schema) @set_schema

; @query: table_definition
(create_table) @table_definition

; @query: column_definition
(column_definition) @column_definition

; @query: primary_key_constraint
(constraint) @primary_key_edge

; @query: foreign_key_constraint
(constraint) @foreign_key_edge

; @query: unique_constraint
(constraint) @unique_edge

; @query: check_constraint
(constraint) @check_constraint_edge

; @query: create_index
(create_index) @index_definition

; @query: index_column
(index_fields) @index_column_edge

; @query: view_definition
(create_view) @view_definition

; @query: materialized_view_definition
(create_materialized_view) @materialized_view_definition

; @query: function_definition
(create_function) @function_definition

; @query: procedure_definition
(create_function) @procedure_definition

; @query: function_parameter
(function_argument) @function_param_edge

; @query: trigger_definition
(create_trigger) @trigger_definition

; @query: select_statement
(select) @select_node

; @query: insert_statement
(insert) @insert_edge

; @query: update_statement
(update) @update_edge

; @query: delete_statement
(delete) @delete_edge

; @query: join_clause
(join) @join_edge

; @query: from_clause
(from) @from_edge

; @query: where_clause
(where) @where_edge

; @query: cte_definition
(cte) @cte_definition

; @query: recursive_cte
(cte) @recursive_cte

; @query: subquery
(subquery) @subquery_edge

; @query: begin_transaction
(transaction) @begin_transaction

; @query: commit_transaction
(keyword_commit) @commit_transaction

; @query: rollback_transaction
(keyword_rollback) @rollback_transaction

; @query: create_role
(create_role) @role_definition

; @query: json_operator
(binary_expression) @json_access_edge

; @query: array_access
(subscript) @array_access_edge

; @query: returning_clause
(returning) @returning_edge

; @query: any_statement
(statement) @any_statement

; @query: any_expression
(term) @any_expression
