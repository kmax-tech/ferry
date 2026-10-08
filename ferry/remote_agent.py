"""ferry's remote half: one file, sent over ssh as `python3 - '<json>'`.

The laptop pipes this module's source into the remote interpreter and passes
one JSON request as the single argument; the agent answers with one JSON
object on stdout. Everything it prints while working goes to stderr, which
ssh forwards live, so the JSON on stdout stays parseable.

It imports nothing from `ferry` because the remote has no ferry installed,
and it is written for the oldest python a lab machine still carries (3.8:
no `match`, no `X | Y`, no `list[str]` at runtime). Keep it that way.

Requests (field `cmd`), all with `path` = the repo on the remote:

    verify   {head, exclude}          the tree is exactly `head`, nothing else
    setup    {python, install}        venv if missing, then the install command
    start    {command, commit}        run detached, record in .ferry/jobs.tsv
    status   {}                       every recorded job with its state

State the agent keeps, all under `<path>/.ferry/` (never rsynced):

    jobs.tsv            one line per start: job, started, pid, commit, command
    logs/<job>.log      stdout+stderr of the command
    logs/<job>.rc       the exit code, written when the command ends
"""

import datetime
import fnmatch
import json
import os
import subprocess
import sys
from typing import Any, Dict, Iterable, List

JOBS_FILE = ".ferry/jobs.tsv"
LOGS_DIR = ".ferry/logs"
#: paths rsync never carries and the verify step never counts as leftovers
ALWAYS_EXCLUDED = (".ferry",)


def ignored_by(path: str, patterns: Iterable[str]) -> bool:
    """True if `path` or one of its parent directories matches a pattern.

    rsync excludes name a path relative to the repo root (`.venv`,
    `experiments/*/runs`); a leftover file deep inside such a directory is
    covered by the pattern on its ancestor.
    """
    parts = path.rstrip("/").split("/")
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        for pattern in patterns:
            if fnmatch.fnmatchcase(prefix, pattern.rstrip("/")):
                return True
    return False


def leftovers(porcelain: str, exclude: Iterable[str]) -> List[str]:
    """`git status --porcelain` lines NOT covered by the rsync excludes.

    After `rsync --delete` the only files allowed to differ from the commit
    are the excluded ones (the venv, result directories). Anything else means
    the remote tree is not the commit the laptop sent.
    """
    patterns = list(exclude) + list(ALWAYS_EXCLUDED)
    out: List[str] = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        path = line[3:]
        if " -> " in path:  # rename: "R  old -> new"
            path = path.split(" -> ")[-1]
        if not ignored_by(path, patterns):
            out.append(line)
    return out


def git(args: List[str], cwd: str) -> str:
    """Run git in `cwd`; a non-zero exit raises with git's own message."""
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "git %s failed in %s: %s" % (" ".join(args), cwd, proc.stderr.strip())
        )
    return proc.stdout


def verify(path: str, head: str, exclude: List[str]) -> Dict[str, Any]:
    """Compare the remote tree with the commit the laptop pushed."""
    remote_head = git(["rev-parse", "HEAD"], path).strip()
    porcelain = git(["status", "--porcelain", "--untracked-files=all"], path)
    return {
        "head": remote_head,
        "head_matches": remote_head == head,
        "leftovers": leftovers(porcelain, exclude),
    }


def setup(path: str, python: str, install: str) -> Dict[str, Any]:
    """Create `.venv` with `python` if missing, then run `install` in `path`.

    The install command's output streams to stderr so the person sees pip
    working; only the verdict goes back as JSON.
    """
    venv_python = os.path.join(path, ".venv", "bin", "python")
    created = False
    if not os.path.exists(venv_python):
        subprocess.run(
            [python, "-m", "venv", ".venv"], cwd=path, check=True, stdout=sys.stderr
        )
        created = True
    proc = subprocess.run(
        ["sh", "-c", install], cwd=path, check=False, stdout=sys.stderr
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "install command failed (exit %d): %s" % (proc.returncode, install)
        )
    version = subprocess.run(
        [venv_python, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return {"venv_created": created, "python": version}


def _new_job_id(path: str) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    job = stamp
    n = 1
    while os.path.exists(os.path.join(path, LOGS_DIR, job + ".log")):
        n += 1
        job = "%s-%d" % (stamp, n)
    return job


def start(path: str, command: str, commit: str) -> Dict[str, Any]:
    """Run `command` detached (own session, stdin closed, output to a log).

    A wrapper shell runs the command in a subshell (so the command's own
    `exit` cannot skip the bookkeeping) and writes its exit code to the
    `.rc` file when it ends: a process that is gone without an `.rc` file
    died without reporting (killed, machine rebooted) and `status` says so.
    `LABKIT_DETACHED=1` tells labkit's runlog that the detach already
    happened; `FERRY_JOB` lets the command name its own run after the job.
    """
    os.makedirs(os.path.join(path, LOGS_DIR), exist_ok=True)
    job = _new_job_id(path)
    log = os.path.join(path, LOGS_DIR, job + ".log")
    rc = os.path.join(path, LOGS_DIR, job + ".rc")
    env = dict(os.environ, LABKIT_DETACHED="1", FERRY_JOB=job)
    script = "(\n%s\n)\nrc=$?\nprintf '%%s\\n' \"$rc\" > %s\nexit $rc\n" % (
        command,
        _sh_quote(rc),
    )
    with open(log, "w") as log_handle:
        proc = subprocess.Popen(
            ["sh", "-c", script],
            cwd=path,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    started = datetime.datetime.now().isoformat(timespec="seconds")
    line = "\t".join([job, started, str(proc.pid), commit, command.replace("\t", " ")])
    with open(os.path.join(path, JOBS_FILE), "a") as jobs:
        jobs.write(line + "\n")
    return {"job": job, "pid": proc.pid, "log": log}


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def job_state(path: str, job: str, pid: int) -> str:
    """`exit N` when the wrapper reported, `running` while the pid lives, else `lost`."""
    rc = os.path.join(path, LOGS_DIR, job + ".rc")
    if os.path.exists(rc):
        with open(rc) as handle:
            return "exit " + handle.read().strip()
    if _alive(pid):
        return "running"
    return "lost"


def status(path: str) -> Dict[str, Any]:
    """Every job in jobs.tsv, newest last, with its state."""
    jobs_path = os.path.join(path, JOBS_FILE)
    if not os.path.exists(jobs_path):
        return {"jobs": []}
    jobs: List[Dict[str, Any]] = []
    with open(jobs_path) as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if not line:
                continue
            fields = line.split("\t")
            if len(fields) != 5:
                raise RuntimeError("malformed line in %s: %r" % (JOBS_FILE, line))
            job, started, pid, commit, command = fields
            jobs.append(
                {
                    "job": job,
                    "started": started,
                    "pid": int(pid),
                    "commit": commit,
                    "command": command,
                    "state": job_state(path, job, int(pid)),
                }
            )
    return {"jobs": jobs}


def handle(request: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch one request; an unknown command is an error, not a no-op."""
    cmd = request["cmd"]
    path = os.path.expanduser(request["path"])
    if cmd == "verify":
        return verify(path, request["head"], list(request["exclude"]))
    if cmd == "setup":
        os.makedirs(path, exist_ok=True)
        return setup(path, request["python"], request["install"])
    if cmd == "start":
        return start(path, request["command"], request["commit"])
    if cmd == "status":
        return status(path)
    raise RuntimeError("unknown agent command: %r" % cmd)


def main() -> None:
    """Entry point when piped into `python3 - '<json>'` on the remote."""
    if len(sys.argv) != 2:
        raise SystemExit("remote_agent: expected exactly one JSON argument")
    request = json.loads(sys.argv[1])
    print(json.dumps(handle(request)))


if __name__ == "__main__":
    main()
