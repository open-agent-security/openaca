from pathlib import Path

from tools.parsers.mcp_json import parse, parse_mcp_servers

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_npx_emits_npm_purl():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    by_name = {r.name: r for r in refs if r.ecosystem == "npm"}
    assert by_name["@cyanheads/git-mcp-server"].version == "1.1.0"
    assert by_name["@cyanheads/git-mcp-server"].purl == "pkg:npm/%40cyanheads/git-mcp-server@1.1.0"


def test_direct_mcp_ref_carries_output_metadata():
    servers = {
        "filesystem": {
            "command": "npx",
            "args": ["@modelcontextprotocol/server-filesystem@1.0.2"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest=".mcp.json")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.extra["component_type"] == "mcp_server"
    assert ref.extra["runtime_hosts"] == ["claude-code"]
    assert ref.extra["declared_by"] == {"kind": "manifest", "path": ".mcp.json"}


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


def test_url_entry_emits_remote_mcp_ref():
    """Per ADR-0020, an `mcpServers` entry with a `url` field emits a
    source-less ComponentRef under the `mcp-remote/<host>/<path>`
    identity namespace, with `component_type: mcp_server` and the
    original URL preserved in extra."""
    servers = {"asana": {"url": "https://mcp.asana.com/sse"}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    ref = refs[0]
    # ADR-0019 source-less shape: no ecosystem, no name, no version.
    assert ref.ecosystem is None
    assert ref.name is None
    assert ref.version is None
    # ADR-0020 identity namespace.
    assert ref.component_identity == "mcp-remote/mcp.asana.com/sse"
    # ADR-0019 component_type lives in extra.
    assert ref.extra["component_type"] == "mcp_server"
    # Transport metadata + original URL preserved verbatim.
    assert ref.extra["transport"] == "http"  # default when no `type` field
    assert ref.extra["url"] == "https://mcp.asana.com/sse"
    assert ref.source_locator == "$.mcpServers.asana"


def test_remote_mcp_records_sse_transport_when_type_is_sse():
    servers = {"asana": {"type": "sse", "url": "https://mcp.asana.com/sse"}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].extra["transport"] == "sse"
    assert refs[0].component_identity == "mcp-remote/mcp.asana.com/sse"


def test_remote_mcp_records_streamable_http_transport():
    servers = {"x": {"type": "streamableHttp", "url": "https://x.com/mcp"}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].extra["transport"] == "streamableHttp"


def test_remote_mcp_records_explicit_http_transport():
    servers = {"x": {"type": "http", "url": "https://x.com/mcp"}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].extra["transport"] == "http"


def test_remote_mcp_identity_normalizes_scheme_and_query_and_fragment():
    """Identity strips scheme, query, and fragment; original URL is kept
    in extra.url for display."""
    servers = {"x": {"url": "https://example.com/mcp?v=1&t=2#section"}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].component_identity == "mcp-remote/example.com/mcp"
    assert refs[0].extra["url"] == "https://example.com/mcp?v=1&t=2#section"


def test_remote_mcp_identity_strips_default_ports():
    """Default ports (443 for https, 80 for http) are conventional; strip
    them to keep identities stable across configurations that differ
    only in explicit-vs-implicit port."""
    for url, expected in [
        ("https://x.com:443/mcp", "mcp-remote/x.com/mcp"),
        ("http://x.com:80/mcp", "mcp-remote/x.com/mcp"),
    ]:
        refs = parse_mcp_servers({"x": {"url": url}}, source_manifest="fake.json")
        assert refs[0].component_identity == expected, url


def test_remote_mcp_identity_keeps_non_default_ports():
    servers = {"x": {"url": "http://localhost:8080/mcp"}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert refs[0].component_identity == "mcp-remote/localhost:8080/mcp"


def test_remote_mcp_identity_normalizes_empty_path_to_slash():
    servers = {"x": {"url": "https://example.com"}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert refs[0].component_identity == "mcp-remote/example.com/"


def test_remote_mcp_identity_lowercases_host_preserves_path_case():
    servers = {"x": {"url": "https://X.Example.COM/MyMcp/Path"}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert refs[0].component_identity == "mcp-remote/x.example.com/MyMcp/Path"


def test_remote_mcp_identity_strips_credentials():
    """Credentials must never appear in the identity (they'd be a logged
    secret and aren't part of the endpoint's logical name)."""
    servers = {"x": {"url": "https://user:pass@example.com/mcp"}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert refs[0].component_identity == "mcp-remote/example.com/mcp"
    # The original URL stays in extra.url verbatim — but that's a known
    # secret-surface concern (covered by future posture/redaction work,
    # out of scope for this ADR).


def test_disabled_remote_server_is_skipped():
    servers = {"x": {"url": "https://x.com/mcp", "disabled": True}}
    assert parse_mcp_servers(servers, source_manifest="fake.json") == []


def test_remote_mcp_interpolated_url_is_skipped():
    """URLs with `${...}` interpolation can't be normalized without env
    resolution; conservatively skip, matching the stdio convention."""
    servers = {"x": {"url": "https://${HOST}/mcp"}}
    assert parse_mcp_servers(servers, source_manifest="fake.json") == []


def test_remote_mcp_malformed_url_is_skipped_without_raising():
    """A URL with no parseable host should be skipped, not raise."""
    servers = {"x": {"url": "not-a-real-url"}}
    # urlparse accepts arbitrary strings; hostname will be None.
    assert parse_mcp_servers(servers, source_manifest="fake.json") == []


def test_remote_mcp_empty_url_is_skipped():
    servers = {"x": {"url": ""}}
    assert parse_mcp_servers(servers, source_manifest="fake.json") == []


def test_url_wins_over_command_when_both_present():
    """When an entry declares both `url` and `command` (malformed per the
    MCP spec), favor `url` and emit a remote ref. Matches Claude Code's
    runtime behavior of preferring URL transport when set."""
    servers = {
        "x": {
            "command": "npx",
            "args": ["@scope/pkg@1.0.0"],
            "url": "https://x.com/mcp",
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].component_identity == "mcp-remote/x.com/mcp"
    # The npm package should NOT appear as a separate ref — url wins.
    assert refs[0].ecosystem is None


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


def test_uvx_from_github_url_emits_github_purl():
    servers = {
        "serena": {
            "command": "uvx",
            "args": [
                "--from",
                "git+https://github.com/oraios/serena",
                "serena",
                "start-mcp-server",
            ],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.ecosystem == "github"
    assert ref.name == "oraios/serena"
    assert ref.version is None
    assert ref.purl == "pkg:github/oraios/serena"
    assert ref.extra["component_type"] == "mcp_server"
    assert ref.extra["install_source"] == (
        "uvx --from git+https://github.com/oraios/serena serena start-mcp-server"
    )


def test_uvx_from_github_url_keeps_commit_ref_as_version():
    servers = {
        "serena": {
            "command": "uvx",
            "args": [
                "--from=git+https://github.com/oraios/serena.git"
                "@0123456789abcdef0123456789abcdef01234567",
                "serena",
            ],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].name == "oraios/serena"
    assert refs[0].version == "0123456789abcdef0123456789abcdef01234567"
    assert refs[0].purl == "pkg:github/oraios/serena@0123456789abcdef0123456789abcdef01234567"


def test_uvx_from_github_url_with_deeper_path_is_skipped():
    servers = {
        "nested": {
            "command": "uvx",
            "args": ["--from", "git+https://github.com/oraios/serena/packages/mcp", "serena"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert refs == []


def test_docker_run_emits_docker_purl():
    servers = {
        "terraform": {
            "command": "docker",
            "args": [
                "run",
                "-i",
                "--rm",
                "-e",
                "TFE_TOKEN=${TFE_TOKEN}",
                "hashicorp/terraform-mcp-server:0.4.0",
            ],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.ecosystem == "docker"
    assert ref.name == "hashicorp/terraform-mcp-server"
    assert ref.version == "0.4.0"
    assert ref.purl == "pkg:docker/hashicorp/terraform-mcp-server@0.4.0"
    assert ref.extra["component_type"] == "mcp_server"


def test_bun_run_local_mcp_emits_local_identity():
    servers = {
        "discord": {
            "command": "bun",
            "args": ["run", "--cwd", "${CLAUDE_PLUGIN_ROOT}", "--shell=bun", "start"],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].component_identity == "mcp-stdio/local:discord"
    assert refs[0].extra["component_type"] == "mcp_server"


def test_php_artisan_mcp_emits_local_identity():
    servers = {"laravel-boost": {"command": "php", "args": ["artisan", "boost:mcp"]}}
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].component_identity == "mcp-stdio/local:laravel-boost"


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
    assert refs[0].extra["runtime_hosts"] == []


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


def test_uv_allow_insecure_host_before_tool_run_dispatches_as_uvx():
    """`uv --allow-insecure-host <HOST> tool run X` — value-taking flag."""
    servers = {
        "y": {
            "command": "uv",
            "args": [
                "--allow-insecure-host",
                "internal.example",
                "tool",
                "run",
                "weather-mcp==0.5.0",
            ],
        }
    }
    refs = parse_mcp_servers(servers, source_manifest="fake.json")
    assert len(refs) == 1
    assert refs[0].purl == "pkg:pypi/weather-mcp@0.5.0"


# Flat-shape `.mcp.json` (no `mcpServers` wrapper) — observed in real Claude
# Code plugins, e.g. claude-plugins-official/playwright ships
# `{"playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}}`.


def test_parse_flat_shape_with_command_entries(tmp_path):
    """Top-level dict whose values are server-shaped (dict with `command`)
    is parsed as a flat server map — same as if the keys lived under
    `mcpServers`. Mirrors the real Claude Code plugin convention."""
    import json

    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps(
            {
                "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
            }
        )
    )
    refs = parse(path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert any(r.name == "@playwright/mcp" for r in npm_refs)


def test_parse_flat_shape_has_no_runtime_host(tmp_path):
    import json

    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps(
            {
                "playwright": {"command": "npx", "args": ["@playwright/mcp@1.2.3"]},
            }
        )
    )

    refs = parse(path)

    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40playwright/mcp@1.2.3"
    assert refs[0].extra["runtime_hosts"] == []


def test_parse_flat_shape_multiple_servers(tmp_path):
    """Flat shape with multiple entries — all parsed."""
    import json

    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps(
            {
                "a": {"command": "npx", "args": ["@org/a@1.0.0"]},
                "b": {"command": "npx", "args": ["@org/b@2.0.0"]},
            }
        )
    )
    refs = parse(path)
    by_name = {r.name: r for r in refs if r.ecosystem == "npm"}
    assert "@org/a" in by_name
    assert "@org/b" in by_name


def test_parse_flat_shape_url_entries_do_not_crash(tmp_path):
    """HTTP-transport (`url`) entries in flat shape are recognized by the
    shape heuristic. V0 doesn't emit refs for them, but the parser must
    not raise."""
    import json

    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"http-mcp": {"url": "https://example.com/mcp"}}))
    refs = parse(path)
    # Nothing emitted for URL transport in V0; just ensure no crash.
    assert isinstance(refs, list)


def test_parse_flat_shape_not_triggered_when_wrapper_present(tmp_path):
    """If `mcpServers` exists, the wrapped shape wins — flat-shape detection
    is the fallback only."""
    import json

    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "real": {"command": "npx", "args": ["@org/real@1.0.0"]},
                },
                "playwright": {"command": "npx", "args": ["@org/should-not-appear@1.0.0"]},
            }
        )
    )
    refs = parse(path)
    by_name = {r.name for r in refs if r.ecosystem == "npm"}
    assert "@org/real" in by_name
    assert "@org/should-not-appear" not in by_name


def test_parse_flat_shape_rejects_dict_with_unrelated_keys(tmp_path):
    """An object like `{name: "...", description: "..."}` should NOT be
    misdetected as a flat server map. Strict all-values-server-shaped
    check guards against this."""
    import json

    path = tmp_path / "plugin.json"
    path.write_text(json.dumps({"name": "myplugin", "description": "stuff"}))
    refs = parse(path)
    assert refs == []


def test_parse_flat_shape_rejects_empty_dict(tmp_path):
    """An empty top-level dict isn't a flat server map."""
    import json

    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({}))
    assert parse(path) == []
