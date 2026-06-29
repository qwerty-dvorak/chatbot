"""Durable local job queue for long-running ingestion work."""

from __future__ import annotations

import json
import sqlite3
import threading
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    """SQLite job repository safe for use by API and worker threads."""

    def __init__(self, data_dir: str) -> None:
        """Initialize the job store with a data directory."""
        self.data_dir = Path(data_dir).resolve()
        self.upload_dir = self.data_dir / "uploads"
        self.database_path = self.data_dir / "jobs.sqlite3"

    def initialize(self) -> None:
        """Create tables and reset stale running jobs."""
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    filenames_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    steps_json TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )
                """,
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ingestion_jobs_status_created ON ingestion_jobs(status, created_at)",
            )
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'queued', started_at = NULL,
                    error = 'Worker stopped while this job was running; job requeued.'
                WHERE status = 'running'
                """,
            )

    def create(
        self,
        kind: str,
        payload: dict,
        filenames: list[str],
        job_id: str | None = None,
    ) -> dict:
        """Create a new job and return it."""
        job_id = job_id or uuid.uuid4().hex
        created_at = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ingestion_jobs (
                    id, kind, status, payload_json, filenames_json, created_at
                ) VALUES (?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    json.dumps(payload),
                    json.dumps(filenames),
                    created_at,
                ),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        """Get a job by ID."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ingestion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._deserialize(row) if row else None

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        """List jobs, optionally filtered by status."""
        query = "SELECT * FROM ingestion_jobs"
        parameters: list[object] = []
        if status:
            query += " WHERE status = ?"
            parameters.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._deserialize(row) for row in rows]

    def claim_next(self) -> dict | None:
        """Claim the next queued job (atomic)."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM ingestion_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """,
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            started_at = _utc_now()
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'running', started_at = ?, completed_at = NULL, error = NULL
                WHERE id = ? AND status = 'queued'
                """,
                (started_at, row["id"]),
            )
            connection.commit()
        return self.get(row["id"])

    def succeed(self, job_id: str, result: dict) -> None:
        """Mark a job as succeeded."""
        self._finish(job_id, "succeeded", result=result)

    def fail(self, job_id: str, error: str) -> None:
        """Mark a job as failed."""
        self._finish(job_id, "failed", error=error)

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued job."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'cancelled', completed_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (_utc_now(), job_id),
            )
        return cursor.rowcount == 1

    def counts(self) -> dict[str, int]:
        """Return counts per status."""
        counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM ingestion_jobs GROUP BY status",
            ).fetchall()
        for row in rows:
            counts[row["status"]] = row["count"]
        return counts

    def update_steps(self, job_id: str, steps: list[dict]) -> None:
        """Persist step-level progress."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE ingestion_jobs SET steps_json = ? WHERE id = ?",
                (json.dumps(steps), job_id),
            )

    def _finish(
        self,
        job_id: str,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = ?, result_json = ?, error = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    _utc_now(),
                    job_id,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @staticmethod
    def _deserialize(row: sqlite3.Row) -> dict:
        job = dict(row)
        job["payload"] = json.loads(job.pop("payload_json"))
        job["filenames"] = json.loads(job.pop("filenames_json"))
        result_json = job.pop("result_json")
        job["result"] = json.loads(result_json) if result_json else None
        steps_json = job.pop("steps_json")
        job["steps"] = json.loads(steps_json) if steps_json else None
        return job


class IngestionWorker:
    """Single background worker that serializes all index mutations."""

    def __init__(
        self,
        store: JobStore,
        handler: Callable[[dict], dict],
        poll_interval: float = 0.5,
    ) -> None:
        """Initialize the worker with a store and handler."""
        self.store = store
        self.handler = handler
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """Whether the worker thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the background worker thread."""
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="rag-ingestion-worker",
            daemon=True,
        )
        self._thread.start()

    def notify(self) -> None:
        """Wake the worker to check for new jobs."""
        self._wake.set()

    def stop(self, timeout: float = 10) -> None:
        """Stop the worker and wait for the thread to finish."""
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        """Worker loop: claim and process jobs."""
        while not self._stop.is_set():
            job = self.store.claim_next()
            if job is None:
                self._wake.wait(self.poll_interval)
                self._wake.clear()
                continue
            try:
                self.store.succeed(job["id"], self.handler(job))
            except BaseException:  # noqa: BLE001
                self.store.fail(job["id"], traceback.format_exc(limit=20))
