"""Devin CLI's `permissions` lists, merged across levels (ADR-0073).

`allow`, `deny` and `ask` lists sit in `config.json` at the user level and in
`.devin/config.json` and `.devin/config.local.json` at each project level.
Devin merges them rather than letting one level replace another, so an allow
is an exposure only if it is still effective after the merge:

- **a deny anywhere in its chain** that covers it suppresses it — "a deny
  rule always wins";
- **an ask at a higher-precedence level** that covers it suppresses it — "a
  lower-level allow cannot override an ask from a higher-precedence level".
  At its own level an allow at least as specific as an ask wins, and a
  lower-precedence ask cannot reach it.

Two entry shapes are about subjects a posture rule covers; everything else
(`Read(…)`, `Write(…)`, `Fetch(…)`, other tool names) is carried through and
reported by nothing:

- `Exec(<prefix>)` approves a command prefix, matched word by word; the bare
  tool name `exec` approves every command.
- `mcp__<server>__<tool>`, `mcp__<server>__*` and `mcp__*` approve MCP tools.

Imports nothing from `tools.posture` itself: the rules import this module
while that package is still initialising.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.parsers import devin_config

# The manifest key a Devin permissions view travels under. The value is a
# `DevinPermissions` object, which raw JSON cannot produce, so a settings file
# carrying the same key is never mistaken for this view.
MANIFEST_KEY = "devin_permissions"

_EXEC_TOOL = "exec"
_MCP_PREFIX = "mcp__"


@dataclass(frozen=True)
class PermissionLevel:
    """One file's lists, in the order Devin ranks them."""

    path: Path
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectiveAllow:
    entry: str
    path: Path


@dataclass(frozen=True)
class DevinPermissions:
    """The allows still effective after the merge, each with its own file."""

    allows: tuple[EffectiveAllow, ...]


def read_level(path: Path) -> PermissionLevel | None:
    """The `permissions` lists of one config file, or `None` when the file is
    absent, unreadable, or declares none. Non-string entries are dropped."""
    if not path.is_file():
        return None
    try:
        data = devin_config.load(path)
    except (OSError, ValueError):
        return None
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        return None

    def entries(key: str) -> tuple[str, ...]:
        value = permissions.get(key)
        if not isinstance(value, list):
            return ()
        return tuple(entry for entry in value if isinstance(entry, str))

    return PermissionLevel(
        path=path, allow=entries("allow"), deny=entries("deny"), ask=entries("ask")
    )


def effective_allows(
    chain: Sequence[PermissionLevel], own: Sequence[PermissionLevel]
) -> list[EffectiveAllow]:
    """The allows of `own` that survive `chain`, ordered lowest precedence
    first. Every level in `own` must also be in `chain`."""
    denies = [rule for level in chain for rule in level.deny]
    out: list[EffectiveAllow] = []
    for level in own:
        rank = chain.index(level)
        higher_asks = [rule for above in chain[rank + 1 :] for rule in above.ask]
        for entry in level.allow:
            if any(covers(rule, entry) for rule in denies):
                continue
            if any(covers(rule, entry) for rule in higher_asks):
                continue
            out.append(EffectiveAllow(entry=entry, path=level.path))
    return out


def exec_prefix(entry: str) -> tuple[str, ...] | None:
    """The command words an `Exec(…)` entry approves, `()` for bare `exec`
    (every command), or `None` when the entry is not a command approval."""
    if entry == _EXEC_TOOL:
        return ()
    if not (entry.startswith("Exec(") and entry.endswith(")")):
        return None
    words = tuple(entry[len("Exec(") : -1].split())
    return words or None


def mcp_server(entry: str) -> str | None:
    """The server an `mcp__…` entry approves tools on, `*` for every server,
    or `None` when the entry is not one of the three documented forms."""
    if not entry.startswith(_MCP_PREFIX):
        return None
    rest = entry[len(_MCP_PREFIX) :]
    if rest == "*":
        return "*"
    server, separator, tool = rest.partition("__")
    if not separator or not server or not tool:
        return None
    return server


def covers(rule: str, entry: str) -> bool:
    """Whether `rule` matches everything `entry` approves."""
    rule_words, entry_words = exec_prefix(rule), exec_prefix(entry)
    if rule_words is not None and entry_words is not None:
        return entry_words[: len(rule_words)] == rule_words
    if mcp_server(rule) is not None and mcp_server(entry) is not None:
        if rule.endswith("*"):
            return entry.startswith(rule[:-1])
        return entry == rule
    return False
