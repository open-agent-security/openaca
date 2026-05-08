from pathlib import Path

from tools.parsers.mcp_json import parse, parse_mcp_servers

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_npx_emits_npm_purl():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    by_name = {r.name: r for r in refs if r.ecosystem == "npm"}
    assert by_name["@cyanheads/git-mcp-server"].version == "1.1.0"
    assert by_name["@cyanheads/git-mcp-server"].purl == "pkg:npm/%40cyanheads/git-mcp-server@1.1.0"


def test_uvx_emits_pypi_purl_when_pinned():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    by_name = {r.name: r for r in refs if r.ecosystem == "PyPI"}
    assert by_name["weather-mcp"].version == "0.5.0"
    assert by_name["weather-mcp"].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_uvx_unpinned_emits_native_identity():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    unpinned = [r for r in refs if r.component_identity and "unpinned" in r.source_locator]
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


def test_url_transport_emits_no_ref():
    """Entries without a `command` (URL/HTTP transport) must not emit binary:None."""
    servers = {"remote": {"url": "https://example.com/mcp"}}
    assert parse_mcp_servers(servers, source_manifest="fake.json") == []


def test_empty_command_emits_no_ref():
    servers = {"weird": {"command": "", "args": []}}
    assert parse_mcp_servers(servers, source_manifest="fake.json") == []


def test_mcpservers_as_list_does_not_raise():
    """A malformed `mcpServers: [...]` should yield no refs, not AttributeError."""
    refs = parse_mcp_servers([{"command": "npx"}], source_manifest="fake.json")  # type: ignore[arg-type]
    assert refs == []


def test_npx_inline_package_flag_emits_purl():
    servers = {
        "x": {
            "command": "npx",
            "args": ["--package=@scope/server@1.2.3", "--", "server-bin"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40scope/server@1.2.3"


def test_uvx_inline_from_flag_emits_purl():
    servers = {
        "y": {
            "command": "uvx",
            "args": ["--from=weather-mcp==0.5.0", "weather-mcp"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_npx_space_separated_package_flag():
    servers = {"x": {"command": "npx", "args": ["--package", "@scope/server@1.2.3", "bin"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40scope/server@1.2.3"


def test_npx_short_p_flag():
    servers = {"x": {"command": "npx", "args": ["-p", "@scope/server@1.2.3", "bin"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40scope/server@1.2.3"


def test_npx_yes_flag_does_not_become_package():
    servers = {"x": {"command": "npx", "args": ["-y", "@scope/server@1.2.3"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40scope/server@1.2.3"


def test_uvx_space_separated_from_flag():
    servers = {
        "y": {
            "command": "uvx",
            "args": ["--from", "weather-mcp==0.5.0", "weather-mcp"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_uv_tool_run_dispatches_as_uvx():
    servers = {"y": {"command": "uv", "args": ["tool", "run", "weather-mcp==0.5.0"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_disabled_server_is_skipped():
    servers = {
        "off": {
            "command": "npx",
            "args": ["@scope/server@1.0.0"],
            "disabled": True,
        }
    }
    assert parse_mcp_servers(servers, source_manifest="fake.json") == []


def test_interpolated_npx_spec_emits_no_ref():
    """`${PKG}` placeholder must not produce `pkg:npm/${PKG}` garbage."""
    servers = {"x": {"command": "npx", "args": ["${PKG_SPEC}"]}}
    assert parse_mcp_servers(servers, source_manifest="fake.json") == []


def test_interpolated_uvx_from_emits_no_ref():
    servers = {"y": {"command": "uvx", "args": ["--from=${PKG}", "tool"]}}
    assert parse_mcp_servers(servers, source_manifest="fake.json") == []


def test_vscode_servers_root_key(tmp_path):
    """VS Code's `.vscode/mcp.json` uses `servers` instead of `mcpServers`."""
    cfg = tmp_path / "mcp.json"
    cfg.write_text('{"servers": {"git": {"command": "npx", "args": ["@scope/server@1.2.3"]}}}')
    refs = parse(cfg)
    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40scope/server@1.2.3"
    assert refs[0].source_locator == "$.servers.git"


def test_top_level_array_does_not_raise(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text("[]")
    assert parse(cfg) == []


def test_uvx_positional_at_version_emits_purl():
    """uvx weather-mcp@0.5.0 positional form should emit a pinned PyPI PURL."""
    servers = {"y": {"command": "uvx", "args": ["weather-mcp@0.5.0"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_npx_absolute_path_classified_as_npx():
    """Absolute path to npx should emit npm PURL, not mcp-stdio/binary:*."""
    servers = {"x": {"command": "/usr/local/bin/npx", "args": ["@scope/server@1.2.3"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40scope/server@1.2.3"


def test_npx_dot_cmd_extension_stripped():
    """`npx.cmd` (an extension we still strip via Path.stem) classifies as npx."""
    servers = {"x": {"command": "npx.cmd", "args": ["@scope/server@1.2.3"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40scope/server@1.2.3"


def test_non_string_args_are_dropped():
    """Stray non-string args (null, int, nested) must not AttributeError."""
    servers = {
        "x": {
            "command": "npx",
            "args": [None, 42, {"nested": True}, "@scope/server@1.0.0"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40scope/server@1.0.0"


def test_uv_absolute_path_tool_run_dispatches_as_uvx():
    """`/usr/bin/uv tool run weather-mcp==0.5.0` should emit a pinned PURL."""
    servers = {"y": {"command": "/usr/bin/uv", "args": ["tool", "run", "weather-mcp==0.5.0"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_posix_uppercase_command_stays_case_sensitive():
    """POSIX is case-sensitive; /opt/NPX is a different binary, not the
    launcher. (Windows handling deferred to V1.)"""
    servers = {
        "x": {"command": "/opt/NPX", "args": ["@scope/server@1.2.3"]},
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl is None
    assert refs[0].component_identity == "mcp-stdio/binary:/opt/NPX"


def test_npx_call_flag_is_not_treated_as_package():
    """`npx -c "echo hi"` runs a shell snippet; the snippet must not be
    misclassified as a package name."""
    servers = {"x": {"command": "npx", "args": ["-c", "echo hello world"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert refs == []


def test_npx_long_call_flag_is_not_treated_as_package():
    servers = {"x": {"command": "npx", "args": ["--call", "echo hello"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert refs == []


def test_uv_bare_global_flag_before_tool_run_dispatches_as_uvx():
    """Bare global flags (no value) before `tool run` should not block dispatch."""
    servers = {
        "y": {
            "command": "uv",
            "args": ["--offline", "tool", "run", "weather-mcp==0.5.0"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_uv_value_flag_before_tool_run_dispatches_as_uvx():
    """Value-taking global flag (--directory <path>) before `tool run` works."""
    servers = {
        "y": {
            "command": "uv",
            "args": ["--directory", "/tmp/proj", "tool", "run", "weather-mcp==0.5.0"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_uv_directory_named_tool_does_not_falsely_dispatch():
    """`uv --directory tool run python` means dir=tool, run python — NOT
    `uv tool run python`. Must not falsely classify python as a uvx tool."""
    servers = {
        "y": {"command": "uv", "args": ["--directory", "tool", "run", "python"]},
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl is None
    assert refs[0].component_identity == "mcp-stdio/binary:uv"


def test_uv_inline_value_flag_before_tool_run_dispatches_as_uvx():
    """`uv --directory=/tmp tool run X` — inline value form."""
    servers = {
        "y": {
            "command": "uv",
            "args": ["--directory=/tmp", "tool", "run", "weather-mcp==0.5.0"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:pypi/weather-mcp@0.5.0"
