from tools.component_ref import ComponentRef
from tools.identity import (
    canonical_component_identity,
    mcp_package_source,
    safe_unpinned_mcp_install_source,
    source_identity_for_bom,
    unpinned_mcp_package,
)


def test_package_backed_mcp_graph_identity_keeps_package_coordinate_separate():
    ref = ComponentRef(
        ecosystem="npm",
        name="@playwright/mcp",
        version="latest",
        component_identity="mcp-stdio/npx-unpinned:@playwright/mcp",
        extra={
            "component_type": "mcp_server",
            "component_path": [{"type": "mcp_server", "name": "playwright"}],
        },
    )

    graph_identity = canonical_component_identity(ref)

    assert graph_identity == "mcp-server/playwright"
    assert ref.purl == "pkg:npm/%40playwright/mcp@latest"
    assert source_identity_for_bom(ref, graph_identity) == "mcp-stdio/npx-unpinned:@playwright/mcp"


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
    assert source_identity_for_bom(ref, canonical_component_identity(ref)) is None


def test_source_less_remote_mcp_keeps_source_identity_for_matching():
    ref = ComponentRef(
        component_identity="mcp-remote/api.example.com/mcp",
        extra={
            "component_type": "mcp_server",
            "component_path": [{"type": "mcp_server", "name": "example"}],
        },
    )

    graph_identity = canonical_component_identity(ref)

    assert graph_identity == "mcp-server/example"
    assert source_identity_for_bom(ref, graph_identity) == "mcp-remote/api.example.com/mcp"


def test_reconstructed_bom_ref_keeps_source_identity_for_second_serialization():
    ref = ComponentRef(
        component_identity="mcp-server/example",
        extra={
            "component_type": "mcp_server",
            "source_identity": "mcp-remote/api.example.com/mcp",
        },
    )

    graph_identity = canonical_component_identity(ref)

    assert graph_identity == "mcp-server/example"
    assert source_identity_for_bom(ref, graph_identity) == "mcp-remote/api.example.com/mcp"


def test_source_less_direct_component_identity_is_already_graph_identity():
    ref = ComponentRef(component_identity="skill/direct-skill", extra={"component_type": "skill"})

    graph_identity = canonical_component_identity(ref)

    assert graph_identity == "skill/direct-skill"
    assert source_identity_for_bom(ref, graph_identity) is None


def test_unpinned_mcp_package_prefers_source_identity_over_install_source():
    ref = ComponentRef(
        component_identity="mcp-server/weather",
        extra={
            "component_type": "mcp_server",
            "source_identity": "mcp-stdio/uvx-unpinned:weather-mcp",
            "install_source": "uv tool run different-package",
        },
    )

    assert unpinned_mcp_package(ref) == ("PyPI", "weather-mcp")


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


def test_safe_unpinned_install_source_uses_source_identity_then_install_source():
    assert (
        safe_unpinned_mcp_install_source(
            identity="mcp-server/weather",
            source_identity="mcp-stdio/uvx-unpinned:weather-mcp",
            component_name="mcp-server/weather",
            install_source="uv tool run different-package",
        )
        == "uvx weather-mcp"
    )
    assert (
        safe_unpinned_mcp_install_source(
            identity="mcp-server/weather",
            source_identity=None,
            component_name="mcp-server/weather",
            install_source="uv tool run weather-mcp --token secret",
        )
        == "uvx weather-mcp"
    )


def test_binary_or_local_mcp_install_source_is_not_a_package_source():
    assert mcp_package_source("python server.py --token secret") is None
    assert mcp_package_source("/usr/local/bin/server --token secret") is None
