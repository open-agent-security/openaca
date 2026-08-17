"""Claude Code's endpoint composition seed.

Moved verbatim from `graph_build._seed_endpoint` (ADR-0044 host-adapter
boundary); the discovery helpers it calls stay in `tools.graph_build` and are
imported from there. Subagents are NOT seeded here — `build_graph`'s endpoint
branch resolves them once across every selected host (a `~/.claude/agents/*.md`
file is readable by more than one host, so no single host's seed can own it).
"""

from __future__ import annotations

from pathlib import Path

from tools.graph import Graph, Node
from tools.graph_build import (
    SourceNormalizer,
    _add_project_skills,
    _add_skills_from_dir,
    _seed_active_plugins,
    _seed_direct_components,
    _seed_remote_mcps,
)
from tools.parsers import claude_install
from tools.parsers.gitignore import load_gitignore_spec
from tools.parsers.settings_layers import load as load_settings


def seed_endpoint(
    graph: Graph,
    target: Node,
    install_root: Path,
    project_root: Path | None,
    normalize: SourceNormalizer,
    *,
    warnings: list[str] | None = None,
) -> None:
    """Endpoint mode: the target's children are seeded from resolved Claude
    config, not a filesystem glob. Recursive descent (the SAME `descend` used
    in repo mode) still applies under each seeded root.

    Three seed surfaces:

    - **Active plugins** (`installed_plugins.json` ∩ settings `enabledPlugins`):
      each becomes a `plugin` child of the target. We then `descend` into the
      plugin's on-disk install path (reusing the repo-mode plugin branch, which
      walks bundled `skills/<name>/` and their dep manifests), and attach the
      plugin's own tier-2 lockfile deps as `package` children of the plugin.
    - **Project skills** under `<project_root>/.claude/skills/...`: reuse the
      repo-mode project-skill discovery as `skill` children of the target.
    - **Remote MCPs** declared in settings `mcpServers` (URLs/commands, nothing
      on disk): `mcp_server` leaf children of the target, no descent.
    - **Other direct components**: install-root direct skills
      (`<install_root>/skills/<name>/`), personal+project commands
      (`commands/`, `.claude/commands/`), and settings-scoped hooks. All
      children of the target (attribution None — direct, not plugin-bundled).
      See `_seed_direct_components`.
    """
    layers = load_settings(install_root, project_root=project_root)
    effective = layers.merged("endpoint")
    by_scope = layers.by_scope()

    plugins_map, lockfile_path, plugin_warnings = claude_install._load_plugins_map(install_root)
    if warnings is not None:
        warnings.extend(plugin_warnings)
    enabled = effective.get("enabledPlugins") or {}
    if isinstance(enabled, dict) and plugins_map is not None and lockfile_path is not None:
        _seed_active_plugins(
            graph,
            target,
            enabled,
            plugins_map,
            lockfile_path,
            layers,
            normalize,
            warnings=warnings,
        )

    if project_root is not None:
        # Project skills are the one endpoint surface the old _walk_project_skill_dirs
        # filtered by the project root's .gitignore (e.g. skills under an ignored
        # .worktrees/). Thread the project root as root_dir so that filtering is
        # preserved; installed-plugin/install-root surfaces stay unfiltered.
        project_skill_spec = load_gitignore_spec(project_root)
        _add_project_skills(
            graph,
            target,
            project_root,
            normalize=normalize,
            project_root=project_root,
            stamp_provenance=True,
            root_dir=project_root,
            root_spec=project_skill_spec,
            hosts=["claude-code"],
        )
        # iterdir() follows symlinks; os.walk (used by iter_unignored_files) does
        # not. Call _add_skills_from_dir explicitly so symlinked skill dirs under
        # <project>/.claude/skills/ are discovered — parity with the old
        # _walk_project_skill_dirs path that called _walk_skill_dir (iterdir-based)
        # before iter_unignored_files. _add_child dedup collapses non-symlink dupes.
        # stamp_provenance matches _parse_direct_skill, which both old project-skill
        # walks shared.
        _add_skills_from_dir(
            graph,
            target,
            project_root / ".claude" / "skills",
            normalize=normalize,
            project_root=project_root,
            stamp_provenance=True,
        )

    _seed_remote_mcps(graph, target, install_root, project_root, by_scope, normalize)
    _seed_direct_components(graph, target, install_root, project_root, by_scope, normalize)
