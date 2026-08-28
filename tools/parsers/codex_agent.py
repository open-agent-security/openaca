"""Codex subagents, `<root>/agents/*.toml` (plan 043 Task 2).

Codex declares subagents as TOML tables — `name`, `description`,
`developer_instructions` — where Claude Code and Cursor use markdown with YAML
frontmatter. That is the one place Codex's component *formats* diverge from
Claude Code's.

The emitted ref shape deliberately does **not** diverge. Identity stays in the
`claude-agent/` space that `claude_command_agent` already mints, so a subagent
reachable from two kinds keys identically in both graphs (ADR-0045). The prefix
reads oddly for a Codex-owned file, and that is the same accepted cosmetic cost
the Cursor spec records for its own commands and hooks: renaming it would be a
breaking cross-BOM identity change affecting every existing BOM, to no benefit.

Subagents are **user-scope only**. `.codex/agents` has zero references in the
audited binary, so there is no project-scoped counterpart and no `scope_owner`
to qualify identity with.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from tools.component_ref import ComponentRef

# Mirrors `claude_command_agent.parse_file`'s `ecosystem = f"claude-{kind}"`
# for `kind="agent"`. Not a Codex-specific space, deliberately — see module
# docstring.
_ECOSYSTEM = "claude-agent"

_EXTENSIONS = (".toml",)


def parse(path: Path, *, strict: bool = False) -> list[ComponentRef]:
    """Emit one `agent` ref for a single subagent file.

    Malformed TOML raises `tomllib.TOMLDecodeError` regardless of `strict`, so
    `parse_repo_registry_counts` records a `parse_failed` rather than reporting
    a clean, empty, successfully-parsed unit for a file that dropped data.
    `strict` is accepted for registry-signature parity.
    """
    if not path.is_file() or path.suffix not in _EXTENSIONS:
        return []

    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        data = {}

    raw_name = data.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else path.stem

    extra: dict = {"scope_owner": None, "component_type": "agent"}
    description = data.get("description")
    if isinstance(description, str):
        extra["description"] = description

    return [
        ComponentRef(
            name=name,
            component_identity=f"{_ECOSYSTEM}/{name}",
            source_manifest=str(path),
            source_locator="$",
            extra=extra,
        )
    ]
