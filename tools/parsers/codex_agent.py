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


# developers.openai.com/codex/agent-configuration/subagents, "Custom agent
# file schema": "Every standalone custom agent file must define: `name`,
# `description`, `developer_instructions`." Codex itself rejects a file
# missing any of the three (verified against the published schema, not just
# the audited binary) rather than falling back to the filename or omitting
# the field, so a scan that inventoried it anyway would report a component
# Codex never loads.
_REQUIRED_STRING_FIELDS = ("name", "description", "developer_instructions")


def parse(path: Path, *, strict: bool = False) -> list[ComponentRef]:
    """Emit one `agent` ref for a single subagent file.

    Malformed TOML raises `tomllib.TOMLDecodeError` regardless of `strict`, so
    `parse_repo_registry_counts` records a `parse_failed` rather than reporting
    a clean, empty, successfully-parsed unit for a file that dropped data.
    A file present but missing a required field raises `ValueError` the same
    way, for the same reason: Codex does not load it, so this must not report
    a clean parse either. `strict` is accepted for registry-signature parity.
    """
    if not path.is_file() or path.suffix not in _EXTENSIONS:
        return []

    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        data = {}

    for field_name in _REQUIRED_STRING_FIELDS:
        value = data.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"agent file must define a non-empty {field_name!r}")

    name = data["name"]
    extra: dict = {
        "scope_owner": None,
        "component_type": "agent",
        "description": data["description"],
    }

    return [
        ComponentRef(
            name=name,
            component_identity=f"{_ECOSYSTEM}/{name}",
            source_manifest=str(path),
            source_locator="$",
            extra=extra,
        )
    ]


def read_role_layer_description(path: Path) -> str | None:
    """A `[agents."<role>"] config_file` layer's own `description`, if any.

    This is a **different schema** from `parse` above. The published
    "Custom agent file schema" (`name`, `description`, `developer_instructions`
    all required) is documented only for a *standalone* file discovered under
    `agents/`. The configuration reference instead describes `config_file` as
    "a TOML config layer for that role" — the role's identity is already the
    table key, and its description already has a home on the `[agents.*]`
    table itself (`AgentRoleEntry.description`), so neither `name` nor
    `description` is required in the file `config_file` points at. Requiring
    `parse`'s standalone schema here rejected the common case (a layer
    supplying only `developer_instructions`) as if Codex itself would refuse
    it, which is not documented anywhere for this declaration form.

    Malformed TOML still raises `tomllib.TOMLDecodeError`, same as `parse`.
    """
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        return None
    description = data.get("description")
    return description if isinstance(description, str) and description else None
