; ==========================================
; CSS – GRAPH RAG ENTERPRISE EDITION
; Tailwind Enhanced Intelligence Layer
; ==========================================


; ==========================================
; STANDARDIZED CROSS-LANGUAGE CAPTURES
; ==========================================

@query: function_definitions
(rule_set) @function

@query: class_definitions
(class_selector) @class

@query: function_calls
(keyframes_statement) @call

@query: import_statements
(import_statement) @import

@query: id_selector
(id_selector) @id_selector

@query: media
(media_statement) @media

@query: property_name
(property_name) @property_name

@query: property_value
(plain_value) @property_value


; ==========================================
; TAILWIND INTELLIGENCE LAYER
; ==========================================


; ------------------------------------------
; 1️⃣ Tailwind Core At-Rules
; ------------------------------------------
@query: tailwind_at_rule
(
  (at_rule
    (at_keyword) @tailwind_at_name
    (#match? @tailwind_at_name
      "^@(tailwind|apply|layer|config|screen|variants|responsive)$"))
)


; ------------------------------------------
; 2️⃣ @apply → Utility Extraction
; ------------------------------------------
@query: tailwind_apply_utilities
(
  (at_rule
    (at_keyword) @_apply
    (declaration
      (plain_value) @tailwind_apply_utilities)
    (#eq? @_apply "@apply"))
)


; ------------------------------------------
; 3️⃣ IMPORTANT Modifier (!mt-4)
; ------------------------------------------
@query: tailwind_important_utility
(
  (class_selector) @tailwind_important_utility
  (#match? @tailwind_important_utility "^!")
)


; ------------------------------------------
; 4️⃣ Variant Chains (md:hover:dark:...)
; ------------------------------------------
@query: tailwind_variant_chain
(
  (class_selector) @tailwind_variant_chain
  (#match? @tailwind_variant_chain
    "^([a-z0-9\\-]+:)+")
)


; ------------------------------------------
; 5️⃣ Arbitrary Value Utilities
; ------------------------------------------
@query: tailwind_arbitrary_value
(
  (class_selector) @tailwind_arbitrary_value
  (#match? @tailwind_arbitrary_value
    "\\[[^\\]]+\\]")
)


; ------------------------------------------
; 6️⃣ Color Utilities
; ------------------------------------------
@query: tailwind_color_utility
(
  (class_selector) @tailwind_color_utility
  (#match? @tailwind_color_utility
    "^(bg|text|border|from|via|to)-")
)


; ------------------------------------------
; 7️⃣ Spacing Utilities
; ------------------------------------------
@query: tailwind_spacing_utility
(
  (class_selector) @tailwind_spacing_utility
  (#match? @tailwind_spacing_utility
    "^(m|p|mx|my|mt|mb|ml|mr|px|py|pt|pb|pl|pr|gap|space-x|space-y)-")
)


; ------------------------------------------
; 8️⃣ Layout & Display Utilities
; ------------------------------------------
@query: tailwind_layout_utility
(
  (class_selector) @tailwind_layout_utility
  (#match? @tailwind_layout_utility
    "^(flex|inline-flex|grid|block|inline-block|hidden|container)$")
)


; ------------------------------------------
; 9️⃣ Flex & Grid Control Utilities
; ------------------------------------------
@query: tailwind_flex_grid_control
(
  (class_selector) @tailwind_flex_grid_control
  (#match? @tailwind_flex_grid_control
    "^(items|justify|content|self|place|grid-cols|grid-rows)-")
)


; ------------------------------------------
; 🔟 Typography Utilities
; ------------------------------------------
@query: tailwind_typography_utility
(
  (class_selector) @tailwind_typography_utility
  (#match? @tailwind_typography_utility
    "^(text|font|tracking|leading|uppercase|lowercase|capitalize|line-clamp)-")
)


; ------------------------------------------
; 11️⃣ Opacity Modifiers
; ------------------------------------------
@query: tailwind_opacity_modifier
(
  (class_selector) @tailwind_opacity_modifier
  (#match? @tailwind_opacity_modifier
    "\\/[0-9]+$")
)


; ------------------------------------------
; 12️⃣ Dark Mode Detection
; ------------------------------------------
@query: tailwind_dark_variant
(
  (class_selector) @tailwind_dark_variant
  (#match? @tailwind_dark_variant
    "^dark:")
)
