from __future__ import annotations

from enum import StrEnum

from codebase_rag.core.constants import MCPToolName


class AgenticToolName(StrEnum):
    QUERY_GRAPH = "query_graph"
    READ_FILE = "read_file"
    CREATE_FILE = "create_file"
    REPLACE_CODE = "replace_code"
    LIST_DIRECTORY = "list_directory"
    ANALYZE_DOCUMENT = "analyze_document"
    EXECUTE_SHELL = "execute_shell"
    SEMANTIC_SEARCH = "semantic_search"
    GET_FUNCTION_SOURCE = "get_function_source"
    GET_CODE_SNIPPET = "get_code_snippet"
    CONTEXT7_DOCS = "context7_docs"


ANALYZE_DOCUMENT = (
    "Analyzes documents (PDFs, images) to answer questions about their content."
)

CODEBASE_QUERY = (
    "Query the codebase knowledge graph using natural language questions. "
    "Ask in plain English about classes, functions, methods, dependencies, or code structure. "
    "Examples: 'Find all functions that call each other', "
    "'What classes are in the user module', "
    "'Show me functions with the longest call chains'."
)

DIRECTORY_LISTER = "Lists the contents of a directory to explore the codebase."

FILE_WRITER = (
    "Creates a new file with content. IMPORTANT: Check file existence first! "
    "Overwrites completely WITHOUT showing diff. "
    "Use only for new files, not existing file modifications."
)

SHELL_COMMAND = (
    "Executes shell commands from allowlist. "
    "Read-only commands run without approval; write operations require user confirmation."
)

CODE_RETRIEVAL = (
    "Retrieves the source code for a specific function, class, or method "
    "using its full qualified name."
)

SEMANTIC_SEARCH = (
    "Performs a semantic search for functions based on a natural language query "
    "describing their purpose, returning a list of potential matches with similarity scores."
)

GET_FUNCTION_SOURCE = (
    "Retrieves the source code for a specific function or method using its internal node ID, "
    "typically obtained from a semantic search result."
)

CONTEXT7_DOCS = "Fetches reference documentation from Context7 and caches it in the graph for reuse."

FILE_READER = (
    "Reads the content of text-based files. "
    "For documents like PDFs or images, use the 'analyze_document' tool instead."
)

FILE_EDITOR = (
    "Surgically replaces specific code blocks in files. "
    "Requires exact target code and replacement. "
    "Only modifies the specified block, leaving rest of file unchanged. "
    "True surgical patching."
)

# (H) MCP tool descriptions
MCP_LIST_PROJECTS = (
    "List all indexed projects in the knowledge graph database. "
    "Returns a list of project names that have been indexed."
)

MCP_SELECT_ACTIVE_PROJECT = (
    "Preflight tool to set/confirm the active repository context and return project-scoped readiness info. "
    "Optionally accepts repo_path to switch active root, then reports active project, indexed status, "
    "project-scoped graph counts, latest analysis timestamp, and enforced safety policies."
)

MCP_DETECT_PROJECT_DRIFT = (
    "Detect FS↔Graph drift for a repository/project before re-index decisions. "
    "Reports filesystem file counts, graph module/file counts, and drift signals."
)

MCP_DELETE_PROJECT = (
    "Delete a specific project from the knowledge graph database. "
    "This removes all nodes associated with the project while preserving other projects. "
    "Use list_projects first to see available projects."
)

MCP_WIPE_DATABASE = (
    "WARNING: Completely wipe the entire database, removing ALL indexed projects. "
    "This cannot be undone. Use delete_project for removing individual projects."
)

MCP_INDEX_REPOSITORY = (
    "Parse and ingest the repository into the Memgraph knowledge graph. "
    "This builds a comprehensive graph of functions, classes, dependencies, and relationships. "
    "Note: This preserves other projects - only the current project is re-indexed. "
    "This tool MUST be called only when the user explicitly asks for re-indexing and requires user_requested=true. "
    "A non-empty reason is required; for already indexed projects, drift confirmation is required."
)

MCP_SYNC_GRAPH_UPDATES = (
    "Refresh graph state for the active repository without deleting project data first. "
    "Uses GraphUpdater and respects incremental/git-delta configuration for faster updates."
)

MCP_QUERY_CODE_GRAPH = (
    "Query the codebase knowledge graph using natural language. "
    "Ask questions like 'What functions call UserService.create_user?' or "
    "'Show me all classes that implement the Repository interface'."
)

MCP_SEMANTIC_SEARCH = (
    "Perform semantic (vector-based) code search using embeddings. "
    "Use this for intent-based discovery such as 'auth flow' or 'error handling'."
)

MCP_GET_FUNCTION_SOURCE = (
    "Retrieve source code for a function or method by graph node ID. "
    "Typically used after semantic_search returns candidate node IDs."
)

MCP_GET_CODE_SNIPPET = (
    "Retrieve source code for a function, class, or method by its qualified name. "
    "Returns the source code, file path, line numbers, and docstring."
)

MCP_SURGICAL_REPLACE_CODE = (
    "Surgically replace an exact code block in a file using diff-match-patch. "
    "Only modifies the exact target block, leaving the rest unchanged."
)

MCP_READ_FILE = (
    "Read the contents of a file from the project. Supports pagination for large files. "
    "Use this for implementation-level source verification, not as first step for relationship/hop analysis."
)

MCP_WRITE_FILE = "Write content to a file, creating it if it doesn't exist."

MCP_LIST_DIRECTORY = "List contents of a directory in the project."

MCP_PARAM_PROJECT_NAME = "Name of the project to delete (e.g., 'my-project')"
MCP_PARAM_CONFIRM = "Must be true to confirm the wipe operation"
MCP_PARAM_USER_REQUESTED = "Must be true only when the user explicitly requested this potentially destructive operation"
MCP_PARAM_DRIFT_CONFIRMED = (
    "Set true only after proving filesystem↔graph drift for the target project"
)
MCP_PARAM_NATURAL_LANGUAGE_QUERY = "Your question in plain English about the codebase"
MCP_PARAM_QUERY = "Natural language semantic search query"
MCP_PARAM_TOP_K = "Maximum number of semantic matches to return"
MCP_PARAM_NODE_ID = "Graph node ID of the target function/method"
MCP_PARAM_QUALIFIED_NAME = (
    "Fully qualified name (e.g., 'app.services.UserService.create_user')"
)
MCP_PARAM_FILE_PATH = "Relative path to the file from project root"
MCP_PARAM_TARGET_CODE = "Exact code block to replace"
MCP_PARAM_REPLACEMENT_CODE = "New code to insert"
MCP_PARAM_OFFSET = "Line number to start reading from (0-based, optional)"
MCP_PARAM_LIMIT = "Maximum number of lines to read (optional)"
MCP_PARAM_CONTENT = "Content to write to the file"
MCP_PARAM_DIRECTORY_PATH = "Relative path to directory from project root (default: '.')"
MCP_PARAM_REPO_PATH = "Absolute path to target repository root"
MCP_PARAM_METRIC_NAME = "Name of the metric to retrieve (e.g. 'security_score')"
MCP_PARAM_DEPTH = "Depth of the impact analysis (default 3, max 6)"

MCP_GET_GRAPH_STATS = (
    "Get overall statistics about the knowledge graph, including node counts, "
    "relationship counts, and label distributions. Useful for understanding project scale."
)

MCP_GET_DEPENDENCY_STATS = (
    "Get statistics about module dependencies and imports in the project, "
    "including top importers and top dependents."
)

MCP_GET_ANALYSIS_REPORT = (
    "Retrieve the latest full analysis report for the project, "
    "containing comprehensive code quality and security metrics."
)

MCP_GET_ANALYSIS_METRIC = "Retrieve a specific latest analysis metric by name (e.g., 'security_score', 'complexity')."

MCP_IMPACT_GRAPH = (
    "Analyze the blast radius of a potential change. "
    "Given a qualified name or file path, shows which other modules and functions depend on it "
    "up to a specified depth."
)

MCP_PARAM_MODULES = (
    "Comma-separated list or JSON array of module names to analyze (e.g., 'auth, db')"
)
MCP_PARAM_ARTIFACT_NAME = (
    "Artifact name or filename under output/analysis. "
    "You can pass base name (e.g., 'dead_code_report') or full filename (e.g., 'migration_plan.md')."
)
MCP_PARAM_DIAGRAM = (
    "Mermaid exporter diagram type/name (e.g. 'module', 'call', 'component'). "
    "This is not raw Mermaid source code."
)
MCP_PARAM_OUTPUT_PATH = (
    "Optional file path to save the generated diagram (e.g. 'output.mmd')"
)

MCP_RUN_ANALYSIS = (
    "Run the full suite of static analysis tools on the entire codebase. "
    "This can take significant time for large projects. "
    "Consider using run_analysis_subset or specific tools like security_scan for faster results."
)

MCP_RUN_ANALYSIS_SUBSET = (
    "Run the full analysis suite but only on a specific subset of modules. "
    "Faster than run_analysis when you only care about changes in certain components."
)

MCP_SECURITY_SCAN = (
    "Run only the security, secret scanning, and taint tracking analysis modules on the codebase. "
    "Returns a summary report of any security vulnerabilities found."
)

MCP_PERFORMANCE_HOTSPOTS = "Run only the performance hotspots analysis module to find inefficient code patterns."

MCP_GET_ANALYSIS_ARTIFACT = (
    "Retrieve a generated analysis artifact from output/analysis. "
    "Supports .json, .md and .log outputs by base name or filename. "
    "Use this for detailed raw findings and plans (e.g., dead_code_report, migration_plan.md)."
)

MCP_LIST_ANALYSIS_ARTIFACTS = (
    "List files currently available under output/analysis with metadata "
    "(filename, extension, size_bytes, modified_at). "
    "Use this to discover which artifacts can be fetched with get_analysis_artifact."
)

MCP_EXPORT_MERMAID = (
    "Export a Mermaid diagram generated from current graph data to disk. "
    "Provide a diagram type/name supported by MermaidExporter and optionally an output path."
)

MCP_RUN_CYPHER = (
    "Execute a raw Cypher query against the Memgraph database. "
    "Use this for advanced ad-hoc querying not covered by standard tools and for explicit single-hop/multi-hop traversal control. "
    "Query MUST be scoped to active project to avoid cross-project access. "
    "Set write=True ONLY IF you intend to modify the graph (nodes/edges). "
    "For write operations, user_requested=true and a non-empty high-quality reason are required. "
    "Write operations run safe dry-run impact analysis and are blocked when impact is too high. "
    "WARNING: Modifying the graph directly can cause inconsistencies with the actual source code."
)

MCP_APPLY_DIFF_SAFE = (
    "Apply one or more surgical replacements in a single file. "
    "Requires 'file_path' and 'chunks', where chunks is a JSON string list of objects with "
    "'target_code' and 'replacement_code'."
)

MCP_REFACTOR_BATCH = (
    "Apply multiple diff modifications across several files in a single operation. "
    "Requires 'chunks' which is a JSON formatted string containing multiple diffs."
)

MCP_PLAN_TASK = (
    "Ask an agent planner to create a multi-step execution plan for a specified goal. "
    "Provide the 'goal' and optional 'context' the planner might need. "
    "Helpful for breaking down complex refactoring or feature additions."
)

MCP_TEST_GENERATE = (
    "Ask a specialized test-generation agent to create test cases for a specific function or class. "
    "Provide the 'goal' (e.g. 'Generate tests for codebase_rag.core.constants')."
)

MCP_MEMORY_ADD = (
    "Add a memory entry (context, decision, or fact) to the persistent memory store. "
    "Tags are optional but help categorize memories (e.g. 'architecture', 'auth')."
)

MCP_MEMORY_LIST = "List recently added memory entries from the persistent memory store."

MCP_MEMORY_QUERY_PATTERNS = (
    "Query memory for similar successful patterns before planning or refactoring. "
    "Supports free-text query, optional tag filters, and success-only filtering."
)

MCP_EXECUTION_FEEDBACK = (
    "Record execution outcome feedback after a tool run. "
    "If feedback indicates test failure or low coverage, the session can require replanning."
)

MCP_TEST_QUALITY_GATE = (
    "Evaluate test quality score from coverage, edge-case, and negative-test dimensions (0..1 each). "
    "Blocks completion when total score is below threshold."
)

MCP_GET_TOOL_USEFULNESS_RANKING = (
    "Return tool usefulness telemetry ranking for the current session. "
    "Ranks tools by average usefulness score, success rate, and call count."
)

MCP_VALIDATE_DONE_DECISION = (
    "Run done decision protocol using readiness gates, feedback signals, and optional validator-agent rationale. "
    "Returns done/not_done decision, blockers, and protocol checks."
)

MCP_ORCHESTRATE_REALTIME_FLOW = (
    "Execute realtime GraphRAG workflow after code edits: execution_feedback -> sync_graph_updates -> validate_done_decision. "
    "Optionally verifies drift and auto-executes validate_done_decision.next_best_action."
)

MCP_PARAM_CYPHER = "The Cypher query string to execute"
MCP_PARAM_PARAMS = "JSON string of parameters for the Cypher query (optional)"
MCP_PARAM_WRITE = "Boolean indicating if the query modifies the graph (default: False)"
MCP_PARAM_REASON = "Required for write operations: brief human-readable reason for why the write is necessary"
MCP_PARAM_CHUNKS = (
    "The content describing the changes (diff format or JSON array of diffs)"
)
MCP_PARAM_GOAL = "The objective or goal for the agent to achieve"
MCP_PARAM_CONTEXT = "Optional additional context or instructions"
MCP_PARAM_ENTRY = "The memory text to save"
MCP_PARAM_TAGS = "Comma-separated list of tags for the memory entry (optional)"
MCP_PARAM_FILTER_TAGS = (
    "Comma-separated tag filters for memory pattern query (optional)"
)
MCP_PARAM_SUCCESS_ONLY = "When true, returns only successful pattern memories"
MCP_PARAM_ACTION = "Action name to attach feedback to (e.g. refactor_batch)"
MCP_PARAM_RESULT = "Execution result label (e.g. success, partial_success, failed)"
MCP_PARAM_ISSUES = "Comma-separated issue labels (e.g. test failure, low coverage)"
MCP_PARAM_COVERAGE = "Coverage quality score between 0 and 1"
MCP_PARAM_EDGE_CASES = "Edge-case quality score between 0 and 1"
MCP_PARAM_NEGATIVE_TESTS = "Negative-test quality score between 0 and 1"
MCP_PARAM_SYNC_REASON = "Reason for graph synchronization after code edits"
MCP_PARAM_AUTO_EXECUTE_NEXT = "When true, automatically executes next_best_action returned by validate_done_decision"
MCP_PARAM_VERIFY_DRIFT = "When true, runs detect_project_drift after sync_graph_updates for instant validation"
MCP_PARAM_DEBOUNCE_SECONDS = (
    "Debounce delay in seconds before sync (recommended 2-5 for burst edits)"
)


MCP_TOOLS: dict[MCPToolName, str] = {
    MCPToolName.LIST_PROJECTS: MCP_LIST_PROJECTS,
    MCPToolName.SELECT_ACTIVE_PROJECT: MCP_SELECT_ACTIVE_PROJECT,
    MCPToolName.DETECT_PROJECT_DRIFT: MCP_DETECT_PROJECT_DRIFT,
    MCPToolName.DELETE_PROJECT: MCP_DELETE_PROJECT,
    MCPToolName.WIPE_DATABASE: MCP_WIPE_DATABASE,
    MCPToolName.INDEX_REPOSITORY: MCP_INDEX_REPOSITORY,
    MCPToolName.SYNC_GRAPH_UPDATES: MCP_SYNC_GRAPH_UPDATES,
    MCPToolName.QUERY_CODE_GRAPH: MCP_QUERY_CODE_GRAPH,
    MCPToolName.SEMANTIC_SEARCH: MCP_SEMANTIC_SEARCH,
    MCPToolName.GET_FUNCTION_SOURCE: MCP_GET_FUNCTION_SOURCE,
    MCPToolName.GET_CODE_SNIPPET: MCP_GET_CODE_SNIPPET,
    MCPToolName.SURGICAL_REPLACE_CODE: MCP_SURGICAL_REPLACE_CODE,
    MCPToolName.READ_FILE: MCP_READ_FILE,
    MCPToolName.WRITE_FILE: MCP_WRITE_FILE,
    MCPToolName.LIST_DIRECTORY: MCP_LIST_DIRECTORY,
    MCPToolName.GET_GRAPH_STATS: MCP_GET_GRAPH_STATS,
    MCPToolName.GET_DEPENDENCY_STATS: MCP_GET_DEPENDENCY_STATS,
    MCPToolName.GET_ANALYSIS_REPORT: MCP_GET_ANALYSIS_REPORT,
    MCPToolName.GET_ANALYSIS_METRIC: MCP_GET_ANALYSIS_METRIC,
    MCPToolName.IMPACT_GRAPH: MCP_IMPACT_GRAPH,
    MCPToolName.RUN_ANALYSIS: MCP_RUN_ANALYSIS,
    MCPToolName.RUN_ANALYSIS_SUBSET: MCP_RUN_ANALYSIS_SUBSET,
    MCPToolName.SECURITY_SCAN: MCP_SECURITY_SCAN,
    MCPToolName.PERFORMANCE_HOTSPOTS: MCP_PERFORMANCE_HOTSPOTS,
    MCPToolName.GET_ANALYSIS_ARTIFACT: MCP_GET_ANALYSIS_ARTIFACT,
    MCPToolName.LIST_ANALYSIS_ARTIFACTS: MCP_LIST_ANALYSIS_ARTIFACTS,
    MCPToolName.EXPORT_MERMAID: MCP_EXPORT_MERMAID,
    MCPToolName.RUN_CYPHER: MCP_RUN_CYPHER,
    MCPToolName.APPLY_DIFF_SAFE: MCP_APPLY_DIFF_SAFE,
    MCPToolName.REFACTOR_BATCH: MCP_REFACTOR_BATCH,
    MCPToolName.PLAN_TASK: MCP_PLAN_TASK,
    MCPToolName.TEST_GENERATE: MCP_TEST_GENERATE,
    MCPToolName.MEMORY_ADD: MCP_MEMORY_ADD,
    MCPToolName.MEMORY_LIST: MCP_MEMORY_LIST,
    MCPToolName.MEMORY_QUERY_PATTERNS: MCP_MEMORY_QUERY_PATTERNS,
    MCPToolName.EXECUTION_FEEDBACK: MCP_EXECUTION_FEEDBACK,
    MCPToolName.TEST_QUALITY_GATE: MCP_TEST_QUALITY_GATE,
    MCPToolName.GET_TOOL_USEFULNESS_RANKING: MCP_GET_TOOL_USEFULNESS_RANKING,
    MCPToolName.VALIDATE_DONE_DECISION: MCP_VALIDATE_DONE_DECISION,
    MCPToolName.ORCHESTRATE_REALTIME_FLOW: MCP_ORCHESTRATE_REALTIME_FLOW,
    MCPToolName.GET_EXECUTION_READINESS: "Return confidence gate (graph/code/semantic), semantic pattern-reuse score, and evidence-based completion-gate status for the current session.",
}

AGENTIC_TOOLS: dict[AgenticToolName, str] = {
    AgenticToolName.QUERY_GRAPH: CODEBASE_QUERY,
    AgenticToolName.READ_FILE: FILE_READER,
    AgenticToolName.CREATE_FILE: FILE_WRITER,
    AgenticToolName.REPLACE_CODE: FILE_EDITOR,
    AgenticToolName.LIST_DIRECTORY: DIRECTORY_LISTER,
    AgenticToolName.ANALYZE_DOCUMENT: ANALYZE_DOCUMENT,
    AgenticToolName.EXECUTE_SHELL: SHELL_COMMAND,
    AgenticToolName.SEMANTIC_SEARCH: SEMANTIC_SEARCH,
    AgenticToolName.GET_FUNCTION_SOURCE: GET_FUNCTION_SOURCE,
    AgenticToolName.GET_CODE_SNIPPET: CODE_RETRIEVAL,
    AgenticToolName.CONTEXT7_DOCS: CONTEXT7_DOCS,
}
