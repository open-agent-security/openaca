from tools.component_ref import ComponentRef
from tools.identity import (
    MatchCoordinate,
    canonical_component_identity,
    match_coordinate_for_bom,
    match_coordinates,
    mcp_package_source,
    safe_unpinned_mcp_install_source,
    unpinned_mcp_package,
)


def test_package_backed_mcp_graph_identity_keeps_package_coordinate_separate():
    ref = ComponentRef(
        ecosystem="npm",
        name="@playwright/mcp",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx @playwright/mcp",
            "component_path": [{"type": "mcp_server", "name": "playwright"}],
        },
    )

    graph_identity = canonical_component_identity(ref)

    assert graph_identity == "mcp-server/playwright"
    assert ref.purl == "pkg:npm/%40playwright/mcp"
    assert match_coordinate_for_bom(ref) is None
    assert match_coordinates(ref) == [
        MatchCoordinate(kind="package", ecosystem="npm", name="@playwright/mcp")
    ]


def test_plugin_dependency_graph_identity_uses_parent_without_observed_version():
    ref = ComponentRef(
        ecosystem="npm",
        name="hono",
        version="4.12.5",
        attributed_to="plugin/claude-plugins-official/discord@0.0.4",
    )

    assert (
        canonical_component_identity(ref) == "plugin/claude-plugins-official/discord/deps/npm/hono"
    )
    assert match_coordinate_for_bom(ref) is None


def test_source_less_remote_mcp_has_no_match_coordinate_by_default():
    ref = ComponentRef(
        component_identity="mcp-remote/api.example.com/mcp",
        extra={
            "component_type": "mcp_server",
            "component_path": [{"type": "mcp_server", "name": "example"}],
        },
    )

    graph_identity = canonical_component_identity(ref)

    assert graph_identity == "mcp-server/example"
    assert match_coordinate_for_bom(ref) is None
    assert match_coordinates(ref) == []


def test_explicit_external_match_coordinate_round_trips():
    ref = ComponentRef(
        component_identity="skill/frontend-design",
        extra={
            "component_type": "skill",
            "match_coordinate": "skills.sh:anthropics/skills/frontend-design",
        },
    )

    graph_identity = canonical_component_identity(ref)

    assert graph_identity == "skill/frontend-design"
    assert match_coordinate_for_bom(ref) == "skills.sh:anthropics/skills/frontend-design"
    assert match_coordinates(ref) == [
        MatchCoordinate(kind="external_audit", value="skills.sh:anthropics/skills/frontend-design")
    ]


def test_source_less_direct_component_identity_is_already_graph_identity():
    ref = ComponentRef(component_identity="skill/direct-skill", extra={"component_type": "skill"})

    graph_identity = canonical_component_identity(ref)

    assert graph_identity == "skill/direct-skill"
    assert match_coordinate_for_bom(ref) is None


def test_unpinned_mcp_package_uses_install_source():
    ref = ComponentRef(
        component_identity="mcp-server/weather",
        extra={
            "component_type": "mcp_server",
            "install_source": "uv tool run weather-mcp",
        },
    )

    assert unpinned_mcp_package(ref) == ("PyPI", "weather-mcp")


def test_match_coordinates_never_fall_back_to_graph_identity():
    ref = ComponentRef(
        component_identity="skill/local-helper",
        extra={"component_type": "skill"},
    )

    assert canonical_component_identity(ref) == "skill/local-helper"
    assert match_coordinates(ref) == []


def test_match_coordinates_normalize_unpinned_stdio_mcp_to_package_coordinate():
    ref = ComponentRef(
        component_identity="mcp-server/playwright",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx @playwright/mcp",
        },
    )

    assert match_coordinates(ref) == [
        MatchCoordinate(
            kind="package",
            ecosystem="npm",
            name="@playwright/mcp",
        )
    ]


def test_mcp_package_source_extracts_launcher_specific_packages():
    assert mcp_package_source("npx -y @scope/pkg --token secret") == ("npx", "npm", "@scope/pkg")
    assert mcp_package_source("npx -p @scope/pkg cmd --token secret") == (
        "npx",
        "npm",
        "@scope/pkg",
    )
    assert mcp_package_source("uvx --python 3.11 my-tool --token secret") == (
        "uvx",
        "PyPI",
        "my-tool",
    )
    assert mcp_package_source("uvx -p 3.11 my-tool --token secret") == (
        "uvx",
        "PyPI",
        "my-tool",
    )
    assert mcp_package_source("uv tool run weather-mcp --token secret") == (
        "uvx",
        "PyPI",
        "weather-mcp",
    )


def test_safe_unpinned_install_source_uses_install_source():
    assert (
        safe_unpinned_mcp_install_source(
            install_source="uv tool run weather-mcp --token secret",
        )
        == "uvx weather-mcp"
    )


def test_binary_or_local_mcp_install_source_is_not_a_package_source():
    assert mcp_package_source("python server.py --token secret") is None
    assert mcp_package_source("/usr/local/bin/server --token secret") is None
