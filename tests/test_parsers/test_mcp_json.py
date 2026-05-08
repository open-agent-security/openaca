from pathlib import Path

from tools.parsers.mcp_json import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_npx_emits_npm_purl():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    by_name = {r.name: r for r in refs if r.ecosystem == "npm"}
    assert by_name["@cyanheads/git-mcp-server"].version == "1.1.0"
    assert (
        by_name["@cyanheads/git-mcp-server"].purl
        == "pkg:npm/%40cyanheads/git-mcp-server@1.1.0"
    )


def test_uvx_emits_pypi_purl_when_pinned():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    by_name = {r.name: r for r in refs if r.ecosystem == "PyPI"}
    assert by_name["weather-mcp"].version == "0.5.0"
    assert by_name["weather-mcp"].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_uvx_unpinned_emits_native_identity():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    unpinned = [
        r for r in refs if r.component_identity and "unpinned" in r.source_locator
    ]
    assert len(unpinned) == 1
    assert unpinned[0].component_identity == "mcp-stdio/uvx-unpinned:sketchy-mcp"


def test_binary_command_emits_native_identity():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    binary = [
        r
        for r in refs
        if r.component_identity and r.component_identity.startswith("mcp-stdio/binary:")
    ]
    assert len(binary) == 1
    identity = binary[0].component_identity
    assert identity is not None
    assert "/opt/local/bin/custom-mcp-server" in identity


def test_source_locator_jsonpath():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    git = [r for r in refs if r.name == "@cyanheads/git-mcp-server"][0]
    assert git.source_locator == "$.mcpServers.git"
