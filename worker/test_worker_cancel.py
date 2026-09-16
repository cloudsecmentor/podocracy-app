"""Tests for stopping a run and for recovering from an orphaned one.

The portal cannot signal this container's processes, so a stop is a file the
worker polls. The two things worth proving here are that the stop reaches the
*whole* stage subtree, and that a worker restart cannot leave a project looking
busy forever.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import unittest
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKER_ROOT))

import worker_poll


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until(predicate, timeout=15.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class WorkerCancelTestCase(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects = Path(self.temp.name)
        self._previous_dir = worker_poll.PROJECTS_DIR
        worker_poll.PROJECTS_DIR = self.projects
        self.addCleanup(lambda: setattr(worker_poll, "PROJECTS_DIR", self._previous_dir))

        self.project = self.projects / "project-test"
        (self.project / "logs").mkdir(parents=True)
        self.log_path = self.project / "logs" / "orchestrator.log"

    def status(self) -> dict:
        return json.loads((self.project / "status.json").read_text(encoding="utf-8"))

    def write_status(self, **fields) -> None:
        worker_poll.write_json(self.project / "status.json", {"project_id": "project-test", **fields})


class ProcessGroupCancelTests(WorkerCancelTestCase):
    def test_stop_kills_the_grandchild_too(self):
        """The orchestrator shells out per stage and stages shell out to ffmpeg.
        Signalling only the direct child would leave those running, still
        writing into the project and still hammering the TTS server."""
        child_pid_file = self.project / "grandchild.pid"
        # Mirrors the real tree: a child that spawns its own long-running child.
        command = [
            "/bin/sh",
            "-c",
            f"sleep 120 & echo $! > {child_pid_file}; sleep 120",
        ]
        self.write_status(state="running", stage="voiceover")

        result: dict = {}

        def run():
            result["outcome"] = worker_poll.run_with_cancel(
                self.project, command, os.environ.copy(), self.log_path
            )

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(wait_until(lambda: child_pid_file.exists()), "grandchild never started")
        grandchild = int(child_pid_file.read_text().strip())
        self.assertTrue(pid_alive(grandchild))

        worker_poll.cancel_request_path(self.project).write_text("stop", encoding="utf-8")
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "run did not stop")

        self.assertEqual(result["outcome"][0], "cancelled")
        self.assertTrue(wait_until(lambda: not pid_alive(grandchild), timeout=10),
                        "grandchild survived the stop")

    def test_a_run_that_ignores_sigterm_is_killed_anyway(self):
        command = ["/bin/sh", "-c", "trap '' TERM; sleep 120"]
        self.write_status(state="running", stage="voiceover")
        worker_poll.CANCEL_GRACE_SECONDS = 1.0
        self.addCleanup(lambda: setattr(worker_poll, "CANCEL_GRACE_SECONDS", 10.0))

        result: dict = {}
        thread = threading.Thread(
            target=lambda: result.update(
                outcome=worker_poll.run_with_cancel(
                    self.project, command, os.environ.copy(), self.log_path
                )
            ),
            daemon=True,
        )
        thread.start()
        time.sleep(0.5)
        worker_poll.cancel_request_path(self.project).write_text("stop", encoding="utf-8")

        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "SIGKILL escalation did not happen")
        self.assertEqual(result["outcome"][0], "cancelled")
        self.assertIn("SIGKILL", self.log_path.read_text(encoding="utf-8"))

    def test_an_untouched_run_finishes_normally(self):
        self.write_status(state="running", stage="voiceover")
        outcome, returncode = worker_poll.run_with_cancel(
            self.project, ["/bin/sh", "-c", "exit 0"], os.environ.copy(), self.log_path
        )
        self.assertEqual((outcome, returncode), ("finished", 0))

    def test_a_failing_run_reports_its_exit_code(self):
        self.write_status(state="running", stage="voiceover")
        outcome, returncode = worker_poll.run_with_cancel(
            self.project, ["/bin/sh", "-c", "exit 3"], os.environ.copy(), self.log_path
        )
        self.assertEqual((outcome, returncode), ("finished", 3))

    def test_liveness_is_refreshed_during_a_quiet_stage(self):
        # Voiceover can run for hours without a stage transition; without this
        # the portal would decide the run had died and let it be re-queued.
        self.write_status(state="running", stage="voiceover")
        worker_poll.HEARTBEAT_SECONDS = 0.1
        self.addCleanup(lambda: setattr(worker_poll, "HEARTBEAT_SECONDS", 10.0))

        worker_poll.run_with_cancel(
            self.project, ["/bin/sh", "-c", "sleep 1.5"], os.environ.copy(), self.log_path
        )
        status = self.status()
        self.assertIn("heartbeat", status)
        self.assertEqual(status["worker_boot_id"], worker_poll.WORKER_BOOT_ID)


class InterruptedRunSweepTests(WorkerCancelTestCase):
    def test_a_restart_clears_a_run_it_orphaned(self):
        """The stuck-forever case: the worker died mid-run, so nothing will ever
        move this project off `running` by itself."""
        self.write_status(state="running", stage="voiceover", progress=65)

        worker_poll.sweep_interrupted_runs()

        status = self.status()
        self.assertEqual(status["state"], "interrupted")
        self.assertEqual(status["stage"], "voiceover")
        self.assertEqual(status["progress"], 65)
        self.assertIn("restart", status["error"])

    def test_a_pending_stop_is_discarded_by_the_sweep(self):
        self.write_status(state="cancelling", stage="voiceover")
        worker_poll.cancel_request_path(self.project).write_text("stop", encoding="utf-8")

        worker_poll.sweep_interrupted_runs()

        self.assertEqual(self.status()["state"], "interrupted")
        # Otherwise it would kill the next run on sight.
        self.assertFalse(worker_poll.cancel_request_path(self.project).exists())

    def test_the_sweep_leaves_a_genuinely_locked_project_alone(self):
        # A second worker holding the lock really is processing it.
        self.write_status(state="running", stage="voiceover")
        fd = worker_poll.acquire_lock(self.project)
        self.assertIsNotNone(fd)
        self.addCleanup(lambda: worker_poll.release_lock(self.project, fd))

        worker_poll.sweep_interrupted_runs()

        self.assertEqual(self.status()["state"], "running")

    def test_finished_projects_are_untouched(self):
        for state in ("completed", "failed", "queued", "draft"):
            self.write_status(state=state, stage=state)
            worker_poll.sweep_interrupted_runs()
            self.assertEqual(self.status()["state"], state)


class CancelBookkeepingTests(WorkerCancelTestCase):
    def test_status_updates_carry_the_portals_job_kind_through_the_run(self):
        self.write_status(state="queued", job_kind="tts-chunk")
        worker_poll.update_status(self.project, "running", "voiceover", 50, "working", alive=True)
        status = self.status()
        self.assertEqual(status["job_kind"], "tts-chunk")
        self.assertEqual(status["worker_boot_id"], worker_poll.WORKER_BOOT_ID)

    def test_beat_does_not_resurrect_a_finished_run(self):
        self.write_status(state="completed", stage="completed")
        worker_poll.beat(self.project)
        self.assertNotIn("heartbeat", self.status())

    def test_clearing_a_stop_request_is_idempotent(self):
        worker_poll.clear_cancel_request(self.project)
        worker_poll.cancel_request_path(self.project).write_text("stop", encoding="utf-8")
        worker_poll.clear_cancel_request(self.project)
        self.assertFalse(worker_poll.cancel_requested(self.project))


class ConcurrentStatusWriteTests(WorkerCancelTestCase):
    """status.json is written by the worker, the pipeline and the portal. It used
    to be truncated in place, so a reader could catch it empty; that read raised
    and took the whole worker down."""

    def test_a_torn_read_falls_back_instead_of_raising(self):
        path = self.project / "status.json"
        for content in ("", "{ partial", "not json at all"):
            path.write_text(content, encoding="utf-8")
            self.assertEqual(worker_poll.read_json(path, {"fallback": True}), {"fallback": True})

    def test_writes_leave_no_temp_files_behind(self):
        self.write_status(state="running", stage="voiceover")
        leftovers = [item.name for item in self.project.glob(".status.json.tmp*")]
        self.assertEqual(leftovers, [])

    def test_a_write_is_never_visible_as_a_partial_file(self):
        path = self.project / "status.json"
        self.write_status(state="running", stage="voiceover")
        stop = threading.Event()
        torn: list[str] = []

        def reader():
            while not stop.is_set():
                try:
                    raw = path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    torn.append("missing")
                    continue
                if raw:
                    try:
                        json.loads(raw)
                    except json.JSONDecodeError:
                        torn.append(raw[:20])

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        for index in range(300):
            worker_poll.update_status(self.project, "running", "voiceover", index % 100, "working", alive=True)
        stop.set()
        thread.join(timeout=5)

        self.assertEqual(torn, [], "a reader saw a partially written status.json")

    def test_a_corrupt_status_file_does_not_stop_a_run(self):
        (self.project / "status.json").write_text("", encoding="utf-8")
        worker_poll.HEARTBEAT_SECONDS = 0.1
        self.addCleanup(lambda: setattr(worker_poll, "HEARTBEAT_SECONDS", 10.0))

        outcome, returncode = worker_poll.run_with_cancel(
            self.project, ["/bin/sh", "-c", "sleep 0.5"], os.environ.copy(), self.log_path
        )
        self.assertEqual((outcome, returncode), ("finished", 0))

    def test_a_corrupt_status_file_does_not_stop_a_cancel(self):
        """The exact crash seen in the container: the cancel branch read
        status.json for its progress value and hit a truncated file."""
        (self.project / "status.json").write_text("", encoding="utf-8")
        result: dict = {}
        thread = threading.Thread(
            target=lambda: result.update(
                outcome=worker_poll.run_with_cancel(
                    self.project, ["/bin/sh", "-c", "sleep 60"], os.environ.copy(), self.log_path
                )
            ),
            daemon=True,
        )
        thread.start()
        time.sleep(0.5)
        worker_poll.cancel_request_path(self.project).write_text("stop", encoding="utf-8")

        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "the cancel crashed instead of stopping the run")
        self.assertEqual(result["outcome"][0], "cancelled")


if __name__ == "__main__":
    unittest.main()
