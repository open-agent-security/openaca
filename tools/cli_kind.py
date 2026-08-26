"""Shared `--kind` option and its `--config-dir` pairing rule.

`scan endpoint`, `bom endpoint`, and `remote sync endpoint` all resolve
installed agents from a config directory, and all three become ambiguous the
moment a second kind is registered: an unqualified `--config-dir` could name
either kind's root. One module owns the option and the validation so the
three CLIs enforce byte-identical behavior instead of drifting.
"""

from __future__ import annotations

from pathlib import Path

import click

# Not `from tools.agent_kinds import REGISTRY` — that would freeze the choice
# list (and the validation below) at this module's import time, ahead of any
# test that swaps in a synthetic registry via `monkeypatch.setattr`. Reading
# `agent_kinds.REGISTRY`/`agent_kinds.kind_for` through the module keeps both
# live against whatever is currently registered.
from tools import agent_kinds

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
    if kind is not None:
        try:
            agent_kinds.kind_for(kind)
        except KeyError:
            known = ", ".join(sorted(k.id for k in agent_kinds.REGISTRY))
            raise click.ClickException(
                f"unknown agent kind {kind!r}; known kinds: {known}"
            ) from None
    if config_dir is not None and kind is None:
        raise click.ClickException(
            "--config-dir requires --kind: with more than one installed agent "
            "kind, --config-dir alone cannot say which kind's root it names."
        )
    if config_dir is not None and kind is not None:
        # A root override is a per-kind capability, not a property of the flag
        # (ADR-0054): a kind qualifies only where naming a root fully specifies
        # the target. A refusing kind names its own reason rather than
        # inheriting a generic one.
        refusal = agent_kinds.kind_for(kind).root_override_refusal
        if refusal is not None:
            raise click.ClickException(
                f"--config-dir is not supported for --kind {kind}: {refusal}."
            )
