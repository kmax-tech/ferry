"""The command layer against a FakeShell: what ferry sends to git, rsync and ssh."""

import json
import unittest
from pathlib import Path

from ferry import cli
from ferry.config import Config, Host
from ferry.shell import FakeShell

HOST = Host(name="lab1", ssh="lab1", path="~/projects/fd", python="python3")
CFG = Config(
    root=Path("/repo"),
    default="lab1",
    hosts={"lab1": HOST},
    exclude=(".venv", "output"),
    pull=("output",),
    install=".venv/bin/pip install -r requirements.txt",
)
HEAD = "a" * 40


def verdict(head: str = HEAD, leftovers: list[str] | None = None) -> str:
    return json.dumps(
        {"head": head, "head_matches": head == HEAD, "leftovers": leftovers or []}
    )


class TestPush(unittest.TestCase):
    def test_clean_tree_is_rsynced_and_verified(self):
        shell = FakeShell(outputs=["", HEAD + "\n", "", verdict()])
        head = cli.push(CFG, HOST, shell, allow_dirty=False)
        self.assertEqual(head, HEAD)
        self.assertEqual(
            shell.calls[0][-3:], ["status", "--porcelain", "--untracked-files=all"]
        )
        self.assertEqual(
            shell.calls[2],
            [
                "rsync",
                "-a",
                "--delete",
                "--exclude",
                ".ferry",
                "--exclude",
                ".venv",
                "--exclude",
                "output",
                "--",
                "/repo/",
                "lab1:~/projects/fd/",
            ],
        )
        ssh = shell.calls[3]
        self.assertEqual(ssh[:4], ["ssh", "-o", "BatchMode=yes", "lab1"])
        self.assertTrue(ssh[4].startswith("python3 - "))
        self.assertIn('"cmd": "verify"', ssh[4])
        self.assertIn("def leftovers", shell.stdins[3] or "")

    def test_dirty_tree_is_refused_before_rsync(self):
        shell = FakeShell(outputs=[" M a.py\n?? new.txt\n"])
        with self.assertRaisesRegex(cli.FerryError, "not clean.*\n.* M a.py"):
            cli.push(CFG, HOST, shell, allow_dirty=False)
        self.assertEqual(len(shell.calls), 1)

    def test_dirty_flag_pushes_and_says_so(self):
        shell = FakeShell(
            outputs=[" M a.py\n", HEAD + "\n", "", verdict(leftovers=[" M a.py"])]
        )
        self.assertEqual(cli.push(CFG, HOST, shell, allow_dirty=True), HEAD)
        self.assertEqual(shell.calls[2][0], "rsync")

    def test_remote_head_mismatch_is_refused(self):
        shell = FakeShell(outputs=["", HEAD + "\n", "", verdict(head="b" * 40)])
        with self.assertRaisesRegex(cli.FerryError, "remote HEAD is bbbbbbbbbbbb"):
            cli.push(CFG, HOST, shell, allow_dirty=False)

    def test_remote_leftovers_are_refused(self):
        shell = FakeShell(
            outputs=["", HEAD + "\n", "", verdict(leftovers=["?? stray.txt"])]
        )
        with self.assertRaisesRegex(
            cli.FerryError, "beyond the excludes:\n  \\?\\? stray.txt"
        ):
            cli.push(CFG, HOST, shell, allow_dirty=False)


class TestVerbs(unittest.TestCase):
    def test_run_pushes_then_starts(self):
        shell = FakeShell(
            outputs=[
                "",
                HEAD + "\n",
                "",
                verdict(),
                json.dumps({"job": "j1", "pid": 7, "log": "l"}),
            ]
        )
        args = cli.build_parser().parse_args(
            ["run", "--", "python", "x.py", "--n", "3"]
        )
        self.assertEqual(cli.cmd_run(CFG, HOST, shell, args), 0)
        start = json.loads(shell.calls[4][4][len("python3 - '") : -1])
        self.assertEqual(start["cmd"], "start")
        self.assertEqual(start["command"], "python x.py --n 3")
        self.assertEqual(start["commit"], HEAD)

    def test_run_without_command_is_refused(self):
        args = cli.build_parser().parse_args(["run"])
        with self.assertRaisesRegex(cli.FerryError, "nothing to run"):
            cli.cmd_run(CFG, HOST, FakeShell(), args)

    def test_setup_without_install_is_refused(self):
        cfg = Config(**{**CFG.__dict__, "install": None})
        args = cli.build_parser().parse_args(["setup"])
        with self.assertRaisesRegex(cli.FerryError, "no `install`"):
            cli.cmd_setup(cfg, HOST, FakeShell(), args)

    def test_pull_uses_config_paths_and_never_deletes(self):
        shell = FakeShell()
        args = cli.build_parser().parse_args(["pull"])
        cli.cmd_pull(CFG, HOST, shell, args)
        self.assertEqual(
            shell.calls,
            [
                [
                    "rsync",
                    "-a",
                    "--relative",
                    "--",
                    "lab1:~/projects/fd/./output",
                    "/repo/",
                ]
            ],
        )

    def test_pull_explicit_paths_win(self):
        shell = FakeShell()
        args = cli.build_parser().parse_args(["pull", "experiments/*/runs"])
        cli.cmd_pull(CFG, HOST, shell, args)
        self.assertEqual(shell.calls[0][4], "lab1:~/projects/fd/./experiments/*/runs")

    def test_logs_hands_the_terminal_to_ssh(self):
        shell = FakeShell()
        args = cli.build_parser().parse_args(["logs", "j1", "-f", "-n", "5"])
        cli.cmd_logs(CFG, HOST, shell, args)
        self.assertEqual(
            shell.calls[0][4], "tail -n 5 -f '~/projects/fd/.ferry/logs/j1.log'"
        )
        self.assertEqual(shell.captures, [False])

    def test_status_table(self):
        jobs = [
            {
                "job": "j1",
                "state": "exit 0",
                "started": "t",
                "commit": HEAD,
                "command": "x",
            },
            {
                "job": "j2",
                "state": "running",
                "started": "t",
                "commit": HEAD,
                "command": "y z",
            },
        ]
        table = cli.format_status(jobs)
        self.assertIn("JOB  STATE    STARTED  COMMIT        COMMAND", table)
        self.assertIn("j2   running  t        aaaaaaaaaaaa  y z", table)
        self.assertEqual(cli.format_status([]), "no jobs recorded")


if __name__ == "__main__":
    unittest.main()
