import tempfile
import time
from pathlib import Path

import pytest

from codebase_rag.parsers.core.incremental_cache import (
    FileHashCache,
    IncrementalParsingCache,
    ParseResultCache,
)
from codebase_rag.parsers.core.process_manager import (
    ParserJobInfo,
    ParserJobStatus,
    ParserProcessManager,
)


class TestParserProcessManager:
    """Test parser process manager functionality."""

    def test_init_defaults(self):
        """Test initialization with defaults."""
        manager = ParserProcessManager()
        assert manager.num_workers > 0
        assert manager.timeout == 300.0
        assert not manager.running

    def test_init_custom_workers(self):
        """Test initialization with custom worker count."""
        manager = ParserProcessManager(num_workers=2)
        assert manager.num_workers >= 1

    def test_start_workers(self):
        """Test starting worker processes."""
        manager = ParserProcessManager(num_workers=2)
        manager.start()
        assert manager.running
        assert len(manager.worker_processes) == 2
        manager.shutdown()

    def test_submit_single_job(self):
        """Test submitting a single job."""
        manager = ParserProcessManager(num_workers=1)

        def dummy_parser(file_path, language):
            return {"status": "ok", "file": file_path}

        job_id = manager.submit_job("test.py", "python", dummy_parser)
        assert job_id is not None
        assert isinstance(job_id, str)

        manager.shutdown()

    def test_submit_batch_jobs(self):
        """Test submitting multiple jobs."""
        manager = ParserProcessManager(num_workers=2)

        def dummy_parser(file_path, language):
            return {"file": file_path, "language": language}

        jobs = [
            ("file1.py", "python", dummy_parser),
            ("file2.py", "python", dummy_parser),
            ("file3.py", "python", dummy_parser),
        ]

        job_ids = manager.submit_batch(jobs)
        assert len(job_ids) == 3
        assert all(isinstance(jid, str) for jid in job_ids)

        manager.shutdown()

    def test_get_job_status(self):
        """Test getting job status."""
        manager = ParserProcessManager()

        def dummy_parser(file_path, language):
            return {"status": "ok"}

        job_id = manager.submit_job("test.py", "python", dummy_parser)
        status = manager.get_job_status(job_id)

        assert status is not None
        assert isinstance(status, ParserJobInfo)
        assert status.job_id == job_id
        assert status.file_path == "test.py"
        assert status.language == "python"

        manager.shutdown()

    def test_get_progress(self):
        """Test progress tracking."""
        manager = ParserProcessManager()

        def dummy_parser(file_path, language):
            return {"status": "ok"}

        progress = manager.get_progress()
        assert progress["total_jobs"] == 0
        assert progress["completed"] == 0

        manager.submit_job("test1.py", "python", dummy_parser)
        manager.submit_job("test2.py", "python", dummy_parser)

        progress = manager.get_progress()
        assert progress["total_jobs"] == 2

        manager.shutdown()

    def test_job_execution(self):
        """Test actual job execution."""
        manager = ParserProcessManager(num_workers=1)

        def dummy_parser(file_path, language):
            time.sleep(0.1)
            return {"file": file_path, "language": language}

        manager.start()
        manager.submit_job("test.py", "python", dummy_parser)

        result = manager.wait_all(timeout=5.0)
        assert result.total_jobs == 1
        assert result.completed == 1

        manager.shutdown()

    def test_batch_processing(self):
        """Test batch processing of multiple jobs."""
        manager = ParserProcessManager(num_workers=2)

        def dummy_parser(file_path, language):
            return {"file": file_path, "parsed": True}

        jobs = [(f"file{i}.py", "python", dummy_parser) for i in range(5)]

        manager.submit_batch(jobs)
        result = manager.wait_all(timeout=10.0)

        assert result.total_jobs == 5
        assert result.completed > 0
        assert result.throughput > 0

        manager.shutdown()

    def test_error_handling(self):
        """Test error handling in job execution."""
        manager = ParserProcessManager(num_workers=1)

        def failing_parser(file_path, language):
            raise ValueError("Test error")

        manager.start()
        job_id = manager.submit_job("test.py", "python", failing_parser)

        result = manager.wait_all(timeout=5.0)
        assert result.failed >= 1

        job_status = manager.get_job_status(job_id)
        assert job_status is not None
        assert job_status.status == ParserJobStatus.FAILED
        assert job_status.error is not None

        manager.shutdown()

    def test_statistics(self):
        """Test statistics collection."""
        manager = ParserProcessManager(num_workers=1)

        def dummy_parser(file_path, language):
            return {"file": file_path}

        manager.submit_job("test.py", "python", dummy_parser)
        manager.wait_all(timeout=5.0)

        stats = manager.get_statistics()
        assert stats["total_jobs"] >= 1
        assert stats["num_workers"] == 1
        assert "elapsed_time" in stats
        assert "average_execution_time" in stats

        manager.shutdown()

    def test_shutdown_graceful(self):
        """Test graceful shutdown."""
        manager = ParserProcessManager(num_workers=2)
        manager.start()

        assert manager.running
        manager.shutdown(wait=True, timeout=5.0)
        assert not manager.running


class TestFileHashCache:
    """Test file hash cache functionality."""

    def test_init_default(self):
        """Test initialization with default cache dir."""
        cache = FileHashCache()
        assert cache.cache_dir is not None
        assert isinstance(cache.hashes, dict)

    def test_init_custom_dir(self):
        """Test initialization with custom cache dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = FileHashCache(Path(tmpdir))
            assert cache.cache_dir == Path(tmpdir)

    def test_get_file_hash(self, tmp_path):
        """Test file hash calculation."""
        cache = FileHashCache(tmp_path)

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        hash1 = cache.get_file_hash(test_file)
        assert hash1 is not None
        assert len(hash1) == 64
        assert isinstance(hash1, str)

    def test_has_changed_new_file(self, tmp_path):
        """Test change detection for new file."""
        cache = FileHashCache(tmp_path)

        test_file = tmp_path / "new.txt"
        test_file.write_text("content")

        assert cache.has_changed(test_file)

    def test_has_changed_modified_file(self, tmp_path):
        """Test change detection for modified file."""
        cache = FileHashCache(tmp_path)

        test_file = tmp_path / "test.txt"
        test_file.write_text("original")
        cache.update_hash(test_file)

        test_file.write_text("modified")

        assert cache.has_changed(test_file)

    def test_has_not_changed_unchanged_file(self, tmp_path):
        """Test no change detection for unchanged file."""
        cache = FileHashCache(tmp_path)

        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        cache.update_hash(test_file)

        assert not cache.has_changed(test_file)

    def test_update_hash(self, tmp_path):
        """Test hash update."""
        cache = FileHashCache(tmp_path)

        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        cache.update_hash(test_file)
        assert str(test_file.resolve()) in cache.hashes

    def test_clear(self, tmp_path):
        """Test cache clearing."""
        cache = FileHashCache(tmp_path)

        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        cache.update_hash(test_file)

        assert len(cache.hashes) > 0
        cache.clear()
        assert len(cache.hashes) == 0


class TestParseResultCache:
    """Test parse result caching functionality."""

    def test_init_custom_dir(self):
        """Test initialization with custom cache dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ParseResultCache(Path(tmpdir))
            assert cache.cache_dir is not None

    def test_put_and_get(self, tmp_path):
        """Test caching and retrieving parse results."""
        cache = ParseResultCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        result = {"type": "function", "name": "foo"}
        cache.put(test_file, result, language="python")

        cached = cache.get(test_file)
        assert cached == result

    def test_cache_miss_on_change(self, tmp_path):
        """Test cache miss when file changes."""
        cache = ParseResultCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        result = {"type": "function", "name": "foo"}
        cache.put(test_file, result)

        test_file.write_text("def bar(): pass")

        cached = cache.get(test_file)
        assert cached is None

    def test_invalidate_cache(self, tmp_path):
        """Test explicit cache invalidation."""
        cache = ParseResultCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        result = {"type": "function", "name": "foo"}
        cache.put(test_file, result)

        assert cache.get(test_file) is not None

        cache.invalidate(test_file)

        assert cache.get(test_file) is None

    def test_cache_stats(self, tmp_path):
        """Test cache statistics."""
        cache = ParseResultCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        cache.put(test_file, {"name": "foo"}, language="python")

        stats = cache.get_cache_stats()
        assert stats["cached_files"] >= 1
        assert stats["total_size_bytes"] > 0

    def test_clear_all(self, tmp_path):
        """Test clearing all cached results."""
        cache = ParseResultCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        cache.put(test_file, {"name": "foo"})
        cache.clear()

        stats = cache.get_cache_stats()
        assert stats["cached_files"] == 0


class TestIncrementalParsingCache:
    """Test incremental parsing cache functionality."""

    def test_init(self):
        """Test initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = IncrementalParsingCache(Path(tmpdir))
            assert cache.hash_cache is not None
            assert cache.result_cache is not None

    def test_needs_parsing_new_file(self, tmp_path):
        """Test parsing needed detection for new file."""
        cache = IncrementalParsingCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        assert cache.needs_parsing(test_file)

    def test_needs_parsing_unchanged_file(self, tmp_path):
        """Test parsing not needed for unchanged cached file."""
        cache = IncrementalParsingCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        result = {"name": "foo"}
        cache.cache_result(test_file, result)

        assert not cache.needs_parsing(test_file)

    def test_needs_parsing_changed_file(self, tmp_path):
        """Test parsing needed when file changes."""
        cache = IncrementalParsingCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        result = {"name": "foo"}
        cache.cache_result(test_file, result)

        test_file.write_text("def bar(): pass")

        assert cache.needs_parsing(test_file)

    def test_get_result_hit(self, tmp_path):
        """Test getting cached result."""
        cache = IncrementalParsingCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        result = {"name": "foo", "type": "function"}
        cache.cache_result(test_file, result)

        cached = cache.get_result(test_file)
        assert cached == result

    def test_get_result_miss(self, tmp_path):
        """Test cache miss when file changed."""
        cache = IncrementalParsingCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        result = {"name": "foo"}
        cache.cache_result(test_file, result)

        test_file.write_text("def bar(): pass")

        assert cache.get_result(test_file) is None

    def test_cache_result(self, tmp_path):
        """Test caching parse result."""
        cache = IncrementalParsingCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        result = {"name": "foo", "language": "python"}
        cache.cache_result(test_file, result, language="python")

        assert cache.get_result(test_file) is not None

    def test_invalidate(self, tmp_path):
        """Test cache invalidation."""
        cache = IncrementalParsingCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        cache.cache_result(test_file, {"name": "foo"})
        cache.invalidate(test_file)

        assert cache.get_result(test_file) is None

    def test_clear_all(self, tmp_path):
        """Test clearing all cache."""
        cache = IncrementalParsingCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        cache.cache_result(test_file, {"name": "foo"})
        cache.clear_all()

        stats = cache.get_statistics()
        assert stats["cached_files"] == 0

    def test_statistics(self, tmp_path):
        """Test statistics collection."""
        cache = IncrementalParsingCache(tmp_path)

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        cache.cache_result(test_file, {"name": "foo"})

        stats = cache.get_statistics()
        assert "cached_files" in stats
        assert "total_size_bytes" in stats
        assert "hash_cache_entries" in stats


class TestPhase4Integration:
    """Test Phase 4 integration and backward compatibility."""

    def test_process_manager_with_cache(self, tmp_path):
        """Test process manager with caching."""
        cache = IncrementalParsingCache(tmp_path)
        manager = ParserProcessManager(num_workers=1)

        def cached_parser(file_path, language):
            file_path = Path(file_path)

            cached_result = cache.get_result(file_path)
            if cached_result:
                return cached_result

            result = {"file": str(file_path), "parsed": True}

            cache.cache_result(file_path, result, language=language)
            return result

        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        manager.start()
        manager.submit_job(str(test_file), "python", cached_parser)
        manager.wait_all(timeout=5.0)
        manager.shutdown()

        assert not cache.needs_parsing(test_file)

    def test_backward_compatibility(self):
        """Test that Phase 4 doesn't break Phase 1-3."""
        from codebase_rag.parsers.frameworks.detectors import PythonFrameworkDetector
        from codebase_rag.parsers.languages.ruby import RubyParserMixin
        from codebase_rag.parsers.query.query_engine import QueryEngine

        assert QueryEngine is not None
        assert PythonFrameworkDetector is not None
        assert RubyParserMixin is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
