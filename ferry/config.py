"""`ferry.toml`: where a repo's long runs execute.

One file per repo, committed, found by searching upward from the current
directory (the same lookup labkit uses for `models.toml`). It holds no
secrets: a host is named by an ssh alias, and user, hostname, key and jump
host stay in each person's `~/.ssh/config`.

    default = "lab1"                      # host used when --host is absent
    exclude = [".venv", "output"]           # rsync excludes, both directions
    pull = ["output"]                       # what `ferry pull` brings back
    install = ".venv/bin/pip install -r requirements.txt"   # `ferry setup`, run in path

    [hosts.lab1]
    ssh = "lab1"                          # Host alias in ~/.ssh/config
    path = "~/projects/my-repo"   # the repo on the remote
    python = "python3"                      # optional: interpreter for the venv

Every key is checked: an unknown key or a missing required one raises with
the field's name, because a typo that silently means "default" sends a
night's run to the wrong machine.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

FILENAME = "ferry.toml"

_TOP_KEYS = {"default", "exclude", "pull", "install", "hosts"}
_HOST_KEYS = {"ssh", "path", "python"}


class ConfigError(ValueError):
    """A malformed or missing `ferry.toml`; the message names the field."""


@dataclass(frozen=True)
class Host:
    """One remote machine: an ssh alias and the repo's path there."""

    name: str
    ssh: str
    path: str
    python: str


@dataclass(frozen=True)
class Config:
    """The parsed `ferry.toml` with the repo root it was found in."""

    root: Path
    default: str
    hosts: dict[str, Host]
    exclude: tuple[str, ...]
    pull: tuple[str, ...]
    install: str | None

    def host(self, name: str | None) -> Host:
        """The named host, or the default when `name` is None."""
        key = name or self.default
        if key not in self.hosts:
            known = ", ".join(sorted(self.hosts)) or "none"
            raise ConfigError(f"host {key!r} is not in {FILENAME} (hosts: {known})")
        return self.hosts[key]


def find(start: Path) -> Path:
    """The nearest `ferry.toml` at or above `start`; raises when there is none."""
    start = start.resolve()
    for directory in [start, *start.parents]:
        candidate = directory / FILENAME
        if candidate.is_file():
            return candidate
    raise ConfigError(f"{FILENAME} not found (searched upward from {start})")


def _str_list(table: dict[str, object], key: str) -> tuple[str, ...]:
    raw = table.get(key, [])
    if not isinstance(raw, list):
        raise ConfigError(f"{FILENAME}: `{key}` must be a list of strings")
    items = cast(list[object], raw)
    if not all(isinstance(x, str) for x in items):
        raise ConfigError(f"{FILENAME}: `{key}` must be a list of strings")
    return tuple(str(x) for x in items)


def _str(table: dict[str, object], key: str, where: str) -> str:
    raw = table.get(key)
    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{FILENAME}: {where} needs a non-empty string `{key}`")
    return raw


def _host(name: str, raw: object) -> Host:
    """One `[hosts.<name>]` table, every key checked."""
    where = f"[hosts.{name}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{FILENAME}: {where} must be a table")
    table = cast(dict[str, object], raw)
    unknown = set(table) - _HOST_KEYS
    if unknown:
        raise ConfigError(f"{FILENAME}: {where} unknown key(s) {sorted(unknown)}")
    python = table.get("python", "python3")
    if not isinstance(python, str) or not python:
        raise ConfigError(f"{FILENAME}: {where} `python` must be a non-empty string")
    return Host(
        name=name,
        ssh=_str(table, "ssh", where),
        path=_str(table, "path", where),
        python=python,
    )


def load(path: Path) -> Config:
    """Parse and validate `ferry.toml` at `path`."""
    data: dict[str, object] = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"{FILENAME}: unknown key(s) {sorted(unknown)}")
    hosts_raw = data.get("hosts")
    if not isinstance(hosts_raw, dict) or not hosts_raw:
        raise ConfigError(f"{FILENAME}: needs at least one [hosts.<name>] table")
    hosts = {
        str(name): _host(str(name), table)
        for name, table in cast(dict[str, object], hosts_raw).items()
    }
    default = data.get("default")
    if default is None:
        if len(hosts) != 1:
            raise ConfigError(
                f"{FILENAME}: `default` is required with more than one host"
            )
        default = next(iter(hosts))
    if not isinstance(default, str) or default not in hosts:
        raise ConfigError(f"{FILENAME}: `default` must name one of {sorted(hosts)}")
    install = data.get("install")
    if install is not None and (not isinstance(install, str) or not install):
        raise ConfigError(f"{FILENAME}: `install` must be a non-empty string")
    return Config(
        root=path.parent,
        default=default,
        hosts=hosts,
        exclude=_str_list(data, "exclude"),
        pull=_str_list(data, "pull"),
        install=install,
    )
