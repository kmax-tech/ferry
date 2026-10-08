"""ferry — carry a committed repo to a remote machine, run there, bring results back.

The laptop's git is the source of truth. `ferry` refuses to push a dirty
tree, rsyncs the working tree including `.git/`, and before anything starts
the remote half checks that it holds exactly that commit and nothing else.
Configuration is one `ferry.toml` per repo (committed, no secrets); the ssh
alias it names lives in each person's `~/.ssh/config`.
"""

__version__ = "0.1.0"
