"""
This module defines Pydantic models used for data validation and serialization
throughout the application.

These models serve as schemas for various data structures, such as the results of
tool executions (e.g., graph queries, file operations, shell commands) and health
checks. By using Pydantic, the application benefits from robust data validation,
type coercion, and clear, declarative schema definitions.

The models defined here are often used to structure the data returned by tools
to the LLM agent, ensuring that the agent receives consistent and predictable
output.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .types_defs import ResultRow


class QueryGraphData(BaseModel):
    """
    Represents the structured result of a graph query.

    Attributes:
        query_used (str): The Cypher query that was executed.
        results (list[ResultRow]): The list of result rows from the database.
        summary (str): A natural language summary of the results.
    """

    query_used: str
    results: list[ResultRow]
    summary: str

    @field_validator("results", mode="before")
    @classmethod
    def _format_results(cls, v: list[ResultRow] | None) -> list[ResultRow]:
        """
        Sanitizes and formats the raw database results before validation.

        Ensures that all values in the result rows are JSON-serializable types.
        Non-standard objects are converted to their string representation.

        Args:
            v (list[ResultRow] | None): The raw list of results.

        Returns:
            list[ResultRow]: The cleaned list of results.
        """
        if not isinstance(v, list):
            return []

        clean_results: list[ResultRow] = []
        for row in v:
            clean_row: ResultRow = {
                k: (
                    val
                    if isinstance(
                        val, str | int | float | bool | list | dict | type(None)
                    )
                    else str(val)
                )
                for k, val in row.items()
            }
            clean_results.append(clean_row)
        return clean_results

    model_config = ConfigDict(extra="forbid")


class CodeSnippet(BaseModel):
    """
    Represents a snippet of source code retrieved from a file.

    Attributes:
        qualified_name (str): The fully qualified name of the code entity.
        source_code (str): The actual source code text.
        file_path (str): The path to the file containing the snippet.
        line_start (int): The starting line number of the snippet.
        line_end (int): The ending line number of the snippet.
        docstring (str | None): The docstring associated with the code, if any.
        found (bool): A flag indicating if the snippet was successfully found.
        error_message (str | None): An error message if the retrieval failed.
    """

    qualified_name: str
    source_code: str
    file_path: str
    line_start: int
    line_end: int
    docstring: str | None = None
    found: bool = True
    error_message: str | None = None


class ShellCommandResult(BaseModel):
    """
    Represents the result of executing a shell command.

    Attributes:
        return_code (int): The exit code of the command.
        stdout (str): The standard output from the command.
        stderr (str): The standard error from the command.
    """

    return_code: int
    stdout: str
    stderr: str


class EditResult(BaseModel):
    """
    Represents the result of an in-place file edit operation.

    Attributes:
        file_path (str): The path to the file that was edited.
        success (bool): A flag indicating if the edit was successful.
        error_message (str | None): An error message if the edit failed.
    """

    file_path: str
    success: bool = True
    error_message: str | None = None

    @model_validator(mode="after")
    def _set_success_on_error(self) -> EditResult:
        """
        Ensures the `success` flag is set to False if an error message is present.
        """
        if self.error_message is not None:
            self.success = False
        return self


class FileReadResult(BaseModel):
    """
    Represents the result of a file read operation.

    Attributes:
        file_path (str): The path to the file that was read.
        content (str | None): The content of the file, or None if an error occurred.
        error_message (str | None): An error message if the read failed.
    """

    file_path: str
    content: str | None = None
    error_message: str | None = None


class FileCreationResult(BaseModel):
    """
    Represents the result of a file creation operation.

    Attributes:
        file_path (str): The path to the file that was created.
        success (bool): A flag indicating if the creation was successful.
        error_message (str | None): An error message if the creation failed.
    """

    file_path: str
    success: bool = True
    error_message: str | None = None

    @model_validator(mode="after")
    def _set_success_on_error(self) -> FileCreationResult:
        """
        Ensures the `success` flag is set to False if an error message is present.
        """
        if self.error_message is not None:
            self.success = False
        return self


class HealthCheckResult(BaseModel):
    """
    Represents the result of a single health check for a service.

    Attributes:
        name (str): The name of the service being checked (e.g., 'Ollama').
        passed (bool): A flag indicating if the health check passed.
        message (str): A descriptive message about the status.
        error (str | None): The error message if the check failed.
    """

    name: str
    passed: bool
    message: str
    error: str | None = None
