"""Task 5 regression gate (ADR-0057): parameterizing endpoint seeding must not
change Claude Code's endpoint graph output at all.

`tests/fixtures/golden/endpoint_surface_claude_graph.json` was generated from
`tests/fixtures/installs/endpoint-surface-golden/` *before* the
`EndpointSurface` extraction landed, via the exact `_serialize_graph` logic
below. This test re-derives the graph post-refactor and asserts structurally
identical serialization.

The fixture is copied to a temp directory and its `installPath` rewritten per
run, because `installed_plugins.json` records an absolute path — committing one
would pin the golden to whoever generated it.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

from tools.graph import Graph
from tools.graph_build import build_graph

FIXTURE = Path(__file__).parent / "fixtures" / "installs" / "endpoint-surface-golden"
GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "endpoint_surface_claude_graph.json"


def _materialize(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the fixture and point `installPath` at the copy.

    `tmp_path` is resolved first: on macOS a `/var/...` temp directory reaches
    `/private/var/...` through a symlink, and skill provenance records
    `status: "symlink-target"` when it notices. That would make the golden
    depend on which temp mechanism generated it rather than on the graph.
    """
    root = tmp_path.resolve() / "endpoint"
    shutil.copytree(FIXTURE, root)
    lockfile = root / "plugins" / "installed_plugins.json"
    data = json.loads(lockfile.read_text())
    data["plugins"]["demo@mkt"][0]["installPath"] = str(
        (root / "plugins" / "cache" / "mkt" / "demo" / "1.0.0").resolve()
    )
    lockfile.write_text(json.dumps(data, indent=2))
    return root, root / "project"


def _relativize(value: object, roots: tuple[str, ...]) -> object:
    """Replace every spelling of the fixture root with a stable token.

    Both the resolved and unresolved root are substituted: on macOS `/var` is a
    symlink to `/private/var`, so `Path.resolve()` and the paths recorded in
    `source_manifest` disagree, and matching only one leaves absolute temp
    paths in the output — which would make this golden differ on every run
    while looking like a real regression.
    """
    if isinstance(value, str):
        for root in roots:
            value = value.replace(root, "<ENDPOINT>")
        return value
    if isinstance(value, dict):
        return {k: _relativize(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [_relativize(v, roots) for v in value]
    return value


def _serialize_graph(graph: Graph, root: Path) -> dict:
    # Longest first, so the resolved form is consumed before its prefix.
    root_str = tuple(sorted({str(root.resolve()), str(root)}, key=len, reverse=True))
    nodes = []
    for key in sorted(graph.nodes):
        node = graph.nodes[key]
        ref = None
        if node.ref is not None:
            ref = _relativize(asdict(node.ref), root_str)
            assert isinstance(ref, dict)
            ref["purl"] = node.ref.purl
        nodes.append({"key": _relativize(key, root_str), "kind": node.kind, "ref": ref})
    edges = sorted(
        (
            {"parent": _relativize(e.parent, root_str), "child": _relativize(e.child, root_str)}
            for e in graph.edges
        ),
        key=lambda e: (e["parent"], e["child"]),
    )
    return {"nodes": nodes, "edges": edges}


def _build(tmp_path: Path) -> dict:
    root, project = _materialize(tmp_path)
    graph = build_graph(root, mode="endpoint", project_root=project)
    graph.validate()
    return _serialize_graph(graph, root)


def test_endpoint_surface_golden_graph_unchanged(tmp_path):
    """The gate. If this moves, the parameterization is wrong — do not
    regenerate the golden to match."""
    assert _build(tmp_path) == json.loads(GOLDEN.read_text())


def test_the_fixture_actually_exercises_every_shared_branch(tmp_path):
    """A golden over an empty graph would pass while proving nothing."""
    serialized = _build(tmp_path)
    kinds = {n["kind"] for n in serialized["nodes"]}

    assert "plugin" in kinds, "active-plugin acquisition (forked branch)"
    assert "skill" in kinds, "project skills + direct skills (shared branch)"
    assert "mcp_server" in kinds, "remote MCP (forked branch)"
    assert "command" in kinds, "direct components (shared branch)"
    assert "agent" in kinds, "direct components (shared branch)"
    assert "hook" in kinds, "settings-scoped hooks (shared branch, seeds_hooks)"


def test_claude_code_endpoint_descriptor_is_data_only():
    """ADR-0057: a `Callable` field is the signal this drifted into a strategy
    object. Mode discriminators are rejected on the same grounds."""
    from dataclasses import fields

    from tools.endpoint_surface import CLAUDE_CODE_ENDPOINT, EndpointSurface

    for f in fields(EndpointSurface):
        value = getattr(CLAUDE_CODE_ENDPOINT, f.name)
        assert not callable(value), f"{f.name} is callable — see ADR-0057"


def test_claude_code_endpoint_transcribes_todays_literals():
    """Verbatim transcription is what makes the golden meaningful."""
    from tools.endpoint_surface import CLAUDE_CODE_ENDPOINT

    assert CLAUDE_CODE_ENDPOINT.project_config_dir == ".claude"
    assert CLAUDE_CODE_ENDPOINT.project_skills_subdir == "skills"
    assert CLAUDE_CODE_ENDPOINT.direct_skills_dir == "skills"
    assert CLAUDE_CODE_ENDPOINT.direct_command_agent_dirs == (
        ("commands", "command"),
        ("agents", "agent"),
    )
    assert CLAUDE_CODE_ENDPOINT.seeds_project_command_agents is True
    assert CLAUDE_CODE_ENDPOINT.seeds_hooks is True


# --- Managed (administrator) settings compose (coverage rule) --------------


def test_managed_settings_declare_real_components(tmp_path):
    """An administrator's system-wide policy can carry `mcpServers` and
    `hooks` — components. Skipping it reported an MDM-managed endpoint's
    composition as complete while missing them."""
    from tools.parsers.settings_layers import load_managed

    d = tmp_path / "ClaudeCode"
    d.mkdir()
    (d / "managed-settings.json").write_text(
        json.dumps(
            {
                "mcpServers": {"corp": {"url": "https://corp.test/mcp/"}},
                "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}]},
            }
        ),
        encoding="utf-8",
    )

    merged = load_managed(d)

    assert merged is not None
    assert "corp" in merged["mcpServers"]
    assert "SessionStart" in merged["hooks"]


def test_managed_dropins_merge_over_the_base_file(tmp_path):
    """`NN-name.json` drop-ins apply in sorted order after the base file —
    the order `policy_cli`'s own collision check walks."""
    from tools.parsers.settings_layers import load_managed

    d = tmp_path / "ClaudeCode"
    (d / "managed-settings.d").mkdir(parents=True)
    (d / "managed-settings.json").write_text(
        json.dumps({"mcpServers": {"base": {"url": "https://a.test/"}}}), encoding="utf-8"
    )
    (d / "managed-settings.d" / "50-openaca-policy.json").write_text(
        json.dumps({"mcpServers": {"dropin": {"url": "https://b.test/"}}}), encoding="utf-8"
    )

    merged = load_managed(d)

    assert merged is not None
    assert set(merged["mcpServers"]) == {"base", "dropin"}


def test_no_managed_directory_is_not_a_layer(tmp_path):
    """The common case: most endpoints have no administrator policy at all,
    and must be byte-identical to before this layer was read."""
    from tools.parsers.settings_layers import load_managed

    assert load_managed(tmp_path / "absent") is None


def test_load_accepts_an_explicit_managed_dir(tmp_path):
    """Injectable so a test never reads the real system policy directory."""
    from tools.parsers.settings_layers import load

    d = tmp_path / "ClaudeCode"
    d.mkdir()
    (d / "managed-settings.json").write_text(
        json.dumps({"mcpServers": {"corp": {"command": "true"}}}), encoding="utf-8"
    )

    layers = load(tmp_path / "install", managed_dir=d)

    assert layers.managed is not None
    assert "corp" in layers.managed["mcpServers"]
