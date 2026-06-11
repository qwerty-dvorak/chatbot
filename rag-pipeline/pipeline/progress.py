"""Step-level progress tracker for pipeline jobs.

Each job progresses through defined steps (e.g. extract, store, chunk, hyde,
embed, index).  Progress is persisted in the SQLite job store so the API can
expose it to frontends in real time.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Step:
    name: str
    status: str = "pending"  # pending | running | completed | failed
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str = ""
    detail: str = ""


class ProgressTracker:
    """Tracks step-level progress for a single pipeline job.

    Usage::

        tracker = ProgressTracker(job_id, step_names=[
            "extract", "store_raw", "chunk", "hyde", "embed", "index",
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
        persist_fn: Optional[Callable[[str, list[dict]], None]] = None,
    ):
        self.job_id = job_id
        self._persist_fn = persist_fn
        self.steps: dict[str, Step] = {
            name: Step(name=name) for name in step_names
        }
        self._order = step_names

    @property
    def current_step(self) -> Optional[str]:
        for name in self._order:
            if self.steps[name].status == "running":
                return name
        return None

    @property
    def summary(self) -> dict:
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
        step = self.steps.get(name)
        if step is None:
            raise ValueError(f"Unknown step {name!r}")
        step.status = "running"
        step.started_at = time.time()
        step.detail = detail
        log_line = f"[job {self.job_id[:8]}] step={name}  status=running  {detail}"
        print(log_line)
        logger.info(log_line)
        self._persist()

    def complete(self, name: str, detail: str = "") -> None:
        step = self.steps.get(name)
        if step is None:
            raise ValueError(f"Unknown step {name!r}")
        step.status = "completed"
        step.completed_at = time.time()
        elapsed = step.completed_at - step.started_at if step.started_at else 0
        step.detail = detail
        log_line = (
            f"[job {self.job_id[:8]}] step={name}  status=completed  "
            f"elapsed={elapsed:.2f}s  {detail}"
        )
        print(log_line)
        logger.info(log_line)
        self._persist()

    def fail(self, name: str, error: str, detail: str = "") -> None:
        step = self.steps.get(name)
        if step is None:
            raise ValueError(f"Unknown step {name!r}")
        step.status = "failed"
        step.completed_at = time.time()
        step.error = error
        step.detail = detail
        log_line = (
            f"[job {self.job_id[:8]}] step={name}  status=failed  "
            f"error={error}  {detail}"
        )
        print(log_line)
        logger.error(log_line)
        self._persist()
