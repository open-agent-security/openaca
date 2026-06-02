import pytest

from tools.component_ref import (
    ComponentRef,
    canonical_ecosystem,
    encode_purl_name,
    is_package_source_ref,
    safe_pinned_mcp_install_source,
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
        attributed_to="claude-plugin/foo@1.0.0",
    )
    assert ref.attributed_to == "claude-plugin/foo@1.0.0"


def test_attributed_to_participates_in_equality():
    a = ComponentRef(ecosystem="npm", name="x", version="1.0")
    b = ComponentRef(
        ecosystem="npm",
        name="x",
        version="1.0",
        attributed_to="claude-plugin/foo@1.0.0",
    )
    assert a != b
