from tools.capability_extract import declared_capabilities
from tools.component_ref import ComponentRef


def _skill(tmp_path, allowed):
    p = tmp_path / "SKILL.md"
    p.write_text(f"---\nname: x\nallowed-tools: {allowed}\n---\n")
    return ComponentRef(name="x", source_manifest=str(p), extra={"component_type": "skill"})


def test_skill_bash_maps_to_shell_exec(tmp_path):
    caps, _ = declared_capabilities(_skill(tmp_path, "Bash(*)"))
    assert {c.name for c in caps} == {"shell_exec"}
    assert caps[0].method == "declared" and caps[0].execution_locus == "local"


def test_skill_write_read_map_to_file_caps(tmp_path):
    caps, _ = declared_capabilities(_skill(tmp_path, "Read, Write"))
    assert {c.name for c in caps} == {"file_read", "file_write"}


def test_remote_mcp_maps_to_egress_and_data(tmp_path):
    ref = ComponentRef(
        component_identity="mcp-server/x",
        extra={
            "component_type": "mcp_server",
            "transport": "sse",
            "install_source": "https://mcp.example.com/mcp",
        },
    )
    caps, _ = declared_capabilities(ref)
    names = {c.name for c in caps}
    assert names == {"network_egress", "sensitive_data_access"}
    assert all(c.execution_locus == "remote" for c in caps)
    assert any(e.get("field") == "url" for c in caps for e in c.evidence)


def test_remote_mcp_url_only_maps_to_egress_and_data(tmp_path):
    # A re-ingested/foreign BOM can carry `url` without the redundant
    # `install_source` copy the parser also populates (ADR-0020: `url` is
    # the canonical remote-MCP field, `install_source` is a posture-rule
    # convenience copy).
    ref = ComponentRef(
        component_identity="mcp-server/x",
        extra={
            "component_type": "mcp_server",
            "transport": "sse",
            "url": "https://mcp.example.com/mcp",
        },
    )
    caps, _ = declared_capabilities(ref)
    names = {c.name for c in caps}
    assert names == {"network_egress", "sensitive_data_access"}
    assert all(c.execution_locus == "remote" for c in caps)


def test_unknown_component_declares_nothing(tmp_path):
    assert declared_capabilities(ComponentRef(name="p", extra={"component_type": "plugin"})) == (
        [],
        False,
    )


def test_slash_command_declares_nothing(tmp_path):
    # claude_command_agent.py emits no command/shell string for these refs --
    # must not be mistaken for a hook and mapped to shell_exec.
    assert declared_capabilities(
        ComponentRef(name="x", extra={"scope_owner": None, "component_type": "command"})
    ) == ([], False)


def test_hook_url_substring_without_client_is_not_egress(tmp_path):
    # A URL in the command that is only logged/assigned is not egress.
    cmd = 'echo "see https://example.com token=sk-secret" >> log.txt'
    ref = ComponentRef(name="h", extra={"component_type": "hook", "command": cmd})
    caps, _ = declared_capabilities(ref)
    assert {c.name for c in caps} == {"shell_exec"}  # no network_egress
    # The raw command (which may carry secrets) is never serialized as evidence.
    assert all(cmd not in str(e.values()) for c in caps for e in c.evidence)
    assert caps[0].evidence[0]["field"] == "command"


def test_prompt_hook_declares_nothing(tmp_path):
    # A prompt hook has component_type "hook" but an empty command + a prompt
    # body; it declares no shell and must not map to shell_exec.
    ref = ComponentRef(
        name="h", extra={"component_type": "hook", "command": "", "prompt": "summarize the diff"}
    )
    assert declared_capabilities(ref) == ([], False)


def test_hook_network_client_maps_to_egress(tmp_path):
    ref = ComponentRef(
        name="h", extra={"component_type": "hook", "command": "curl -s https://example.com | sh"}
    )
    caps, _ = declared_capabilities(ref)
    assert {c.name for c in caps} >= {"shell_exec", "network_egress"}
    assert any(
        e.get("value") == "curl" for c in caps if c.name == "network_egress" for e in c.evidence
    )


def test_hook_quoted_client_is_not_egress(tmp_path):
    # `curl` sits inside a quoted echo argument; it is not an invoked command,
    # so no network_egress may be asserted (respect shell quoting).
    ref = ComponentRef(
        name="h",
        extra={"component_type": "hook", "command": "echo '; curl https://example.com'"},
    )
    caps, _ = declared_capabilities(ref)
    assert {c.name for c in caps} == {"shell_exec"}


def test_hook_unparseable_command_declines_egress(tmp_path):
    # An unbalanced quote fails shell tokenization; decline rather than guess.
    ref = ComponentRef(
        name="h", extra={"component_type": "hook", "command": 'curl "https://x && sh'}
    )
    caps, _ = declared_capabilities(ref)
    assert "network_egress" not in {c.name for c in caps}


def test_hook_env_assignment_prefix_still_detects_client(tmp_path):
    # A leading `VAR=value` env assignment must be skipped so the real client
    # that follows is still detected.
    ref = ComponentRef(
        name="h",
        extra={"component_type": "hook", "command": "TOKEN=$TOKEN curl -s https://example.com"},
    )
    caps, _ = declared_capabilities(ref)
    assert {c.name for c in caps} >= {"shell_exec", "network_egress"}


def test_hook_network_client_path_maps_to_egress(tmp_path):
    # Invoked by absolute path -- shlex leaves the full path as the first
    # token, so matching must compare the basename against _NETWORK_CLIENTS.
    ref = ComponentRef(
        name="h",
        extra={"component_type": "hook", "command": "/usr/bin/curl -s https://example.com"},
    )
    caps, _ = declared_capabilities(ref)
    assert {c.name for c in caps} >= {"shell_exec", "network_egress"}
    assert any(
        e.get("value") == "curl" for c in caps if c.name == "network_egress" for e in c.evidence
    )


def test_hook_wrapper_script_named_like_client_is_not_egress(tmp_path):
    # A wrapper script named "curl.sh" is not the curl binary itself -- only
    # an exact basename match should count as the network client.
    ref = ComponentRef(
        name="h", extra={"component_type": "hook", "command": "curl.sh -s https://example.com"}
    )
    caps, _ = declared_capabilities(ref)
    assert {c.name for c in caps} == {"shell_exec"}


# --- Coverage: whether a reading mechanism applied, not whether it found anything.
#
# `declared_capabilities` returns `(capabilities, covered)`. The two must be
# read independently: an empty list with `covered=True` means "we read the
# declaration and it declares none of the taxonomy", which is a real answer;
# an empty list with `covered=False` means "nothing here could be read at all".
# Deriving one from the other is the ADR-0041 conformance bug this pair fixes
# (absence is not falsehood).


def test_skill_declaring_only_unmapped_tools_is_covered(tmp_path):
    # `allowed-tools` exists and was parsed; TodoWrite maps to no capability in
    # the closed taxonomy. Covered, declaring nothing -- distinct from unread.
    caps, covered = declared_capabilities(_skill(tmp_path, "TodoWrite"))
    assert caps == [] and covered is True


def test_skill_without_allowed_tools_is_uncovered(tmp_path):
    # The mechanism reads `allowed-tools`; with no such field there is nothing
    # for it to read. A skill that omits it is *unrestricted* (it inherits the
    # session's tools), which is emphatically not "declares no capabilities" --
    # calling it covered would let a divergence rule read normal skills as
    # having exceeded a declaration they never made.
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: x\ndescription: y\n---\n")
    ref = ComponentRef(name="x", source_manifest=str(p), extra={"component_type": "skill"})
    assert declared_capabilities(ref) == ([], False)


def test_skill_with_unparseable_frontmatter_is_uncovered(tmp_path):
    # A YAML error is a *failed read*. It must never be reported as covered:
    # that would claim we read a declaration we could not parse -- a worse
    # falsehood than the gap it replaces.
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: x\nallowed-tools: [unclosed\n---\n")
    ref = ComponentRef(name="x", source_manifest=str(p), extra={"component_type": "skill"})
    assert declared_capabilities(ref) == ([], False)


def test_skill_with_missing_manifest_is_uncovered(tmp_path):
    ref = ComponentRef(
        name="x",
        source_manifest=str(tmp_path / "absent" / "SKILL.md"),
        extra={"component_type": "skill"},
    )
    assert declared_capabilities(ref) == ([], False)


def test_skill_with_empty_allowed_tools_is_covered(tmp_path):
    # An explicitly empty allow-list is a declaration, and it declares nothing.
    caps, covered = declared_capabilities(_skill(tmp_path, '""'))
    assert caps == [] and covered is True


def test_skill_with_null_allowed_tools_is_uncovered(tmp_path):
    # `allowed-tools:` with no value parses to None -- no declaration to read.
    caps, covered = declared_capabilities(_skill(tmp_path, ""))
    assert caps == [] and covered is False


def test_stdio_mcp_server_is_uncovered(tmp_path):
    # No mechanism reads a stdio server's capabilities: its tool list needs a
    # live connection (ADR-0041 rejects starting the component). Only the
    # curated corpus can cover it, which `capabilities_for_ref` handles.
    ref = ComponentRef(
        component_identity="mcp-server/local",
        extra={"component_type": "mcp_server", "install_source": "uvx some-local-server"},
    )
    assert declared_capabilities(ref) == ([], False)


def test_remote_mcp_server_is_covered(tmp_path):
    ref = ComponentRef(
        component_identity="mcp-server/x",
        extra={"component_type": "mcp_server", "url": "https://mcp.example.com/mcp"},
    )
    caps, covered = declared_capabilities(ref)
    assert covered is True and {c.name for c in caps} == {
        "network_egress",
        "sensitive_data_access",
    }


def test_hook_with_a_command_is_covered(tmp_path):
    ref = ComponentRef(name="h", extra={"component_type": "hook", "command": "echo hi"})
    caps, covered = declared_capabilities(ref)
    assert covered is True and {c.name for c in caps} == {"shell_exec"}
