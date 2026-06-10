"""Durable local job queue for long-running ingestion work."""

from __future__ import annotations

import json
import sqlite3
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """SQLite job repository safe for use by API and worker threads."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir).resolve()
        self.upload_dir = self.data_dir / "uploads"
        self.database_path = self.data_dir / "jobs.sqlite3"

    def initialize(self) -> None:
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
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ingestion_jobs_status_created "
                "ON ingestion_jobs(status, created_at)"
            )
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'queued', started_at = NULL,
                    error = 'Worker stopped while this job was running; job requeued.'
                WHERE status = 'running'
                """
            )

    def create(
        self,
        kind: str,
        payload: dict,
        filenames: list[str],
        job_id: str | None = None,
    ) -> dict:
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
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._deserialize(row) if row else None

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM ingestion_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
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
        self._finish(job_id, "succeeded", result=result)

    def fail(self, job_id: str, error: str) -> None:
        self._finish(job_id, "failed", error=error)

    def cancel(self, job_id: str) -> bool:
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
        counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM ingestion_jobs GROUP BY status"
            ).fetchall()
        for row in rows:
            counts[row["status"]] = row["count"]
        return counts

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
        return job


class IngestionWorker:
    """Single background worker that serializes all index mutations."""

    def __init__(
        self,
        store: JobStore,
        handler: Callable[[dict], dict],
        poll_interval: float = 0.5,
    ):
        self.store = store
        self.handler = handler
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
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
        self._wake.set()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self.store.claim_next()
            if job is None:
                self._wake.wait(self.poll_interval)
                self._wake.clear()
                continue
            try:
                self.store.succeed(job["id"], self.handler(job))
            except Exception:
                self.store.fail(job["id"], traceback.format_exc(limit=20))
