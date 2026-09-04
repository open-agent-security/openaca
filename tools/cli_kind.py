"""Shared `--kind` option and its `--config-dir` pairing rule.

`scan endpoint` and `bom endpoint` both resolve installed agents from a config
directory, and both become ambiguous the moment a second kind is registered: an
unqualified `--config-dir` could name either kind's root. One module owns the
option and the validation so both CLIs enforce byte-identical behavior instead
of drifting.

The rule itself lives in `tools/kind_selection.py`, below the command layer, so
`collect_installed_agents` and its facade callers reach the same check rather
than a copy of it. What stays here is the command-line half: the option, and
the translation of `KindSelectionError` into the `click.ClickException` this
module has always raised — same message, same exit code.
"""

from __future__ import annotations

from pathlib import Path

import click

from tools.kind_selection import KindSelectionError, validate_kind_selection

kind_option = click.option(
    "--kind",
    default=None,
    help=(
        "Limit discovery to one installed agent kind. Required alongside "
        "--config-dir. Omit both to discover every installed kind whose own "
        "default root exists."
    ),
)


def require_kind_for_config_dir(kind: str | None, config_dir: Path | None) -> None:
    """Validate `--kind` and its pairing with `--config-dir`.

    `--config-dir` alone is ambiguous once more than one kind exists — a hard
    error, never a silent arbitration toward one kind's root. A named kind may
    additionally refuse the override outright (ADR-0054).
    """
    try:
        validate_kind_selection(kind, config_dir)
    except KindSelectionError as exc:
        raise click.ClickException(str(exc)) from None
