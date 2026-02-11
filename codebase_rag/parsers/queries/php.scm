; =========================================================
; PHP TREE-SITTER EXTENDED QUERY FILE
; Extracts functions, classes, methods, calls, inheritance,
; traits, properties, static calls, object creation, closures
; =========================================================


; =========================================================
; @query: function_definitions
; =========================================================

(function_definition
  name: (name) @func_name
  parameters: (formal_parameters)? @params
  body: (compound_statement)? @body) @function


; =========================================================
; @query: class_definitions
; =========================================================

(class_declaration
  name: (name) @class_name
  body: (declaration_list)? @body) @class


; =========================================================
; @query: interface_definitions
; =========================================================

(interface_declaration
  name: (name) @interface_name
  body: (declaration_list)? @body) @interface


; =========================================================
; @query: trait_definitions
; =========================================================

(trait_declaration
  name: (name) @trait_name
  body: (declaration_list)? @body) @trait


; =========================================================
; @query: inheritance_extends
; =========================================================

(class_declaration
  name: (name) @class_name
  base_clause: (base_clause
    (qualified_name) @parent_class)) @inheritance


; =========================================================
; @query: implements_interfaces
; =========================================================

(class_declaration
  name: (name) @class_name
  class_interface_clause: (class_interface_clause
    (qualified_name) @implemented_interface)) @implements


; =========================================================
; @query: trait_use
; =========================================================

(trait_use_clause
  (qualified_name) @used_trait) @trait_use


; =========================================================
; @query: method_definitions
; =========================================================

(method_declaration
  name: (name) @method_name
  parameters: (formal_parameters)? @params
  body: (compound_statement)? @body) @function


; =========================================================
; @query: property_declarations
; =========================================================

(property_declaration
  (property_element
    name: (variable_name) @property_name)) @property


; =========================================================
; @query: class_constants
; =========================================================

(class_constant_declaration
  (const_element
    name: (name) @const_name)) @class_constant


; =========================================================
; @query: function_calls
; =========================================================

(function_call_expression
  function: (_) @func
  arguments: (arguments)? @args) @call


; =========================================================
; @query: member_calls
; object->method()
; =========================================================

(member_call_expression
  object: (_) @object
  name: (name) @method_name
  arguments: (arguments)? @args) @member_call


; =========================================================
; @query: static_method_calls
; Class::method()
; =========================================================

(scoped_call_expression
  scope: (_) @static_class
  name: (name) @method_name
  arguments: (arguments)? @args) @static_call


; =========================================================
; @query: object_creation
; new ClassName()
; =========================================================

(object_creation_expression
  (qualified_name) @class_name
  arguments: (arguments)? @args) @object_creation


; =========================================================
; @query: closure_definitions
; function() {}
; =========================================================

(anonymous_function) @closure


; =========================================================
; @query: arrow_functions
; fn() => expr
; =========================================================

(arrow_function) @arrow_function


; =========================================================
; @query: attributes
; =========================================================

(attribute_group
  (attribute
    name: (name) @attr_name)) @attribute


; =========================================================
; @query: route_definitions (Laravel)
; Route::get(...)
; =========================================================

(scoped_call_expression
  scope: (name) @static_class
  name: (name) @method
  (#eq? @static_class "Route")
  (#match? @method "^(get|post|put|patch|delete|options|any|match|group)$")) @route_def


; =========================================================
; @query: symfony_routes
; #[Route(...)]
; =========================================================

(attribute_group
  (attribute
    name: (name) @symfony_route_attr
    (#match? @symfony_route_attr "Route"))) @symfony_route


; =========================================================
; @query: include_statements
; =========================================================

(include_expression) @include
(require_expression) @require


; =========================================================
; @query: namespaces
; =========================================================

(namespace_definition) @namespace


; =========================================================
; @query: use_statements
; =========================================================

(namespace_use_declaration) @use
