; =========================================================
; HTML TREE-SITTER ADVANCED QUERY FILE
; Standardized query names for SCM overrides
; =========================================================


; =========================================================
; @query: document
; =========================================================

(document) @document


; =========================================================
; @query: doctype
; =========================================================

(doctype) @doctype


; =========================================================
; @query: elements
; =========================================================

(element
  (start_tag
    (tag_name) @tag_name)) @element


; =========================================================
; @query: self_closing_elements
; =========================================================

(self_closing_tag
  (tag_name) @tag_name) @self_closing_element


; =========================================================
; @query: script_blocks
; =========================================================

(script_element
  (raw_text) @script_body) @script


; =========================================================
; @query: style_blocks
; =========================================================

(style_element
  (raw_text) @style_body) @style


; =========================================================
; @query: external_scripts
; =========================================================

(script_element
  (start_tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @src)))
  (#eq? @attr_name "src")) @external_script


; =========================================================
; @query: css_links
; =========================================================

(element
  (start_tag
    (tag_name) @tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @href)))
  (#eq? @tag "link")
  (#eq? @attr_name "href")) @css_link


; =========================================================
; @query: anchors
; =========================================================

(element
  (start_tag
    (tag_name) @tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @href)))
  (#eq? @tag "a")
  (#eq? @attr_name "href")) @anchor


; =========================================================
; @query: forms
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "form")) @form


; =========================================================
; @query: form_actions
; =========================================================

(element
  (start_tag
    (tag_name) @tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @action)))
  (#eq? @tag "form")
  (#eq? @attr_name "action")) @form_action


; =========================================================
; @query: inputs
; =========================================================

(self_closing_tag
  (tag_name) @tag
  (#eq? @tag "input")) @input


; =========================================================
; @query: buttons
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "button")) @button


; =========================================================
; @query: meta_tags
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "meta")) @meta


; =========================================================
; @query: titles
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "title")
  (text) @title_text) @title


; =========================================================
; @query: attributes
; =========================================================

(attribute
  (attribute_name) @attr_name
  (quoted_attribute_value
    (attribute_value) @attr_value)) @attribute


; =========================================================
; @query: id_attributes
; =========================================================

(attribute
  (attribute_name) @id_attr
  (quoted_attribute_value
    (attribute_value) @id_value)
  (#eq? @id_attr "id")) @id_attribute


; =========================================================
; @query: class_attributes
; =========================================================

(attribute
  (attribute_name) @class_attr
  (quoted_attribute_value
    (attribute_value) @class_value)
  (#eq? @class_attr "class")) @class_attribute


; =========================================================
; @query: tailwind_class_attributes
; =========================================================

(attribute
  (attribute_name) @tailwind_attr_name
  (quoted_attribute_value
    (attribute_value) @tailwind_class_value)
  (#match? @tailwind_attr_name "^(class|className)$")) @tailwind_class


; =========================================================
; @query: data_attributes
; =========================================================

(attribute
  (attribute_name) @data_attr
  (#match? @data_attr "^data-")) @data_attribute


; =========================================================
; @query: event_handlers
; =========================================================

(attribute
  (attribute_name) @event_name
  (quoted_attribute_value
    (attribute_value) @event_code)
  (#match? @event_name "^on")) @event_handler


; =========================================================
; @query: comments
; =========================================================

(comment) @comment


; =========================================================
; @query: text_nodes
; =========================================================

(text) @text


; =========================================================
; @query: template_like_expressions
; (useful for Vue/Blade/JSX-like hybrid HTML)
; =========================================================

(text
  @text_node
  (#match? @text_node "{{.*}}")) @template_expression


; =========================================================
; @query: inline_styles
; =========================================================

(attribute
  (attribute_name) @style_attr
  (quoted_attribute_value
    (attribute_value) @style_value)
  (#eq? @style_attr "style")) @inline_style

; =========================================================
; @query: htmx_http_methods
; hx-get / hx-post / hx-put / hx-delete / hx-patch
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (quoted_attribute_value
    (attribute_value) @endpoint)
  (#match? @htmx_attr "^hx-(get|post|put|patch|delete)$")) @htmx_http


; =========================================================
; @query: htmx_boost
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (#eq? @htmx_attr "hx-boost")) @htmx_boost


; =========================================================
; @query: htmx_target
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (quoted_attribute_value
    (attribute_value) @target)
  (#eq? @htmx_attr "hx-target")) @htmx_target


; =========================================================
; @query: htmx_trigger
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (quoted_attribute_value
    (attribute_value) @trigger)
  (#eq? @htmx_attr "hx-trigger")) @htmx_trigger


; =========================================================
; @query: htmx_swap
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (quoted_attribute_value
    (attribute_value) @swap_mode)
  (#eq? @htmx_attr "hx-swap")) @htmx_swap


; =========================================================
; @query: htmx_include
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (quoted_attribute_value
    (attribute_value) @include_selector)
  (#eq? @htmx_attr "hx-include")) @htmx_include


; =========================================================
; @query: htmx_vals
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (quoted_attribute_value
    (attribute_value) @vals)
  (#eq? @htmx_attr "hx-vals")) @htmx_vals


; =========================================================
; @query: htmx_headers
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (quoted_attribute_value
    (attribute_value) @headers)
  (#eq? @htmx_attr "hx-headers")) @htmx_headers


; =========================================================
; @query: htmx_confirm
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (quoted_attribute_value
    (attribute_value) @confirm_message)
  (#eq? @htmx_attr "hx-confirm")) @htmx_confirm


; =========================================================
; @query: htmx_ext
; =========================================================

(attribute
  (attribute_name) @htmx_attr
  (quoted_attribute_value
    (attribute_value) @extension)
  (#eq? @htmx_attr "hx-ext")) @htmx_extension


; =========================================================
; @query: form_submissions
; =========================================================

(element
  (start_tag
    (tag_name) @tag
    (attribute
      (attribute_name) @method_attr
      (quoted_attribute_value
        (attribute_value) @method))
    (attribute
      (attribute_name) @action_attr
      (quoted_attribute_value
        (attribute_value) @endpoint)))
  (#eq? @tag "form")
  (#eq? @method_attr "method")
  (#eq? @action_attr "action")) @form_submission


; =========================================================
; @query: internal_anchors
; =========================================================

(element
  (start_tag
    (tag_name) @tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value
        (attribute_value) @href)))
  (#eq? @tag "a")
  (#eq? @attr_name "href")
  (#match? @href "^/")) @internal_anchor


; =========================================================
; @query: inline_fetch_calls
; =========================================================

(attribute
  (attribute_name) @event_name
  (quoted_attribute_value
    (attribute_value) @event_code)
  (#match? @event_name "^on")
  (#match? @event_code "fetch\\(")) @inline_fetch


; =========================================================
; @query: csrf_inputs
; =========================================================

(self_closing_tag
  (tag_name) @tag
  (attribute
    (attribute_name) @name_attr
    (quoted_attribute_value
      (attribute_value) @name))
  (#eq? @tag "input")
  (#eq? @name_attr "name")
  (#eq? @name "_token")) @csrf_input


; =========================================================
; ADVANCED STRUCTURAL & SECURITY ANALYSIS EXTENSIONS
; =========================================================


; =========================================================
; @query: script_injections
; Detect inline JS (XSS risk areas)
; =========================================================

(script_element
  (raw_text) @inline_script_body) @inline_script


; =========================================================
; @query: dynamic_script_injection
; Detect document.write or innerHTML usage inside inline scripts
; =========================================================

(script_element
  (raw_text) @script_code
  (#match? @script_code "(document\\.write|innerHTML|outerHTML)")) @dynamic_dom_injection


; =========================================================
; @query: inline_eval_usage
; Detect eval usage
; =========================================================

(script_element
  (raw_text) @script_code
  (#match? @script_code "eval\\(")) @eval_usage


; =========================================================
; @query: javascript_protocol_links
; href="javascript:..."
; =========================================================

(attribute
  (attribute_name) @attr_name
  (quoted_attribute_value
    (attribute_value) @href)
  (#eq? @attr_name "href")
  (#match? @href "^javascript:")) @javascript_protocol_link


; =========================================================
; @query: target_blank_without_rel
; Security: target="_blank" without rel="noopener"
; =========================================================

(attribute
  (attribute_name) @target_attr
  (quoted_attribute_value
    (attribute_value) @target_value)
  (#eq? @target_attr "target")
  (#eq? @target_value "_blank")) @target_blank


; =========================================================
; @query: accessibility_missing_alt
; Detect img without alt attribute
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "img")) @img_element


; =========================================================
; @query: aria_attributes
; =========================================================

(attribute
  (attribute_name) @aria_attr
  (#match? @aria_attr "^aria-")) @aria_attribute


; =========================================================
; @query: role_attributes
; =========================================================

(attribute
  (attribute_name) @role_attr
  (#eq? @role_attr "role")) @role_attribute


; =========================================================
; @query: template_tags
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "template")) @template_tag


; =========================================================
; @query: iframe_usage
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "iframe")) @iframe_element


; =========================================================
; @query: video_elements
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "video")) @video_element


; =========================================================
; @query: audio_elements
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "audio")) @audio_element


; =========================================================
; @query: svg_elements
; =========================================================

(element
  (start_tag
    (tag_name) @tag)
  (#eq? @tag "svg")) @svg_element


; =========================================================
; @query: alpine_directives
; x-data / x-bind / x-on
; =========================================================

(attribute
  (attribute_name) @alpine_attr
  (#match? @alpine_attr "^x-")) @alpine_directive


; =========================================================
; @query: vue_directives
; v-if / v-for / v-bind / v-model
; =========================================================

(attribute
  (attribute_name) @vue_attr
  (#match? @vue_attr "^v-")) @vue_directive


; =========================================================
; @query: jsx_like_classname
; className detection (React SSR)
; =========================================================

(attribute
  (attribute_name) @attr
  (#eq? @attr "className")) @jsx_classname


; =========================================================
; @query: dynamic_route_links
; href with template syntax
; =========================================================

(attribute
  (attribute_name) @attr
  (quoted_attribute_value
    (attribute_value) @href)
  (#eq? @attr "href")
  (#match? @href "{{|\\$\\{|@")) @dynamic_route


; =========================================================
; @query: inline_style_js_expressions
; style="{{...}}"
; =========================================================

(attribute
  (attribute_name) @style_attr
  (quoted_attribute_value
    (attribute_value) @style_value)
  (#eq? @style_attr "style")
  (#match? @style_value "{{|\\$\\{")) @dynamic_inline_style


; =========================================================
; @query: progressive_enhancement_forms
; Forms with hx-* OR fetch inline
; =========================================================

(element
  (start_tag
    (tag_name) @tag
    (attribute
      (attribute_name) @htmx_attr
      (#match? @htmx_attr "^hx-")))
  (#eq? @tag "form")) @progressive_form


; =========================================================
; @query: http_links_external
; Detect absolute external links
; =========================================================

(attribute
  (attribute_name) @attr
  (quoted_attribute_value
    (attribute_value) @href)
  (#eq? @attr "href")
  (#match? @href "^https?://")) @external_link


; =========================================================
; @query: mailto_links
; =========================================================

(attribute
  (attribute_name) @attr
  (quoted_attribute_value
    (attribute_value) @href)
  (#eq? @attr "href")
  (#match? @href "^mailto:")) @mailto_link


; =========================================================
; @query: tel_links
; =========================================================

(attribute
  (attribute_name) @attr
  (quoted_attribute_value
    (attribute_value) @href)
  (#eq? @attr "href")
  (#match? @href "^tel:")) @tel_link

; =========================================================
; @query: img_without_alt
; Accessibility check
; =========================================================

(element
  (start_tag
    (tag_name) @tag
    (_)*)
  (#eq? @tag "img")
  (#not-match? @img_missing_alt "alt=")) @img_missing_alt


; =========================================================
; @query: target_blank_no_rel
; =========================================================

(element
  (start_tag
    (tag_name) @tag
    (attribute
      (attribute_name) @target_attr
      (quoted_attribute_value
        (attribute_value) @target_value)))
  (#eq? @target_attr "target")
  (#eq? @target_value "_blank")
  (#not-match? @target_blank_no_rel "rel=")) @target_blank_no_rel


; =========================================================
; @query: http_endpoints
; Unified endpoint extraction
; =========================================================

(attribute
  (attribute_name) @attr
  (quoted_attribute_value
    (attribute_value) @endpoint)
  (#match? @attr "^(href|action|hx-(get|post|put|patch|delete))$")) @http_endpoint


; =========================================================
; @query: fetch_calls_in_script
; =========================================================

(script_element
  (raw_text) @script_code
  (#match? @script_code "fetch\\(")) @fetch_call


; =========================================================
; @query: innerhtml_assignment
; =========================================================

(script_element
  (raw_text) @script_code
  (#match? @script_code "\\.innerHTML\\s*=")) @innerhtml_assignment


; =========================================================
; @query: blade_directives
; =========================================================

(text
  @text_node
  (#match? @text_node "@(if|foreach|section|yield|csrf)")) @blade_directive


; =========================================================
; @query: jinja_expressions
; =========================================================

(text
  @text_node
  (#match? @text_node "{%.*%}")) @jinja_block


; =========================================================
; @query: htmx_requests_full
; =========================================================

(element
  (start_tag
    (tag_name) @tag
    (attribute
      (attribute_name) @htmx_method
      (quoted_attribute_value
        (attribute_value) @endpoint)))
  (#match? @htmx_method "^hx-(get|post|put|patch|delete)$")) @htmx_request


; =========================================================
; @query: dangerous_inline_event
; =========================================================

(attribute
  (attribute_name) @event
  (quoted_attribute_value
    (attribute_value) @code)
  (#match? @event "^on")
  (#match? @code "(eval|innerHTML|fetch|XMLHttpRequest)")) @dangerous_event_handler
