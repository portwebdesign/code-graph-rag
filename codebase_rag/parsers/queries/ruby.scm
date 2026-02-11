; =========================================================
; RUBY ADVANCED GRAPH RAG ENGINE
; Production-Ready ruby.scm v2
; Rails + DSL + MetaProgramming Aware Edition
; =========================================================



; =========================================================
; CLASS DEFINITIONS
; =========================================================

@query: class_definitions
(class
  name: (constant) @class_name
  superclass: (superclass
    (constant) @superclass_name)?) @class_node



; =========================================================
; MODULE DEFINITIONS
; =========================================================

@query: module_definitions
(module
  name: (constant) @module_name) @module_node



; =========================================================
; METHOD DEFINITIONS
; =========================================================

@query: instance_methods
(method
  (identifier) @method_name) @instance_method_node


@query: class_methods
(method
  (singleton_method) @method_name) @class_method_node



; =========================================================
; INCLUDE / EXTEND
; =========================================================

@query: module_includes
(call
  (identifier) @include_method
  (argument_list
    (constant) @included_module)
  (#match? @include_method "^(include|extend|prepend)$")) @include_edge



; =========================================================
; INHERITANCE EDGE
; =========================================================

@query: inheritance
(class
  name: (constant) @child
  superclass: (superclass (constant) @parent)) @inheritance_edge



; =========================================================
; ACTIVE RECORD ASSOCIATIONS
; =========================================================

@query: associations
(call
  (identifier) @assoc_type
  (argument_list
    (simple_symbol) @assoc_name)
  (#match? @assoc_type "^(has_many|has_one|belongs_to|has_and_belongs_to_many)$")) @association_edge



; =========================================================
; VALIDATIONS
; =========================================================

@query: validations
(call
  (identifier) @validation_type
  (argument_list
    (simple_symbol) @validated_field)
  (#match? @validation_type "^validates")) @validation_edge



; =========================================================
; SCOPES
; =========================================================

@query: scopes
(call
  (identifier) @scope_method
  (argument_list
    (simple_symbol) @scope_name
    (lambda)? @scope_body)
  (#match? @scope_method "^scope$")) @scope_node



; =========================================================
; CALLBACKS
; =========================================================

@query: callbacks
(call
  (identifier) @callback_type
  (argument_list
    (simple_symbol) @callback_method)
  (#match? @callback_type "^(before_|after_|around_).*")) @callback_edge



; =========================================================
; CONTROLLER ACTIONS
; =========================================================

@query: controller_actions
(class
  name: (constant) @controller_name
  body: (_
    (method
      name: (identifier) @action_name))) @controller_action_node



; =========================================================
; ROUTES DSL
; =========================================================

@query: routes
(call
  (identifier) @route_type
  (argument_list
    (simple_symbol) @resource_name)
  (#match? @route_type "^(resources|resource|get|post|patch|put|delete)$")) @route_edge



; =========================================================
; MIGRATIONS DSL
; =========================================================

@query: migrations
(call
  (identifier) @migration_method
  (argument_list
    (simple_symbol) @table_name)
  (#match? @migration_method "^(create_table|add_column|remove_column|change_column)$")) @migration_edge



; =========================================================
; META PROGRAMMING
; =========================================================

@query: define_method_usage
(call
  (identifier) @meta_method
  (argument_list
    (simple_symbol) @defined_method_name)
  (#match? @meta_method "^(define_method|define_singleton_method)$")) @meta_definition_edge


@query: dynamic_send
(call
  (identifier) @dynamic_call
  (#match? @dynamic_call "^(send|public_send)$")) @dynamic_dispatch_edge



; =========================================================
; REQUIRE / LOAD
; =========================================================

@query: require_statements
(call
  (identifier) @require_method
  (argument_list
    (string) @required_path)
  (#match? @require_method "^(require|require_relative|load)$")) @require_edge



; =========================================================
; STRING & SYMBOL LITERALS
; =========================================================

@query: string_literals
(string) @string_node


@query: symbol_literals
(simple_symbol) @symbol_node



; =========================================================
; CONSTANT USAGE
; =========================================================

@query: constant_usage
(constant) @constant_ref



; =========================================================
; ATTRIBUTE MACROS
; =========================================================

@query: attribute_macros
(call
  (identifier) @attr_macro
  (argument_list
    (simple_symbol) @attribute_name)
  (#match? @attr_macro "^(attr_accessor|attr_reader|attr_writer)$")) @attribute_edge



; =========================================================
; COMMENT CAPTURE
; =========================================================

@query: comments
(comment) @comment_node



; =========================================================
; UNIVERSAL FALLBACK
; =========================================================

@query: any_expression
(_) @any_node
