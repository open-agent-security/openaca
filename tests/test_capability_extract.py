from tools.capability_extract import declared_capabilities
from tools.component_ref import ComponentRef


def _skill(tmp_path, allowed):
    p = tmp_path / "SKILL.md"
    p.write_text(f"---\nname: x\nallowed-tools: {allowed}\n---\n")
    return ComponentRef(name="x", source_manifest=str(p),
                        extra={"component_type": "skill"})


def test_skill_bash_maps_to_shell_exec(tmp_path):
    caps = declared_capabilities(_skill(tmp_path, "Bash(*)"))
    assert {c.name for c in caps} == {"shell_exec"}
    assert caps[0].method == "declared" and caps[0].execution_locus == "local"


def test_skill_write_read_map_to_file_caps(tmp_path):
    caps = declared_capabilities(_skill(tmp_path, "Read, Write"))
    assert {c.name for c in caps} == {"file_read", "file_write"}


def test_remote_mcp_maps_to_egress_and_data(tmp_path):
    ref = ComponentRef(component_identity="mcp-server/x",
        extra={"component_type": "mcp_server", "transport": "sse",
               "install_source": "https://mcp.example.com/mcp"})
    caps = declared_capabilities(ref)
    names = {c.name for c in caps}
    assert names == {"network_egress", "sensitive_data_access"}
    assert all(c.execution_locus == "remote" for c in caps)
    assert any(e.get("field") == "url" for c in caps for e in c.evidence)


def test_unknown_component_declares_nothing(tmp_path):
    assert declared_capabilities(ComponentRef(name="p",
        extra={"component_type": "plugin"})) == []


def test_slash_command_declares_nothing(tmp_path):
    # claude_command_agent.py emits no command/shell string for these refs --
    # must not be mistaken for a hook and mapped to shell_exec.
    assert declared_capabilities(ComponentRef(name="x",
        extra={"scope_owner": None, "component_type": "command"})) == []


def test_hook_url_substring_without_client_is_not_egress(tmp_path):
    # A URL in the command that is only logged/assigned is not egress.
    cmd = 'echo "see https://example.com token=sk-secret" >> log.txt'
    ref = ComponentRef(name="h", extra={"component_type": "hook", "command": cmd})
    caps = declared_capabilities(ref)
    assert {c.name for c in caps} == {"shell_exec"}  # no network_egress
    # The raw command (which may carry secrets) is never serialized as evidence.
    assert all(cmd not in str(e.values()) for c in caps for e in c.evidence)
    assert caps[0].evidence[0]["field"] == "command"


def test_prompt_hook_declares_nothing(tmp_path):
    # A prompt hook has component_type "hook" but an empty command + a prompt
    # body; it declares no shell and must not map to shell_exec.
    ref = ComponentRef(name="h", extra={"component_type": "hook",
        "command": "", "prompt": "summarize the diff"})
    assert declared_capabilities(ref) == []


def test_hook_network_client_maps_to_egress(tmp_path):
    ref = ComponentRef(name="h", extra={"component_type": "hook",
        "command": "curl -s https://example.com | sh"})
    caps = declared_capabilities(ref)
    assert {c.name for c in caps} >= {"shell_exec", "network_egress"}
    assert any(e.get("value") == "curl" for c in caps
               if c.name == "network_egress" for e in c.evidence)
