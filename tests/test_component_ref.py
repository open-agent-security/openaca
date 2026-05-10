import pytest

from tools.component_ref import ComponentRef, encode_purl_name


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
