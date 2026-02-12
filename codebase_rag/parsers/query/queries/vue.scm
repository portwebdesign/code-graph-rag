; =========================================================
; VUE – GRAMMAR COMPAT EDITION
; =========================================================

; =========================================================
; ROOT LAYER
; =========================================================

@query: vue_component
(document) @vue_component

@query: template_root
(element
  (start_tag
    (tag_name) @template_tag
    (#eq? @template_tag "template"))) @template_root

@query: script_root
(script_element) @script_root

@query: style_root
(style_element) @style_root


; =========================================================
; TEMPLATE LAYER
; =========================================================

@query: element_definition
(element
  (start_tag
    (tag_name) @element_name)) @element_node


; =========================================================
; DIRECTIVES (Control Flow)
; =========================================================

@query: v_if_directive
(attribute
  (attribute_name) @directive
  (quoted_attribute_value
    (attribute_value) @condition)?
  (#eq? @directive "v-if")) @if_cfg_node

@query: v_for_directive
(attribute
  (attribute_name) @directive
  (quoted_attribute_value
    (attribute_value) @loop_expression)?
  (#eq? @directive "v-for")) @for_cfg_node

@query: v_model_directive
(attribute
  (attribute_name) @directive
  (quoted_attribute_value
    (attribute_value) @model_binding)?
  (#match? @directive "^v-model")) @two_way_binding


; =========================================================
; EVENT BINDINGS
; =========================================================

@query: event_handler
(attribute
  (attribute_name) @event_attr
  (quoted_attribute_value
    (attribute_value) @handler)?
  (#match? @event_attr "^@|^v-on:")) @event_binding_edge


; =========================================================
; CLASS / TAILWIND
; =========================================================

@query: tailwind_class_attributes
(
  attribute
    (attribute_name) @class_attr
    (quoted_attribute_value
      (attribute_value) @class_value)
  (#match? @class_attr "^(class|:class|v-bind:class)$")
) @css_class_edge


; =========================================================
; SCRIPT – IMPORTS (raw)
; =========================================================

@query: import_statements
(script_element
  (raw_text) @import_source) @import_edge


; =========================================================
; UNIVERSAL FALLBACK
; =========================================================

@query: any_element
(element) @any_element

@query: any_attribute
(attribute) @any_attribute
