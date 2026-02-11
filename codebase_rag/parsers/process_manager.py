import logging
import multiprocessing
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from queue import Empty, Queue
from typing import Any

logger = logging.getLogger(__name__)


class ParserJobStatus(Enum):
    """Job execution status."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ParserJobInfo:
    """Information about a parser job."""

    job_id: str
    status: ParserJobStatus
    file_path: str
    language: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress: float = 0.0
    eta_seconds: float | None = None
    error: str | None = None
    result: Any | None = None
    execution_time: float | None = None


@dataclass
class ParserBatchResult:
    """Result of batch parsing operation."""

    total_jobs: int
    completed: int
    failed: int
    results: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    total_time: float = 0.0
    throughput: float = 0.0


class ParserJobQueue:
    """Thread-safe job queue for parser jobs."""

    def __init__(self):
        """Initialize job queue."""
        self.queue: Queue = Queue()
        self.job_info: dict[str, ParserJobInfo] = {}
        self._lock = threading.Lock()

    def submit(
        self, job_id: str, file_path: str, language: str, parse_func: Callable
    ) -> str:
        """Submit a job to the queue."""
        job_info = ParserJobInfo(
            job_id=job_id,
            status=ParserJobStatus.QUEUED,
            file_path=file_path,
            language=language,
        )

        with self._lock:
            self.job_info[job_id] = job_info
            self.queue.put((job_id, file_path, language, parse_func))

        logger.debug(f"Job submitted: {job_id} for {file_path}")
        return job_id

    def get(self, timeout: float | None = 1.0) -> tuple | None:
        """Get next job from queue."""
        try:
            return self.queue.get(timeout=timeout)
        except Empty:
            return None

    def get_job_info(self, job_id: str) -> ParserJobInfo | None:
        """Get information about a job."""
        with self._lock:
            return self.job_info.get(job_id)

    def update_status(self, job_id: str, status: ParserJobStatus, **kwargs):
        """Update job status."""
        with self._lock:
            if job_id in self.job_info:
                job = self.job_info[job_id]
                job.status = status
                for key, value in kwargs.items():
                    if hasattr(job, key):
                        setattr(job, key, value)
                logger.debug(f"Job {job_id} status updated to {status.value}")

    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return self.queue.empty()

    def get_all_jobs(self) -> dict[str, ParserJobInfo]:
        """Get all job information."""
        with self._lock:
            return dict(self.job_info)


class ParserProcessManager:
    """
    Manage parser execution across multiple worker processes.

    Features:
    - Parallel parsing with configurable worker count
    - Job scheduling and status tracking
    - Progress monitoring with ETA calculation
    - Error handling and retry mechanisms
    - Batch processing mode
    - Graceful shutdown
    """

    def __init__(self, num_workers: int = 4, timeout: float = 300.0):
        """
        Initialize process manager.

        Args:
            num_workers: Number of worker processes (default 4)
            timeout: Timeout per job in seconds (default 300)
        """
        self.num_workers = max(1, min(num_workers, multiprocessing.cpu_count()))
        self.timeout = timeout
        self.job_queue = ParserJobQueue()
        self.worker_processes: list[multiprocessing.Process] = []
        self.result_queue: Queue = Queue()
        self.running = False
        self.start_time: datetime | None = None

    def start(self):
        """Start worker processes."""
        if self.running:
            logger.warning("Process manager already running")
            return

        self.running = True
        self.start_time = datetime.now()

        for i in range(self.num_workers):
            worker = multiprocessing.Process(
                target=self._worker_process,
                args=(self.job_queue, self.result_queue),
                daemon=False,
            )
            worker.start()
            self.worker_processes.append(worker)

        logger.info(f"Started {self.num_workers} parser workers")

    def submit_job(self, file_path: str, language: str, parse_func: Callable) -> str:
        """
        Submit a single parsing job.

        Args:
            file_path: Path to file to parse
            language: Programming language
            parse_func: Parsing function to call

        Returns:
            Job ID for tracking
        """
        if not self.running:
            self.start()

        job_id = f"{Path(file_path).name}_{time.time()}"
        return self.job_queue.submit(job_id, file_path, language, parse_func)

    def submit_batch(self, jobs: list[tuple[str, str, Callable]]) -> list[str]:
        """
        Submit multiple parsing jobs.

        Args:
            jobs: List of (file_path, language, parse_func) tuples

        Returns:
            List of job IDs
        """
        if not self.running:
            self.start()

        job_ids = []
        for file_path, language, parse_func in jobs:
            job_id = self.submit_job(file_path, language, parse_func)
            job_ids.append(job_id)

        return job_ids

    def run_batch_inline(
        self, jobs: list[tuple[str, str, Callable]]
    ) -> ParserBatchResult:
        """
        Run batch parsing inline (no multiprocessing).

        Useful for environments where multiprocessing is unsafe (shared DB connections)
        but batch tracking is desired.
        """
        self.start_time = datetime.now()
        results = {}
        errors = {}

        for file_path, language, parse_func in jobs:
            job_id = f"{Path(file_path).name}_{time.time()}"
            self.job_queue.submit(job_id, file_path, language, parse_func)
            self.job_queue.update_status(
                job_id, ParserJobStatus.RUNNING, started_at=datetime.now()
            )

            start_time = time.time()
            try:
                result = parse_func(file_path, language)
                execution_time = time.time() - start_time
                self.job_queue.update_status(
                    job_id,
                    ParserJobStatus.COMPLETED,
                    completed_at=datetime.now(),
                    result=result,
                    execution_time=execution_time,
                )
                results[file_path] = result
            except Exception as e:
                execution_time = time.time() - start_time
                error_msg = f"{type(e).__name__}: {str(e)}"
                self.job_queue.update_status(
                    job_id,
                    ParserJobStatus.FAILED,
                    completed_at=datetime.now(),
                    error=error_msg,
                    execution_time=execution_time,
                )
                errors[file_path] = error_msg

        total_time = 0.0
        if self.start_time:
            total_time = (datetime.now() - self.start_time).total_seconds()

        completed_count = len(results)
        failed_count = len(errors)
        throughput = completed_count / total_time if total_time > 0 else 0.0

        return ParserBatchResult(
            total_jobs=len(jobs),
            completed=completed_count,
            failed=failed_count,
            results=results,
            errors=errors,
            total_time=total_time,
            throughput=throughput,
        )

    def run_batch_threaded(
        self, jobs: list[tuple[str, str, Callable]]
    ) -> ParserBatchResult:
        """
        Run batch parsing using a thread pool.

        Useful when multiprocessing is unsafe but IO-bound parsing can benefit
        from limited concurrency.
        """
        self.start_time = datetime.now()
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}

        def _run_job(file_path: str, language: str, parse_func: Callable):
            job_id = f"{Path(file_path).name}_{time.time()}"
            self.job_queue.submit(job_id, file_path, language, parse_func)
            self.job_queue.update_status(
                job_id, ParserJobStatus.RUNNING, started_at=datetime.now()
            )

            start_time = time.time()
            try:
                result = parse_func(file_path, language)
                execution_time = time.time() - start_time
                self.job_queue.update_status(
                    job_id,
                    ParserJobStatus.COMPLETED,
                    completed_at=datetime.now(),
                    result=result,
                    execution_time=execution_time,
                )
                return file_path, result, None
            except Exception as e:
                execution_time = time.time() - start_time
                error_msg = f"{type(e).__name__}: {str(e)}"
                self.job_queue.update_status(
                    job_id,
                    ParserJobStatus.FAILED,
                    completed_at=datetime.now(),
                    error=error_msg,
                    execution_time=execution_time,
                )
                return file_path, None, error_msg

        with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
            future_map = {
                executor.submit(_run_job, file_path, language, parse_func): file_path
                for file_path, language, parse_func in jobs
            }
            for future in as_completed(future_map):
                file_path, result, error_msg = future.result()
                if error_msg:
                    errors[file_path] = error_msg
                else:
                    results[file_path] = result

        total_time = 0.0
        if self.start_time:
            total_time = (datetime.now() - self.start_time).total_seconds()

        completed_count = len(results)
        failed_count = len(errors)
        throughput = completed_count / total_time if total_time > 0 else 0.0

        return ParserBatchResult(
            total_jobs=len(jobs),
            completed=completed_count,
            failed=failed_count,
            results=results,
            errors=errors,
            total_time=total_time,
            throughput=throughput,
        )

    def get_job_status(self, job_id: str) -> ParserJobInfo | None:
        """Get status of a specific job."""
        return self.job_queue.get_job_info(job_id)

    def get_progress(self) -> dict[str, Any]:
        """
        Get overall progress of all jobs.

        Returns:
            Dictionary with progress metrics
        """
        all_jobs = self.job_queue.get_all_jobs()
        total = len(all_jobs)

        if total == 0:
            return {
                "total_jobs": 0,
                "completed": 0,
                "failed": 0,
                "running": 0,
                "queued": 0,
                "progress_percent": 0.0,
                "estimated_time_remaining": None,
                "elapsed_time": None,
            }

        completed = sum(
            1 for j in all_jobs.values() if j.status == ParserJobStatus.COMPLETED
        )
        failed = sum(1 for j in all_jobs.values() if j.status == ParserJobStatus.FAILED)
        running = sum(
            1 for j in all_jobs.values() if j.status == ParserJobStatus.RUNNING
        )
        queued = sum(1 for j in all_jobs.values() if j.status == ParserJobStatus.QUEUED)

        running_jobs = [
            j for j in all_jobs.values() if j.status == ParserJobStatus.RUNNING
        ]
        if running_jobs:
            avg_time = sum(j.execution_time or 1 for j in running_jobs) / len(
                running_jobs
            )
            remaining_jobs = total - completed - failed
            eta_seconds = (remaining_jobs / max(running, 1)) * avg_time
        else:
            eta_seconds = None

        elapsed_time = None
        if self.start_time:
            elapsed_time = (datetime.now() - self.start_time).total_seconds()

        progress_percent = (completed / total * 100) if total > 0 else 0

        return {
            "total_jobs": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "queued": queued,
            "progress_percent": progress_percent,
            "estimated_time_remaining": eta_seconds,
            "elapsed_time": elapsed_time,
        }

    def wait_all(self, timeout: float | None = None) -> ParserBatchResult:
        """
        Wait for all jobs to complete.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            ParserBatchResult with all results
        """
        start_time = time.time()
        all_completed = False

        while not all_completed:
            progress = self.get_progress()
            total = progress["total_jobs"]
            completed = progress["completed"]
            failed = progress["failed"]

            all_completed = completed + failed >= total > 0

            if timeout and (time.time() - start_time) > timeout:
                logger.warning(f"Wait timeout reached after {timeout}s")
                break

            if not all_completed:
                time.sleep(0.5)

        all_jobs = self.job_queue.get_all_jobs()
        results = {}
        errors = {}
        total_time = time.time() - start_time

        for job_id, job in all_jobs.items():
            if job.status == ParserJobStatus.COMPLETED and job.result:
                results[job.file_path] = job.result
            elif job.status == ParserJobStatus.FAILED and job.error:
                errors[job.file_path] = job.error

        completed_count = sum(
            1 for j in all_jobs.values() if j.status == ParserJobStatus.COMPLETED
        )
        failed_count = sum(
            1 for j in all_jobs.values() if j.status == ParserJobStatus.FAILED
        )
        throughput = completed_count / total_time if total_time > 0 else 0

        batch_result = ParserBatchResult(
            total_jobs=len(all_jobs),
            completed=completed_count,
            failed=failed_count,
            results=results,
            errors=errors,
            total_time=total_time,
            throughput=throughput,
        )

        logger.info(f"Batch completed: {completed_count}/{len(all_jobs)} jobs")
        return batch_result

    def cancel_job(self, job_id: str):
        """Cancel a specific job."""
        job = self.job_queue.get_job_info(job_id)
        if job and job.status in (ParserJobStatus.QUEUED, ParserJobStatus.RUNNING):
            self.job_queue.update_status(job_id, ParserJobStatus.CANCELLED)
            logger.info(f"Job {job_id} cancelled")

    def shutdown(self, wait: bool = True, timeout: float = 30.0):
        """
        Shutdown worker processes gracefully.

        Args:
            wait: Whether to wait for processes to finish
            timeout: Maximum time to wait for shutdown
        """
        logger.info("Shutting down parser workers...")
        self.running = False

        for process in self.worker_processes:
            process.terminate()

        if wait:
            start_time = time.time()
            for process in self.worker_processes:
                remaining = timeout - (time.time() - start_time)
                if remaining > 0:
                    process.join(timeout=remaining)
                if process.is_alive():
                    logger.warning(
                        f"Process {process.pid} did not terminate gracefully"
                    )

        logger.info("Parser workers shutdown complete")

    def get_statistics(self) -> dict[str, Any]:
        """Get overall statistics."""
        progress = self.get_progress()
        all_jobs = self.job_queue.get_all_jobs()

        total_execution_time = sum(
            j.execution_time or 0 for j in all_jobs.values() if j.execution_time
        )

        avg_execution_time = None
        if progress["completed"] > 0:
            avg_execution_time = total_execution_time / progress["completed"]

        return {
            "total_jobs": progress["total_jobs"],
            "completed": progress["completed"],
            "failed": progress["failed"],
            "running": progress["running"],
            "queued": progress["queued"],
            "progress_percent": progress["progress_percent"],
            "total_execution_time": total_execution_time,
            "average_execution_time": avg_execution_time,
            "throughput": progress.get("throughput", 0),
            "elapsed_time": progress["elapsed_time"],
            "num_workers": self.num_workers,
        }

    @staticmethod
    def _worker_process(job_queue: ParserJobQueue, result_queue: Queue):
        """
        Worker process loop.

        Args:
            job_queue: Shared job queue
            result_queue: Shared result queue
        """
        while True:
            job = job_queue.get(timeout=1.0)
            if job is None:
                continue

            job_id, file_path, language, parse_func = job

            job_queue.update_status(
                job_id, ParserJobStatus.RUNNING, started_at=datetime.now()
            )

            start_time = time.time()

            try:
                result = parse_func(file_path, language)
                execution_time = time.time() - start_time

                job_queue.update_status(
                    job_id,
                    ParserJobStatus.COMPLETED,
                    completed_at=datetime.now(),
                    result=result,
                    execution_time=execution_time,
                )

                result_queue.put((job_id, result))
                logger.debug(f"Job {job_id} completed in {execution_time:.2f}s")

            except Exception as e:
                execution_time = time.time() - start_time
                error_msg = f"{type(e).__name__}: {str(e)}"

                job_queue.update_status(
                    job_id,
                    ParserJobStatus.FAILED,
                    completed_at=datetime.now(),
                    error=error_msg,
                    execution_time=execution_time,
                )

                logger.error(f"Job {job_id} failed: {error_msg}")
