"""The three outside tools ferry drives: git, rsync, ssh.

Everything that leaves the process goes through `Shell.run`, so the command
layer can be tested against a `FakeShell` that records the calls and plays
back scripted outputs — ssh and rsync themselves are not under test here.
"""

from __future__ import annotations

import inspect
import json
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol

from ferry import remote_agent


class ShellError(RuntimeError):
    """A tool exited non-zero; the message carries the command and its stderr."""


@dataclass(frozen=True)
class Result:
    """What a finished command left behind."""

    stdout: str
    returncode: int


class Shell(Protocol):
    """Run one command; `capture=False` hands the terminal to it (tail -f)."""

    def run(
        self, args: list[str], *, stdin: str | None = None, capture: bool = True
    ) -> Result:
        """Execute `args`; raises `ShellError` on a non-zero exit."""
        ...


class RealShell:
    """`subprocess.run` with the house rule: a failure is an exception, not a code."""

    def run(
        self, args: list[str], *, stdin: str | None = None, capture: bool = True
    ) -> Result:
        """Execute `args`; raises `ShellError` on a non-zero exit."""
        proc = subprocess.run(
            args,
            input=stdin,
            text=True,
            capture_output=capture,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip() if capture else ""
            raise ShellError(
                f"`{shlex.join(args)}` exited {proc.returncode}"
                + (f":\n{detail}" if detail else "")
            )
        return Result(stdout=proc.stdout if capture else "", returncode=proc.returncode)


def _strs() -> list[str]:
    return []


def _calls() -> list[list[str]]:
    return []


def _stdins() -> list[str | None]:
    return []


def _bools() -> list[bool]:
    return []


@dataclass
class FakeShell:
    """Test double: records every call, answers from a queue of outputs."""

    outputs: list[str] = field(default_factory=_strs)
    calls: list[list[str]] = field(default_factory=_calls)
    stdins: list[str | None] = field(default_factory=_stdins)
    captures: list[bool] = field(default_factory=_bools)

    def run(
        self, args: list[str], *, stdin: str | None = None, capture: bool = True
    ) -> Result:
        """Record the call and return the next scripted output (empty when none is left)."""
        self.calls.append(list(args))
        self.stdins.append(stdin)
        self.captures.append(capture)
        out = self.outputs.pop(0) if self.outputs else ""
        return Result(stdout=out, returncode=0)


def ssh_args(alias: str, remote_command: str) -> list[str]:
    """`ssh` in batch mode: a password prompt would hang a night run, so it fails instead."""
    return ["ssh", "-o", "BatchMode=yes", alias, remote_command]


def agent(shell: Shell, alias: str, request: dict[str, Any]) -> dict[str, Any]:
    """Run one `remote_agent` request on `alias` and return its JSON answer.

    The agent's source travels on stdin (`python3 -`), the request as the
    single argument; nothing has to be installed on the remote beyond python.
    """
    source = inspect.getsource(remote_agent)
    command = "python3 - " + shlex.quote(json.dumps(request))
    result = shell.run(ssh_args(alias, command), stdin=source)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ShellError(
            f"remote agent on {alias} answered no JSON ({exc}): {result.stdout[:500]!r}"
        ) from exc
