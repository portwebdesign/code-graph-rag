from pathlib import Path

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.core import logs
from codebase_rag.data_models.types_defs import LanguageQueries, NodeIdentifier
from codebase_rag.infrastructure.language_spec import get_language_spec_for_path
from codebase_rag.services import IngestorProtocol
from codebase_rag.utils.path_utils import (
    compute_file_hash,
    is_test_path,
    should_skip_path,
    to_posix,
)


class StructureProcessor:
    """
    Processes the directory structure of the repository.

    This class walks the directory tree, identifying packages based on language
    indicators (e.g., `__init__.py`, `package.json`), and creates nodes for
    packages, folders, and files.
    """

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        unignore_paths: frozenset[str] | None = None,
        exclude_paths: frozenset[str] | None = None,
    ):
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries
        self.structural_elements: dict[Path, str | None] = {}
        self.unignore_paths = unignore_paths
        self.exclude_paths = exclude_paths

    def _get_parent_identifier(
        self, parent_rel_path: Path, parent_container_qn: str | None
    ) -> NodeIdentifier:
        """
        Determine the node identifier for the parent container.

        Args:
            parent_rel_path (Path): Relative path to the parent directory.
            parent_container_qn (str | None): Qualified name of the parent container.

        Returns:
            NodeIdentifier: The identifier (Label, Key, Value) for the parent.
        """
        if parent_rel_path == Path(cs.PATH_CURRENT_DIR):
            return (cs.NodeLabel.PROJECT, cs.KEY_NAME, self.project_name)
        if parent_container_qn:
            return (cs.NodeLabel.PACKAGE, cs.KEY_QUALIFIED_NAME, parent_container_qn)
        return (cs.NodeLabel.FOLDER, cs.KEY_PATH, to_posix(parent_rel_path))

    def identify_structure(self) -> None:
        """
        Scan and process the repository structure, identifying packages and folders.
        """
        directories = {self.repo_path}
        for path in self.repo_path.rglob(cs.GLOB_ALL):
            if path.is_dir() and not should_skip_path(
                path,
                self.repo_path,
                exclude_paths=self.exclude_paths,
                unignore_paths=self.unignore_paths,
            ):
                directories.add(path)

        for root in sorted(directories):
            relative_root = root.relative_to(self.repo_path)

            parent_rel_path = relative_root.parent
            parent_container_qn = self.structural_elements.get(parent_rel_path)

            is_package = False
            package_indicators: set[str] = set()

            for lang_queries in self.queries.values():
                lang_config = lang_queries[cs.QUERY_CONFIG]
                package_indicators.update(lang_config.package_indicators)

            for indicator in package_indicators:
                if (root / indicator).exists():
                    is_package = True
                    break

            if is_package:
                package_qn = cs.SEPARATOR_DOT.join(
                    [self.project_name] + list(relative_root.parts)
                )
                self.structural_elements[relative_root] = package_qn
                logger.info(
                    logs.STRUCT_IDENTIFIED_PACKAGE.format(package_qn=package_qn)
                )
                parent_qn = (
                    package_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
                    if cs.SEPARATOR_DOT in package_qn
                    else self.project_name
                )
                self.ingestor.ensure_node_batch(
                    cs.NodeLabel.PACKAGE,
                    {
                        cs.KEY_QUALIFIED_NAME: package_qn,
                        cs.KEY_NAME: root.name,
                        cs.KEY_PATH: to_posix(relative_root),
                        cs.KEY_REPO_REL_PATH: to_posix(relative_root),
                        cs.KEY_ABS_PATH: root.resolve().as_posix(),
                        cs.KEY_SYMBOL_KIND: cs.NodeLabel.PACKAGE.value.lower(),
                        cs.KEY_PARENT_QN: parent_qn,
                        cs.KEY_NAMESPACE: parent_qn,
                        cs.KEY_PACKAGE: package_qn,
                        cs.KEY_IS_TEST: is_test_path(relative_root),
                    },
                )
                parent_identifier = self._get_parent_identifier(
                    parent_rel_path, parent_container_qn
                )
                self.ingestor.ensure_relationship_batch(
                    parent_identifier,
                    cs.RelationshipType.CONTAINS_PACKAGE,
                    (cs.NodeLabel.PACKAGE, cs.KEY_QUALIFIED_NAME, package_qn),
                )
            elif root != self.repo_path:
                self.structural_elements[relative_root] = None
                logger.info(
                    logs.STRUCT_IDENTIFIED_FOLDER.format(relative_root=relative_root)
                )
                self.ingestor.ensure_node_batch(
                    cs.NodeLabel.FOLDER,
                    {
                        cs.KEY_PATH: to_posix(relative_root),
                        cs.KEY_NAME: root.name,
                        cs.KEY_REPO_REL_PATH: to_posix(relative_root),
                        cs.KEY_ABS_PATH: root.resolve().as_posix(),
                        cs.KEY_SYMBOL_KIND: cs.NodeLabel.FOLDER.value.lower(),
                        cs.KEY_IS_TEST: is_test_path(relative_root),
                    },
                )
                parent_identifier = self._get_parent_identifier(
                    parent_rel_path, parent_container_qn
                )
                self.ingestor.ensure_relationship_batch(
                    parent_identifier,
                    cs.RelationshipType.CONTAINS_FOLDER,
                    (cs.NodeLabel.FOLDER, cs.KEY_PATH, to_posix(relative_root)),
                )

    def process_generic_file(self, file_path: Path, file_name: str) -> None:
        """
        Process a generic file and link it to its parent container.

        Args:
            file_path (Path): Path to the file.
            file_name (str): Name of the file.
        """
        relative_filepath = to_posix(file_path.relative_to(self.repo_path))
        relative_root = file_path.parent.relative_to(self.repo_path)

        parent_container_qn = self.structural_elements.get(relative_root)
        parent_identifier = self._get_parent_identifier(
            relative_root, parent_container_qn
        )
        language_value = None
        if lang_spec := get_language_spec_for_path(file_path):
            if isinstance(lang_spec.language, cs.SupportedLanguage):
                language_value = lang_spec.language.value
            else:
                language_value = str(lang_spec.language)

        file_props = {
            cs.KEY_PATH: relative_filepath,
            cs.KEY_NAME: file_name,
            cs.KEY_EXTENSION: file_path.suffix,
            cs.KEY_REPO_REL_PATH: relative_filepath,
            cs.KEY_ABS_PATH: file_path.resolve().as_posix(),
            cs.KEY_SYMBOL_KIND: cs.NodeLabel.FILE.value.lower(),
            cs.KEY_IS_TEST: is_test_path(file_path.relative_to(self.repo_path)),
            cs.KEY_FILE_HASH: compute_file_hash(file_path),
        }
        if language_value:
            file_props[cs.KEY_LANGUAGE] = language_value

        self.ingestor.ensure_node_batch(
            cs.NodeLabel.FILE,
            file_props,
        )

        self.ingestor.ensure_relationship_batch(
            parent_identifier,
            cs.RelationshipType.CONTAINS_FILE,
            (cs.NodeLabel.FILE, cs.KEY_PATH, relative_filepath),
        )
