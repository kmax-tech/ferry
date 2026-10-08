"""ferry.toml: lookup, validation, defaults."""

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ferry import config

GOOD = textwrap.dedent(
    """
    default = "lab1"
    exclude = [".venv", "output"]
    pull = ["output"]
    install = ".venv/bin/pip install -r requirements.txt"

    [hosts.lab1]
    ssh = "lab1"
    path = "~/projects/my-repo"

    [hosts.box]
    ssh = "box"
    path = "~/fd"
    python = "python3.12"
    """
)


def write(tmp: str, text: str) -> Path:
    path = Path(tmp) / config.FILENAME
    path.write_text(text, encoding="utf-8")
    return path


class TestLoad(unittest.TestCase):
    def test_full_file(self):
        with TemporaryDirectory() as tmp:
            cfg = config.load(write(tmp, GOOD))
        self.assertEqual(cfg.default, "lab1")
        self.assertEqual(cfg.exclude, (".venv", "output"))
        self.assertEqual(cfg.pull, ("output",))
        self.assertEqual(cfg.install, ".venv/bin/pip install -r requirements.txt")
        self.assertEqual(cfg.host(None).ssh, "lab1")
        self.assertEqual(cfg.host(None).python, "python3")
        self.assertEqual(cfg.host("box").python, "python3.12")

    def test_single_host_needs_no_default(self):
        with TemporaryDirectory() as tmp:
            cfg = config.load(write(tmp, '[hosts.one]\nssh = "one"\npath = "~/x"\n'))
        self.assertEqual(cfg.default, "one")
        self.assertIsNone(cfg.install)
        self.assertEqual(cfg.exclude, ())

    def test_two_hosts_need_default(self):
        text = '[hosts.a]\nssh="a"\npath="~/x"\n[hosts.b]\nssh="b"\npath="~/y"\n'
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(config.ConfigError, "`default` is required"):
                config.load(write(tmp, text))

    def test_unknown_key_is_refused(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(config.ConfigError, "unknown key.*excludes"):
                config.load(
                    write(tmp, 'excludes = [".venv"]\n[hosts.a]\nssh="a"\npath="~/x"\n')
                )

    def test_unknown_host_key_is_refused(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                config.ConfigError, r"\[hosts.a\] unknown key.*hostname"
            ):
                config.load(
                    write(tmp, '[hosts.a]\nssh="a"\npath="~/x"\nhostname="h"\n')
                )

    def test_missing_path_names_the_field(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                config.ConfigError, r"\[hosts.a\] needs .* `path`"
            ):
                config.load(write(tmp, '[hosts.a]\nssh="a"\n'))

    def test_unknown_host_lists_known(self):
        with TemporaryDirectory() as tmp:
            cfg = config.load(write(tmp, GOOD))
        with self.assertRaisesRegex(config.ConfigError, "host 'nope'.*box, lab1"):
            cfg.host("nope")


class TestFind(unittest.TestCase):
    def test_searches_upward(self):
        with TemporaryDirectory() as tmp:
            top = write(tmp, GOOD)
            deep = Path(tmp) / "experiments" / "42"
            deep.mkdir(parents=True)
            self.assertEqual(config.find(deep).resolve(), top.resolve())

    def test_missing_names_the_start(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(config.ConfigError, "not found"):
                config.find(Path(tmp))


if __name__ == "__main__":
    unittest.main()
