"""The `openaca` Click group, published for a consumer to mount under its own
command line.

A consumer registers the command objects it wants in its own Click group and
gets OpenACA's real commands — the same options, the same output, the same exit
codes — rendered under its own program name, because Click builds a usage line
from the invocation rather than from where a command was defined. That is the
alternative to either reimplementing a command or importing `tools.cli`, which
is internal and out of contract (ADR-0028).

Promised: `main` is a `click.Group`, and `scan`, `bom` and `policy` are
reachable on it by those names.

Not promised: the internal structure of any command, its option set, or its
output format — those are the CLI's contract to its users, a looser thing than
a library API by design, so a flag can be added and a consumer that
re-registers the command inherits it for free rather than breaking. Nor is
OpenACA obliged to keep any particular command in the group: a consumer
registering a name that later disappears gets an import-time or lookup-time
failure, which is the right moment to find out.

The CLI group is for offering OpenACA's commands to a person; `openaca.core` is
for a program calling OpenACA. A caller that already holds a policy document in
memory wants the second, because constructing flag strings for arguments a
function takes directly turns a renamed parameter into a runtime failure
instead of a type error at upgrade time.
"""

from tools.cli import main

__all__ = ["main"]
