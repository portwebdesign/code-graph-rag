; Kotlin tree-sitter query file
; Extracts functions, classes, data classes, interfaces, and extensions

; @query: function_declarations
; Matches: fun functionName(params): ReturnType = ...
(function_declaration
  (identifier) @name) @func

; @query: class_declarations
; Matches: class ClassName(constructor) : Superclass { }
(class_declaration
  (identifier) @name) @class

; @query: data_class_declarations
; Matches: data class DataClass(properties) { }
(class_declaration
  (identifier) @name) @data_class

; @query: interface_declarations
; Matches: interface InterfaceName { }
(class_declaration
  (identifier) @name) @interface

; @query: extension_functions
; Matches: fun String.extensionName(params) = ...
(function_declaration
  (identifier) @name) @function

; @query: enum_declarations
; Matches: enum class EnumName { }
(enum_class_body) @enum

; @query: sealed_class_declarations
; Matches: sealed class SealedName { }
(class_declaration
  (identifier) @name) @sealed

; @query: lambda_expressions
; Matches: { param -> expression }
(lambda_literal) @lambda

; @query: coroutine_calls
; Matches: launch { }, async { }, withContext { }
(call_expression
  (identifier) @name) @coroutine

; @query: property_declarations
; Matches: val name: Type = value, var name: Type = value
(property_declaration
  (identifier) @name) @property

; @query: type_aliases
; Matches: typealias Alias = RealType
(type_alias
  (identifier) @name) @type_alias

; @query: annotations
; Matches: @Annotation(param = value)
(annotation) @annotation

; @query: when_expressions
; Matches: when (x) { is Type -> ... }
(when_expression) @when

; @query: string_interpolation
; Matches: "Hello $name"
(interpolation) @string_interpolation

; @query: imports
; Matches: import ...
(import) @import

; @query: comments
; Matches: all comment lines
(comment) @comment
