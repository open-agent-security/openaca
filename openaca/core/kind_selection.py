"""Facade re-export: kind-selection validation. See ADR-0028.

`collect_installed_agents` takes a kind and a config root, so the rules
governing which pairs are legal are part of that call. `validate_kind_selection`
raises `KindSelectionError` with the message `openaca scan endpoint` shows
today; `tools/cli_kind.py` translates it into a `click.ClickException`, so the
command line is unchanged.

The check is published, not the facts it checks: the kind ids and each kind's
refusal stay internal (`tests/test_core_facade.py` asserts `REGISTRY` and
`kind_for` are absent). Facts would let a consumer rebuild the validation and
phrase its own errors, and the two wordings would then drift apart while both
claimed to describe the same rule.
"""

from tools.kind_selection import KindSelectionError, validate_kind_selection

__all__ = ["KindSelectionError", "validate_kind_selection"]
