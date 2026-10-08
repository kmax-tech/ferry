# ferry

Carry a committed repo to a remote machine, run a command there detached,
bring the results back. The laptop's git is the source of truth; the remote
is a mirror that proves it holds exactly one commit before anything starts.

```
ferry setup   [--host H]              push, then venv + install on the remote
ferry push    [--host H] [--dirty]    clean check, rsync, remote verify
ferry run     [--host H] -- CMD...    push, then start CMD detached
ferry status  [--host H]              every job the remote recorded, with state
ferry logs    [--host H] JOB [-f]     tail a job's log
ferry pull    [--host H] [PATH...]    rsync result paths back (never deletes)
```

## The cycle

```bash
git commit -m "..."                               # 1. the laptop commits
ferry run -- .venv/bin/python experiments/42/measure.py   # 2. push + start; prints the job id
ferry status                                      # 3. running / exit N / lost
ferry logs 20261008-231500 -f                     #    follow the output
ferry pull                                        # 4. results back (paths from ferry.toml)
git add experiments/42/runs && git commit         # 5. the laptop commits the results
```

Three checks guard every `run` and `push`:

1. **Laptop tree clean** — tracked, staged and untracked. A dirty tree is
   refused; `--dirty` pushes anyway and says so, and the run then belongs
   to no commit.
2. **rsync with `--delete`**, including `.git/`, so the remote holds nothing
   the commit does not — except the configured `exclude` paths (the venv,
   result directories), which survive on the remote.
3. **Remote verify** — the remote reports its HEAD and every `git status`
   line the excludes do not cover. A different HEAD or a leftover file
   refuses the start with the evidence in the message.

Commits happen only on the laptop. Nothing on the remote is edited by hand;
a hotfix is committed locally and pushed with the next `ferry run`.

## Install (once per person)

ferry runs on the laptop, not in the repos it carries, so it is installed
once, not pinned per project:

```bash
git clone <this repo> ~/projects/ferry
python3 -m venv ~/.venvs/ferry && ~/.venvs/ferry/bin/pip install -e ~/projects/ferry
ln -s ~/.venvs/ferry/bin/ferry ~/.local/bin/ferry      # or any directory on PATH
```

The remote needs `git`, `rsync` and a `python3` (3.8 or newer); nothing of
ferry is installed there — its remote half travels over ssh on every call.
Python 3.12 on the laptop.

## Configure (once per repo): `ferry.toml`

Committed in the repo root, found by searching upward from the current
directory. No secrets: a host is an ssh alias, and user, hostname, key and
jump host stay in `~/.ssh/config`.

```toml
default = "lab1"                               # host when --host is absent
exclude = [".venv", "output", "mlflow.db"]       # rsync excludes, both directions
pull = ["output"]                                # what `ferry pull` brings back
install = ".venv/bin/pip install -r requirements.txt"   # `ferry setup`, run in path

[hosts.lab1]
ssh = "lab1"                                   # Host alias in ~/.ssh/config
path = "~/projects/my-repo"            # the repo on the remote
python = "python3"                               # optional: interpreter for the venv
```

Every key is checked; an unknown or missing one raises with its name.
`.ferry/` (jobs and logs on the remote) is always excluded.

Files git ignores (`data/raw/`, a model cache) are not excluded by default:
rsync carries them along, and the remote verify does not count them as
leftovers because git ignores them there too. Exclude what the remote
produces (results, databases) and what is platform-specific (the venv).

And in `~/.ssh/config`:

```
Host lab1
    HostName <the machine's hostname>
    User <your login there>
```

## The environment on the remote

`ferry setup` creates `.venv` with the host's `python` if it is missing and
runs the `install` command in the repo; pip streams to the terminal. Run it
again whenever the lockfile changes — `ferry run` does not install. The
venv is never rsynced (it is platform-specific), which is why `.venv` is in
`exclude`.

## Jobs

`ferry run` starts the command through a wrapper shell in its own session
with stdin closed, output to `.ferry/logs/<job>.log`, and the exit code to
`.ferry/logs/<job>.rc` when it ends. `ferry status` reads `.ferry/jobs.tsv`:

| state | meaning |
|---|---|
| `running` | the wrapper's pid is alive |
| `exit N` | the command ended with code N |
| `lost` | no pid and no exit code: killed, or the machine rebooted |

The command sees `LABKIT_DETACHED=1` (labkit's runlog accepts an external
detach) and `FERRY_JOB=<job>`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
git config core.hooksPath .githooks      # ruff, format, pyright, pytest before every commit
```

ssh and rsync are not under test; the command layer is tested against a
recording `FakeShell`, the remote agent against a temporary git repo.
