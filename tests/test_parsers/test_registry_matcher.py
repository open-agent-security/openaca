"""Task 042-3: registry three-way split and the `pathspec`-backed matcher.

The differential cases below encode ground truth captured from the
hand-rolled `_registry_pattern_matches` *before* it was deleted (see the
task-3 report for the capture script). They are hardcoded here — not
re-derived from the old function, which no longer exists — so this file is
the record of "the new matcher agrees with the old one" for every pattern
shape the old code special-cased.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.agent_kinds import claude_code
from tools.parsers import (
    CLAUDE_CODE_MANIFEST_REGISTRY,
    CURSOR_MANIFEST_REGISTRY,
    HOST_AGNOSTIC_REGISTRY,
    REGISTRY,
    ManifestPattern,
    _pattern_matches,
    agent_plugins,
    claude_plugin,
    claude_skill,
    mcp_json,
    package_json,
    pyproject_toml,
)
from tools.parsers import _parse_repo_agent_plugins as parse_repo_agent_plugins
from tools.parsers import _parse_repo_cursor_agent as parse_repo_cursor_agent
from tools.parsers import _parse_repo_cursor_command as parse_repo_cursor_command
from tools.repo_surface import CURSOR_SURFACE


def test_host_agnostic_registry_is_the_five_dependency_manifests():
    assert [e.pattern for e in HOST_AGNOSTIC_REGISTRY] == [
        "package.json",
        "pyproject.toml",
        "package-lock.json",
        "uv.lock",
        "bun.lock",
    ]


def test_registry_compat_alias_concatenates_host_agnostic_then_claude_code():
    assert REGISTRY == [*HOST_AGNOSTIC_REGISTRY, *CLAUDE_CODE_MANIFEST_REGISTRY]
    assert claude_code.KIND.manifest_patterns == tuple(REGISTRY)


def test_cursor_manifest_registry_matches_the_task_brief_table_exactly():
    """Command/agent rows cover every extension and compat root
    `tools/cursor_commands.py`/`tools/cursor_subagents.py` actually resolve
    (`.md`/`.txt` under `.cursor|.claude/commands`, `.md`/`.mdc`/`.markdown`
    under `.cursor|.claude/agents`) — a narrower table here would undercount
    `n_found` against files composition does discover.

    Skill rows are `**/SKILL.md`, mirroring composition's RECURSIVE walk of
    Cursor's skill roots, and the plugin rows include Claude Code's manifest
    because Cursor's ordered candidate list realizes bundles through it.

    Command/agent rows carry a depth guard (not `None`) — the glob alone
    can't express the resolvers' 10-segment traversal limit, so a guard
    keeps `n_found`/`n_failed` from counting a file composition drops."""
    entries = [tuple(e) for e in CURSOR_MANIFEST_REGISTRY]
    guards = [e[2] for e in entries]
    assert [(p, parser) for p, parser, _ in entries] == [
        ("**/.cursor/mcp.json", mcp_json.parse),
        ("**/.cursor/skills/**/SKILL.md", claude_skill.parse),
        ("**/.agents/skills/**/SKILL.md", claude_skill.parse),
        ("**/.claude/skills/**/SKILL.md", claude_skill.parse),
        ("**/.codex/skills/**/SKILL.md", claude_skill.parse),
        ("**/.cursor/commands/**/*.md", parse_repo_cursor_command),
        ("**/.cursor/commands/**/*.txt", parse_repo_cursor_command),
        ("**/.claude/commands/**/*.md", parse_repo_cursor_command),
        ("**/.claude/commands/**/*.txt", parse_repo_cursor_command),
        ("**/.cursor/agents/**/*.md", parse_repo_cursor_agent),
        ("**/.cursor/agents/**/*.mdc", parse_repo_cursor_agent),
        ("**/.cursor/agents/**/*.markdown", parse_repo_cursor_agent),
        ("**/.claude/agents/**/*.md", parse_repo_cursor_agent),
        ("**/.claude/agents/**/*.mdc", parse_repo_cursor_agent),
        ("**/.claude/agents/**/*.markdown", parse_repo_cursor_agent),
        ("**/.cursor-plugin/plugin.json", claude_plugin.parse),
        ("**/.claude-plugin/plugin.json", claude_plugin.parse),
        ("plugin.json", parse_repo_agent_plugins),
    ]
    assert guards[:5] == [None] * 5, "mcp.json and skill rows have no depth guard"
    assert all(callable(g) for g in guards[5:15]), (
        "every command/agent row must carry a depth guard"
    )
    assert guards[15:17] == [None, None], "plugin manifest rows have no depth guard"
    assert guards[17] is agent_plugins.is_agent_plugins_manifest


def test_cursor_registry_never_has_a_bare_mcp_json_pattern():
    """Cursor's direct MCP surface is the path-scoped `.cursor/mcp.json`;
    bundle roots are reached only through the plugin route. A bare
    `mcp.json`/`.mcp.json` pattern would re-create the cross-format collision
    the per-agent-graph model exists to prevent."""
    patterns = {e.pattern for e in CURSOR_MANIFEST_REGISTRY}
    assert "mcp.json" not in patterns, (
        "Cursor registry must not contain a bare 'mcp.json' pattern — its MCP "
        "surface is the path-scoped .cursor/mcp.json; a bare pattern would "
        "collide with any other kind's bundle-root mcp.json."
    )
    assert ".mcp.json" not in patterns, (
        "Cursor registry must not contain a bare '.mcp.json' pattern — its MCP "
        "surface is the path-scoped .cursor/mcp.json; a bare pattern would "
        "collide with any other kind's bundle-root .mcp.json."
    )


def test_manifest_pattern_is_hashable():
    """`scan.py`/`bom_cli.py` cache on `(scan_root, kind.manifest_patterns)` —
    a plain `NamedTuple` stays hashable even with a `guard` field; a
    dataclass would not."""
    entry = ManifestPattern(
        "plugin.json", agent_plugins.parse, agent_plugins.is_agent_plugins_manifest
    )
    hash(entry)
    cache: dict[tuple[ManifestPattern, ...], int] = {tuple(CURSOR_MANIFEST_REGISTRY): 1}
    assert cache[tuple(CURSOR_MANIFEST_REGISTRY)] == 1


# --- Differential corpus: (path, old_pattern, new_pattern, expected) --------
#
# `old_pattern` is the pattern string the hand-rolled matcher ran against
# (pre-re-anchor for the two patterns that changed shape). `new_pattern` is
# what the pathspec-backed matcher runs against post-split. For every case
# below the *old* result (captured before deletion) is `expected`, and the
# new matcher run against `new_pattern` must agree.
DIFFERENTIAL_CASES: list[tuple[str, str, bool]] = [
    # Skill pattern: exactly one directory between skills/ and SKILL.md.
    (".claude/skills/a/b/SKILL.md", "**/.claude/skills/*/SKILL.md", False),
    (".claude/skills/foo/SKILL.md", "**/.claude/skills/*/SKILL.md", True),
    # .claude-plugin/plugin.json re-anchored to **/... — old unanchored
    # special-case meant "at any depth"; new pattern must preserve that.
    ("x/.claude-plugin/plugin.json", "**/.claude-plugin/plugin.json", True),
    (".claude-plugin/plugin.json", "**/.claude-plugin/plugin.json", True),
    # .claude/settings.json re-anchored the same way. settings.local.json is
    # a different filename and must never match.
    (".claude/settings.local.json", "**/.claude/settings.json", False),
    (".claude/settings.json", "**/.claude/settings.json", True),
    ("sub/.claude/settings.json", "**/.claude/settings.json", True),
    # Nested command file.
    ("a/.claude/commands/x/y.md", "**/.claude/commands/**/*.md", True),
    # Root-level match with no leading directory (bare filename pattern).
    ("package.json", "package.json", True),
    ("sub/dir/package.json", "package.json", True),
    ("a/.claude/agents/deep/name.md", "**/.claude/agents/**/*.md", True),
]


@pytest.mark.parametrize("path_str,pattern,expected", DIFFERENTIAL_CASES)
def test_new_matcher_agrees_with_captured_old_matcher_results(path_str, pattern, expected):
    root = Path("/root")
    assert _pattern_matches(root / path_str, root, pattern) is expected


# Every path from DIFFERENTIAL_CASES, plus the collision candidates the plan
# review named explicitly: a bare `mcp.json` at a repo root, a `plugin.json`
# under `.claude-plugin/`, and a `package.json` inside a plugin bundle.
REGISTRY_COLLISION_CORPUS: list[str] = [
    *{path_str for path_str, _pattern, _expected in DIFFERENTIAL_CASES},
    "mcp.json",
    ".mcp.json",
    "claude_desktop_config.json",
    "pyproject.toml",
    "package-lock.json",
    "uv.lock",
    "bun.lock",
    "my-plugin/.claude-plugin/plugin.json",
    "my-plugin/package.json",
    ".claude-plugin/package.json",
]


def test_registry_order_is_unobservable_because_no_corpus_path_matches_two_patterns():
    """`REGISTRY = [*HOST_AGNOSTIC_REGISTRY, *CLAUDE_CODE_MANIFEST_REGISTRY]`
    reorders entries relative to the pre-split flat list. That reorder is
    unobservable only because `parse_repo_grouped`'s `break` picks the first
    matching pattern per path AND no path in this corpus matches more than
    one `REGISTRY` pattern — if two patterns ever did overlap, `break` would
    make the *order* of `REGISTRY` decide the outcome, and reordering would
    become a real behaviour change. Pinning that zero-overlap property here,
    not just arguing it in prose.
    """
    root = Path("/root")
    for path_str in REGISTRY_COLLISION_CORPUS:
        matched = [
            entry.pattern
            for entry in REGISTRY
            if _pattern_matches(root / path_str, root, entry.pattern)
        ]
        assert len(matched) <= 1, (
            f"{path_str!r} matched more than one REGISTRY pattern {matched!r} — "
            "the walker's break-on-first-match means REGISTRY's order now "
            "decides the outcome for this path; reordering is no longer safe "
            "without an explicit decision about which pattern should win."
        )


def test_directory_only_pattern_does_not_match_a_same_named_file():
    """A trailing-slash (directory-only) pattern must not match a file that
    merely shares its name — no registry entry uses this shape today, but
    the old pathlib-based fallback (`Path.match`) got this backwards for a
    bare trailing-slash pattern, so the new matcher is asserted directly
    rather than against the old (buggy, never-exercised) behaviour."""
    root = Path("/root")
    assert _pattern_matches(root / "skills", root, "skills/") is False
    assert _pattern_matches(root / "skills" / "SKILL.md", root, "skills/") is True


def test_windows_backslash_path_is_normalized_before_matching():
    """The old matcher never normalized backslashes, so a Windows-style
    relative path (a single path component containing literal backslashes)
    silently failed to match `.claude/settings.json` even at root. The new
    matcher normalizes to forward slashes first, so the re-anchored pattern
    matches as intended."""
    root = Path("/root")
    path = root / "a\\.claude\\settings.json"
    assert _pattern_matches(path, root, "**/.claude/settings.json") is True


def test_one_file_one_route_break_stops_at_first_match(tmp_path):
    """Before the walker `break`, a path matching two registry patterns
    produced two grouped entries and counted `n_found` twice. A bare
    `plugin.json` pattern and a `**/...plugin.json` pattern both match the
    same file — this is exactly the shape task 3 asks the walker (not
    registry hygiene) to resolve."""
    from tools.parsers import parse_repo_grouped

    (tmp_path / ".cursor-plugin").mkdir()
    (tmp_path / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}', encoding="utf-8")

    overlapping_registry = (
        ManifestPattern("**/.cursor-plugin/plugin.json", claude_plugin.parse),
        ManifestPattern("plugin.json", package_json.parse),
    )
    grouped, n_found = parse_repo_grouped(tmp_path, registry=overlapping_registry)

    assert n_found == 1
    assert len(grouped) == 1


def test_guard_failure_does_not_increment_n_found(tmp_path):
    """The guard runs before `n_found` increments — a bare `plugin.json`
    pattern whose guard rejects the file must not inflate the manifest
    count."""
    from tools.parsers import parse_repo_grouped

    (tmp_path / "plugin.json").write_text('{"not": "an agent-plugins manifest"}', encoding="utf-8")

    grouped, n_found = parse_repo_grouped(tmp_path, registry=(CURSOR_MANIFEST_REGISTRY[-1],))

    assert n_found == 0
    assert grouped == []


def test_guard_success_parses_and_counts(tmp_path):
    (tmp_path / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "demo"}',
        encoding="utf-8",
    )

    from tools.parsers import parse_repo_grouped

    grouped, n_found = parse_repo_grouped(tmp_path, registry=(CURSOR_MANIFEST_REGISTRY[-1],))

    assert n_found == 1
    assert len(grouped) == 1


def test_guard_success_but_fatal_validation_failure_counts_as_parse_failed(tmp_path):
    """The guard only confirms `$schema` qualifies — `validate_manifest` can
    still reject the manifest (e.g. an uppercase `name`). That's a real
    defect past the guard, not a guard miss, so the registry route must
    raise (via `_parse_repo_agent_plugins`'s `strict=True`) rather than
    silently return `[]`: an uncounted `[]` would let `parse_repo_grouped`
    report a fatally invalid plugin as a clean, empty, successfully-parsed
    unit."""
    (tmp_path / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",'
        ' "name": "Invalid-Name"}',
        encoding="utf-8",
    )

    from tools.parsers import parse_repo_grouped, parse_repo_registry_counts

    grouped, n_found = parse_repo_grouped(tmp_path, registry=(CURSOR_MANIFEST_REGISTRY[-1],))
    assert n_found == 1
    assert grouped == []

    per_key, union = parse_repo_registry_counts(
        tmp_path, {"cursor": (CURSOR_MANIFEST_REGISTRY[-1],)}
    )
    assert per_key["cursor"] == (1, 1)
    assert union == (1, 1)


def test_registry_excludes_native_plugin_fixture_nested_in_a_realized_plugin_root(tmp_path):
    """`_realize_plugins` (`tools/graph_build_cursor.py`) excludes a nested
    plugin candidate strictly below an already-realized root from realizing
    as its own bundle. The registry-count walk must apply the same
    exclusion, so a bundled fixture like
    `examples/demo/.cursor-plugin/plugin.json` neither inflates
    `source_unit_count` nor can register a `parse_failed` for content Cursor
    never loads."""
    from tools.parsers import parse_repo_grouped, parse_repo_registry_counts

    outer = tmp_path / ".claude-plugin"
    outer.mkdir()
    (outer / "plugin.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")

    nested = tmp_path / "examples" / "demo" / ".cursor-plugin"
    nested.mkdir(parents=True)
    (nested / "plugin.json").write_text(json.dumps({"name": "fixture"}), encoding="utf-8")

    grouped, n_found = parse_repo_grouped(
        tmp_path, registry=CURSOR_MANIFEST_REGISTRY, surface=CURSOR_SURFACE
    )
    matched_paths = {path for path, _ in grouped}
    assert (outer / "plugin.json") in matched_paths
    assert (nested / "plugin.json") not in matched_paths
    assert n_found == 1

    per_key, union = parse_repo_registry_counts(
        tmp_path, {"cursor": CURSOR_MANIFEST_REGISTRY}, surfaces={"cursor": CURSOR_SURFACE}
    )
    assert per_key["cursor"] == (1, 0)
    assert union == (1, 0)


def test_registry_excludes_a_malformed_nested_fixture_rather_than_failing_it(tmp_path):
    """A malformed nested fixture (invalid JSON) must not register as a
    `parse_failed` either — Cursor never attempts to load it, so it must be
    invisible to the count, the same as a valid one."""
    from tools.parsers import parse_repo_grouped, parse_repo_registry_counts

    outer = tmp_path / ".claude-plugin"
    outer.mkdir()
    (outer / "plugin.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")

    nested = tmp_path / "examples" / "demo" / ".cursor-plugin"
    nested.mkdir(parents=True)
    (nested / "plugin.json").write_text("{not valid json", encoding="utf-8")

    grouped, n_found = parse_repo_grouped(
        tmp_path, registry=CURSOR_MANIFEST_REGISTRY, surface=CURSOR_SURFACE
    )
    assert n_found == 1
    assert len(grouped) == 1

    per_key, union = parse_repo_registry_counts(
        tmp_path, {"cursor": CURSOR_MANIFEST_REGISTRY}, surfaces={"cursor": CURSOR_SURFACE}
    )
    assert per_key["cursor"] == (1, 0)
    assert union == (1, 0)


def test_registry_still_counts_a_standalone_native_plugin_not_nested_under_another(tmp_path):
    """Guards against over-exclusion: a `.cursor-plugin` bundle that is a
    sibling of an unrelated realized plugin, not nested beneath it, still
    counts."""
    from tools.parsers import parse_repo_grouped

    outer = tmp_path / "claude-plugin" / ".claude-plugin"
    outer.mkdir(parents=True)
    (outer / "plugin.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")

    standalone = tmp_path / "standalone" / ".cursor-plugin"
    standalone.mkdir(parents=True)
    (standalone / "plugin.json").write_text(json.dumps({"name": "standalone"}), encoding="utf-8")

    grouped, n_found = parse_repo_grouped(
        tmp_path, registry=CURSOR_MANIFEST_REGISTRY, surface=CURSOR_SURFACE
    )
    matched_paths = {path for path, _ in grouped}
    assert (standalone / "plugin.json") in matched_paths
    assert n_found == 2


def test_two_tuple_registry_entries_still_work(tmp_path):
    """Existing call sites construct/read plain 2-tuples — normalize them
    rather than requiring every caller to migrate to `ManifestPattern`."""
    from tools.parsers import parse_repo_grouped

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

    grouped, n_found = parse_repo_grouped(
        tmp_path, registry=(("pyproject.toml", pyproject_toml.parse),)
    )
    assert n_found == 1
    assert len(grouped) == 1


def test_cursor_registry_counts_non_md_command_and_agent_extensions(tmp_path):
    """`tools/cursor_commands.py`/`tools/cursor_subagents.py` compose a
    `.cursor/commands/*.txt` command and a `.cursor/agents/*.mdc` subagent —
    a registry that only matched `.md` would undercount `n_found` (and thus
    `openaca:source_unit_count`/scan stats) against what composition actually
    discovers, and silently drop the parse via `claude_command_agent.parse_file`'s
    default `.md`-only `extensions`."""
    from tools.parsers import parse_repo_grouped

    (tmp_path / ".cursor" / "commands").mkdir(parents=True)
    (tmp_path / ".cursor" / "commands" / "deploy.txt").write_text("deploy", encoding="utf-8")
    (tmp_path / ".cursor" / "agents").mkdir(parents=True)
    (tmp_path / ".cursor" / "agents" / "helper.mdc").write_text("helper", encoding="utf-8")
    (tmp_path / ".claude" / "commands").mkdir(parents=True)
    (tmp_path / ".claude" / "commands" / "build.md").write_text("build", encoding="utf-8")

    grouped, n_found = parse_repo_grouped(
        tmp_path, registry=CURSOR_MANIFEST_REGISTRY, surface=CURSOR_SURFACE
    )

    assert n_found == 3
    refs_by_path = {path: refs for path, refs in grouped}
    assert refs_by_path[tmp_path / ".cursor" / "commands" / "deploy.txt"][0].name == "deploy"
    assert refs_by_path[tmp_path / ".cursor" / "agents" / "helper.mdc"][0].name == "helper"


def _make_nested_file(root: Path, *, depth: int) -> Path:
    """Build a path `depth` segments below `root` (depth counts the file
    itself), matching how `tools/cursor_commands.py`/`tools/cursor_subagents.py`
    count `relative_path.parts` off their `commands`/`agents` root."""
    current = root
    for i in range(depth - 1):
        current = current / f"d{i}"
    current.mkdir(parents=True, exist_ok=True)
    return current


@pytest.mark.parametrize("depth", [10, 11])
def test_cursor_registry_command_depth_matches_resolver_traversal_limit(tmp_path, depth):
    """`tools/cursor_commands.py`'s `_iter_command_files` drops a command
    whose path relative to `commands_dir` exceeds 10 segments
    (`tools/cursor_commands.py:144`). The registry's glob pattern has no way
    to express that limit on its own, so it must carry a depth guard that
    agrees with the resolver exactly at the boundary — depth 10 still counts,
    depth 11 must not."""
    from tools.cursor_commands import resolve_repo
    from tools.parsers import parse_repo_grouped

    commands_dir = tmp_path / ".cursor" / "commands"
    leaf_dir = _make_nested_file(commands_dir, depth=depth)
    (leaf_dir / "deploy.md").write_text("deploy", encoding="utf-8")

    _, n_found = parse_repo_grouped(
        tmp_path, registry=CURSOR_MANIFEST_REGISTRY, surface=CURSOR_SURFACE
    )
    resolved = resolve_repo(tmp_path)

    if depth <= 10:
        assert n_found == 1
        assert len(resolved) == 1
    else:
        assert n_found == 0
        assert len(resolved) == 0


@pytest.mark.parametrize("depth", [10, 11])
def test_cursor_registry_agent_depth_matches_resolver_traversal_limit(tmp_path, depth):
    """Same boundary as the command case above, for
    `tools/cursor_subagents.py`'s `_MAX_TRAVERSAL_DEPTH` (`tools/cursor_subagents.py:143`)."""
    from tools.cursor_subagents import resolve_repo
    from tools.parsers import parse_repo_grouped

    agents_dir = tmp_path / ".cursor" / "agents"
    leaf_dir = _make_nested_file(agents_dir, depth=depth)
    (leaf_dir / "helper.md").write_text("helper", encoding="utf-8")

    _, n_found = parse_repo_grouped(
        tmp_path, registry=CURSOR_MANIFEST_REGISTRY, surface=CURSOR_SURFACE
    )
    resolved = resolve_repo(tmp_path)

    if depth <= 10:
        assert n_found == 1
        assert len(resolved) == 1
    else:
        assert n_found == 0
        assert len(resolved) == 0


def test_cursor_registry_depth_guard_also_covers_claude_compat_dirs(tmp_path):
    """The `.claude/commands` and `.claude/agents` compat routes share the
    same unrestricted glob shape as the `.cursor` routes, so they need the
    same depth guard — not just `.cursor`."""
    from tools.parsers import parse_repo_grouped

    leaf_dir = _make_nested_file(tmp_path / ".claude" / "commands", depth=11)
    (leaf_dir / "deploy.md").write_text("deploy", encoding="utf-8")

    _, n_found = parse_repo_grouped(
        tmp_path, registry=CURSOR_MANIFEST_REGISTRY, surface=CURSOR_SURFACE
    )
    assert n_found == 0


# --- Registry accounting excludes every plugin-owned surface ----------------
#
# The exclusion used to be gated on an allowlist of three plugin-manifest
# patterns, so bundled NON-manifest content still inflated `source_unit_count`
# and could register a false `parse_failed` for files composition never loads.
#
# These assert on the PATHS claimed, not a bare total: the realized plugin's
# own manifest is itself a legitimate claimed unit, so a count alone cannot
# distinguish "the fixture was excluded" from "nothing matched at all".


def _realized_claude_plugin(root):
    plugin_dir = root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    return plugin_dir / "plugin.json"


def _claimed_paths(root):
    from tools.parsers import parse_repo_grouped

    grouped, _ = parse_repo_grouped(root, registry=CURSOR_MANIFEST_REGISTRY, surface=CURSOR_SURFACE)
    return {p for p, _refs in grouped}


def test_registry_excludes_a_bundled_cursor_mcp_json(tmp_path):
    manifest = _realized_claude_plugin(tmp_path)
    fixture = tmp_path / "examples" / ".cursor"
    fixture.mkdir(parents=True)
    bundled = fixture / "mcp.json"
    bundled.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    assert _claimed_paths(tmp_path) == {manifest}
    assert bundled not in _claimed_paths(tmp_path)


def test_registry_excludes_a_bundled_cursor_command(tmp_path):
    manifest = _realized_claude_plugin(tmp_path)
    fixture = tmp_path / "examples" / ".cursor" / "commands"
    fixture.mkdir(parents=True)
    (fixture / "demo.md").write_text("# demo\n", encoding="utf-8")

    assert _claimed_paths(tmp_path) == {manifest}


def test_registry_excludes_a_bundled_cursor_skill(tmp_path):
    manifest = _realized_claude_plugin(tmp_path)
    fixture = tmp_path / "examples" / ".cursor" / "skills" / "demo"
    fixture.mkdir(parents=True)
    (fixture / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")

    assert _claimed_paths(tmp_path) == {manifest}


def test_registry_excludes_a_bundled_malformed_cursor_mcp_json(tmp_path):
    """A malformed bundled manifest must not register a `parse_failed` either:
    Cursor never attempts to load it, so it is invisible to the count."""
    from tools.parsers import parse_repo_registry_counts

    _realized_claude_plugin(tmp_path)
    fixture = tmp_path / "examples" / ".cursor"
    fixture.mkdir(parents=True)
    (fixture / "mcp.json").write_text("{not valid json", encoding="utf-8")

    per_key, _union = parse_repo_registry_counts(
        tmp_path, {"cursor": CURSOR_MANIFEST_REGISTRY}, surfaces={"cursor": CURSOR_SURFACE}
    )
    # The outer manifest is the one claimed unit; the malformed fixture is
    # neither counted nor failed.
    assert per_key["cursor"] == (1, 0)


def test_registry_still_counts_a_cursor_surface_beside_a_realized_plugin(tmp_path):
    """Over-suppression guard: ownership is ancestry-scoped, so a Cursor
    surface that is a SIBLING of a realized plugin still counts."""
    manifest = _realized_claude_plugin(tmp_path / "vendored")
    own = tmp_path / ".cursor"
    own.mkdir()
    own_mcp = own / "mcp.json"
    own_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    assert _claimed_paths(tmp_path) == {manifest, own_mcp}


# --- Codex registry (plan 043 Task 8) --------------------------------------


def test_codex_registry_claims_its_own_surfaces():
    from tools.parsers import CODEX_MANIFEST_REGISTRY

    patterns = {e.pattern for e in CODEX_MANIFEST_REGISTRY}

    assert patterns == {
        "**/.codex/config.toml",
        "**/.codex/hooks.json",
        "**/.codex/skills/**/SKILL.md",
        "**/.codex-plugin/plugin.json",
        "**/.claude-plugin/plugin.json",
    }


def test_codex_registry_has_no_bare_mcp_pattern():
    """Codex's MCP servers live INSIDE `config.toml` as `[mcp_servers.*]`,
    never as a standalone manifest. A bare `mcp.json` route would claim files
    Codex does not read and re-create the cross-format collision the
    per-agent-graph model exists to prevent."""
    from tools.parsers import CODEX_MANIFEST_REGISTRY

    patterns = {e.pattern for e in CODEX_MANIFEST_REGISTRY}

    assert "mcp.json" not in patterns
    assert ".mcp.json" not in patterns
    assert not any(p.endswith("/mcp.json") for p in patterns)


def test_codex_registry_has_no_commands_or_project_agents_route():
    """Codex has no commands surface, and `.codex/agents` has zero references
    in the audited binary — subagents are user-scope endpoint state."""
    from tools.parsers import CODEX_MANIFEST_REGISTRY

    patterns = {e.pattern for e in CODEX_MANIFEST_REGISTRY}

    assert not any("commands" in p for p in patterns)
    assert not any("/agents/" in p for p in patterns)


def test_codex_skill_counting_is_recursive(tmp_path):
    """Counts must mirror what composition walks, or source_unit_count and the
    graph disagree about the same tree."""
    from tools.parsers import CODEX_MANIFEST_REGISTRY, parse_repo_grouped
    from tools.repo_surface import CODEX_SURFACE

    nested = tmp_path / ".codex" / "skills" / "group" / "tool"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("---\nname: tool\n---\nS\n", encoding="utf-8")

    _, n_found = parse_repo_grouped(
        tmp_path, registry=CODEX_MANIFEST_REGISTRY, surface=CODEX_SURFACE
    )

    assert n_found == 1


def test_codex_registry_excludes_plugin_owned_content(tmp_path):
    """Via the shared `_entry_claims` chokepoint and
    `CODEX_SURFACE.excludes_plugin_owned_content`."""
    from tools.parsers import CODEX_MANIFEST_REGISTRY, parse_repo_grouped
    from tools.repo_surface import CODEX_SURFACE

    plugin_dir = tmp_path / ".codex-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    fixture = tmp_path / "examples" / ".codex" / "skills" / "demo"
    fixture.mkdir(parents=True)
    (fixture / "SKILL.md").write_text("---\nname: demo\n---\nS\n", encoding="utf-8")

    grouped, _ = parse_repo_grouped(
        tmp_path, registry=CODEX_MANIFEST_REGISTRY, surface=CODEX_SURFACE
    )

    assert {p for p, _refs in grouped} == {plugin_dir / "plugin.json"}


def test_codex_registry_counts_only_the_resolved_plugin_manifest(tmp_path):
    """A root carrying BOTH `.codex-plugin/plugin.json` and
    `.claude-plugin/plugin.json` realizes as ONE plugin in composition
    (`_resolve_plugin_format`: first qualifying candidate in list order wins,
    `.codex-plugin` before `.claude-plugin`). Registry accounting must claim
    only that same file, or it double-counts one real manifest as two."""
    from tools.parsers import CODEX_MANIFEST_REGISTRY, parse_repo_grouped
    from tools.repo_surface import CODEX_SURFACE

    codex_manifest = tmp_path / ".codex-plugin" / "plugin.json"
    codex_manifest.parent.mkdir()
    codex_manifest.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    claude_manifest = tmp_path / ".claude-plugin" / "plugin.json"
    claude_manifest.parent.mkdir()
    claude_manifest.write_text(json.dumps({"name": "demo"}), encoding="utf-8")

    grouped, n_found = parse_repo_grouped(
        tmp_path, registry=CODEX_MANIFEST_REGISTRY, surface=CODEX_SURFACE
    )

    assert {p for p, _refs in grouped} == {codex_manifest}
    assert n_found == 1


def test_codex_registry_falls_through_when_codex_plugin_candidate_does_not_qualify(tmp_path):
    """`.codex-plugin/plugin.json` without a string `name` fails
    `_detect_named_plugin_manifest`, so `_resolve_plugin_format` falls through
    to `.claude-plugin/plugin.json`. Registry accounting must follow the same
    fallback rather than independently parsing the disqualified candidate —
    otherwise a candidate composition never reads can register a phantom
    parse failure (or a phantom success) that the graph never produced."""
    from tools.parsers import CODEX_MANIFEST_REGISTRY, parse_repo_grouped
    from tools.repo_surface import CODEX_SURFACE

    codex_manifest = tmp_path / ".codex-plugin" / "plugin.json"
    codex_manifest.parent.mkdir()
    codex_manifest.write_text(json.dumps({}), encoding="utf-8")
    claude_manifest = tmp_path / ".claude-plugin" / "plugin.json"
    claude_manifest.parent.mkdir()
    claude_manifest.write_text(json.dumps({"name": "demo"}), encoding="utf-8")

    grouped, n_found = parse_repo_grouped(
        tmp_path, registry=CODEX_MANIFEST_REGISTRY, surface=CODEX_SURFACE
    )

    assert {p for p, _refs in grouped} == {claude_manifest}
    assert n_found == 1


def test_codex_registry_resolves_the_gitignored_candidate_as_absent(tmp_path):
    """The higher-priority `.codex-plugin/plugin.json` is gitignored; the
    fallback `.claude-plugin/plugin.json` is not. `_find_plugin_roots`
    resolves plugin format with the walk's own gitignore spec
    (`_resolve_plugin_format(..., eval_root=directory, spec=spec)`), so
    composition treats the ignored candidate as absent and falls through to
    the visible one. The registry's guard must apply that same gitignore
    context — passing none would see the ignored file on disk, resolve to
    `.codex-plugin`, and reject the visible `.claude-plugin` manifest
    composition actually reads."""
    from tools.parsers import CODEX_MANIFEST_REGISTRY, parse_repo_grouped
    from tools.repo_surface import CODEX_SURFACE

    (tmp_path / ".gitignore").write_text(".codex-plugin/\n", encoding="utf-8")
    codex_manifest = tmp_path / ".codex-plugin" / "plugin.json"
    codex_manifest.parent.mkdir()
    codex_manifest.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    claude_manifest = tmp_path / ".claude-plugin" / "plugin.json"
    claude_manifest.parent.mkdir()
    claude_manifest.write_text(json.dumps({"name": "demo"}), encoding="utf-8")

    grouped, n_found = parse_repo_grouped(
        tmp_path, registry=CODEX_MANIFEST_REGISTRY, surface=CODEX_SURFACE
    )

    assert {p for p, _refs in grouped} == {claude_manifest}
    assert n_found == 1


def test_a_malformed_codex_config_registers_a_parse_failure(tmp_path):
    from tools.parsers import CODEX_MANIFEST_REGISTRY, parse_repo_registry_counts
    from tools.repo_surface import CODEX_SURFACE

    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("{ not toml", encoding="utf-8")

    per_key, _union = parse_repo_registry_counts(
        tmp_path, {"codex": CODEX_MANIFEST_REGISTRY}, surfaces={"codex": CODEX_SURFACE}
    )

    assert per_key["codex"] == (1, 1)
