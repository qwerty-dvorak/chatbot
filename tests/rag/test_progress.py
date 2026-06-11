"""Tests for the step-level progress tracker."""

import unittest
from pipeline.progress import ProgressTracker, Step


class ProgressTrackerTests(unittest.TestCase):
    def setUp(self):
        self.step_names = ["extract", "store_raw", "chunk", "embed", "index"]
        self.persisted: list[tuple[str, list[dict]]] = []

        def _persist(job_id: str, steps: list[dict]):
            self.persisted.append((job_id, steps))

        self.tracker = ProgressTracker(
            "test-job-123",
            self.step_names,
            persist_fn=_persist,
        )

    def test_initial_all_pending(self):
        for step in self.tracker.steps.values():
            self.assertEqual(step.status, "pending")

    def test_summary_initial(self):
        s = self.tracker.summary
        self.assertEqual(s["total"], 5)
        self.assertEqual(s["pending"], 5)
        self.assertEqual(s["completed"], 0)
        self.assertEqual(s["running"], 0)
        self.assertEqual(s["failed"], 0)

    def test_start_changes_status(self):
        self.tracker.start("extract")
        self.assertEqual(self.tracker.steps["extract"].status, "running")
        self.assertGreater(self.tracker.steps["extract"].started_at, 0)

    def test_complete_changes_status(self):
        self.tracker.start("extract")
        self.tracker.complete("extract")
        self.assertEqual(self.tracker.steps["extract"].status, "completed")
        self.assertGreater(self.tracker.steps["extract"].completed_at, 0)

    def test_fail_changes_status(self):
        self.tracker.start("embed")
        self.tracker.fail("embed", "API timeout")
        self.assertEqual(self.tracker.steps["embed"].status, "failed")
        self.assertEqual(self.tracker.steps["embed"].error, "API timeout")

    def test_current_step(self):
        self.assertIsNone(self.tracker.current_step)
        self.tracker.start("extract")
        self.assertEqual(self.tracker.current_step, "extract")
        self.tracker.complete("extract")
        self.tracker.start("store_raw")
        self.assertEqual(self.tracker.current_step, "store_raw")

    def test_summary_after_steps(self):
        self.tracker.start("extract")
        self.tracker.complete("extract")
        self.tracker.start("chunk")
        s = self.tracker.summary
        self.assertEqual(s["completed"], 1)
        self.assertEqual(s["running"], 1)
        self.assertEqual(s["pending"], 3)

    def test_to_dict(self):
        self.tracker.start("extract", "reading file")
        self.tracker.complete("extract", "done")
        d = self.tracker.to_dict()
        self.assertEqual(len(d), 5)
        extract_step = d[0]
        self.assertEqual(extract_step["name"], "extract")
        self.assertEqual(extract_step["status"], "completed")
        self.assertEqual(extract_step["detail"], "done")

    def test_unknown_step_raises(self):
        with self.assertRaises(ValueError):
            self.tracker.start("nonexistent")

    def test_persist_called_on_transitions(self):
        self.tracker.start("extract")
        self.assertEqual(len(self.persisted), 1)
        self.assertEqual(self.persisted[0][0], "test-job-123")
        steps_data = self.persisted[0][1]
        self.assertEqual(len(steps_data), 5)
        extract_entry = next(s for s in steps_data if s["name"] == "extract")
        self.assertEqual(extract_entry["status"], "running")

    def test_full_lifecycle(self):
        tracker = ProgressTracker("lifecycle-test", ["a", "b", "c"])
        tracker.start("a")
        tracker.complete("a")
        tracker.start("b")
        tracker.fail("b", "error")
        tracker.start("c")
        tracker.complete("c")
        s = tracker.summary
        self.assertEqual(s["completed"], 2)
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["pending"], 0)
        self.assertEqual(s["running"], 0)


class StepTests(unittest.TestCase):
    def test_step_defaults(self):
        step = Step(name="test")
        self.assertEqual(step.name, "test")
        self.assertEqual(step.status, "pending")
        self.assertEqual(step.error, "")
        self.assertEqual(step.detail, "")


if __name__ == "__main__":
    unittest.main()
