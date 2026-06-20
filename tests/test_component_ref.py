import pytest

from tools.component_ref import (
    ComponentRef,
    canonical_component_identity,
    canonical_ecosystem,
    encode_purl_name,
    is_package_source_ref,
    safe_pinned_mcp_install_source,
    unpinned_mcp_package,
)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("simple", "simple"),
        ("@scope/name", "%40scope/name"),
        ("name with spaces", "name%20with%20spaces"),
    ],
)
def test_encode_purl_name(name, expected):
    assert encode_purl_name(name) == expected


def test_purl_for_npm_with_scope():
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.2.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    assert ref.purl == "pkg:npm/%40cyanheads/git-mcp-server@1.2.0"


def test_purl_for_pypi():
    ref = ComponentRef(
        ecosystem="PyPI",
        name="aws-mcp-server",
        version="0.3.1",
        source_manifest="requirements.txt",
        source_locator="line:5",
    )
    assert ref.purl == "pkg:pypi/aws-mcp-server@0.3.1"


def test_canonical_identity_for_package_backed_mcp_uses_server_occurrence():
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

    assert canonical_component_identity(ref) == "mcp-server/playwright"


def test_canonical_identity_prefers_stored_mcp_server_identity_when_no_component_path():
    # BOM round-trip: component was written with openaca:identity = "mcp-server/playwright"
    # and purl = pkg:npm/%40playwright/mcp@latest, but without openaca:component_path.
    # Reading it back sets ref.name = "@playwright/mcp" (from PURL) and
    # ref.component_identity = "mcp-server/playwright" (from stored identity).
    # canonical_component_identity() must return the stored identity, not
    # "mcp-server/@playwright/mcp".
    ref = ComponentRef(
        ecosystem="npm",
        name="@playwright/mcp",
        version="latest",
        source_manifest=".mcp.json",
        source_locator="$.mcpServers.playwright",
        component_identity="mcp-server/playwright",
        extra={"component_type": "mcp_server"},
    )

    assert canonical_component_identity(ref) == "mcp-server/playwright"


def test_canonical_identity_for_package_dependency_is_graph_native():
    ref = ComponentRef(
        ecosystem="npm",
        name="hono",
        version="4.12.5",
        source_manifest="external_plugins/discord/bun.lock",
        source_locator="$.packages.hono",
        scope="agent-dependency",
    )

    assert canonical_component_identity(ref) == "package/npm/hono"


def test_canonical_identity_preserves_explicit_source_less_identity():
    ref = ComponentRef(
        component_identity="claude-hook/hook:a3fd7e17b2bab038",
        extra={"component_type": "hook"},
    )

    assert canonical_component_identity(ref) == "claude-hook/hook:a3fd7e17b2bab038"


@pytest.mark.parametrize(
    "ecosystem, expected",
    [
        ("npm", "npm"),
        ("PyPI", "pypi"),
        ("GitHub", "github"),
        ("Docker", "docker"),
        (None, None),
    ],
)
def test_canonical_ecosystem(ecosystem, expected):
    assert canonical_ecosystem(ecosystem) == expected


def test_package_source_ref_accepts_bom_canonicalized_ecosystems():
    assert is_package_source_ref(ComponentRef(ecosystem="GitHub", name="org/repo"))
    assert is_package_source_ref(ComponentRef(ecosystem="Docker", name="org/image"))


def test_unpinned_mcp_package_uses_install_source_for_uv_tool_run_bom_ref():
    ref = ComponentRef(
        component_identity="mcp-server/weather",
        extra={
            "component_type": "mcp_server",
            "install_source": "uv tool run weather-mcp",
        },
    )

    assert unpinned_mcp_package(ref) == ("PyPI", "weather-mcp")


def test_unpinned_mcp_package_treats_uv_tool_run_as_uvx_launch():
    ref = ComponentRef(
        ecosystem="PyPI",
        name="weather-mcp",
        extra={"component_type": "mcp_server", "install_source": "uv tool run weather-mcp"},
    )

    assert unpinned_mcp_package(ref) == ("PyPI", "weather-mcp")


@pytest.mark.parametrize(
    "purl, name, version, expected",
    [
        ("pkg:npm/%40scope/pkg@1.2.3", "@scope/pkg", "1.2.3", "npx @scope/pkg@1.2.3"),
        ("pkg:pypi/pkg@1.2.3", "pkg", "1.2.3", "uvx pkg==1.2.3"),
        (
            "pkg:github/org/repo@0123456789abcdef0123456789abcdef01234567",
            "org/repo",
            "0123456789abcdef0123456789abcdef01234567",
            "uvx git+https://github.com/org/repo@0123456789abcdef0123456789abcdef01234567",
        ),
        ("pkg:docker/org/image@1.2.3", "org/image", "1.2.3", "docker org/image:1.2.3"),
        (
            "pkg:docker/org/image@sha256:0123",
            "org/image",
            "sha256:0123",
            "docker org/image@sha256:0123",
        ),
    ],
)
def test_safe_pinned_mcp_install_source(purl, name, version, expected):
    launcher = "docker" if purl.startswith("pkg:docker/") else "uvx"
    if purl.startswith("pkg:npm/"):
        launcher = "npx"
    assert (
        safe_pinned_mcp_install_source(launcher=launcher, purl=purl, name=name, version=version)
        == expected
    )


def test_safe_pinned_mcp_install_source_preserves_github_source_subdirectory():
    assert safe_pinned_mcp_install_source(
        launcher="uvx",
        purl="pkg:github/org/mono@0123456789abcdef0123456789abcdef01234567",
        name="org/mono",
        version="0123456789abcdef0123456789abcdef01234567",
        source_subdirectory="packages/mcp",
    ) == (
        "uvx git+https://github.com/org/mono@0123456789abcdef0123456789abcdef01234567"
        "#subdirectory=packages/mcp"
    )


def test_native_identity_for_unknown_ecosystem():
    ref = ComponentRef(
        ecosystem=None,
        name=None,
        version=None,
        source_manifest="mcp.json",
        source_locator="$.mcpServers.gh",
        component_identity="mcp-stdio/uvx-launch:some-package@unpinned",
    )
    assert ref.purl is None
    assert ref.component_identity == "mcp-stdio/uvx-launch:some-package@unpinned"


def test_attributed_to_defaults_to_none():
    ref = ComponentRef(ecosystem="npm", name="x", version="1.0")
    assert ref.attributed_to is None


def test_attributed_to_round_trips():
    ref = ComponentRef(
        ecosystem="npm",
        name="x",
        version="1.0",
        attributed_to="plugin/foo@1.0.0",
    )
    assert ref.attributed_to == "plugin/foo@1.0.0"


def test_attributed_to_participates_in_equality():
    a = ComponentRef(ecosystem="npm", name="x", version="1.0")
    b = ComponentRef(
        ecosystem="npm",
        name="x",
        version="1.0",
        attributed_to="plugin/foo@1.0.0",
    )
    assert a != b
