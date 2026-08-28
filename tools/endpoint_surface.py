"""Endpoint-mode surface descriptors (ADR-0057).

Endpoint mode inspects a runtime *installed on a machine*, where globbing does
not work: "which plugins does this agent have?" is answered by reading the
runtime's own install bookkeeping, not by files lying around.

ADR-0053 forked that reading per kind, correctly, because Cursor had no
analogue for any of Claude Code's records. Codex has an analogue for some but
not all, so ADR-0057 splits it:

- **Shared, and driven by this descriptor** — project skills and direct
  components. Same procedure, different directory names.
- **Forked per kind** — plugin acquisition and remote MCP. Claude Code opens a
  lockfile and settings and *intersects* them; Codex reads a TOML table and
  *enumerates a cache directory*. Different numbers of files, different order,
  different combining operation. Those are not one procedure with different
  labels, and no field here describes them.

**This descriptor carries data only.** No field may be a `Callable`, and no
field may be a mode discriminator selecting between differing control flows
inside a shared function — a `Literal["intersect", "enumerate"]` switch is
branching wearing a data costume, and ADR-0057 rejects it by name. Where a kind
has no counterpart for a shared branch, the field carries an absence (`None`,
`()`, `False`), never a switch.

Imports nothing from `tools.graph_build` (which imports this module) and
nothing from `tools.agent_kinds` (ADR-0044's one-way dependency).
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.parsers.claude_command_agent import Kind

# `(directory_name, component_kind)` — the markdown command/agent surfaces
# seeded directly under an install root. `Kind` rather than `str` so a typo
# fails at type-check instead of at the parser.
CommandAgentDirs = tuple[tuple[str, Kind], ...]


@dataclass(frozen=True)
class EndpointSurface:
    """Names a kind reads out of an installed runtime's tree.

    - `project_config_dir`: the project-scoped config directory (`.claude`).
      Project skills live at
      `<project>/<project_config_dir>/<project_skills_subdir>/`, and project
      commands/agents at `<project>/<project_config_dir>/<dir>/`.
    - `direct_skills_dir`: install-root skills subdirectory, or `None` when the
      kind seeds no direct skills.
    - `direct_command_agent_dirs`: `(dirname, kind)` pairs parsed as **markdown**
      command/agent files. A kind whose agents are not markdown supplies `()`
      and seeds them itself — the format difference is not expressible here,
      and pretending otherwise would put a parser choice in a data field.
    - `seeds_project_command_agents`: whether project-scoped commands/agents
      are seeded at all.
    - `seeds_hooks`: whether settings-scoped hooks are seeded. Codex declares
      hooks in a repo (`.codex/hooks.json`), not at its endpoint, so this is
      `False` there — an absence, not a switch between two behaviors.
    """

    project_config_dir: str
    project_skills_subdir: str
    direct_skills_dir: str | None
    direct_command_agent_dirs: CommandAgentDirs
    seeds_project_command_agents: bool
    seeds_hooks: bool


# Transcribed verbatim from the literals in `_seed_endpoint` and
# `_seed_direct_components`. Changing any value here changes Claude Code's
# shipped endpoint output, which `tests/test_endpoint_surface.py` gates.
CLAUDE_CODE_ENDPOINT = EndpointSurface(
    project_config_dir=".claude",
    project_skills_subdir="skills",
    direct_skills_dir="skills",
    direct_command_agent_dirs=(("commands", "command"), ("agents", "agent")),
    seeds_project_command_agents=True,
    seeds_hooks=True,
)


# Codex's endpoint surface. Only the shared branches appear here — plugin
# acquisition and remote MCP are forked (ADR-0057) and have no fields.
CODEX_ENDPOINT = EndpointSurface(
    project_config_dir=".codex",
    project_skills_subdir="skills",
    direct_skills_dir="skills",
    # Codex's subagents are `agents/*.toml`, not markdown, so they cannot go
    # through the shared markdown command/agent walk. The format difference is
    # not a name this descriptor could carry, so Codex seeds them itself.
    # Codex has no commands surface at all.
    direct_command_agent_dirs=(),
    # No project-scoped commands or agents: `.codex/agents` has zero references
    # in the audited binary.
    seeds_project_command_agents=False,
    # Codex declares hooks in a repo (`.codex/hooks.json`), never at its
    # endpoint. An absence, not a switch between two behaviors.
    seeds_hooks=False,
)
