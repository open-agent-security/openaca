"""Which `(kind, config_dir)` pairs are legal, below the command layer.

`scan endpoint` and `bom endpoint` both resolve installed agents from a config
directory, and so does `collect_installed_agents` — so the rules governing which
pairs are legal belong to the collection call rather than to the commands
wrapping it. A consumer that cannot reach
them does not merely lose error messages, it gets different behaviour: an
unknown kind silently collects nothing, and a refused root override is silently
ignored so a *different directory than the caller named* is read.

`tools/cli_kind.py` is the command-line adapter over this module: it translates
`KindSelectionError` into the `click.ClickException` it has always raised, so
every message and exit code is unchanged.
"""

from __future__ import annotations

from pathlib import Path

# Not `from tools.agent_kinds import REGISTRY` — that would freeze the choice
# list (and the validation below) at this module's import time, ahead of any
# test that swaps in a synthetic registry via `monkeypatch.setattr`. Reading
# `agent_kinds.REGISTRY`/`agent_kinds.kind_for` through the module keeps both
# live against whatever is currently registered.
from tools import agent_kinds


class KindSelectionError(Exception):
    """An illegal `(kind, config_dir)` pair. Carries the message verbatim."""


def validate_kind_selection(kind: str | None, config_dir: Path | None) -> None:
    """Validate a kind selection and its pairing with a config root.

    A config root alone is ambiguous once more than one kind exists — a hard
    error, never a silent arbitration toward one kind's root. A named kind may
    additionally refuse the override outright (ADR-0054).
    """
    if kind is not None:
        try:
            agent_kinds.kind_for(kind)
        except KeyError:
            known = ", ".join(sorted(k.id for k in agent_kinds.REGISTRY))
            raise KindSelectionError(f"unknown agent kind {kind!r}; known kinds: {known}") from None
    if config_dir is not None and kind is None:
        raise KindSelectionError(
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
            raise KindSelectionError(f"--config-dir is not supported for --kind {kind}: {refusal}.")
