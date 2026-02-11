from __future__ import annotations

import gc
import importlib
import time
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger


@dataclass
class PerformanceSnapshot:
    """
    A point-in-time snapshot of performance metrics.

    Attributes:
        memory_mb (float | None): Process memory usage in Megabytes.
        timestamp (float): The time the snapshot was taken.
    """

    memory_mb: float | None
    timestamp: float


class ParserPerformanceOptimizer:
    """
    Optimizes parser performance by monitoring memory and enforcing limits.

    Attributes:
        enforce_limits (Callable[[], None]): Callback to enforce resource limits (e.g., clear caches).
        memory_threshold_mb (int | None): Memory threshold in MB to trigger cleanup.
        check_interval (int): Number of items processed between checks.
        min_interval_seconds (float): Minimum seconds between checks.
        profile_enabled (bool): Whether profiling involves recording snapshots.
        profile_interval_seconds (float): Minimum seconds between profile snapshots.
        max_snapshots (int): Maximum number of snapshots to keep.
    """

    def __init__(
        self,
        enforce_limits: Callable[[], None],
        memory_threshold_mb: int | None = None,
        check_interval: int = 200,
        min_interval_seconds: float = 2.0,
        profile_enabled: bool = False,
        profile_interval_seconds: float = 5.0,
        max_snapshots: int = 1000,
    ) -> None:
        """
        Initialize the Performance Optimizer.

        Args:
            enforce_limits (Callable[[], None]): Callback to enforce limits.
            memory_threshold_mb (int | None): Memory limit in MB. Defaults to None.
            check_interval (int): Check every N items. Defaults to 200.
            min_interval_seconds (float): Min seconds between checks. Defaults to 2.0.
            profile_enabled (bool): Enable profiling history. Defaults to False.
            profile_interval_seconds (float): Min seconds between snapshots. Defaults to 5.0.
            max_snapshots (int): Max history snapshots. Defaults to 1000.
        """
        self.enforce_limits = enforce_limits
        self.memory_threshold_mb = memory_threshold_mb
        self.check_interval = max(1, check_interval)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.profile_enabled = profile_enabled
        self.profile_interval_seconds = max(0.0, profile_interval_seconds)
        self.max_snapshots = max(1, max_snapshots)
        self._last_check_time = 0.0
        self._last_profile_time = 0.0
        self._processed_since_check = 0
        self._snapshots: list[PerformanceSnapshot] = []

    def checkpoint(self, processed_increment: int = 1, force: bool = False) -> None:
        """
        Records progress and potentially triggers a check/cleanup.

        Args:
            processed_increment (int): Number of items processed since last call. Defaults to 1.
            force (bool): whether to force a check regardless of interval. Defaults to False.
        """
        self._processed_since_check += processed_increment
        if not force and self._processed_since_check < self.check_interval:
            return

        now = time.time()
        if not force and (now - self._last_check_time) < self.min_interval_seconds:
            return

        self._processed_since_check = 0
        self._last_check_time = now

        memory_mb = self._get_process_memory_mb()
        if self.profile_enabled:
            if (
                force
                or (now - self._last_profile_time) >= self.profile_interval_seconds
            ):
                self._record_snapshot(memory_mb, now)
        should_cleanup = force
        if self.memory_threshold_mb:
            if memory_mb is None:
                should_cleanup = True
            else:
                should_cleanup = memory_mb >= self.memory_threshold_mb

        if should_cleanup or self.memory_threshold_mb is None:
            self._cleanup(memory_mb)

    def _cleanup(self, memory_mb: float | None) -> None:
        """
        Executes cleanup actions (enforce limits, garbage collection).

        Args:
            memory_mb (float | None): Current memory usage.
        """
        self.enforce_limits()
        gc.collect()
        logger.debug(
            "Performance optimizer cleanup executed (memory_mb={})",
            f"{memory_mb:.2f}" if memory_mb is not None else "unknown",
        )

    def _record_snapshot(self, memory_mb: float | None, timestamp: float) -> None:
        """
        Records a performance snapshot.

        Args:
            memory_mb (float | None): Memory usage.
            timestamp (float): Timestamp.
        """
        self._snapshots.append(
            PerformanceSnapshot(memory_mb=memory_mb, timestamp=timestamp)
        )
        self._last_profile_time = timestamp
        if len(self._snapshots) > self.max_snapshots:
            self._snapshots = self._snapshots[-self.max_snapshots :]

    def get_profile_summary(self) -> dict[str, float | int | None]:
        """
        Calculates a summary of recorded performance snapshots.

        Returns:
            dict[str, float | int | None]: A dictionary with min, max, avg memory usage and sample count.
        """
        if not self._snapshots:
            return {
                "samples": 0,
                "min_mb": None,
                "max_mb": None,
                "avg_mb": None,
                "last_mb": None,
            }
        values = [s.memory_mb for s in self._snapshots if s.memory_mb is not None]
        last_mb = self._snapshots[-1].memory_mb
        if not values:
            return {
                "samples": len(self._snapshots),
                "min_mb": None,
                "max_mb": None,
                "avg_mb": None,
                "last_mb": last_mb,
            }
        return {
            "samples": len(self._snapshots),
            "min_mb": min(values),
            "max_mb": max(values),
            "avg_mb": sum(values) / len(values),
            "last_mb": last_mb,
        }

    @staticmethod
    def _get_process_memory_mb() -> float | None:
        """
        Attempts to get the current process RSS memory usage in MB.

        Returns:
            float | None: Memory usage in MB, or None if psutil is not available.
        """
        try:
            psutil = importlib.import_module("psutil")
            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        except Exception:
            return None
