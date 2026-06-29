"""
Step-level progress tracker for pipeline jobs.

Each job progresses through defined steps (e.g. extract, store, chunk, hyde,
embed, index).  Progress is persisted in the SQLite job store so the API can
expose it to frontends in real time.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass
class Step:
    """A single step in a pipeline job's progress."""

    name: str
    status: str = "pending"  # pending | running | completed | failed
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str = ""
    detail: str = ""


class ProgressTracker:
    """
    Tracks step-level progress for a single pipeline job.

    Usage::

        tracker = ProgressTracker(job_id, step_names=[
            "extract", "store_raw", "db_insert", "chunk",
            "summary", "hyde", "embed", "index",
        ])
        tracker.start("extract")
        ...
        tracker.complete("extract")
        tracker.start("chunk")
        ...
        tracker.complete("chunk")
        tracker.fail("embed", "API timeout")
    """

    def __init__(
        self,
        job_id: str,
        step_names: list[str],
        persist_fn: Callable[[str, list[dict]], None] | None = None,
    ) -> None:
        """Initialize tracker for a job with named steps."""
        self.job_id = job_id
        self._persist_fn = persist_fn
        self.steps: dict[str, Step] = {name: Step(name=name) for name in step_names}
        self._order = step_names

    @property
    def current_step(self) -> str | None:
        """Return the name of the currently running step, if any."""
        for name in self._order:
            if self.steps[name].status == "running":
                return name
        return None

    @property
    def summary(self) -> dict:
        """Return a summary of step completion counts."""
        completed = sum(1 for s in self.steps.values() if s.status == "completed")
        failed = sum(1 for s in self.steps.values() if s.status == "failed")
        running = sum(1 for s in self.steps.values() if s.status == "running")
        pending = sum(1 for s in self.steps.values() if s.status == "pending")
        return {
            "total": len(self.steps),
            "completed": completed,
            "failed": failed,
            "running": running,
            "pending": pending,
        }

    def to_dict(self) -> list[dict]:
        """Serialize steps to a list of dicts."""
        return [
            {
                "name": s.name,
                "status": s.status,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
                "error": s.error,
                "detail": s.detail,
            }
            for s in self.steps.values()
        ]

    def _persist(self) -> None:
        if self._persist_fn:
            self._persist_fn(self.job_id, self.to_dict())

    def start(self, name: str, detail: str = "") -> None:
        """Mark a step as running and persist."""
        step = self.steps.get(name)
        if step is None:
            msg = f"Unknown step {name!r}"
            raise ValueError(msg)
        step.status = "running"
        step.started_at = time.time()
        step.detail = detail
        log_line = f"[job {self.job_id[:8]}] step={name}  status=running  {detail}"
        logger.info(log_line)
        self._persist()

    def complete(self, name: str, detail: str = "") -> None:
        """Mark a step as completed and persist."""
        step = self.steps.get(name)
        if step is None:
            msg = f"Unknown step {name!r}"
            raise ValueError(msg)
        step.status = "completed"
        step.completed_at = time.time()
        elapsed = step.completed_at - step.started_at if step.started_at else 0
        step.detail = detail
        log_line = f"[job {self.job_id[:8]}] step={name}  status=completed  elapsed={elapsed:.2f}s  {detail}"
        logger.info(log_line)
        self._persist()

    def fail(self, name: str, error: str, detail: str = "") -> None:
        """Mark a step as failed and persist."""
        step = self.steps.get(name)
        if step is None:
            msg = f"Unknown step {name!r}"
            raise ValueError(msg)
        step.status = "failed"
        step.completed_at = time.time()
        step.error = error
        step.detail = detail
        log_line = f"[job {self.job_id[:8]}] step={name}  status=failed  error={error}  {detail}"
        logger.error(log_line)
        self._persist()
