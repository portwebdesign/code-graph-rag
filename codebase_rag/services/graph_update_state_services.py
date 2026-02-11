from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.data_models.types_defs import (
    FunctionRegistry,
    SimpleNameLookup,
)


class GraphStateService:
    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        ast_cache: MutableMapping[Path, tuple[object, cs.SupportedLanguage]],
        function_registry: FunctionRegistry | None,
        simple_name_lookup: SimpleNameLookup,
    ) -> None:
        self.repo_path = repo_path
        self.project_name = project_name
        self.ast_cache = ast_cache
        self.function_registry = function_registry
        self.simple_name_lookup = simple_name_lookup

    def remove_file_from_state(self, file_path: Path) -> None:
        logger.debug(ls.REMOVING_STATE.format(path=file_path))

        if file_path in self.ast_cache:
            del self.ast_cache[file_path]
            logger.debug(ls.REMOVED_FROM_CACHE)

        if self.function_registry is None:
            return

        relative_path = file_path.relative_to(self.repo_path)
        is_init_file = file_path.name == cs.INIT_PY
        path_parts = (
            relative_path.parent.parts
            if is_init_file
            else relative_path.with_suffix("").parts
        )
        module_qn_prefix = cs.SEPARATOR_DOT.join([self.project_name, *path_parts])

        package_dir = file_path.parent if is_init_file else None

        def _is_submodule(segment: str) -> bool:
            if not package_dir:
                return False
            return (package_dir / f"{segment}{cs.EXT_PY}").is_file() or (
                package_dir / segment
            ).is_dir()

        qns_to_remove = set()

        for qn in list(self.function_registry.keys()):
            if qn == module_qn_prefix:
                qns_to_remove.add(qn)
                del self.function_registry[qn]
                continue
            if qn.startswith(f"{module_qn_prefix}."):
                if is_init_file:
                    remainder = qn[len(module_qn_prefix) + 1 :]
                    head = remainder.split(cs.SEPARATOR_DOT, 1)[0]
                    if _is_submodule(head):
                        continue
                qns_to_remove.add(qn)
                del self.function_registry[qn]

        if qns_to_remove:
            logger.debug(ls.REMOVING_QNS.format(count=len(qns_to_remove)))

        for simple_name, qn_set in self.simple_name_lookup.items():
            original_count = len(qn_set)
            new_qn_set = qn_set - qns_to_remove
            if len(new_qn_set) < original_count:
                self.simple_name_lookup[simple_name] = new_qn_set
                logger.debug(ls.CLEANED_SIMPLE_NAME.format(name=simple_name))
