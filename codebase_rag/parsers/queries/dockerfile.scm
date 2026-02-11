; ===================================================
; DOCKERFILE – MULTI-LANGUAGE GRAPH RAG EDITION
; ===================================================


; ---------------------------------------------------
; FROM (Stage Root Node)
; ---------------------------------------------------
@query: function_definitions
(
  from_instruction
    image: (image_spec) @stage.base_image
    tag: (tag)? @stage.image_tag
    digest: (digest)? @stage.image_digest
    alias: (identifier)? @stage.alias
) @stage


; ---------------------------------------------------
; RUN (Generic Command)
; ---------------------------------------------------
@query: function_calls
(
  run_instruction
    (shell_command) @command.run
) @run


; ---------------------------------------------------
; RUN – Language Package Install Detection
; ---------------------------------------------------

@query: node_package_install
(
  run_instruction
    (shell_command) @pkg.node
  (#match? @pkg.node "(npm install|yarn install|pnpm install)")
)

@query: python_package_install
(
  run_instruction
    (shell_command) @pkg.python
  (#match? @pkg.python "(pip install|poetry install)")
)

@query: go_package_install
(
  run_instruction
    (shell_command) @pkg.go
  (#match? @pkg.go "go mod (download|tidy)")
)

@query: rust_package_install
(
  run_instruction
    (shell_command) @pkg.rust
  (#match? @pkg.rust "cargo (build|install)")
)

@query: system_package_install
(
  run_instruction
    (shell_command) @pkg.system
  (#match? @pkg.system "(apt-get install|apk add|yum install|dnf install)")
)


; ---------------------------------------------------
; COPY (Critical Cross-File Edge)
; ---------------------------------------------------
@query: copy_instruction
(
  copy_instruction
    source: (path) @copy.source
    destination: (path) @copy.destination
    from: (identifier)? @copy.from_stage
) @copy


; ---------------------------------------------------
; COPY – Dependency File Detection
; ---------------------------------------------------

@query: copy_package_json
(
  copy_instruction
    source: (path) @copy.package_json
  (#match? @copy.package_json "package\\.json$")
)

@query: copy_requirements
(
  copy_instruction
    source: (path) @copy.requirements
  (#match? @copy.requirements "requirements\\.txt$")
)

@query: copy_go_mod
(
  copy_instruction
    source: (path) @copy.go_mod
  (#match? @copy.go_mod "go\\.mod$")
)

@query: copy_cargo_toml
(
  copy_instruction
    source: (path) @copy.cargo_toml
  (#match? @copy.cargo_toml "Cargo\\.toml$")
)

@query: copy_pyproject
(
  copy_instruction
    source: (path) @copy.pyproject
  (#match? @copy.pyproject "pyproject\\.toml$")
)


; ---------------------------------------------------
; WORKDIR
; ---------------------------------------------------
@query: workdir
(
  workdir_instruction
    (path) @context.workdir
) @context


; ---------------------------------------------------
; ENV
; ---------------------------------------------------
@query: environment_variables
(
  env_instruction
    (env_pair
      key: (identifier) @env.key
      value: (string) @env.value
    )+
) @env


; ---------------------------------------------------
; ARG
; ---------------------------------------------------
@query: build_arguments
(
  arg_instruction
    name: (identifier) @arg.name
    value: (string)? @arg.value
) @arg


; ---------------------------------------------------
; CMD
; ---------------------------------------------------
@query: cmd_instruction
(
  cmd_instruction
    (json_array)? @command.cmd_json
    (shell_command)? @command.cmd_shell
) @runtime


; ---------------------------------------------------
; ENTRYPOINT
; ---------------------------------------------------
@query: entrypoint_instruction
(
  entrypoint_instruction
    (json_array)? @command.entry_json
    (shell_command)? @command.entry_shell
) @runtime


; ---------------------------------------------------
; EXPOSE
; ---------------------------------------------------
@query: expose_ports
(
  expose_instruction
    (port) @network.port
) @network


; ---------------------------------------------------
; VOLUME
; ---------------------------------------------------
@query: volume_mounts
(
  volume_instruction
    (path)+ @storage.path
) @storage
