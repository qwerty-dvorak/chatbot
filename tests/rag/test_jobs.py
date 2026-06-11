import tempfile
import time
import unittest

from pipeline.jobs import IngestionWorker, JobStore


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = JobStore(self.tempdir.name)
        self.store.initialize()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_job_lifecycle(self):
        job = self.store.create(
            "ingest",
            {"path": self.tempdir.name, "tier": "instant"},
            ["document.txt"],
        )
        self.assertEqual(job["status"], "queued")

        claimed = self.store.claim_next()
        self.assertEqual(claimed["id"], job["id"])
        self.assertEqual(claimed["status"], "running")

        self.store.succeed(job["id"], {"files_processed": 1})
        completed = self.store.get(job["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["result"]["files_processed"], 1)

    def test_cancel_only_affects_queued_jobs(self):
        job = self.store.create("ingest", {"path": "."}, ["document.txt"])
        self.assertTrue(self.store.cancel(job["id"]))
        self.assertEqual(self.store.get(job["id"])["status"], "cancelled")
        self.assertFalse(self.store.cancel(job["id"]))

    def test_running_jobs_are_requeued_on_initialize(self):
        job = self.store.create("ingest", {"path": "."}, ["document.txt"])
        self.store.claim_next()

        recovered = JobStore(self.tempdir.name)
        recovered.initialize()
        self.assertEqual(recovered.get(job["id"])["status"], "queued")


class IngestionWorkerTests(unittest.TestCase):
    def test_worker_executes_jobs_sequentially(self):
        with tempfile.TemporaryDirectory() as data_dir:
            store = JobStore(data_dir)
            store.initialize()
            observed: list[str] = []

            def handler(job):
                observed.append(job["id"])
                return {"handled": job["filenames"]}

            first = store.create("ingest", {"path": "."}, ["first.txt"])
            second = store.create("ingest", {"path": "."}, ["second.txt"])
            worker = IngestionWorker(store, handler, poll_interval=0.01)
            worker.start()
            worker.notify()
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    statuses = [store.get(first["id"])["status"], store.get(second["id"])["status"]]
                    if statuses == ["succeeded", "succeeded"]:
                        break
                    time.sleep(0.01)
            finally:
                worker.stop()

            self.assertEqual(observed, [first["id"], second["id"]])
            self.assertEqual(store.get(first["id"])["status"], "succeeded")
            self.assertEqual(store.get(second["id"])["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
