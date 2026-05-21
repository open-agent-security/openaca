import json

from tools.bom import build_agent_bom, component_refs_from_cyclonedx
from tools.component_ref import ComponentRef


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
    package = _component(doc, "pkg:npm/%40mcpjam/inspector@1.4.2")
    assert package["purl"] == "pkg:npm/%40mcpjam/inspector@1.4.2"
    assert _property(package, "openaca:component_type") == "mcp_server"
    remote = _component(doc, "mcp-remote/api.example.com/mcp")
    assert _property(remote, "openaca:identity") == "mcp-remote/api.example.com/mcp"
    assert "vulnerabilities" not in doc


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
    purl = "pkg:npm/standalone-tool@1.0.0"
    assert purl in deps_by_ref
    assert deps_by_ref[purl] == []


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
