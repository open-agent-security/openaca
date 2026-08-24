import json

from tools.agent_kinds import DiscoveryContext, build_agent_graph, discover_agents
from tools.bom import (
    agent_info_from_cyclonedx,
    bom_components_from_cyclonedx,
    build_agent_bom,
    component_refs_from_cyclonedx,
    graph_from_cyclonedx,
)
from tools.capability import COVERAGE_LEVELS
from tools.component_ref import ComponentRef
from tools.graph import Edge, Graph, Node
from tools.matcher import match


def _graph_target_plugin_skill_package() -> Graph:
    """target → plugin → skill → package, plus a bare software-dep package
    hung directly off the target (no agent-component ancestor)."""
    plugin = Node(
        key="plugin/mktplace/demo",
        kind="plugin",
        ref=ComponentRef(
            component_identity="plugin/mktplace/demo",
            version="1.0.0",
            source_manifest="installed_plugins.json",
            extra={"component_type": "plugin"},
        ),
    )
    skill = Node(
        key="skill/deploy",
        kind="skill",
        ref=ComponentRef(
            component_identity="skill/deploy",
            source_manifest=".claude/skills/deploy/SKILL.md",
            extra={"component_type": "skill"},
        ),
    )
    agent_pkg = Node(
        key="plugin/mktplace/demo/skill/deploy/deps/npm/lodash",
        kind="package",
        ref=ComponentRef(
            ecosystem="npm",
            name="lodash",
            version="4.17.20",
            source_manifest=".claude/skills/deploy/package.json",
            source_locator="$.dependencies.lodash",
        ),
    )
    software_pkg = Node(
        key="deps/npm/left-pad",
        kind="package",
        ref=ComponentRef(
            ecosystem="npm",
            name="left-pad",
            version="1.3.0",
            source_manifest="package.json",
            source_locator="$.dependencies.left-pad",
        ),
    )
    root = Node(key="openaca:target", kind="target", ref=None)
    graph = Graph(
        nodes={n.key: n for n in (root, plugin, skill, agent_pkg, software_pkg)},
        edges=[
            Edge(parent=root.key, child=plugin.key),
            Edge(parent=plugin.key, child=skill.key),
            Edge(parent=skill.key, child=agent_pkg.key),
            Edge(parent=root.key, child=software_pkg.key),
        ],
    )
    graph.validate()
    return graph


def test_graph_bom_target_is_metadata_component_not_in_components():
    graph = _graph_target_plugin_skill_package()

    doc = build_agent_bom(
        [], target_type="endpoint", target="~/.claude", graph=graph
    ).to_cyclonedx()

    assert doc["metadata"]["component"]["bom-ref"] == "openaca:target"
    assert _property(doc["metadata"]["component"], "openaca:component_type") == "target"
    assert "openaca:target" not in {c["bom-ref"] for c in doc["components"]}


def test_graph_bom_component_bom_ref_equals_node_key_and_edges_match_graph():
    graph = _graph_target_plugin_skill_package()

    doc = build_agent_bom(
        [], target_type="endpoint", target="~/.claude", graph=graph
    ).to_cyclonedx()

    bom_refs = {c["bom-ref"] for c in doc["components"]}
    assert bom_refs == {
        "plugin/mktplace/demo",
        "skill/deploy",
        "plugin/mktplace/demo/skill/deploy/deps/npm/lodash",
    }
    deps_by_ref = {d["ref"]: set(d["dependsOn"]) for d in doc["dependencies"]}
    # Edge whose parent is the target's bom-ref.
    assert "plugin/mktplace/demo" in deps_by_ref["openaca:target"]
    assert deps_by_ref["plugin/mktplace/demo"] == {"skill/deploy"}
    assert deps_by_ref["skill/deploy"] == {"plugin/mktplace/demo/skill/deploy/deps/npm/lodash"}


def test_graph_bom_component_refs_carry_bom_ref_extra():
    # component_refs() feeds posture/observation collectors, which join
    # findings back to a component via `ref.extra["bom_ref"]` (ADR-0042:
    # occurrence-level signals attach by bom-ref). It must be stamped here,
    # the same way `_refs_from_graph`/`_collect_endpoint_components` stamp it
    # for the non-graph-reconstructed ref list.
    graph = _graph_target_plugin_skill_package()

    bom = build_agent_bom([], target_type="endpoint", target="~/.claude", graph=graph)

    bom_ref_by_key = {c.bom_ref: (c.ref.extra or {}).get("bom_ref") for c in bom.components}
    assert bom_ref_by_key == {
        "plugin/mktplace/demo": "plugin/mktplace/demo",
        "skill/deploy": "skill/deploy",
        "plugin/mktplace/demo/skill/deploy/deps/npm/lodash": (
            "plugin/mktplace/demo/skill/deploy/deps/npm/lodash"
        ),
    }


def test_graph_bom_excludes_software_dependency_nodes_from_components():
    graph = _graph_target_plugin_skill_package()

    doc = build_agent_bom(
        [], target_type="endpoint", target="~/.claude", graph=graph
    ).to_cyclonedx()

    bom_refs = {c["bom-ref"] for c in doc["components"]}
    assert "deps/npm/left-pad" not in bom_refs
    # The software-dep node is not an included endpoint, so no edge references it.
    deps_by_ref = {d["ref"]: set(d["dependsOn"]) for d in doc["dependencies"]}
    assert "deps/npm/left-pad" not in deps_by_ref
    assert "deps/npm/left-pad" not in deps_by_ref["openaca:target"]


def test_graph_from_cyclonedx_round_trips_nodes_and_edges():
    # [correct-new-behavior] A BOM produced by build_agent_bom reconstructs the
    # graph's agent-scope projection: same node keys + kinds and the same edge
    # set. The software-dependency package is excluded from components[] at emit
    # time, so it does not round-trip — assert against the included set.
    g = _graph_target_plugin_skill_package()

    doc = build_agent_bom([], target_type="endpoint", target="~/.claude", graph=g).to_cyclonedx()
    g2 = graph_from_cyclonedx(doc)

    g2.validate()
    assert g2.root.key == "openaca:target"

    included = {
        k: n
        for k, n in g.nodes.items()
        if n.ref is None or g.scope_of(n) in {"agent-component", "agent-dependency"}
    }
    assert {n.key: n.kind for n in g2.nodes.values()} == {k: n.kind for k, n in included.items()}
    included_edges = {
        (e.parent, e.child) for e in g.edges if e.parent in included and e.child in included
    }
    assert {(e.parent, e.child) for e in g2.edges} == included_edges


def test_graph_from_cyclonedx_tolerates_foreign_dependson_parent():
    # [bug-fixed] A dependencies[] entry whose `ref` is not any component's
    # bom-ref (a foreign parent) must be a no-op, not a dangling edge that
    # crashes validate(). The children fall back to direct target children.
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "component": {"bom-ref": "openaca:target", "type": "application", "name": "t"}
        },
        "components": [
            {
                "type": "library",
                "bom-ref": "pkg:npm/lodash@4.17.20",
                "name": "lodash",
                "version": "4.17.20",
                "purl": "pkg:npm/lodash@4.17.20",
                "properties": [{"name": "openaca:component_type", "value": "package"}],
            }
        ],
        "dependencies": [
            {"ref": "pkg:npm/not-a-real-component", "dependsOn": ["pkg:npm/lodash@4.17.20"]}
        ],
    }

    graph = graph_from_cyclonedx(doc)

    graph.validate()
    child = graph.nodes["pkg:npm/lodash@4.17.20"]
    assert {(e.parent, e.child) for e in graph.edges} == {
        ("openaca:target", "pkg:npm/lodash@4.17.20")
    }
    assert graph.lineage(child)[-1].key == "openaca:target"


def test_graph_bom_drops_openaca_attributed_to_property():
    graph = _graph_target_plugin_skill_package()

    doc = build_agent_bom(
        [], target_type="endpoint", target="~/.claude", graph=graph
    ).to_cyclonedx()

    for component in doc["components"]:
        assert _property(component, "openaca:attributed_to") is None


def test_bom_components_from_cyclonedx_pairs_refs_with_bom_refs():
    original = build_agent_bom(
        [
            ComponentRef(
                ecosystem="npm",
                name="hono",
                version="4.12.5",
                source_manifest="bun.lock",
                source_locator="$.packages.hono",
                extra={"transitive": True},
            ),
            ComponentRef(
                ecosystem="github",
                name="oraios/serena",
                version="0123456789abcdef0123456789abcdef01234567",
                source_manifest=".mcp.json",
                source_locator="$.mcpServers.serena",
                extra={"component_type": "mcp_server"},
            ),
        ],
        target_type="repo",
        target=".",
    )
    encoded = json.loads(json.dumps(original.to_cyclonedx()))

    components = bom_components_from_cyclonedx(encoded)

    # bom-refs are preserved from the doc, matching what build_agent_bom assigned.
    assert [c.bom_ref for c in components] == [c.bom_ref for c in original.components]
    # The bare-ref reconstruction is exactly the .ref of each paired component.
    assert [c.ref for c in components] == component_refs_from_cyclonedx(encoded)
    # Reconstruction is faithful to the original identities.
    assert [c.ref.purl for c in components] == [
        "pkg:npm/hono@4.12.5",
        "pkg:github/oraios/serena@0123456789abcdef0123456789abcdef01234567",
    ]


def test_cyclonedx_serializes_package_and_openaca_identity_components():
    refs = [
        ComponentRef(
            ecosystem="npm",
            name="@mcpjam/inspector",
            version="1.4.2",
            source_manifest=".mcp.json",
            source_locator="$.mcpServers.inspector",
            extra={"component_type": "mcp_server"},
        ),
        ComponentRef(
            component_identity="mcp-remote/api.example.com/mcp",
            source_manifest="settings.json",
            source_locator="$.mcpServers.remote",
            extra={"component_type": "mcp_server"},
        ),
    ]

    doc = build_agent_bom(refs, target_type="repo", target=".").to_cyclonedx()

    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.7"
    assert _metadata_property(doc, "openaca:schema_version") == "0.5"
    package = _component(doc, "mcp-server/npm/@mcpjam/inspector")
    assert package["type"] == "application"
    assert package["purl"] == "pkg:npm/%40mcpjam/inspector@1.4.2"
    assert _property(package, "openaca:identity") == "mcp-server/npm/@mcpjam/inspector"
    assert _property(package, "openaca:component_type") == "mcp_server"
    remote = _component(doc, "mcp-remote/api.example.com/mcp")
    assert _property(remote, "openaca:identity") == "mcp-remote/api.example.com/mcp"
    assert "vulnerabilities" not in doc


def test_package_backed_mcp_bom_uses_agent_graph_identity_as_bom_ref():
    ref = ComponentRef(
        ecosystem="npm",
        name="@playwright/mcp",
        version="latest",
        source_manifest=".mcp.json",
        source_locator="$.mcpServers.playwright",
        extra={
            "component_type": "mcp_server",
            "component_path": [{"type": "mcp_server", "name": "playwright"}],
        },
    )

    doc = build_agent_bom(
        [ref], target_type="endpoint", target="endpoint:user-scope"
    ).to_cyclonedx()

    component = _component(doc, "mcp-server/npm/@playwright/mcp")
    assert component["name"] == "@playwright/mcp"
    assert component["purl"] == "pkg:npm/%40playwright/mcp@latest"
    assert _property(component, "openaca:identity") == "mcp-server/npm/@playwright/mcp"


def test_remote_mcp_bom_does_not_invent_match_coordinate():
    ref = ComponentRef(
        component_identity="mcp-remote/api.example.com/mcp",
        source_manifest=".mcp.json",
        source_locator="$.mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "transport": "http",
            "url": "https://api.example.com/mcp",
            "component_path": [{"type": "mcp_server", "name": "example"}],
        },
    )

    doc = build_agent_bom(
        [ref], target_type="endpoint", target="endpoint:user-scope"
    ).to_cyclonedx()

    component = _component(doc, "mcp-remote/api.example.com/mcp")
    assert _property(component, "openaca:identity") == "mcp-remote/api.example.com/mcp"
    assert _property(component, "openaca:match_coordinate") is None
    refs = component_refs_from_cyclonedx(doc)
    assert refs[0].component_identity == "mcp-remote/api.example.com/mcp"
    assert "match_coordinate" not in refs[0].extra
    assert match(refs=refs, advisories=[]) == []


def test_explicit_external_match_coordinate_round_trips_for_matching():
    ref = ComponentRef(
        component_identity="skill/frontend-design",
        source_manifest="skills/frontend-design/SKILL.md",
        source_locator="$",
        extra={
            "component_type": "skill",
            "match_coordinate": "skills.sh:anthropics/skills/frontend-design",
        },
    )
    advisory = {
        "id": "MAL-2026-SKILL",
        "affected": [],
        "database_specific": {
            "openaca": {"match_coordinate": "skills.sh:anthropics/skills/frontend-design"}
        },
    }

    doc = build_agent_bom(
        [ref], target_type="endpoint", target="endpoint:user-scope"
    ).to_cyclonedx()

    component = _component(doc, "skill/skills.sh/anthropics/skills/frontend-design")
    assert (
        _property(component, "openaca:identity")
        == "skill/skills.sh/anthropics/skills/frontend-design"
    )
    assert (
        _property(component, "openaca:match_coordinate")
        == "skills.sh:anthropics/skills/frontend-design"
    )
    refs = component_refs_from_cyclonedx(doc)
    assert refs[0].component_identity == "skill/skills.sh/anthropics/skills/frontend-design"
    assert refs[0].extra["match_coordinate"] == "skills.sh:anthropics/skills/frontend-design"
    findings = match(refs=refs, advisories=[advisory])
    assert len(findings) == 1
    assert findings[0].advisory_id == "MAL-2026-SKILL"
    assert findings[0].confidence == "high"


def test_uv_tool_run_bom_uses_purl_and_install_context_for_matching():
    ref = ComponentRef(
        ecosystem="PyPI",
        name="weather-mcp",
        source_manifest=".mcp.json",
        source_locator="$.mcpServers.weather",
        extra={
            "component_type": "mcp_server",
            "install_source": "uv tool run weather-mcp",
            "component_path": [{"type": "mcp_server", "name": "weather"}],
        },
    )
    advisory = {
        "id": "CVE-2026-UVTOOL",
        "affected": [
            {
                "package": {"ecosystem": "PyPI", "name": "weather-mcp"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": "1.0.0"}],
                    }
                ],
            }
        ],
    }

    doc = build_agent_bom(
        [ref], target_type="endpoint", target="endpoint:user-scope"
    ).to_cyclonedx()

    component = _component(doc, "mcp-server/pypi/weather-mcp")
    assert component["purl"] == "pkg:pypi/weather-mcp"
    assert _property(component, "openaca:identity") == "mcp-server/pypi/weather-mcp"
    assert _property(component, "openaca:match_coordinate") is None
    refs = component_refs_from_cyclonedx(doc)
    assert refs[0].component_identity == "mcp-server/pypi/weather-mcp"
    assert "match_coordinate" not in refs[0].extra
    findings = match(refs=refs, advisories=[advisory])
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-UVTOOL"
    assert findings[0].confidence == "unknown"


def test_plugin_dependency_bom_keeps_purl_as_match_coordinate_only():
    ref = ComponentRef(
        ecosystem="npm",
        name="hono",
        version="4.12.5",
        source_manifest="external_plugins/discord/bun.lock",
        source_locator="$.packages.hono",
        scope="agent-dependency",
    )

    doc = build_agent_bom([ref], target_type="repo", target=".").to_cyclonedx()

    component = _component(doc, "package/npm/hono")
    assert component["type"] == "library"
    assert component["purl"] == "pkg:npm/hono@4.12.5"
    assert _property(component, "openaca:identity") == "package/npm/hono"
    assert _property(component, "openaca:component_type") == "package"
    assert _property(component, "openaca:scope") == "agent-dependency"
    refs = component_refs_from_cyclonedx(doc)
    assert refs[0].extra["component_type"] == "package"


def test_skill_artifact_coordinates_round_trip_through_bom():
    ref = ComponentRef(
        component_identity="skill/deploy-helper",
        name="deploy-helper",
        source_manifest=".claude/skills/deploy-helper/SKILL.md",
        source_locator="$.frontmatter",
        extra={
            "component_type": "skill",
            "artifact_coordinates": [
                {
                    "kind": "skill-content-hash",
                    "algorithm": "sha256",
                    "value": "sha256:abc123",
                }
            ],
        },
    )

    doc = build_agent_bom(
        [ref], target_type="endpoint", target="endpoint:user-scope"
    ).to_cyclonedx()

    component = doc["components"][0]
    assert _property(component, "openaca:identity") is None
    artifact_coordinates = _property(component, "openaca:artifact_coordinates")
    assert artifact_coordinates is not None
    assert json.loads(artifact_coordinates) == [
        {"kind": "skill-content-hash", "algorithm": "sha256", "value": "sha256:abc123"}
    ]
    (round_tripped,) = component_refs_from_cyclonedx(doc)
    assert round_tripped.extra["artifact_coordinates"] == [
        {"kind": "skill-content-hash", "algorithm": "sha256", "value": "sha256:abc123"}
    ]


def test_duplicate_preferred_bom_refs_get_stable_suffixes():
    refs = [
        ComponentRef(
            component_identity="skill/bootstrap",
            source_manifest=".claude/skills/a/SKILL.md",
            extra={"component_type": "skill"},
        ),
        ComponentRef(
            component_identity="skill/bootstrap",
            source_manifest=".claude/skills/b/SKILL.md",
            extra={"component_type": "skill"},
        ),
    ]

    doc = build_agent_bom(refs, target_type="repo", target=".").to_cyclonedx()
    bom_refs = [c["bom-ref"] for c in doc["components"]]

    assert len(set(bom_refs)) == 2
    assert all(ref.startswith("openaca:unidentified:") for ref in bom_refs)


def test_cyclonedx_dependencies_includes_all_components_including_leaves():
    refs = [
        ComponentRef(
            component_identity="plugin/my-plugin",
            source_manifest="installed_plugins.json",
            extra={"component_type": "plugin"},
        ),
        ComponentRef(
            component_identity="mcp-stdio/some-server",
            source_manifest="plugin.json",
            extra={"component_type": "mcp_server"},
        ),
        ComponentRef(
            ecosystem="npm",
            name="standalone-tool",
            version="1.0.0",
            source_manifest=".mcp.json",
            extra={"component_type": "mcp_server"},
        ),
    ]

    doc = build_agent_bom(refs, target_type="endpoint", target="~/.claude").to_cyclonedx()

    # A flat ref list (no Graph) carries no composition edges, so every component
    # is a leaf in dependencies[] with an empty dependsOn. Edges come from the
    # graph-backed path (see graph_from_cyclonedx round-trip tests).
    deps_by_ref = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
    unidentified_refs = [ref for ref in deps_by_ref if ref.startswith("openaca:unidentified:")]
    assert len(unidentified_refs) == 2
    assert all(deps_by_ref[ref] == [] for ref in unidentified_refs)
    assert "mcp-server/npm/standalone-tool" in deps_by_ref
    assert deps_by_ref["mcp-server/npm/standalone-tool"] == []


def test_cyclonedx_round_trips_components_needed_for_matching():
    original = build_agent_bom(
        [
            ComponentRef(
                ecosystem="PyPI",
                name="fastmcp",
                version="2.0.0",
                source_manifest="pyproject.toml",
                source_locator="project.dependencies",
                extra={"component_type": "mcp_server"},
            ),
            ComponentRef(
                component_identity="claude-hook/hook:abc123",
                source_manifest="settings.json",
                source_locator="$.hooks.PreToolUse[0]",
                extra={"component_type": "hook"},
            ),
        ],
        target_type="repo",
        target=".",
    )
    encoded = json.loads(json.dumps(original.to_cyclonedx()))

    refs = component_refs_from_cyclonedx(encoded)

    assert refs[0].ecosystem == "PyPI"
    assert refs[0].name == "fastmcp"
    assert refs[0].version == "2.0.0"
    assert refs[0].purl == "pkg:pypi/fastmcp@2.0.0"
    assert refs[1].component_identity is None
    assert refs[1].extra["component_type"] == "hook"


def test_cyclonedx_round_trips_output_context_metadata():
    original = build_agent_bom(
        [
            ComponentRef(
                ecosystem="npm",
                name="@cyanheads/git-mcp-server",
                version="1.1.0",
                source_manifest="/repo/sample-mcp/mcp.json",
                source_locator="$.mcpServers.git",
                extra={
                    "component_type": "mcp_server",
                    "runtime_hosts": ["claude-code"],
                    "declared_by": {"kind": "manifest", "path": "/repo/sample-mcp/mcp.json"},
                    "component_path": [{"type": "mcp_server", "name": "git"}],
                    "install_source": "npx @cyanheads/git-mcp-server@1.1.0",
                    "transport": "stdio",
                },
            )
        ],
        target_type="repo",
        target="/repo",
    )
    encoded = json.loads(json.dumps(original.to_cyclonedx()))

    refs = component_refs_from_cyclonedx(encoded)
    component = encoded["components"][0]

    assert _property(component, "openaca:agent_host") is None
    assert _property(component, "openaca:runtime_hosts") is None
    assert refs[0].extra["declared_by"] == {
        "kind": "manifest",
        "path": "/repo/sample-mcp/mcp.json",
    }
    assert refs[0].extra["component_path"] == [{"type": "mcp_server", "name": "git"}]
    assert refs[0].extra["install_source"] == "npx @cyanheads/git-mcp-server@1.1.0"
    assert refs[0].extra["transport"] == "stdio"


def test_cyclonedx_round_trips_plugin_scope_and_git_commit_sha():
    """extra["scope"] (enabling scope) and extra["gitCommitSha"] survive BOM encode/decode.

    render.py uses both for plugin tree headers ([scope=...] and sha: ...).
    Without serializing them, BOM-then-scan output showed [scope=None] for any
    plugin that had been installed via the endpoint install state.
    """
    original = build_agent_bom(
        [
            ComponentRef(
                component_identity="plugin/marketplace/demo",
                version="1.2.3",
                source_manifest="installed_plugins.json",
                source_locator="$.plugins.marketplace/demo[0]",
                extra={
                    "component_type": "plugin",
                    "runtime_hosts": ["claude-code"],
                    "scope": "user",
                    "gitCommitSha": "deadbeef1234abcd",
                },
            )
        ],
        target_type="endpoint",
        target="~/.claude",
    )
    encoded = json.loads(json.dumps(original.to_cyclonedx()))

    refs = component_refs_from_cyclonedx(encoded)

    assert refs[0].extra["scope"] == "user"
    assert refs[0].extra["gitCommitSha"] == "deadbeef1234abcd"


def test_parse_purl_strips_qualifiers_and_subpath():
    """PURLs with qualifiers (?...) or subpath (#...) must yield a clean version."""
    doc = {
        "components": [
            {
                "type": "library",
                "bom-ref": "pkg:npm/foo@1.2.3?arch=x64",
                "name": "foo",
                "purl": "pkg:npm/foo@1.2.3?arch=x64",
            },
            {
                "type": "library",
                "bom-ref": "pkg:pypi/bar@2.0.0#subpath",
                "name": "bar",
                "purl": "pkg:pypi/bar@2.0.0#subpath",
            },
            {
                "type": "library",
                "bom-ref": "pkg:npm/baz@3.0.0?os=linux#lib/main",
                "name": "baz",
                "purl": "pkg:npm/baz@3.0.0?os=linux#lib/main",
            },
        ]
    }

    refs = component_refs_from_cyclonedx(doc)

    assert refs[0].version == "1.2.3"
    assert refs[1].version == "2.0.0"
    assert refs[2].version == "3.0.0"


def test_infer_unpinned_mcp_package_skips_launcher_flags():
    """scan bom must recover the package when npx/uvx argv has launcher flags before it.

    npx -y @scope/pkg: -y is a flag with no value, must be skipped to find @scope/pkg.
    uvx --python 3.11 my-tool: --python takes a value (3.11), must skip both tokens.
    """
    doc = {
        "components": [
            {
                "type": "library",
                "bom-ref": "mcp-server/npx-mcp",
                "name": "mcp-server/npx-mcp",
                "properties": [
                    {"name": "openaca:identity", "value": "mcp-server/npx-mcp"},
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    {"name": "openaca:install_source", "value": "npx -y @scope/pkg"},
                    {"name": "openaca:source_manifest", "value": ".mcp.json"},
                    {"name": "openaca:source_locator", "value": "$.mcpServers.npx-mcp"},
                ],
            },
            {
                "type": "library",
                "bom-ref": "mcp-server/uvx-mcp",
                "name": "mcp-server/uvx-mcp",
                "properties": [
                    {"name": "openaca:identity", "value": "mcp-server/uvx-mcp"},
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    {"name": "openaca:install_source", "value": "uvx --python 3.11 my-tool"},
                    {"name": "openaca:source_manifest", "value": ".mcp.json"},
                    {"name": "openaca:source_locator", "value": "$.mcpServers.uvx-mcp"},
                ],
            },
        ]
    }

    refs = component_refs_from_cyclonedx(doc)

    assert refs[0].ecosystem == "npm"
    assert refs[0].name == "@scope/pkg"
    assert refs[1].ecosystem == "PyPI"
    assert refs[1].name == "my-tool"


def test_infer_unpinned_mcp_package_prefers_package_flag():
    """npx --package @scope/pkg cmd must resolve to @scope/pkg, not cmd."""
    doc = {
        "components": [
            {
                "type": "library",
                "bom-ref": "mcp-server/pkg-flag-mcp",
                "name": "mcp-server/pkg-flag-mcp",
                "properties": [
                    {"name": "openaca:identity", "value": "mcp-server/pkg-flag-mcp"},
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    {"name": "openaca:install_source", "value": "npx --package @scope/pkg cmd"},
                    {"name": "openaca:source_manifest", "value": ".mcp.json"},
                    {"name": "openaca:source_locator", "value": "$.mcpServers.pkg-flag-mcp"},
                ],
            },
        ]
    }

    refs = component_refs_from_cyclonedx(doc)

    assert refs[0].ecosystem == "npm"
    assert refs[0].name == "@scope/pkg"


def test_infer_unpinned_mcp_package_option_terminator():
    """npx -- @scope/pkg must resolve to @scope/pkg (documented `npm exec -- <pkg>` form)."""
    doc = {
        "components": [
            {
                "type": "library",
                "bom-ref": "mcp-server/terminator-mcp",
                "name": "mcp-server/terminator-mcp",
                "properties": [
                    {"name": "openaca:identity", "value": "mcp-server/terminator-mcp"},
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    {"name": "openaca:install_source", "value": "npx -- @scope/pkg"},
                    {"name": "openaca:source_manifest", "value": ".mcp.json"},
                    {"name": "openaca:source_locator", "value": "$.mcpServers.terminator-mcp"},
                ],
            },
        ]
    }

    refs = component_refs_from_cyclonedx(doc)

    assert refs[0].ecosystem == "npm"
    assert refs[0].name == "@scope/pkg"


def test_infer_unpinned_uvx_package_skips_short_python_flag():
    """uvx -p 3.11 my-tool must treat -p as --python, not a package flag."""
    doc = {
        "components": [
            {
                "type": "library",
                "bom-ref": "mcp-server/uvx-short-python",
                "name": "mcp-server/uvx-short-python",
                "properties": [
                    {"name": "openaca:identity", "value": "mcp-server/uvx-short-python"},
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    {"name": "openaca:install_source", "value": "uvx -p 3.11 my-tool"},
                    {"name": "openaca:source_manifest", "value": ".mcp.json"},
                    {
                        "name": "openaca:source_locator",
                        "value": "$.mcpServers.uvx-short-python",
                    },
                ],
            },
        ]
    }

    refs = component_refs_from_cyclonedx(doc)

    assert refs[0].ecosystem == "PyPI"
    assert refs[0].name == "my-tool"


def _skill_with_bash(tmp_path) -> ComponentRef:
    manifest = tmp_path / "SKILL.md"
    manifest.write_text("---\nname: x\nallowed-tools: Bash(*)\n---\n", encoding="utf-8")
    return ComponentRef(
        component_identity="skill/x",
        source_manifest=str(manifest),
        extra={"component_type": "skill"},
    )


def _graph_with_bash_skill(tmp_path) -> Graph:
    manifest = tmp_path / "SKILL.md"
    manifest.write_text("---\nname: x\nallowed-tools: Bash(*)\n---\n", encoding="utf-8")
    skill = Node(
        key="skill/x",
        kind="skill",
        ref=ComponentRef(
            component_identity="skill/x",
            source_manifest=str(manifest),
            extra={"component_type": "skill"},
        ),
    )
    root = Node(key="openaca:target", kind="target", ref=None)
    return Graph(
        nodes={n.key: n for n in (root, skill)},
        edges=[Edge(parent=root.key, child=skill.key)],
    )


def test_build_agent_bom_annotates_plain_refs(tmp_path):
    ref = _skill_with_bash(tmp_path)
    bom = build_agent_bom([ref], target_type="repo")
    out_ref = bom.components[0].ref
    assert out_ref.extra["capability_coverage"] in {"partial", "complete"}
    assert any(c["name"] == "shell_exec" for c in out_ref.extra["capabilities"])


def test_build_agent_bom_annotates_graph_refs(tmp_path):
    graph = _graph_with_bash_skill(tmp_path)
    bom = build_agent_bom([], target_type="repo", graph=graph)
    skill = next(c.ref for c in bom.components if c.ref.extra.get("component_type") == "skill")
    assert skill.extra["capability_coverage"] in {"partial", "complete"}
    assert any(c["name"] == "shell_exec" for c in skill.extra["capabilities"])


def test_build_agent_bom_preserves_reingested_capabilities():
    capability = {
        "name": "shell_exec",
        "execution_locus": "local",
        "method": "curated",
        "source": "openaca",
        "source_version": "0.4.0",
        "confidence": "high",
        "evidence": [
            {
                "kind": "curated_review",
                "reviewed_version": "1.0",
                "last_reviewed": "2026-07-03",
            }
        ],
    }
    ref = ComponentRef(
        component_identity="skill/x",
        source_manifest=".claude/skills/x/SKILL.md",
        extra={
            "component_type": "skill",
            "capabilities": [capability],
            "capability_coverage": "complete",
        },
    )
    bom = build_agent_bom([ref], target_type="repo")
    out_ref = bom.components[0].ref
    assert out_ref.extra["capability_coverage"] == "complete"
    assert out_ref.extra["capabilities"] == [capability]


def test_build_agent_bom_recomputes_half_annotated_ref(tmp_path):
    ref = ComponentRef(
        component_identity="skill/x",
        source_manifest=str(_skill_with_bash(tmp_path).source_manifest),
        extra={"component_type": "skill", "capability_coverage": "complete"},
    )
    bom = build_agent_bom([ref], target_type="repo")
    out_ref = bom.components[0].ref
    assert out_ref.extra["capability_coverage"] in COVERAGE_LEVELS
    assert isinstance(out_ref.extra["capabilities"], list)
    assert any(c["name"] == "shell_exec" for c in out_ref.extra["capabilities"])


def _curated_capability() -> dict:
    return {
        "name": "shell_exec",
        "execution_locus": "local",
        "method": "curated",
        "source": "openaca",
        "source_version": "0.4.0",
        "confidence": "high",
        "evidence": [
            {
                "kind": "curated_review",
                "reviewed_version": "1.0",
                "last_reviewed": "2026-07-03",
            }
        ],
    }


def test_bom_emits_capability_descriptors():
    ref = ComponentRef(
        component_identity="mcp-server/x",
        extra={
            "component_type": "mcp_server",
            "capabilities": [_curated_capability()],
            "capability_coverage": "partial",
        },
    )
    doc = build_agent_bom([ref], target_type="repo").to_cyclonedx()
    props = _props(doc["components"][0])
    assert json.loads(props["openaca:capabilities"])[0]["name"] == "shell_exec"
    assert props["openaca:capability_coverage"] == "partial"
    assert _metadata_property(doc, "openaca:schema_version") == "0.5"


def test_bom_emits_coverage_for_uncovered_component():
    # No declared/curated signal: capabilities=[] but coverage must still emit —
    # dropping it here would recreate the silent-empty state ADR-0041 rule 2 forbids.
    ref = ComponentRef(
        component_identity="plugin/y",
        extra={
            "component_type": "plugin",
            "capabilities": [],
            "capability_coverage": "unknown",
        },
    )
    doc = build_agent_bom([ref], target_type="repo").to_cyclonedx()
    props = _props(doc["components"][0])
    assert json.loads(props["openaca:capabilities"]) == []
    assert props["openaca:capability_coverage"] == "unknown"


def test_bom_roundtrips_capability_properties():
    # scan bom rebuilds refs via component_refs_from_cyclonedx(), which restores
    # extra from properties through an explicit allow-list (_extra_from_properties).
    # Descriptors must survive that round trip or a re-ingested BOM silently loses
    # them.
    ref = ComponentRef(
        component_identity="mcp-server/x",
        extra={
            "component_type": "mcp_server",
            "capabilities": [_curated_capability()],
            "capability_coverage": "partial",
        },
    )
    doc = build_agent_bom([ref], target_type="repo").to_cyclonedx()
    rebuilt = component_refs_from_cyclonedx(doc)[0]
    assert rebuilt.extra["capabilities"][0]["name"] == "shell_exec"
    assert rebuilt.extra["capability_coverage"] == "partial"


def test_reingest_drops_malformed_capability_items():
    # A BOM whose openaca:capabilities parses as a list but has an invalid item
    # (unknown name) must not be treated as annotated: restore neither property so
    # Task 7 recomputes rather than re-emitting an invalid descriptor.
    doc = {
        "components": [
            {
                "bom-ref": "mcp-server/x",
                "type": "application",
                "properties": [
                    {"name": "openaca:capability_coverage", "value": "complete"},
                    {
                        "name": "openaca:capabilities",
                        "value": json.dumps([{"name": "not_a_capability"}]),
                    },
                ],
            }
        ]
    }
    rebuilt = component_refs_from_cyclonedx(doc)[0]
    assert "capabilities" not in rebuilt.extra
    assert "capability_coverage" not in rebuilt.extra


def _props(component: dict) -> dict[str, str]:
    return {prop["name"]: prop["value"] for prop in component.get("properties", [])}


def _metadata_property(doc: dict, name: str) -> str | None:
    for prop in doc["metadata"]["properties"]:
        if prop["name"] == name:
            return prop["value"]
    return None


def _property(component: dict, name: str) -> str | None:
    for prop in component.get("properties", []):
        if prop["name"] == name:
            return prop["value"]
    return None


def _component(doc: dict, bom_ref: str) -> dict:
    for component in doc["components"]:
        if component["bom-ref"] == bom_ref:
            return component
    raise AssertionError(f"missing component {bom_ref}")


def test_agent_bom_metadata_component_is_the_agent(tmp_path):
    root = tmp_path / ".claude"
    (root / "skills" / "deploy").mkdir(parents=True)
    (root / "skills" / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: d\n---\n", encoding="utf-8"
    )
    agent = discover_agents(DiscoveryContext(source="installed", config_dir=root))[0]
    graph = build_agent_graph(agent)

    doc = build_agent_bom(
        [],
        graph=graph,
        target=str(root),
        agent_kind=agent.kind_id,
        agent_name=agent.display_name,
        composition_source=agent.source,
        composition_coverage="complete",
    ).to_cyclonedx()

    component = doc["metadata"]["component"]
    assert component["bom-ref"] == "root/claude-code"
    assert component["name"] == "Claude Code"
    props = {p["name"]: p["value"] for p in component["properties"]}
    assert props == {
        "openaca:agent_kind": "claude-code",
        "openaca:composition_source": "installed",
        "openaca:composition_coverage": "complete",
    }
    metadata_props = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
    assert metadata_props["openaca:schema_version"] == "0.5"
    assert "openaca:target_type" not in metadata_props
    assert any(c["bom-ref"].startswith("claude-code/") for c in doc["components"])


def test_legacy_place_rooted_bom_keeps_schema_version_0_4():
    """The remote collector (`tools/remote/collector.py`) still builds a
    graph-backed BOM without `agent_kind`, which `_metadata_component`
    serializes via its legacy branch: `metadata.component` keyed on the
    synthetic target rather than `root/<kind>`, plus `openaca:target_type`/
    `openaca:target` properties. Schema `0.5` is defined as the agent-rooted
    shape (ADR-0044), so a document in this pre-0.5 shape must keep claiming
    `0.4` — the version its shape actually is — or a version-aware consumer
    reads `0.5` and expects a shape it isn't getting."""
    root = Node(key="openaca:target", kind="target", ref=None)
    graph = Graph(nodes={root.key: root})

    doc = build_agent_bom(
        [], target_type="endpoint", target="endpoint:user-scope", graph=graph
    ).to_cyclonedx()

    assert _metadata_property(doc, "openaca:schema_version") == "0.4"
    assert doc["metadata"]["component"]["bom-ref"] == "openaca:target"


def test_agent_bom_stops_writing_agent_host_and_runtime_hosts(tmp_path):
    root = tmp_path / ".claude"
    root.mkdir()
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )
    agent = discover_agents(DiscoveryContext(source="installed", config_dir=root))[0]
    doc = build_agent_bom(
        [],
        graph=build_agent_graph(agent),
        agent_kind="claude-code",
        agent_name="Claude Code",
        composition_source="installed",
        composition_coverage="complete",
    ).to_cyclonedx()

    names = {p["name"] for c in doc["components"] for p in c.get("properties", [])}
    assert "openaca:agent_host" not in names
    assert "openaca:runtime_hosts" not in names


def test_agent_info_round_trips():
    doc = {
        "metadata": {
            "component": {
                "bom-ref": "root/claude-code",
                "name": "Claude Code",
                "properties": [
                    {"name": "openaca:agent_kind", "value": "claude-code"},
                    {"name": "openaca:composition_source", "value": "installed"},
                    {"name": "openaca:composition_coverage", "value": "complete"},
                ],
            }
        }
    }

    info = agent_info_from_cyclonedx(doc)

    assert info is not None
    assert (info.kind, info.agent_id, info.source, info.coverage) == (
        "claude-code",
        None,
        "installed",
        "complete",
    )


def test_agent_info_is_none_for_a_stored_0_4_document():
    doc = {"metadata": {"component": {"bom-ref": "openaca:target", "name": "/home/u/.claude"}}}

    assert agent_info_from_cyclonedx(doc) is None


def test_stored_0_4_runtime_hosts_are_still_restored():
    """Removing a property means stopping the write, not the read: a stored 0.4
    document still carries both, and `component_refs_from_cyclonedx` restores
    them into `extra` for `scan bom`."""
    stored = {
        "components": [
            {
                "type": "application",
                "bom-ref": "mcp-server/npm/@x/gh",
                "name": "@x/gh",
                "properties": [
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    {"name": "openaca:agent_host", "value": "claude-code"},
                    {"name": "openaca:runtime_hosts", "value": '["claude-code"]'},
                ],
            }
        ]
    }

    refs = component_refs_from_cyclonedx(stored)

    assert refs[0].extra["runtime_hosts"] == ["claude-code"]
    assert refs[0].extra["agent_host"] == "claude-code"
