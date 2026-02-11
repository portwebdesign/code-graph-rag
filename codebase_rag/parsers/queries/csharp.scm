; ==========================================
; C# – GRAPH RAG ENTERPRISE EDITION
; ==========================================

; ------------------------------------------
; NAMESPACE
; ------------------------------------------
@query: namespace
(namespace_declaration
	name: (qualified_name) @namespace.name
) @namespace


; ------------------------------------------
; CLASS / STRUCT / INTERFACE / RECORD
; ------------------------------------------
@query: class
(class_declaration
	name: (identifier) @class.name
	base_list: (base_list)? @class.base_list
	modifiers: (modifier)* @class.modifiers
) @class

@query: struct
(struct_declaration
	name: (identifier) @struct.name
	modifiers: (modifier)* @struct.modifiers
) @struct

@query: interface
(interface_declaration
	name: (identifier) @interface.name
	base_list: (base_list)? @interface.base_list
) @interface

@query: record
(record_declaration
	name: (identifier) @record.name
	parameter_list: (parameter_list)? @record.parameters
) @record


; ------------------------------------------
; PARTIAL CLASS
; ------------------------------------------
@query: partial_class
(class_declaration
	modifiers: (modifier)* @partial_mod
	(#match? @partial_mod "partial"))
@partial_class


; ------------------------------------------
; GENERIC TYPE DECLARATION
; ------------------------------------------
@query: generic_definition
(type_parameter_list
	(type_parameter
		name: (identifier) @generic.param))
@generic_definition

@query: generic_constraint
(type_parameter_constraint_clause
	name: (identifier) @generic.constraint.target
) @generic_constraint


; ------------------------------------------
; GENERIC TYPE USAGE
; ------------------------------------------
@query: generic_usage
(generic_name
	name: (identifier) @generic.name
	type_argument_list: (type_argument_list
		(type (_) @generic.arg)))
@generic_usage


; ------------------------------------------
; INHERITANCE / IMPLEMENTATION
; ------------------------------------------
@query: inheritance
(base_list
	(type_identifier) @base.type
) @inheritance


; ------------------------------------------
; METHODS
; ------------------------------------------
@query: method
(method_declaration
	name: (identifier) @method.name
	type: (_) @method.return_type
	parameter_list: (parameter_list) @method.parameters
	modifiers: (modifier)* @method.modifiers
) @method

@query: constructor
(constructor_declaration
	name: (identifier) @ctor.name
	parameter_list: (parameter_list) @ctor.parameters
) @constructor

@query: local_function
(local_function_statement) @local_function

@query: lambda
(lambda_expression) @lambda

@query: anonymous_method
(anonymous_method_expression) @anonymous_method


; ------------------------------------------
; ASYNC METHOD
; ------------------------------------------
@query: async_method
(method_declaration
	modifiers: (modifier)* @async_mod
	(#match? @async_mod "async"))
@async_method

@query: await_expression
(await_expression) @await_expression


; ------------------------------------------
; TASK RETURN TYPES
; ------------------------------------------
@query: task_returning_method
(method_declaration
	type: (generic_name
		name: (identifier) @task_type
		(#match? @task_type "^(Task|ValueTask)$")))
@task_returning_method


; ------------------------------------------
; PARAMETERS
; ------------------------------------------
@query: parameter
(parameter
	type: (_) @param.type
	name: (identifier) @param.name
) @parameter


; ------------------------------------------
; RECORD POSITIONAL PARAMETERS
; ------------------------------------------
@query: record_positional_param
(record_declaration
	parameter_list: (parameter_list
		(parameter
			type: (_) @record_param.type
			name: (identifier) @record_param.name)))
@record_positional_param


; ------------------------------------------
; NULLABLE TYPES
; ------------------------------------------
@query: nullable_type
(nullable_type) @nullable_type


; ------------------------------------------
; PROPERTIES
; ------------------------------------------
@query: property
(property_declaration
	type: (_) @property.type
	name: (identifier) @property.name
) @property


; ------------------------------------------
; FIELDS
; ------------------------------------------
@query: field
(field_declaration
	type: (_) @field.type
	(variable_declaration
		(variable_declarator
			name: (identifier) @field.name)))
@field


; ------------------------------------------
; EXTENSION METHODS
; ------------------------------------------
@query: extension_method
(method_declaration
	modifiers: (modifier)* @static_mod
	parameters: (parameter_list
		(parameter
			modifiers: (modifier)* @this_mod
			type: (_) @extension.target_type)))
	(#match? @static_mod "static")
	(#match? @this_mod "this"))
@extension_method


; ------------------------------------------
; METHOD CALLS
; ------------------------------------------
@query: call
(invocation_expression) @call

@query: member_call
(invocation_expression
	function: (member_access_expression
		expression: (_) @call.target
		name: (identifier) @call.name))


; ------------------------------------------
; LINQ CALLS
; ------------------------------------------
@query: linq_call
(invocation_expression
	function: (member_access_expression
		name: (identifier) @linq_method)
	(#match? @linq_method
		"^(Select|Where|OrderBy|OrderByDescending|ThenBy|ThenByDescending|Include|ThenInclude|GroupBy|Join|Any|All|Count|First|FirstOrDefault|Single|SingleOrDefault)$"))
@linq_call


; ------------------------------------------
; ASP.NET MINIMAL API
; ------------------------------------------
@query: minimal_api_call
(invocation_expression
	function: (member_access_expression
		name: (identifier) @map_method)
	(#match? @map_method
		"^(MapGet|MapPost|MapPut|MapDelete|MapPatch|MapMethods)$"))
@minimal_api_call


; ------------------------------------------
; CONTROLLER ACTIONS
; ------------------------------------------
@query: controller_action
(method_declaration
	(attribute_list
		(attribute
			name: (identifier) @http_attr))
	(#match? @http_attr "^(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch)$"))
@controller_action


; ------------------------------------------
; DEPENDENCY INJECTION (Constructor)
; ------------------------------------------
@query: dependency_injection
(constructor_declaration
	parameters: (parameter_list
		(parameter
			type: (_) @injected.type
			name: (identifier) @injected.name)))
@dependency_injection


; ------------------------------------------
; EF DbContext DETECTION
; ------------------------------------------
@query: dbcontext
(class_declaration
	name: (identifier) @dbcontext.name
	base_list: (base_list
		(type_identifier) @db_base)
	(#match? @db_base "DbContext"))
@dbcontext


; ------------------------------------------
; EF DbSet EXTRACTION
; ------------------------------------------
@query: dbset_property
(property_declaration
	type: (generic_name
		name: (identifier) @dbset.type
		type_argument_list: (type_argument_list
			(type (_) @entity.type)))
	(#match? @dbset.type "DbSet"))
	name: (identifier) @dbset.name)
@dbset_property


; ------------------------------------------
; EXCEPTION FLOW
; ------------------------------------------
@query: try_statement
(try_statement
	block: (block) @try.block
	catch_clause: (catch_clause
		declaration: (catch_declaration
			type: (_) @catch.type
			name: (identifier)? @catch.name))
		finally_clause: (finally_clause)? @finally.block)
) @try_statement

@query: throw_statement
(throw_statement
	expression: (_) @throw.expression)
@throw_statement


; ------------------------------------------
; ATTRIBUTES
; ------------------------------------------
@query: attribute
(attribute_list
	(attribute
		name: (identifier) @attr.name))
@attribute


; ------------------------------------------
; USING DIRECTIVES
; ------------------------------------------
@query: import
(using_directive
	name: (qualified_name) @using.name
) @import


; ------------------------------------------
; REPOSITORY HEURISTIC
; ------------------------------------------
@query: repository_class
(class_declaration
	name: (identifier) @repository.name
	(#match? @repository.name ".*Repository$"))
@repository_class


; ------------------------------------------
; AGGREGATE ROOT HEURISTIC
; ------------------------------------------
@query: aggregate_root
(class_declaration
	name: (identifier) @aggregate.name
	(#match? @aggregate.name ".*Aggregate$|.*Root$"))
@aggregate_root


; ------------------------------------------
; MEDIATR HANDLER (CQRS)
; ------------------------------------------
@query: mediatr_handler
(class_declaration
	base_list: (base_list
		(generic_name
			name: (identifier) @handler_interface
			(#match? @handler_interface "IRequestHandler"))))
@mediatr_handler
