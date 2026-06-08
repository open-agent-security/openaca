import json

from tools.bom import (
    bom_components_from_cyclonedx,
    build_agent_bom,
    component_refs_from_cyclonedx,
)
from tools.component_ref import ComponentRef


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
    assert _metadata_property(doc, "openaca:schema_version") == "0.1"
    package = _component(doc, "mcp-server/@mcpjam/inspector")
    assert package["purl"] == "pkg:npm/%40mcpjam/inspector@1.4.2"
    assert _property(package, "openaca:identity") == "mcp-server/@mcpjam/inspector"
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

    component = _component(doc, "mcp-server/playwright")
    assert component["name"] == "@playwright/mcp"
    assert component["purl"] == "pkg:npm/%40playwright/mcp@latest"
    assert _property(component, "openaca:identity") == "mcp-server/playwright"


def test_plugin_dependency_bom_keeps_purl_as_source_identity_only():
    ref = ComponentRef(
        ecosystem="npm",
        name="hono",
        version="4.12.5",
        source_manifest="external_plugins/discord/bun.lock",
        source_locator="$.packages.hono",
        attributed_to="claude-plugin/claude-plugins-official/discord@0.0.4",
        scope="agent-dependency",
    )

    doc = build_agent_bom([ref], target_type="repo", target=".").to_cyclonedx()

    component = _component(doc, "claude-plugin/claude-plugins-official/discord/deps/npm/hono")
    assert component["purl"] == "pkg:npm/hono@4.12.5"
    assert (
        _property(component, "openaca:identity")
        == "claude-plugin/claude-plugins-official/discord/deps/npm/hono"
    )


def test_cyclonedx_build_edges_resolves_versioned_attributed_to():
    """Bundled components with attributed_to='<identity>@<version>' resolve correctly.

    In real endpoint scans, a plugin is stored with versionless component_identity
    (e.g., 'claude-plugin/mktplace/name') but bundled refs receive
    attributed_to='claude-plugin/mktplace/name@1.2.3'. Without indexing the versioned
    form, _build_edges silently emits no edge and the CycloneDX graph is incomplete.
    """
    plugin = ComponentRef(
        component_identity="claude-plugin/claude-plugins-official/github",
        version="2.0.0",
        source_manifest="installed_plugins.json",
        extra={"component_type": "plugin"},
    )
    bundled_mcp = ComponentRef(
        component_identity="mcp-remote/api.githubcopilot.com/mcp/",
        source_manifest="plugin.json",
        attributed_to="claude-plugin/claude-plugins-official/github@2.0.0",
        extra={"component_type": "mcp_server"},
    )

    doc = build_agent_bom(
        [plugin, bundled_mcp], target_type="endpoint", target="~/.claude"
    ).to_cyclonedx()

    deps_by_ref = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
    plugin_bom_ref = "claude-plugin/claude-plugins-official/github"
    assert plugin_bom_ref in deps_by_ref
    assert "mcp-remote/api.githubcopilot.com/mcp/" in deps_by_ref[plugin_bom_ref]


def test_cyclonedx_dependencies_capture_plugin_attribution_edges():
    plugin = ComponentRef(
        component_identity="claude-plugin/claude-plugins-official/github@unknown",
        version="unknown",
        source_manifest="installed_plugins.json",
        extra={"component_type": "plugin"},
    )
    mcp = ComponentRef(
        component_identity="mcp-remote/api.githubcopilot.com/mcp/",
        source_manifest="plugin.json",
        attributed_to="claude-plugin/claude-plugins-official/github@unknown",
        extra={"component_type": "mcp_server"},
    )

    doc = build_agent_bom([plugin, mcp], target_type="endpoint", target="~/.claude").to_cyclonedx()

    assert {
        "ref": "claude-plugin/claude-plugins-official/github@unknown",
        "dependsOn": ["mcp-remote/api.githubcopilot.com/mcp/"],
    } in doc["dependencies"]


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
    assert all(ref.startswith("skill/bootstrap#") for ref in bom_refs)


def test_cyclonedx_dependencies_includes_all_components_including_leaves():
    refs = [
        ComponentRef(
            component_identity="claude-plugin/my-plugin",
            source_manifest="installed_plugins.json",
            extra={"component_type": "plugin"},
        ),
        ComponentRef(
            component_identity="mcp-stdio/some-server",
            source_manifest="plugin.json",
            attributed_to="claude-plugin/my-plugin",
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

    deps_by_ref = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
    assert "claude-plugin/my-plugin" in deps_by_ref
    assert "mcp-stdio/some-server" in deps_by_ref
    assert deps_by_ref["mcp-stdio/some-server"] == []
    assert "mcp-server/standalone-tool" in deps_by_ref
    assert deps_by_ref["mcp-server/standalone-tool"] == []


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
    assert refs[1].component_identity == "claude-hook/hook:abc123"
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

    assert refs[0].extra["runtime_hosts"] == ["claude-code"]
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
                component_identity="claude-plugin/marketplace/demo",
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
