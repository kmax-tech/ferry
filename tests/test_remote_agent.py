"""The remote half, run locally: exclude filtering, start/status on a temp repo."""

import os
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ferry import remote_agent as ra


class TestLeftovers(unittest.TestCase):
    def test_excluded_paths_and_their_children_are_dropped(self):
        porcelain = (
            "?? .venv/\n?? output/x.json\n?? experiments/42/runs/r1/metrics.json\n"
        )
        self.assertEqual(
            ra.leftovers(porcelain, [".venv", "output", "experiments/*/runs"]), []
        )

    def test_anything_else_is_a_leftover(self):
        porcelain = " M src/a.py\n?? stray.txt\n?? .ferry/jobs.tsv\n"
        self.assertEqual(
            ra.leftovers(porcelain, [".venv"]), [" M src/a.py", "?? stray.txt"]
        )

    def test_rename_uses_the_new_name(self):
        self.assertEqual(ra.leftovers("R  old.py -> output/new.py\n", ["output"]), [])

    def test_pattern_does_not_match_a_prefix_of_a_name(self):
        self.assertEqual(ra.leftovers("?? outputs/x\n", ["output"]), ["?? outputs/x"])


def git(args: list[str], cwd: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(tmp: str) -> str:
    git(["init", "-q"], tmp)
    git(["config", "user.email", "t@example.org"], tmp)
    git(["config", "user.name", "t"], tmp)
    Path(tmp, "a.py").write_text("print(1)\n")
    git(["add", "a.py"], tmp)
    git(["commit", "-q", "-m", "one"], tmp)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class TestVerify(unittest.TestCase):
    def test_clean_tree_matches(self):
        with TemporaryDirectory() as tmp:
            head = make_repo(tmp)
            v = ra.handle({"cmd": "verify", "path": tmp, "head": head, "exclude": []})
        self.assertTrue(v["head_matches"])
        self.assertEqual(v["leftovers"], [])

    def test_stray_file_is_reported_unless_excluded(self):
        with TemporaryDirectory() as tmp:
            head = make_repo(tmp)
            Path(tmp, "output").mkdir()
            Path(tmp, "output", "r.json").write_text("{}")
            v = ra.handle({"cmd": "verify", "path": tmp, "head": head, "exclude": []})
            self.assertEqual(v["leftovers"], ["?? output/r.json"])
            v = ra.handle(
                {"cmd": "verify", "path": tmp, "head": head, "exclude": ["output"]}
            )
            self.assertEqual(v["leftovers"], [])

    def test_wrong_head_is_reported(self):
        with TemporaryDirectory() as tmp:
            make_repo(tmp)
            v = ra.handle(
                {"cmd": "verify", "path": tmp, "head": "0" * 40, "exclude": []}
            )
        self.assertFalse(v["head_matches"])


def wait_for(path: Path, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} did not appear in {seconds}s")
        time.sleep(0.05)


class TestStartStatus(unittest.TestCase):
    def test_start_records_job_and_status_reports_exit_code(self):
        with TemporaryDirectory() as tmp:
            r = ra.handle(
                {
                    "cmd": "start",
                    "path": tmp,
                    "command": "echo hi; exit 3",
                    "commit": "abc",
                }
            )
            wait_for(Path(tmp, ra.LOGS_DIR, r["job"] + ".rc"))
            s = ra.handle({"cmd": "status", "path": tmp})
            log = Path(r["log"]).read_text()
        self.assertEqual(len(s["jobs"]), 1)
        job = s["jobs"][0]
        self.assertEqual(job["job"], r["job"])
        self.assertEqual(job["state"], "exit 3")
        self.assertEqual(job["commit"], "abc")
        self.assertEqual(job["command"], "echo hi; exit 3")
        self.assertEqual(log, "hi\n")

    def test_detached_command_sees_the_env(self):
        with TemporaryDirectory() as tmp:
            r = ra.handle(
                {
                    "cmd": "start",
                    "path": tmp,
                    "command": "echo $LABKIT_DETACHED $FERRY_JOB",
                    "commit": "c",
                }
            )
            wait_for(Path(tmp, ra.LOGS_DIR, r["job"] + ".rc"))
            self.assertEqual(Path(r["log"]).read_text(), f"1 {r['job']}\n")

    def test_running_and_lost(self):
        with TemporaryDirectory() as tmp:
            r = ra.handle(
                {"cmd": "start", "path": tmp, "command": "sleep 30", "commit": "c"}
            )
            s = ra.handle({"cmd": "status", "path": tmp})
            self.assertEqual(s["jobs"][0]["state"], "running")
            os.killpg(r["pid"], 9)
            # On the remote the agent process exits and init reaps the child;
            # here the test process is the parent, so reap it ourselves — a
            # zombie still answers `kill -0`.
            os.waitpid(r["pid"], 0)
            self.assertEqual(ra.job_state(tmp, r["job"], r["pid"]), "lost")

    def test_no_jobs_file_means_no_jobs(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(ra.handle({"cmd": "status", "path": tmp}), {"jobs": []})

    def test_unknown_command_raises(self):
        with self.assertRaisesRegex(RuntimeError, "unknown agent command"):
            ra.handle({"cmd": "dance", "path": "/tmp"})


if __name__ == "__main__":
    unittest.main()
