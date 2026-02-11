; =========================================================
; YAML – ADVANCED GRAPH RAG EDITION
; Production-Ready YAML.scm v3
; DevOps + Config + Dependency Aware
; =========================================================



; =========================================================
; DOCUMENT ROOT
; =========================================================

@query: yaml_document
(document) @yaml_document

@query: multi_document
(stream) @yaml_stream



; =========================================================
; MAPPING STRUCTURE
; =========================================================

@query: mapping_pair
(block_mapping_pair
  (flow_node) @defined_key
  (block_node) @assigned_value) @key_value_pair


@query: flow_pair
(flow_mapping
  (flow_pair
    (_) @defined_key
    (_) @assigned_value)) @flow_pair



; =========================================================
; NESTED STRUCTURES
; =========================================================

@query: nested_mapping
(block_mapping) @mapping_node

@query: nested_sequence
(block_sequence) @sequence_node

@query: nested_object_edge
(block_mapping_pair
  (_) @parent_key
  (block_node
    (block_mapping))) @object_edge

@query: nested_array_edge
(block_mapping_pair
  (_) @parent_key
  (block_node
    (block_sequence))) @array_edge



; =========================================================
; SEQUENCES
; =========================================================

@query: block_sequence
(block_sequence) @block_sequence

@query: block_sequence_item
(block_sequence_item
  (block_node) @sequence_item) @sequence_edge

@query: flow_sequence
(flow_sequence
  (_) @sequence_item) @flow_sequence_edge



; =========================================================
; SCALAR TYPES
; =========================================================

@query: string_scalar
(string_scalar) @string_value

@query: plain_scalar
(plain_scalar) @plain_value

@query: block_scalar
(block_scalar) @block_value



; =========================================================
; BOOLEAN & NUMBER DETECTION
; =========================================================

@query: boolean_scalar
(plain_scalar) @boolean_value
(#match? @boolean_value "^(true|false|True|False)$")

@query: numeric_scalar
(plain_scalar) @number_value
(#match? @number_value "^[0-9\\.]+$")



; =========================================================
; ANCHORS & ALIASES (Dependency Graph)
; =========================================================

@query: anchor_definition
(anchor
  (anchor_name) @anchor_name) @anchor_definition

@query: alias_reference
(alias) @alias_reference



; =========================================================
; TAGS (Type System Awareness)
; =========================================================

@query: tag_usage
(tag) @tag_usage



; =========================================================
; PATH EXTRACTION (Hierarchy Graph)
; =========================================================

@query: parent_child_key_edge
(block_mapping_pair
  (flow_node) @parent_key
  (block_node
    (block_mapping
      (block_mapping_pair
        (flow_node) @child_key)))) @key_hierarchy_edge



; =========================================================
; DEVOPS INTELLIGENCE LAYER
; =========================================================

; --- Docker Compose Detection ---
@query: docker_service
(block_mapping_pair
  (flow_node) @service_key
  (#eq? @service_key "services")) @docker_services_root


; --- Kubernetes Kind ---
@query: k8s_kind
(block_mapping_pair
  (flow_node) @kind_key
  (block_node) @kind_value
  (#eq? @kind_key "kind")) @k8s_resource


; --- Kubernetes Metadata Name ---
@query: k8s_metadata_name
(block_mapping_pair
  (flow_node) @meta_key
  (block_node
    (block_mapping
      (block_mapping_pair
        (flow_node) @name_key
        (block_node) @resource_name)))
  (#eq? @meta_key "metadata")
  (#eq? @name_key "name")) @k8s_name_edge


; --- Environment Variables ---
@query: environment_variable
(block_mapping_pair
  (flow_node) @env_key
  (#match? @env_key "^[A-Z0-9_]+$")) @env_variable_edge



; =========================================================
; SECRET DETECTION
; =========================================================

@query: possible_secret_key
(block_mapping_pair
  (flow_node) @secret_key
  (#match? @secret_key "(password|secret|token|apikey|api_key|auth)")) @secret_key_edge



; =========================================================
; CI/CD INTELLIGENCE
; =========================================================

; GitHub Actions
@query: github_actions_job
(block_mapping_pair
  (flow_node) @jobs_key
  (#eq? @jobs_key "jobs")) @github_jobs_root


; GitLab CI
@query: gitlab_stage
(block_mapping_pair
  (flow_node) @stage_key
  (#eq? @stage_key "stages")) @gitlab_stage_root



; =========================================================
; UNIVERSAL FALLBACK
; =========================================================

@query: any_mapping
(block_mapping) @any_mapping

@query: any_sequence
(block_sequence) @any_sequence

@query: any_scalar
(string_scalar) @any_string
(plain_scalar)  @any_plain
(block_scalar)  @any_block
