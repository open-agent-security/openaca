"""Task 1 regression gate (ADR-0053): the repo-surface parameterization must
not change Claude Code's repo-mode graph output at all.

`tests/fixtures/golden/repo_surface_claude_graph.json` was generated from
`tests/fixtures/repos/repo-surface-golden/` on `main`, *before* the
`RepoSurface` refactor landed, via the exact `_serialize_graph` logic below.
This test re-derives the graph post-refactor and asserts byte-identical
(structurally identical) serialization — reproducible from the fixture and
the golden file alone, no pre-refactor code required.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from tools.agent_kinds import claude_code
from tools.graph import Graph
from tools.graph_build import build_graph
from tools.parsers import REGISTRY

FIXTURE = Path(__file__).parent / "fixtures" / "repos" / "repo-surface-golden"
GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "repo_surface_claude_graph.json"


def _relativize(value: object, root_str: str) -> object:
    if isinstance(value, str):
        return value.replace(root_str, "<FIXTURE>")
    if isinstance(value, dict):
        return {k: _relativize(v, root_str) for k, v in value.items()}
    if isinstance(value, list):
        return [_relativize(v, root_str) for v in value]
    return value


def _serialize_graph(graph: Graph, fixture_root: Path) -> dict:
    root_str = str(fixture_root.resolve())
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


def test_repo_surface_golden_graph_unchanged():
    graph = build_graph(FIXTURE, mode="repo")
    graph.validate()
    actual = _serialize_graph(graph, FIXTURE)
    expected = json.loads(GOLDEN.read_text())
    assert actual == expected


def test_claude_code_manifest_patterns_matches_registry():
    assert claude_code.KIND.manifest_patterns == tuple(REGISTRY)


def test_claude_code_manifest_patterns_frozen_literal():
    """`.claude-plugin/plugin.json` and `.claude/settings.json` carry `**/`
    (task 042-3): git-wildmatch anchors a slashed pattern at the root, so the
    literal string had to change to keep the pre-`pathspec` "any depth"
    meaning — see `tools/parsers/__init__.py`."""
    assert [entry.pattern for entry in claude_code.KIND.manifest_patterns] == [
        "package.json",
        "pyproject.toml",
        "package-lock.json",
        "uv.lock",
        "bun.lock",
        "mcp.json",
        ".mcp.json",
        "claude_desktop_config.json",
        "**/.claude-plugin/plugin.json",
        "**/.claude/settings.json",
        "**/.claude/skills/*/SKILL.md",
        "**/.claude/commands/**/*.md",
        "**/.claude/agents/**/*.md",
    ]
