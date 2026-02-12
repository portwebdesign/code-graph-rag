from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.services import IngestorProtocol


@dataclass
class TemplateExtraction:
    """
    Data class to hold extracted elements from a Django template.

    Args:
        tags (list[str]): List of custom template tags found.
        variables (list[str]): List of template variables found.
        includes (list[str]): List of included template paths.
        extends (list[str]): List of extended parent template paths.
    """

    tags: list[str]
    variables: list[str]
    includes: list[str]
    extends: list[str]


class DjangoTemplateParser:
    """
    Parser for extracting relationships and entities from Django templates.

    This class identifies template inheritance (extends), inclusions (include),
    variables, and custom tags to build a graph representation of the template structure.

    Args:
        repo_path (Path): Path to the repository root.
        project_name (str): Name of the project.
        ingestor (IngestorProtocol): Ingestor instance for database operations.
    """

    TAG_PATTERN = re.compile(r"{%\s*([a-zA-Z_][\w-]*)\b", re.MULTILINE)
    VAR_PATTERN = re.compile(r"{{\s*([^}]+?)\s*}}", re.MULTILINE)
    INCLUDE_PATTERN = re.compile(r"{%\s*include\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
    EXTENDS_PATTERN = re.compile(r"{%\s*extends\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        ingestor: IngestorProtocol,
    ) -> None:
        self.repo_path = repo_path
        self.project_name = project_name
        self.ingestor = ingestor

    @staticmethod
    def build_template_index(repo_path: Path) -> dict[str, str]:
        """
        Builds an index of template files for quick lookup by relative path or name.

        Args:
            repo_path (Path): Path to the repository root.

        Returns:
            dict[str, str]: A dictionary mapping template names/paths to their repo-relative paths.
        """
        index: dict[str, str] = {}
        for file_path in repo_path.rglob("*.htm"):
            DjangoTemplateParser._add_template_index_keys(index, repo_path, file_path)
        for file_path in repo_path.rglob("*.html"):
            DjangoTemplateParser._add_template_index_keys(index, repo_path, file_path)
        return index

    @staticmethod
    def _add_template_index_keys(
        index: dict[str, str], repo_path: Path, file_path: Path
    ) -> None:
        """
        Adds multiple keys to the template index for a single file (fullpath, filename, templates/ suffix).

        Args:
            index (dict[str, str]): The index dictionary to update.
            repo_path (Path): Repository root path.
            file_path (Path): Path to the template file.
        """
        try:
            rel_path = str(file_path.relative_to(repo_path)).replace("\\", "/")
        except ValueError:
            return
        if rel_path not in index:
            index[rel_path] = rel_path

        filename = file_path.name
        if filename not in index:
            index[filename] = rel_path

        if "/templates/" in rel_path:
            suffix = rel_path.split("/templates/", 1)[1]
            if suffix not in index:
                index[suffix] = rel_path

    @staticmethod
    def _normalize_template_name(name: str) -> str:
        """
        Normalizes a template name found in source code (strips quotes, standardizes slashes).

        Args:
            name (str): The raw template name string.

        Returns:
            str: The normalized template name.
        """
        cleaned = name.strip().strip("\"'")
        cleaned = cleaned.replace("\\", "/")
        return cleaned

    def parse_template(self, file_path: Path, source: str) -> TemplateExtraction:
        """
        Parses the template source code to extract tags, variables, definitions, and references.

        Args:
            file_path (Path): Path to the template file (for logging/context if needed).
            source (str): The template source code.

        Returns:
            TemplateExtraction: An object containing the extracted elements.
        """
        tags = list(dict.fromkeys(self.TAG_PATTERN.findall(source)))
        variables_raw = self.VAR_PATTERN.findall(source)
        variables = []
        for raw in variables_raw:
            var = raw.split("|")[0].strip()
            var = var.split(".")[0].strip()
            if var:
                variables.append(var)
        variables = list(dict.fromkeys(variables))

        includes = [
            self._normalize_template_name(val)
            for val in self.INCLUDE_PATTERN.findall(source)
        ]
        extends = [
            self._normalize_template_name(val)
            for val in self.EXTENDS_PATTERN.findall(source)
        ]

        return TemplateExtraction(
            tags=tags, variables=variables, includes=includes, extends=extends
        )

    def ingest_template(
        self,
        file_path: Path,
        extraction: TemplateExtraction,
        template_index: dict[str, str],
    ) -> None:
        """
        Ingests the extracted template information into the graph database.

        Args:
            file_path (Path): Path to the template file.
            extraction (TemplateExtraction): The extracted data.
            template_index (dict[str, str]): Index for resolving template references.
        """
        relative_path = str(file_path.relative_to(self.repo_path))
        file_node = (cs.NodeLabel.FILE, cs.KEY_PATH, relative_path)

        for tag in extraction.tags:
            block_qn = self._ensure_block_node(tag, "django_tag")
            self.ingestor.ensure_relationship_batch(
                file_node,
                cs.RelationshipType.CONTAINS,
                (cs.NodeLabel.BLOCK, cs.KEY_QUALIFIED_NAME, block_qn),
                {cs.KEY_RELATION_TYPE: "django_template"},
            )

        for var in extraction.variables:
            block_qn = self._ensure_block_node(var, "django_var")
            self.ingestor.ensure_relationship_batch(
                file_node,
                cs.RelationshipType.CONTAINS,
                (cs.NodeLabel.BLOCK, cs.KEY_QUALIFIED_NAME, block_qn),
                {cs.KEY_RELATION_TYPE: "django_template"},
            )

        for ref in extraction.includes + extraction.extends:
            target_path = template_index.get(ref)
            if not target_path:
                continue
            self.ingestor.ensure_relationship_batch(
                file_node,
                cs.RelationshipType.EMBEDS,
                (cs.NodeLabel.FILE, cs.KEY_PATH, target_path),
                {cs.KEY_RELATION_TYPE: "django_template"},
            )

    def _ensure_block_node(self, block_name: str, block_type: str) -> str:
        """
        Ensures a block node exists for a tag or variable.

        Args:
            block_name (str): The name of the block (tag/var name).
            block_type (str): The type of block (e.g., "django_tag", "django_var").

        Returns:
            str: The qualified name of the created/ensured block.
        """
        block_qn = (
            f"{self.project_name}{cs.SEPARATOR_DOT}block.{block_type}.{block_name}"
        )
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.BLOCK,
            {
                cs.KEY_QUALIFIED_NAME: block_qn,
                cs.KEY_NAME: block_name,
                cs.KEY_BLOCK_NAME: block_name,
                cs.KEY_BLOCK_TYPE: block_type,
            },
        )
        return block_qn

    def process_files(
        self, files: Iterable[Path], template_index: dict[str, str]
    ) -> None:
        """
        Processes a batch of files, parsing and ingesting valid Django templates.

        Args:
            files (Iterable[Path]): A collection of file paths to process.
            template_index (dict[str, str]): The template index for resolution.
        """
        for file_path in files:
            try:
                source = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "{{" not in source and "{%" not in source:
                continue
            extraction = self.parse_template(file_path, source)
            if (
                not extraction.tags
                and not extraction.variables
                and not extraction.includes
                and not extraction.extends
            ):
                continue
            try:
                self.ingest_template(file_path, extraction, template_index)
            except Exception as exc:
                logger.debug("Django template ingest failed for {}: {}", file_path, exc)
