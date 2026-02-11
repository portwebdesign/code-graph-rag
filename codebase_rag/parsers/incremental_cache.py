import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileHashCache:
    """
    Cache file content hashes for change detection.

    Uses SHA256 hashing to detect file modifications since last parse.
    Thread-safe implementation using locks.
    """

    def __init__(self, cache_dir: Path | None = None):
        """
        Initialize file hash cache.

        Args:
            cache_dir: Directory to store hash cache (default: ~/.cache/codebase_rag)
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "codebase_rag"

        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "file_hashes.json"
        self.hashes: dict[str, str] = self._load_hashes()
        self._lock = threading.Lock()

    def get_file_hash(self, file_path: Path) -> str:
        """
        Calculate SHA256 hash of file content.

        Args:
            file_path: Path to file

        Returns:
            SHA256 hash as hex string, or empty string if file read fails.
        """
        file_path = Path(file_path)

        try:
            with open(file_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError as e:
            logger.error(f"Failed to read file {file_path}: {e}")
            return ""

    def has_changed(self, file_path: Path) -> bool:
        """
        Check if file has changed since last cache update.

        Args:
            file_path: Path to file

        Returns:
            True if file is new or has changed from cached version.
        """
        file_path = Path(file_path)
        current_hash = self.get_file_hash(file_path)
        previous_hash = self.hashes.get(str(file_path.resolve()))

        changed = current_hash != previous_hash
        logger.debug(
            f"File change check: {file_path} -> {'changed' if changed else 'unchanged'}"
        )

        return changed

    def update_hash(self, file_path: Path):
        """
        Update cached hash for file to current content.

        Args:
            file_path: Path to file
        """
        file_path = Path(file_path)
        file_hash = self.get_file_hash(file_path)

        with self._lock:
            self.hashes[str(file_path.resolve())] = file_hash
            self._save_hashes()

        logger.debug(f"Hash updated for {file_path}")

    def get_hash(self, file_path: Path) -> str | None:
        """
        Get currently cached hash for file.

        Args:
            file_path: Path to file

        Returns:
            Cached hash or None if not in cache
        """
        return self.hashes.get(str(Path(file_path).resolve()))

    def clear(self):
        """Clear all cached hashes."""
        with self._lock:
            self.hashes.clear()
            self._save_hashes()
        logger.info("File hash cache cleared")

    def _load_hashes(self) -> dict[str, str]:
        """Load cached hashes from disk."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    hashes = json.load(f)
                logger.debug(f"Loaded {len(hashes)} cached file hashes")
                return hashes
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load hash cache: {e}")
                return {}
        return {}

    def _save_hashes(self):
        """Save cached hashes to disk."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.hashes, f, indent=2)
        except OSError as e:
            logger.error(f"Failed to save hash cache: {e}")


class ParseResultCache:
    """
    Cache parse results for unchanged files.

    Integrates with FileHashCache to detect file changes and automatically
    invalidate stale results. Supports TTL (Time To Live) expiration.
    """

    def __init__(self, cache_dir: Path | None = None, ttl_seconds: float | None = None):
        """
        Initialize parse result cache.

        Args:
            cache_dir: Directory to store cache (default: ~/.cache/codebase_rag)
            ttl_seconds: Optional expiration time in seconds for cached items.
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "codebase_rag"

        self.cache_dir = Path(cache_dir) / "parse_results"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hash_cache = FileHashCache(cache_dir)
        self.metadata_file = self.cache_dir / "metadata.json"
        self.metadata: dict[str, dict[str, Any]] = self._load_metadata()
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds
        self._hits = 0
        self._misses = 0
        self._expirations = 0

    def get(self, file_path: Path) -> dict[str, Any] | None:
        """
        Get cached parse result if file unchanged and cache valid.

        Args:
            file_path: Path to file

        Returns:
            Cached parse result or None if file changed, cache expired, or not found.
        """
        file_path = Path(file_path)

        if self.hash_cache.has_changed(file_path):
            logger.debug(f"File changed, invalidating cache: {file_path}")
            self._misses += 1
            return None

        if self._is_expired(file_path):
            self._misses += 1
            return None

        cache_file = self._get_cache_file(file_path)
        if not cache_file.exists():
            self._misses += 1
            return None

        try:
            with open(cache_file, encoding="utf-8") as f:
                result = json.load(f)
            logger.debug(f"Cache hit for {file_path}")
            self._hits += 1
            return result
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load cached result for {file_path}: {e}")
            self._misses += 1
            return None

    def put(
        self,
        file_path: Path,
        result: dict[str, Any],
        language: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """
        Cache parse result for a file.

        Args:
            file_path: Path to file
            result: Parse result to cache
            language: Programming language (optional)
            metadata: Additional metadata to store (optional)
        """
        file_path = Path(file_path)
        cache_file = self._get_cache_file(file_path)

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)

            self.hash_cache.update_hash(file_path)

            with self._lock:
                entry = {
                    "cached_at": datetime.now().isoformat(),
                    "language": language,
                    "result_size": len(json.dumps(result)),
                }
                if metadata:
                    entry.update(metadata)
                self.metadata[str(file_path.resolve())] = entry
                self._save_metadata()

            logger.debug(f"Cached parse result for {file_path}")

        except OSError as e:
            logger.error(f"Failed to cache result for {file_path}: {e}")

    def invalidate(self, file_path: Path):
        """
        Explicitly invalidate cache for a specific file.

        Args:
            file_path: Path to file
        """
        cache_file = self._get_cache_file(file_path)
        if cache_file.exists():
            try:
                cache_file.unlink()
                with self._lock:
                    file_key = str(Path(file_path).resolve())
                    if file_key in self.metadata:
                        del self.metadata[file_key]
                    self._save_metadata()
                logger.debug(f"Cache invalidated for {file_path}")
            except OSError as e:
                logger.error(f"Failed to invalidate cache for {file_path}: {e}")

    def _is_expired(self, file_path: Path) -> bool:
        """Check if cache entry has expired based on TTL."""
        if self.ttl_seconds is None:
            return False

        file_key = str(Path(file_path).resolve())
        metadata = self.metadata.get(file_key)
        if not metadata:
            return False

        cached_at = metadata.get("cached_at")
        if not cached_at:
            return False

        try:
            cached_time = datetime.fromisoformat(cached_at)
        except ValueError:
            return False

        age_seconds = (datetime.now() - cached_time).total_seconds()
        if age_seconds > self.ttl_seconds:
            self._expirations += 1
            self.invalidate(file_path)
            return True

        return False

    def get_metadata(self, file_path: Path) -> dict[str, Any] | None:
        """
        Get metadata associated with cached file.

        Args:
            file_path: Path to file

        Returns:
            Metadata dictionary or None.
        """
        file_key = str(Path(file_path).resolve())
        return self.metadata.get(file_key)

    def clear(self):
        """Clear all cached results and metadata."""
        try:
            if self.cache_dir.exists():
                import shutil

                shutil.rmtree(self.cache_dir)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                self.metadata.clear()
            logger.info("Parse result cache cleared")
        except OSError as e:
            logger.error(f"Failed to clear cache: {e}")

    def get_cache_stats(self) -> dict[str, Any]:
        """
        Get detailed cache statistics.

        Returns:
            Dictionary with cache statistics (hits, misses, size, etc.)
        """
        cached_count = len(self.metadata)
        total_size = sum(m.get("result_size", 0) for m in self.metadata.values())

        return {
            "cached_files": cached_count,
            "total_size_bytes": total_size,
            "cache_dir": str(self.cache_dir),
            "hits": self._hits,
            "misses": self._misses,
            "expirations": self._expirations,
        }

    def _get_cache_file(self, file_path: Path) -> Path:
        """Get cache file path for given source file."""
        file_path = Path(file_path).resolve()
        safe_name = str(file_path).replace("/", "_").replace("\\", "_").replace(":", "")
        return self.cache_dir / f"{safe_name}.json"

    def _load_metadata(self) -> dict[str, dict[str, Any]]:
        """Load cache metadata from disk."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load cache metadata: {e}")
                return {}
        return {}

    def _save_metadata(self):
        """Save cache metadata to disk."""
        try:
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, indent=2)
        except OSError as e:
            logger.error(f"Failed to save cache metadata: {e}")


class IncrementalParsingCache:
    """
    Combined incremental parsing cache with integrated file tracking and result caching.

    Manages both file hash tracking and parse result caching with automatic invalidation.
    Acts as a facade for FileHashCache and ParseResultCache.
    """

    def __init__(self, cache_dir: Path | None = None, ttl_seconds: float | None = None):
        """
        Initialize incremental parsing cache.

        Args:
            cache_dir: Directory to store cache
            ttl_seconds: Optional TTL for cache validity
        """
        self.hash_cache = FileHashCache(cache_dir)
        self.result_cache = ParseResultCache(cache_dir, ttl_seconds=ttl_seconds)

    def needs_parsing(self, file_path: Path) -> bool:
        """
        Check if file needs parsing.

        Args:
            file_path: Path to file

        Returns:
            True if file has changed or result not cached
        """
        return self.hash_cache.has_changed(file_path)

    def get_result(self, file_path: Path) -> dict[str, Any] | None:
        """
        Get cached parse result if available and file unchanged.

        Args:
            file_path: Path to file

        Returns:
            Cached result or None if re-parsing is needed.
        """
        if self.needs_parsing(file_path):
            return None
        return self.result_cache.get(file_path)

    def cache_result(
        self,
        file_path: Path,
        result: dict[str, Any],
        language: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """
        Cache parse result for a file.

        Args:
            file_path: Path to file
            result: Parse result
            language: Programming language
            metadata: Optional metadata
        """
        self.result_cache.put(file_path, result, language, metadata)

    def invalidate(self, file_path: Path):
        """
        Invalidate cache for specific file.

        Args:
            file_path: Path to file
        """
        self.result_cache.invalidate(file_path)

    def clear_all(self):
        """Clear all cached data (hashes and results)."""
        self.hash_cache.clear()
        self.result_cache.clear()

    def get_statistics(self) -> dict[str, Any]:
        """
        Get comprehensive cache statistics.

        Returns:
            Dictionary with cache statistics (hashes, hits, misses, etc.)
        """
        stats = self.result_cache.get_cache_stats()
        stats["hash_cache_entries"] = len(self.hash_cache.hashes)
        return stats

    def get_cached_structure_signature(self, file_path: Path) -> str | None:
        """
        Get the structure signature from cached metadata.

        Args:
            file_path: Path to file

        Returns:
            Structure signature string or None.
        """
        metadata = self.result_cache.get_metadata(file_path)
        if not metadata:
            return None
        signature = metadata.get("structure_signature")
        return signature if isinstance(signature, str) else None


class GitDeltaCache:
    """
    Cache last processed git revision for incremental analysis.
    """

    def __init__(self, cache_dir: Path | None = None):
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "codebase_rag"

        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "git_delta.json"
        self._data = self._load()

    def get_last_head(self, repo_path: Path) -> str | None:
        """Get last processed HEAD commit hash for repo."""
        key = str(Path(repo_path).resolve())
        return self._data.get(key)

    def set_last_head(self, repo_path: Path, head: str) -> None:
        """Set last processed HEAD commit hash."""
        key = str(Path(repo_path).resolve())
        self._data[key] = head
        self._save()

    def _load(self) -> dict[str, str]:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save(self) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError as e:
            logger.error(f"Failed to save git delta cache: {e}")
