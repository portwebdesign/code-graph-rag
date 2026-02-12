; =========================================================
; SVELTE – GRAMMAR COMPAT EDITION
; =========================================================

; =========================================================
; ROOT
; =========================================================

@query: svelte_component
(document) @svelte_component

@query: script_root
(script_element) @script_root

@query: module_script
(script_element
  (start_tag
    (attribute
      (attribute_name) @attr_name
      (#eq? @attr_name "context")))) @module_script


; =========================================================
; IMPORTS (script raw text)
; =========================================================

@query: import_script
(script_element
  (raw_text) @import_source) @import_edge


; =========================================================
; EVENT HANDLERS (on:click)
; =========================================================

@query: event_handler
(attribute
  (attribute_name) @event_name
  (attribute_value)? @handler
  (#match? @event_name "^on:")) @event_binding_edge


; =========================================================
; BINDINGS (bind:value)
; =========================================================

@query: binding_directive
(attribute
  (attribute_name) @bind_name
  (attribute_value)? @bound_value
  (#match? @bind_name "^bind:")) @binding_edge


; =========================================================
; CONTROL FLOW
; =========================================================

@query: if_block
(if_statement) @if_cfg_node

@query: each_block
(each_statement) @each_cfg_node

@query: await_block
(await_statement) @await_cfg_node


; =========================================================
; SLOT USAGE
; =========================================================

@query: element_definition
(element
  (start_tag
    (tag_name) @element_name)) @element_node

@query: slot_definition
(element
  (start_tag
    (tag_name) @slot_tag
    (#eq? @slot_tag "slot"))) @slot_node


; =========================================================
; EXPRESSIONS
; =========================================================

@query: expression_tag
(expression_tag) @expression_node


; =========================================================
; CLASS / TAILWIND
; =========================================================

@query: tailwind_class_attributes
(
  attribute
    (attribute_name) @class_attr
    (attribute_value) @class_value
  (#eq? @class_attr "class")
) @css_class_edge


; =========================================================
; STYLE LAYER
; =========================================================

@query: style_root
(style_element) @style_root

@query: scoped_style
(style_element
  (start_tag
    (attribute
      (attribute_name) @style_attr
      (#eq? @style_attr "scoped")))) @scoped_style_node


; =========================================================
; TRANSITIONS & ANIMATIONS
; =========================================================

@query: transition_directive
(attribute
  (attribute_name) @directive
  (#match? @directive "^(in:|out:|transition:|animate:)$")) @transition_edge


; =========================================================
; UNIVERSAL FALLBACK
; =========================================================

@query: any_element
(element) @any_element

@query: any_expression
(expression) @any_expression
