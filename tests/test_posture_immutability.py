import pytest

from tools.posture.immutability import is_mutable_reference


@pytest.mark.parametrize(
    "ref,expected",
    [
        # npx — unpinned forms
        ("npx @modelcontextprotocol/server-foo", True),
        ("npx @modelcontextprotocol/server-foo@latest", True),
        ("npx mcp-server-bar", True),
        # npx — pinned
        ("npx @modelcontextprotocol/server-foo@1.0.0", False),
        ("npx mcp-server-bar@2.3.4", False),
        # npx — partial-version (not exact)
        ("npx @modelcontextprotocol/server-foo@1.2", True),
        ("npx @modelcontextprotocol/server-foo@^1.0.0", True),
        # uvx — unpinned
        ("uvx mcp-server-bar", True),
        ("uvx mcp-server-bar>=1.0", True),
        # uvx — pinned
        ("uvx mcp-server-bar==1.0.0", False),
        ("uvx mcp-server-bar==2.5.1", False),
        # Git refs — branch / tag / SHA
        ("git+https://github.com/x/y.git#main", True),
        ("git+https://github.com/x/y.git@main", True),
        ("git+https://github.com/x/y.git@v1.0.0", True),  # tag is mutable
        ("git+https://github.com/x/y.git", True),  # no ref pinned
        # Full 40-char SHA → immutable
        ("git+https://github.com/x/y.git@a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0", False),
        # Docker — mutable tags / no digest
        ("ghcr.io/github/github-mcp-server:latest", True),
        ("ghcr.io/github/github-mcp-server", True),  # no tag = latest
        ("ghcr.io/github/github-mcp-server:1.0.0", True),  # tag is mutable
        ("docker.io/library/python:3.11", True),
        # Docker — digest-pinned → immutable
        (
            "ghcr.io/github/github-mcp-server@sha256:"
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            False,
        ),
        # npx — flag forms: --package, -p, --package=value
        ("npx --package @scope/pkg@1.2.3 -- cmd", False),  # pinned via --package
        ("npx -p @scope/pkg@1.2.3 -- cmd", False),  # pinned via -p
        ("npx --package=@scope/pkg@1.2.3 -- cmd", False),  # pinned via --package=
        ("npx --package @scope/pkg -- cmd", True),  # --package but no version
        # uvx — --from flag forms
        ("uvx --from mcp-bar==1.0.0 mcp-bar", False),  # pinned via --from
        ("uvx --from mcp-bar mcp-bar", True),  # --from but no version
        # uv tool run — equivalent to uvx; pinned spec is immutable
        ("uv tool run mcp-bar==1.0.0", False),
        ("uv tool run mcp-bar@1.2.3", False),
        ("uv tool run mcp-bar", True),  # no version
        ("uv tool run mcp-bar>=1.0", True),  # range, not exact
        ("uv --offline tool run mcp-bar==1.0.0", False),  # leading flag (no value)
        # value-taking uv flags — the value token must be skipped too
        ("uv --directory /tmp tool run mcp-bar==1.0.0", False),
        ("uv --directory /tmp tool run mcp-bar", True),  # value-taking flag, no pin
        ("uv --python 3.11 --offline tool run mcp-bar==2.0.0", False),  # mixed flags
        ("uv serve something", True),  # unrecognized subcommand
        # Local paths — never flag (not a remote install ref)
        ("./local/plugin", False),
        ("/Users/x/plugins/foo", False),
        ("file:///opt/plugin", False),
        ("~/plugins/foo", False),
        # http/https URLs (non-git) — handled by other rules
        ("https://example.com/mcp", False),
        ("http://example.com/mcp", False),
        # git+ssh with userinfo (git@host) + SHA in '#' fragment — immutable
        (
            "git+ssh://git@github.com/org/repo.git#a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
            False,
        ),
        # git+ssh with userinfo + branch name in '#' fragment — mutable
        ("git+ssh://git@github.com/org/repo.git#main", True),
        # git+ssh with userinfo + no fragment — mutable
        ("git+ssh://git@github.com/org/repo.git", True),
    ],
)
def test_is_mutable_reference(ref: str, expected: bool):
    assert is_mutable_reference(ref) is expected, ref
