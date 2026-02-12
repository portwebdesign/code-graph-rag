; =========================================================
; RUST ADVANCED GRAPH RAG ENGINE
; Production-Ready rust.scm v3
; Ownership + Trait + Async + Flow Aware
; =========================================================



; =========================================================
; CRATE ROOT
; =========================================================

@query: crate_root
(source_file) @crate_root



; =========================================================
; MODULE GRAPH
; =========================================================

@query: module_declarations
(mod_item
  name: (identifier) @module_name
  body: (_)?) @module_node



; =========================================================
; USE / IMPORT GRAPH
; =========================================================

@query: use_statements
(use_declaration
  argument: (_) @use_path) @use_edge



; =========================================================
; STRUCT / ENUM / UNION
; =========================================================

@query: struct_declarations
(struct_item
  name: (type_identifier) @struct_name
  body: (_)?) @struct_node


@query: enum_declarations
(enum_item
  name: (type_identifier) @enum_name
  body: (_)?) @enum_node


@query: union_declarations
(union_item
  name: (type_identifier) @union_name) @union_node



; =========================================================
; TRAITS
; =========================================================

@query: trait_declarations
(trait_item
  name: (type_identifier) @trait_name
  body: (_)?) @trait_node



; =========================================================
; IMPL GRAPH
; =========================================================

@query: impl_blocks
(impl_item
  type: (_) @impl_type
  trait: (_)?) @impl_node


@query: trait_implementation
(impl_item
  trait: (_) @implemented_trait
  type: (_) @concrete_type) @trait_impl_edge



; =========================================================
; FUNCTIONS
; =========================================================

@query: function_definitions
(function_item
  name: (identifier) @function_name
  parameters: (parameters)? @params
  body: (block)? @body) @function_node



; =========================================================
; METHODS
; =========================================================

@query: method_definitions
(impl_item
  body: (declaration_list
    (function_item
      name: (identifier) @method_name))) @method_node



; =========================================================
; GENERICS
; =========================================================


; =========================================================
; GENERICS
; =========================================================

@query: generic_parameters
(type_parameters
  (type_identifier) @generic_param) @generic_node


@query: where_clauses
(where_clause) @where_node



; =========================================================
; LIFETIME TRACKING
; =========================================================

@query: lifetime_annotations
(lifetime) @lifetime_node



; =========================================================
; OWNERSHIP / BORROW TRACKING
; =========================================================

@query: reference_expressions
(reference_expression
  value: (_) @borrowed_value) @borrow_edge


@query: mutable_references
(reference_expression
  "&"
  (mutable_specifier)?
  value: (_) @mutable_borrow) @mutable_borrow_edge


; @query: dereference
; (pointer_expression
;   value: (_) @dereferenced) @deref_edge



; =========================================================
; SMART POINTER DETECTION
; =========================================================

@query: smart_pointer_usage
(type_identifier) @smart_pointer
(#match? @smart_pointer "^(Box|Rc|Arc|RefCell|Mutex|RwLock)$")



; =========================================================
; CALL GRAPH
; =========================================================

@query: call_expressions
(call_expression
  function: (_) @callee
  arguments: (_)?) @call_edge


@query: method_calls
(call_expression
  function: (field_expression
    value: (_) @receiver
    field: (field_identifier) @method_name)) @method_call_edge



; =========================================================
; ERROR PROPAGATION
; =========================================================

; @query: try_operator
; (try_expression
;   value: (_) @try_value) @error_propagation_edge



; =========================================================
; ASYNC / AWAIT
; =========================================================

@query: async_functions
(function_item
  name: (identifier) @async_name) @async_function_node


@query: await_expressions
(await_expression
  value: (_) @await_target) @await_edge



; =========================================================
; MATCH FLOW GRAPH
; =========================================================

@query: match_expressions
(match_expression
  value: (_) @scrutinee) @match_node


@query: match_arms
(match_arm
  pattern: (_) @pattern
  value: (_) @body) @match_arm_node



; =========================================================
; CONTROL FLOW
; =========================================================

@query: if_expressions
(if_expression
  condition: (_) @condition
  consequence: (_) @then_branch
  alternative: (_)? @else_branch) @if_node


@query: loops
(loop_expression) @loop_node

(while_expression) @while_node

(for_expression) @for_node



; =========================================================
; ASSIGNMENTS
; =========================================================

@query: assignments
(assignment_expression
  left: (_) @lhs
  right: (_) @rhs) @assignment_edge



; =========================================================
; LET BINDINGS
; =========================================================

@query: let_statements
(let_declaration
  pattern: (_) @binding
  value: (_)?) @let_node



; =========================================================
; ATTRIBUTES (macros, derives, cfg)
; =========================================================

@query: attributes
(attribute_item) @attribute_node



; =========================================================
; MACROS
; =========================================================

@query: macro_definitions
(macro_definition
  name: (identifier) @macro_name) @macro_node


@query: macro_invocations
(macro_invocation
  macro: (identifier) @macro_call_name) @macro_call_edge



; =========================================================
; TYPE CAST
; =========================================================

; @query: cast_expressions
; (cast_expression
;   value: (_) @cast_value
;   type: (_) @target_type) @cast_node



; =========================================================
; STRUCT INITIALIZATION
; =========================================================

@query: struct_init
(struct_expression
  name: (_) @struct_type) @struct_init_node



; =========================================================
; RETURN
; =========================================================

@query: return_statements
(return_expression
  (_) @return_value) @return_edge



; =========================================================
; UNIVERSAL FALLBACK
; =========================================================

@query: any_expression
(expression) @any_expression

@query: any_item
(item) @any_item
