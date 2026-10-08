# AGENTS.md — ferry

Entry file for any coding agent working in this repo (Claude Code reads it
through the `CLAUDE.md` symlink).

## What this is

ferry carries a committed repo to a remote machine, runs a command there
detached, and brings the results back. Its one claim:

> What runs on the remote is exactly the commit the laptop is at — or ferry
> refused to start it.

`README.md` is the reference: the verbs, the three checks, `ferry.toml`,
the job states. `ferry/cli.py` holds the verbs, `ferry/remote_agent.py`
the half that runs on the remote, `ferry/config.py` the toml, `ferry/shell.py`
the one door to git, rsync and ssh.

## Setup and tests

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

Before a commit: `.venv/bin/ruff check . && .venv/bin/ruff format --check . &&
.venv/bin/pyright` — all three clean (docs/coding-style.md CS-008 Run the
linter and the type checker), and the suite green. The versioned hook
`.githooks/pre-commit` runs all four; turn it on once per clone with
`git config core.hooksPath .githooks`.

## Rules

- **stdlib only.** ferry is installed once per person and must not pull
  anything into the environments it carries. Dev tools go in the `dev` extra.
- **`remote_agent.py` is sent over ssh as source.** It imports nothing from
  `ferry`, and it is written for Python 3.8 (no `match`, no `X | Y`, no
  `list[str]` at runtime) because that is what an old lab machine carries.
- **Fail loud.** A refusal names the evidence (the dirty lines, both hashes);
  no silent fallback, no bare `except`.
- **One door outside.** Every git, rsync and ssh call goes through
  `Shell.run`, so the command layer stays testable with `FakeShell`.
- **Never delete on pull.** `ferry pull` carries results back; it never
  passes `--delete`. Push does, guarded by the excludes.
- **Language.** Code, identifiers, docstrings, commit messages: English.
  Talk to the user in German.
- **One commit per step**, the message says why. Do not push, tag or
  release; report what is ready to commit.
- `docs/coding-style.md` is a vendored copy; edit the master, not this one.
