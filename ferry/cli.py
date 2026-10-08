"""`ferry`: the command layer. One verb per step of the remote cycle.

    ferry setup   [--host H]            push, then venv + install on the remote
    ferry push    [--host H] [--dirty]  clean check, rsync, remote verify
    ferry run     [--host H] -- CMD...  push, then start CMD detached
    ferry status  [--host H]            every job the remote recorded, with state
    ferry logs    [--host H] JOB [-f]   tail a job's log
    ferry pull    [--host H] [PATH...]  rsync result paths back (never deletes)

Three checks guard a run, in this order:

1. the laptop tree is clean (tracked, staged and untracked) — else refused,
   unless `--dirty` says so out loud;
2. rsync carries the tree including `.git/` with `--delete`, so the remote
   holds nothing the commit does not, except the configured excludes;
3. the remote agent reports its HEAD and every status line the excludes do
   not cover — a mismatch refuses the start with both hashes in the message.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from ferry import config
from ferry.config import Config, Host
from ferry.remote_agent import ALWAYS_EXCLUDED, LOGS_DIR
from ferry.shell import RealShell, Shell, ShellError, agent, ssh_args


class FerryError(RuntimeError):
    """A refusal with a sentence that says what to do."""


def say(message: str) -> None:
    """One line of progress on stderr; stdout stays for data (status table, logs)."""
    print(f"ferry: {message}", file=sys.stderr)


# --- local git state ---------------------------------------------------------


def local_head(shell: Shell, root: Path) -> str:
    """The commit the laptop is at."""
    return shell.run(["git", "-C", str(root), "rev-parse", "HEAD"]).stdout.strip()


def local_status(shell: Shell, root: Path) -> list[str]:
    """Every line of `git status --porcelain` including untracked files."""
    out = shell.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"]
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def require_clean(shell: Shell, root: Path, allow_dirty: bool) -> None:
    """Refuse a dirty tree; with `allow_dirty` announce it instead (loud, visible)."""
    lines = local_status(shell, root)
    if not lines:
        return
    listing = "\n".join("  " + line for line in lines[:20])
    more = f"\n  … and {len(lines) - 20} more" if len(lines) > 20 else ""
    if allow_dirty:
        say(
            f"pushing a DIRTY tree (--dirty): the run will not match HEAD\n{listing}{more}"
        )
        return
    raise FerryError(
        "working tree is not clean — commit first, or `--dirty` to push anyway "
        f"(the run then belongs to no commit):\n{listing}{more}"
    )


# --- rsync -------------------------------------------------------------------


def rsync_push_args(root: Path, host: Host, exclude: tuple[str, ...]) -> list[str]:
    """Mirror the tree (with `.git/`) onto the remote; excludes survive `--delete`."""
    args = ["rsync", "-a", "--delete"]
    for pattern in [*ALWAYS_EXCLUDED, *exclude]:
        args += ["--exclude", pattern]
    args += ["--", f"{root}/", f"{host.ssh}:{host.path}/"]
    return args


def rsync_pull_args(root: Path, host: Host, pattern: str) -> list[str]:
    """Bring one result path back, keeping its place under the root; never deletes.

    `--relative` with the `/./` marker makes `experiments/*/runs` land at the
    same relative path locally; the remote shell expands `~` and the glob.
    """
    return [
        "rsync",
        "-a",
        "--relative",
        "--",
        f"{host.ssh}:{host.path}/./{pattern}",
        f"{root}/",
    ]


# --- the cycle ---------------------------------------------------------------


def push(cfg: Config, host: Host, shell: Shell, allow_dirty: bool) -> str:
    """Clean check, rsync, remote verify. Returns the commit the remote now holds."""
    require_clean(shell, cfg.root, allow_dirty)
    head = local_head(shell, cfg.root)
    say(f"rsync → {host.ssh}:{host.path} ({head[:12]})")
    shell.run(rsync_push_args(cfg.root, host, cfg.exclude))
    verdict = agent(
        shell,
        host.ssh,
        {
            "cmd": "verify",
            "path": host.path,
            "head": head,
            "exclude": list(cfg.exclude),
        },
    )
    if not verdict["head_matches"]:
        raise FerryError(
            f"remote HEAD is {verdict['head'][:12]}, laptop HEAD is {head[:12]} after rsync — "
            "the `.git/` directory did not arrive; is `.git` in `exclude`?"
        )
    leftovers: list[str] = verdict["leftovers"]
    if leftovers and not allow_dirty:
        listing = "\n".join("  " + line for line in leftovers[:20])
        raise FerryError(
            f"remote tree differs from {head[:12]} beyond the excludes:\n{listing}\n"
            "add the path to `exclude` in ferry.toml if it is a result directory"
        )
    say(f"remote holds {head[:12]}, tree clean")
    return head


def cmd_setup(cfg: Config, host: Host, shell: Shell, args: argparse.Namespace) -> int:
    """`ferry setup`: push, then venv + install on the remote."""
    if cfg.install is None:
        raise FerryError(
            "ferry.toml has no `install` command; setup has nothing to run"
        )
    push(cfg, host, shell, allow_dirty=args.dirty)
    say(f"setup on {host.ssh}: venv ({host.python}) + `{cfg.install}`")
    result = agent(
        shell,
        host.ssh,
        {
            "cmd": "setup",
            "path": host.path,
            "python": host.python,
            "install": cfg.install,
        },
    )
    created = "created" if result["venv_created"] else "kept"
    say(f"venv {created}: {result['python']}")
    return 0


def cmd_push(cfg: Config, host: Host, shell: Shell, args: argparse.Namespace) -> int:
    """`ferry push`: the three checks, no start."""
    push(cfg, host, shell, allow_dirty=args.dirty)
    return 0


def cmd_run(cfg: Config, host: Host, shell: Shell, args: argparse.Namespace) -> int:
    """`ferry run -- CMD...`: push, then start detached and print the job id."""
    words: list[str] = list(args.command)
    if words and words[0] == "--":  # argparse REMAINDER keeps the separator
        words = words[1:]
    if not words:
        raise FerryError("nothing to run: `ferry run -- <command>`")
    command = shlex.join(words)
    head = push(cfg, host, shell, allow_dirty=args.dirty)
    result = agent(
        shell,
        host.ssh,
        {"cmd": "start", "path": host.path, "command": command, "commit": head},
    )
    say(f"started job {result['job']} (pid {result['pid']}) on {host.ssh}: {command}")
    say(f"follow it: ferry logs {result['job']} -f")
    print(result["job"])
    return 0


def format_status(jobs: list[dict[str, Any]]) -> str:
    """The status table: job, state, started, commit, command."""
    if not jobs:
        return "no jobs recorded"
    rows = [("JOB", "STATE", "STARTED", "COMMIT", "COMMAND")]
    for job in jobs:
        rows.append(
            (
                job["job"],
                job["state"],
                job["started"],
                str(job["commit"])[:12],
                job["command"],
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    lines: list[str] = []
    for row in rows:
        head = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row[:4]))
        lines.append(f"{head}  {row[4]}")
    return "\n".join(lines)


def cmd_status(cfg: Config, host: Host, shell: Shell, args: argparse.Namespace) -> int:
    """`ferry status`: every job the remote recorded."""
    del cfg, args
    result = agent(shell, host.ssh, {"cmd": "status", "path": host.path})
    print(format_status(result["jobs"]))
    return 0


def cmd_logs(cfg: Config, host: Host, shell: Shell, args: argparse.Namespace) -> int:
    """`ferry logs JOB [-f] [-n N]`: tail the job's log through ssh."""
    del cfg
    log = os.path.join(host.path, LOGS_DIR, args.job + ".log")
    tail = (
        ["tail", "-n", str(args.lines)]
        + (["-f"] if args.follow else [])
        + [shlex.quote(log)]
    )
    shell.run(ssh_args(host.ssh, " ".join(tail)), capture=False)
    return 0


def cmd_pull(cfg: Config, host: Host, shell: Shell, args: argparse.Namespace) -> int:
    """`ferry pull [PATH...]`: rsync result paths back; local files are never deleted."""
    patterns: list[str] = list(args.paths) or list(cfg.pull)
    if not patterns:
        raise FerryError("nothing to pull: give paths or set `pull` in ferry.toml")
    for pattern in patterns:
        say(f"pull {host.ssh}:{host.path}/{pattern}")
        shell.run(rsync_pull_args(cfg.root, host, pattern))
    return 0


# --- argparse ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """The verbs and their flags."""
    parser = argparse.ArgumentParser(
        prog="ferry",
        description="carry a committed repo to a remote machine, run there, bring results back",
    )
    parser.add_argument(
        "--host", help="a [hosts.<name>] from ferry.toml (default: `default`)"
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    def with_dirty(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--dirty",
            action="store_true",
            help="push an uncommitted tree anyway (announced; the run matches no commit)",
        )

    p_setup = sub.add_parser("setup", help="push, then venv + install on the remote")
    with_dirty(p_setup)
    p_setup.set_defaults(func=cmd_setup)

    p_push = sub.add_parser("push", help="clean check, rsync, remote verify")
    with_dirty(p_push)
    p_push.set_defaults(func=cmd_push)

    p_run = sub.add_parser(
        "run", help="push, then start a command detached: ferry run -- CMD"
    )
    with_dirty(p_run)
    p_run.add_argument(
        "command", nargs=argparse.REMAINDER, help="the command, after `--`"
    )
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="jobs the remote recorded, with state")
    p_status.set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="tail a job's log")
    p_logs.add_argument("job")
    p_logs.add_argument("-f", "--follow", action="store_true", help="keep following")
    p_logs.add_argument(
        "-n", "--lines", type=int, default=50, help="lines to show (50)"
    )
    p_logs.set_defaults(func=cmd_logs)

    p_pull = sub.add_parser(
        "pull", help="rsync result paths back (never deletes locally)"
    )
    p_pull.add_argument(
        "paths", nargs="*", help="paths relative to the repo root (default: `pull`)"
    )
    p_pull.set_defaults(func=cmd_pull)
    return parser


def main(argv: list[str], shell: Shell | None = None) -> int:
    """Parse, load `ferry.toml`, dispatch; refusals print one sentence and exit 1."""
    args = build_parser().parse_args(argv)
    shell = shell or RealShell()
    try:
        cfg = config.load(config.find(Path.cwd()))
        host = cfg.host(args.host)
        return int(args.func(cfg, host, shell, args))
    except (FerryError, config.ConfigError, ShellError) as exc:
        say(str(exc))
        return 1


def cli() -> None:
    """Console entry point."""
    raise SystemExit(main(sys.argv[1:]))
