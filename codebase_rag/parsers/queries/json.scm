; =========================================================
; JSON – ADVANCED GRAPH RAG EDITION
; Production-Ready JSON.scm v2
; Graph-Aware + Path-Aware Edition
; =========================================================



; =========================================================
; ROOT STRUCTURE
; =========================================================

@query: json_document
(document) @json_document

@query: json_object
(object) @json_object

@query: json_array
(array) @json_array



; =========================================================
; OBJECT STRUCTURE
; =========================================================

@query: key_value_pair
(pair
  key: (string) @defined_key
  value: (_) @assigned_value) @key_value_pair


@query: object_key
(pair
  key: (string) @object_key)


@query: object_value
(pair
  value: (_) @object_value)



; =========================================================
; NESTED STRUCTURES
; =========================================================

@query: object_edge
(pair
  key: (string) @parent_key
  value: (object) @nested_object) @object_edge


@query: array_edge
(pair
  key: (string) @parent_key
  value: (array) @nested_array) @array_edge


@query: array_object
(array
  (object) @array_object)


@query: nested_array
(array
  (array) @nested_array)



; =========================================================
; ARRAY ELEMENT TRACKING
; =========================================================

@query: array_element
(array
  (_) @array_element)


@query: array_contains_edge
(array
  (_) @array_element) @array_contains_edge



; =========================================================
; VALUE TYPES (Typed Extraction)
; =========================================================

@query: string_value
(string) @string_value

@query: number_value
(number) @number_value

@query: true_value
(true) @true_value

@query: false_value
(false) @false_value

@query: null_value
(null) @null_value



; =========================================================
; TYPE-SPECIFIC KEY/VALUE EDGES
; =========================================================

@query: string_assignment_edge
(pair
  key: (string) @key
  value: (string) @string_value) @string_assignment_edge


@query: number_assignment_edge
(pair
  key: (string) @key
  value: (number) @number_value) @number_assignment_edge


@query: object_assignment_edge
(pair
  key: (string) @key
  value: (object) @object_value) @object_assignment_edge


@query: array_assignment_edge
(pair
  key: (string) @key
  value: (array) @array_value) @array_assignment_edge



; =========================================================
; HIERARCHY PATH EXTRACTION
; =========================================================

@query: key_hierarchy_edge
(pair
  key: (string) @parent_key
  value: (object
            (pair
              key: (string) @child_key))) @key_hierarchy_edge



; =========================================================
; FULL RECURSIVE VALUE CAPTURE
; =========================================================

@query: any_object
(object) @any_object

@query: any_array
(array) @any_array

@query: any_pair
(pair) @any_pair

@query: any_string
(string) @any_string

@query: any_number
(number) @any_number

@query: any_true
(true) @any_true

@query: any_false
(false) @any_false

@query: any_null
(null) @any_null
