from __future__ import annotations

import sys
import time
from collections import OrderedDict
from collections.abc import Callable, ItemsView, KeysView
from pathlib import Path

from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.core.config import settings
from codebase_rag.data_models.types_defs import (
    FunctionRegistry,
    NodeType,
    QualifiedName,
    SimpleNameLookup,
    TrieNode,
)


class FunctionRegistryTrie:
    def __init__(self, simple_name_lookup: SimpleNameLookup | None = None) -> None:
        self.root: TrieNode = {}
        self._entries: FunctionRegistry = {}
        self._simple_name_lookup = simple_name_lookup

    def insert(self, qualified_name: QualifiedName, func_type: NodeType) -> None:
        self._entries[qualified_name] = func_type

        parts = qualified_name.split(cs.SEPARATOR_DOT)
        current: TrieNode = self.root

        for part in parts:
            if part not in current:
                current[part] = {}
            child = current[part]
            assert isinstance(child, dict)
            current = child

        current[cs.TRIE_TYPE_KEY] = func_type
        current[cs.TRIE_QN_KEY] = qualified_name

    def get(
        self, qualified_name: QualifiedName, default: NodeType | None = None
    ) -> NodeType | None:
        return self._entries.get(qualified_name, default)

    def __contains__(self, qualified_name: QualifiedName) -> bool:
        return qualified_name in self._entries

    def __getitem__(self, qualified_name: QualifiedName) -> NodeType:
        return self._entries[qualified_name]

    def __setitem__(self, qualified_name: QualifiedName, func_type: NodeType) -> None:
        self.insert(qualified_name, func_type)

    def __delitem__(self, qualified_name: QualifiedName) -> None:
        if qualified_name not in self._entries:
            return

        del self._entries[qualified_name]

        parts = qualified_name.split(cs.SEPARATOR_DOT)
        self._cleanup_trie_path(parts, self.root)

    def _cleanup_trie_path(self, parts: list[str], node: TrieNode) -> bool:
        if not parts:
            node.pop(cs.TRIE_QN_KEY, None)
            node.pop(cs.TRIE_TYPE_KEY, None)
            return not node

        part = parts[0]
        if part not in node:
            return False

        child = node[part]
        assert isinstance(child, dict)
        if self._cleanup_trie_path(parts[1:], child):
            del node[part]

        is_endpoint = cs.TRIE_QN_KEY in node
        has_children = any(not key.startswith(cs.TRIE_INTERNAL_PREFIX) for key in node)
        return not has_children and not is_endpoint

    def _navigate_to_prefix(self, prefix: str) -> TrieNode | None:
        parts = prefix.split(cs.SEPARATOR_DOT) if prefix else []
        current: TrieNode = self.root
        for part in parts:
            if part not in current:
                return None
            child = current[part]
            assert isinstance(child, dict)
            current = child
        return current

    def _collect_from_subtree(
        self,
        node: TrieNode,
        filter_fn: Callable[[QualifiedName], bool] | None = None,
    ) -> list[tuple[QualifiedName, NodeType]]:
        results: list[tuple[QualifiedName, NodeType]] = []

        def dfs(n: TrieNode) -> None:
            if cs.TRIE_QN_KEY in n:
                qn = n[cs.TRIE_QN_KEY]
                func_type = n[cs.TRIE_TYPE_KEY]
                assert isinstance(qn, str) and isinstance(func_type, NodeType)
                if filter_fn is None or filter_fn(qn):
                    results.append((qn, func_type))

            for key, child in n.items():
                if not key.startswith(cs.TRIE_INTERNAL_PREFIX):
                    assert isinstance(child, dict)
                    dfs(child)

        dfs(node)
        return results

    def keys(self) -> KeysView[QualifiedName]:
        return self._entries.keys()

    def items(self) -> ItemsView[QualifiedName, NodeType]:
        return self._entries.items()

    def __len__(self) -> int:
        return len(self._entries)

    def find_with_prefix_and_suffix(
        self, prefix: str, suffix: str
    ) -> list[QualifiedName]:
        node = self._navigate_to_prefix(prefix)
        if node is None:
            return []
        suffix_pattern = f".{suffix}"
        matches = self._collect_from_subtree(
            node, lambda qn: qn.endswith(suffix_pattern)
        )
        return [qn for qn, _ in matches]

    def find_ending_with(self, suffix: str) -> list[QualifiedName]:
        if self._simple_name_lookup is not None and suffix in self._simple_name_lookup:
            return list(self._simple_name_lookup[suffix])
        return [qn for qn in self._entries.keys() if qn.endswith(f".{suffix}")]

    def find_with_prefix(self, prefix: str) -> list[tuple[QualifiedName, NodeType]]:
        node = self._navigate_to_prefix(prefix)
        return [] if node is None else self._collect_from_subtree(node)


class BoundedASTCache:
    def __init__(
        self,
        max_entries: int | None = None,
        max_memory_mb: int | None = None,
        ttl_seconds: float | None = None,
    ):
        self.cache: OrderedDict[Path, tuple[Node, cs.SupportedLanguage]] = OrderedDict()
        self.max_entries = (
            max_entries if max_entries is not None else settings.CACHE_MAX_ENTRIES
        )
        max_mem = (
            max_memory_mb if max_memory_mb is not None else settings.CACHE_MAX_MEMORY_MB
        )
        self.max_memory_bytes = max_mem * cs.BYTES_PER_MB
        self.ttl_seconds = ttl_seconds
        self._timestamps: dict[Path, float] = {}
        self._hits = 0
        self._misses = 0
        self._expirations = 0

    def __setitem__(self, key: Path, value: tuple[Node, cs.SupportedLanguage]) -> None:
        if key in self.cache:
            del self.cache[key]
            self._timestamps.pop(key, None)

        self.cache[key] = value
        self._timestamps[key] = time.time()

        self._enforce_limits()

    def __getitem__(self, key: Path) -> tuple[Node, cs.SupportedLanguage]:
        if self._is_expired(key):
            self._misses += 1
            raise KeyError(key)
        value = self.cache[key]
        self.cache.move_to_end(key)
        self._hits += 1
        return value

    def __delitem__(self, key: Path) -> None:
        if key in self.cache:
            del self.cache[key]
            self._timestamps.pop(key, None)

    def __contains__(self, key: Path) -> bool:
        if key not in self.cache:
            return False
        if self._is_expired(key):
            return False
        return True

    def items(self) -> ItemsView[Path, tuple[Node, cs.SupportedLanguage]]:
        self._evict_expired()
        return self.cache.items()

    def enforce_limits(self) -> None:
        self._enforce_limits()

    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "expirations": self._expirations,
            "size": len(self.cache),
        }

    def _enforce_limits(self) -> None:
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)

        if self._should_evict_for_memory():
            entries_to_remove = max(
                1, len(self.cache) // settings.CACHE_EVICTION_DIVISOR
            )
            for _ in range(entries_to_remove):
                if self.cache:
                    self.cache.popitem(last=False)

        self._evict_expired()

    def _evict_expired(self) -> None:
        if self.ttl_seconds is None:
            return
        expired_keys = [key for key in list(self.cache.keys()) if self._is_expired(key)]
        for key in expired_keys:
            if key in self.cache:
                del self.cache[key]
            self._timestamps.pop(key, None)
            self._expirations += 1

    def _is_expired(self, key: Path) -> bool:
        if self.ttl_seconds is None:
            return False
        timestamp = self._timestamps.get(key)
        if not timestamp:
            return False
        return (time.time() - timestamp) > self.ttl_seconds

    def _should_evict_for_memory(self) -> bool:
        try:
            cache_size = sum(sys.getsizeof(v) for v in self.cache.values())
            return cache_size > self.max_memory_bytes
        except Exception:
            return (
                len(self.cache)
                > self.max_entries * settings.CACHE_MEMORY_THRESHOLD_RATIO
            )
