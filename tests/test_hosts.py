from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.hosts import HOSTS, all_host_ids, detected_hosts


def test_claude_code_registered():
    assert "claude-code" in HOSTS
    adapter = HOSTS["claude-code"]
    assert adapter.host_id == "claude-code"
    assert adapter.manifest_registry  # non-empty, reuses existing REGISTRY


def test_all_host_ids_stable_order():
    assert all_host_ids() == ["claude-code", "cursor"]


def test_detect_claude_code_config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert HOSTS["claude-code"].detect() is False
    (tmp_path / ".claude").mkdir()
    assert HOSTS["claude-code"].detect() is True


def test_detect_claude_code_respects_env_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-claude-dir"
    override.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(override))
    assert HOSTS["claude-code"].detect() is True


def test_detected_hosts_reflects_env(tmp_path, monkeypatch):
    override = tmp_path / "custom-claude-dir"
    override.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(override))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert detected_hosts() == ["claude-code"]


def test_cursor_registered():
    assert "cursor" in HOSTS
    adapter = HOSTS["cursor"]
    assert adapter.host_id == "cursor"
    # api_endpoint_override is Claude-schema-specific; Cursor must not run it.
    assert "openaca-posture-api-endpoint-override" not in adapter.posture_rule_ids
    assert "openaca-posture-insecure-transport" in adapter.posture_rule_ids
    # mcp_auto_approve keys on a manifest-level autoApprove field that's
    # specific to Claude Code's mcp.json — verified against Cursor's own
    # MCP docs (cursor.com/docs/context/mcp): approval there is Run-Modes/
    # UI state with no documented per-server manifest equivalent. Cursor
    # must not run it either (see Task 9's owning_host-gated fix in
    # mcp_auto_approve.py itself, which is the actual enforcement point —
    # this membership is kept accurate for readers of the adapter, not
    # load-bearing on its own).
    assert "openaca-posture-mcp-auto-approve" not in adapter.posture_rule_ids


def test_all_host_ids_includes_cursor_after_claude_code():
    assert all_host_ids() == ["claude-code", "cursor"]


def test_detect_cursor_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert HOSTS["cursor"].detect() is False
    (tmp_path / ".cursor").mkdir()
    assert HOSTS["cursor"].detect() is True


def test_claude_code_adapter_has_seed_endpoint():
    assert HOSTS["claude-code"].seed_endpoint is not None


def test_cursor_adapter_has_seed_endpoint():
    assert HOSTS["cursor"].seed_endpoint is not None


def test_hosts_module_has_no_static_graph_build_dependency():
    import sys

    saved = {name: mod for name, mod in sys.modules.items() if name.startswith("tools.")}
    for name in saved:
        del sys.modules[name]
    try:
        import tools.hosts  # noqa: F401

        assert "tools.graph_build" not in sys.modules
        assert "tools.endpoint_seeds.claude_code" not in sys.modules
        assert "tools.posture" not in sys.modules
    finally:
        sys.modules.update(saved)


def test_claude_code_collect_endpoint_posture_manifests_matches_collect_endpoint_mcp_manifests(
    tmp_path,
):
    from tools.posture import collect_endpoint_mcp_manifests

    plugin_install = tmp_path / "plugin-install"
    plugin_install.mkdir()
    (plugin_install / "mcp.json").write_text(
        json.dumps({"mcpServers": {"tool": {"command": "npx", "args": ["x"]}}})
    )
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    (config_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"direct": {"command": "npx", "args": ["y"]}}})
    )
    refs = [
        ComponentRef(
            name="demo",
            extra={"component_type": "plugin", "installPath": str(plugin_install)},
        )
    ]

    collect = HOSTS["claude-code"].collect_endpoint_posture_manifests
    assert collect is not None
    expected = collect_endpoint_mcp_manifests(config_dir, None, refs)
    actual = collect(config_dir, None, refs)

    assert actual == expected
    assert len(expected) == 2  # sanity: both the plugin manifest and the direct .mcp.json


def test_cursor_collect_endpoint_posture_manifests_returns_global_and_project_tuples(tmp_path):
    config_root = tmp_path / "cursor-config"
    config_root.mkdir()
    (config_root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"global": {"url": "http://global.example.com/mcp"}}})
    )
    project_root = tmp_path / "project"
    (project_root / ".cursor").mkdir(parents=True)
    (project_root / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"proj": {"url": "http://proj.example.com/mcp"}}})
    )

    collect = HOSTS["cursor"].collect_endpoint_posture_manifests
    assert collect is not None
    result = collect(config_root, project_root, [])

    by_path = dict(result)
    assert set(by_path) == {config_root / "mcp.json", project_root / ".cursor" / "mcp.json"}
    assert (
        by_path[config_root / "mcp.json"]["mcpServers"]["global"]["url"]
        == "http://global.example.com/mcp"
    )
    assert (
        by_path[project_root / ".cursor" / "mcp.json"]["mcpServers"]["proj"]["url"]
        == "http://proj.example.com/mcp"
    )


def test_cursor_collect_endpoint_posture_manifests_drops_malformed_manifest(tmp_path):
    config_root = tmp_path / "cursor-config"
    config_root.mkdir()
    (config_root / "mcp.json").write_text("not json {")

    collect = HOSTS["cursor"].collect_endpoint_posture_manifests
    assert collect is not None
    result = collect(config_root, None, [])

    assert result == []


def test_cursor_posture_skips_never_realized_claude_sibling_manifest(tmp_path):
    # A Cursor bundle shipping a `.claude-plugin/plugin.json` sibling: Cursor
    # realization never reads that manifest, so its inline mcpServers must not
    # surface as Cursor posture.
    bundle = tmp_path / "plugins" / "local" / "demo"
    (bundle / ".cursor-plugin").mkdir(parents=True)
    (bundle / ".cursor-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}))
    (bundle / ".claude-plugin").mkdir()
    (bundle / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "mcpServers": {"planted": {"url": "http://evil/mcp"}}})
    )
    config_root = tmp_path
    ref = ComponentRef(
        name="demo",
        component_identity="plugin/demo",
        source_manifest=str(bundle / ".cursor-plugin" / "plugin.json"),
        source_locator="$",
        extra={"component_type": "plugin", "runtime_hosts": ["cursor"]},
    )

    collect = HOSTS["cursor"].collect_endpoint_posture_manifests
    assert collect is not None
    result = collect(config_root, None, [ref])

    parents = {path.parent.name for path, _ in result}
    assert ".claude-plugin" not in parents
    assert ".cursor-plugin" in parents


def test_claude_endpoint_posture_skips_never_realized_cursor_sibling_manifest(tmp_path):
    # Mirror case: a Claude plugin install path shipping a
    # `.cursor-plugin/plugin.json` must not have that manifest attributed to
    # claude-code posture.
    from tools.posture import collect_endpoint_mcp_manifests

    install = tmp_path / "install" / "demo"
    (install / ".claude-plugin").mkdir(parents=True)
    (install / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}))
    (install / ".cursor-plugin").mkdir()
    (install / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "mcpServers": {"planted": {"url": "http://evil/mcp"}}})
    )
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    ref = ComponentRef(
        name="demo",
        component_identity="plugin/demo",
        source_manifest="installed_plugins.json",
        source_locator="$",
        extra={"component_type": "plugin", "installPath": str(install)},
    )

    result = collect_endpoint_mcp_manifests(config_dir, None, [ref])
    parents = {path.parent.name for path, _ in result}
    assert ".cursor-plugin" not in parents
    assert ".claude-plugin" in parents


def test_cursor_posture_skips_native_manifest_that_lost_to_agent_plugins_fallback(tmp_path):
    # A `.cursor-plugin/plugin.json` with no `name` can't produce a plugin
    # self ref, so `_realize_plugin_bundle` falls back to the bundle's Agent
    # Plugins root manifest — Cursor's own realization never loads the
    # nameless native manifest. Its inline `mcpServers` (an `http://` entry)
    # must not surface as Cursor posture even though the file is still on
    # disk under the bundle root the winning ref resolves to.
    bundle = tmp_path / "plugins" / "local" / "demo"
    (bundle / ".cursor-plugin").mkdir(parents=True)
    (bundle / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"mcpServers": {"planted": {"url": "http://evil/mcp"}}})
    )
    bundle_root_manifest = bundle / "plugin.json"
    bundle_root_manifest.write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    config_root = tmp_path
    winning_ref = ComponentRef(
        name="demo",
        component_identity="plugin/demo",
        source_manifest=str(bundle_root_manifest),
        source_locator="$",
        extra={"component_type": "plugin", "runtime_hosts": ["cursor"]},
    )

    collect = HOSTS["cursor"].collect_endpoint_posture_manifests
    assert collect is not None
    result = collect(config_root, None, [winning_ref])

    assert result == []


def test_cursor_posture_skips_nested_manifest_under_untracked_bundle_root(tmp_path):
    # `_realize_plugin_bundle` only ever reads the bundle-root
    # `.cursor-plugin/plugin.json`. A nested fixture several levels under a
    # real (tracked) bundle root — e.g. a demo/example directory shipped
    # inside the plugin — is still picked up by the recursive `rglob` in
    # `collect_mcp_manifests`, but its own two-level-up ancestor is not
    # itself a tracked bundle root, so it must be dropped rather than
    # surfaced as if Cursor had loaded it.
    bundle = tmp_path / "plugins" / "local" / "demo"
    (bundle / ".cursor-plugin").mkdir(parents=True)
    (bundle / ".cursor-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}))
    nested = bundle / "examples" / "fixture" / ".cursor-plugin"
    nested.mkdir(parents=True)
    (nested / "plugin.json").write_text(
        json.dumps({"mcpServers": {"planted": {"url": "http://evil/mcp"}}})
    )
    config_root = tmp_path
    winning_ref = ComponentRef(
        name="demo",
        component_identity="plugin/demo",
        source_manifest=str(bundle / ".cursor-plugin" / "plugin.json"),
        source_locator="$",
        extra={"component_type": "plugin", "runtime_hosts": ["cursor"]},
    )

    collect = HOSTS["cursor"].collect_endpoint_posture_manifests
    assert collect is not None
    result = collect(config_root, None, [winning_ref])

    parents = {path.parent for path, _ in result}
    assert nested not in parents
    assert result == [(bundle / ".cursor-plugin" / "plugin.json", {"name": "demo"})]


def test_cursor_posture_skips_nested_standalone_mcp_manifest_never_read_by_bundle(tmp_path):
    # Regression guard (Codex finding on commit 21a7be8257): a nested
    # `examples/demo/mcp.json` fixture under a real, tracked bundle root is
    # never read by `_realize_plugin_bundle` (which only reads the bundle's
    # default `mcp.json` or a manifest-declared custom path) and so never
    # produces an `mcp_server` ref. It must not surface as Cursor posture
    # just because it happens to exist on disk somewhere under the bundle.
    bundle = tmp_path / "plugins" / "local" / "demo"
    (bundle / ".cursor-plugin").mkdir(parents=True)
    (bundle / ".cursor-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}))
    nested = bundle / "examples" / "demo"
    nested.mkdir(parents=True)
    (nested / "mcp.json").write_text(
        json.dumps({"mcpServers": {"planted": {"url": "http://evil/mcp"}}})
    )
    config_root = tmp_path
    winning_ref = ComponentRef(
        name="demo",
        component_identity="plugin/demo",
        source_manifest=str(bundle / ".cursor-plugin" / "plugin.json"),
        source_locator="$",
        extra={"component_type": "plugin", "runtime_hosts": ["cursor"]},
    )

    collect = HOSTS["cursor"].collect_endpoint_posture_manifests
    assert collect is not None
    result = collect(config_root, None, [winning_ref])

    assert result == [(bundle / ".cursor-plugin" / "plugin.json", {"name": "demo"})]


def test_cursor_posture_includes_bundle_default_mcp_manifest_that_was_actually_realized(tmp_path):
    # The counterpart to the regression above: a bundle's default `mcp.json`
    # DID get realized into an `mcp_server` ref (as real seeding would
    # produce), so its content must still surface as Cursor posture.
    bundle = tmp_path / "plugins" / "local" / "demo"
    (bundle / ".cursor-plugin").mkdir(parents=True)
    (bundle / ".cursor-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}))
    bundle_mcp = bundle / "mcp.json"
    bundle_mcp.write_text(json.dumps({"mcpServers": {"tool": {"url": "http://insecure/mcp"}}}))
    config_root = tmp_path
    plugin_ref = ComponentRef(
        name="demo",
        component_identity="plugin/demo",
        source_manifest=str(bundle / ".cursor-plugin" / "plugin.json"),
        source_locator="$",
        extra={"component_type": "plugin", "runtime_hosts": ["cursor"]},
    )
    mcp_ref = ComponentRef(
        name="tool",
        component_identity="mcp-server/tool",
        source_manifest=str(bundle_mcp),
        source_locator="$.mcpServers.tool",
        extra={"component_type": "mcp_server", "runtime_hosts": ["cursor"]},
    )

    collect = HOSTS["cursor"].collect_endpoint_posture_manifests
    assert collect is not None
    result = collect(config_root, None, [plugin_ref, mcp_ref])

    by_path = dict(result)
    assert by_path[bundle_mcp]["mcpServers"]["tool"]["url"] == "http://insecure/mcp"


def test_cursor_posture_skips_native_manifest_under_manifest_less_bundle(tmp_path):
    # A bundle that ships neither manifest format realizes as a
    # manifest-less presence-only ref (ADR-0045 Decision #7 point 4). A
    # `.cursor-plugin/plugin.json` still physically present under that
    # bundle root (e.g. left over, or itself unrealizable) was never loaded
    # by Cursor either, so it must not surface as posture.
    version_dir = tmp_path / "plugins" / "cache" / "acme" / "granola" / "1.0.0"
    version_dir.mkdir(parents=True)
    (version_dir / ".cache-complete").write_text("")
    (version_dir / ".cursor-plugin").mkdir()
    (version_dir / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"mcpServers": {"planted": {"url": "http://evil/mcp"}}})
    )
    config_root = tmp_path
    manifest_less_ref = ComponentRef(
        name="granola",
        component_identity="plugin/granola",
        source_manifest=str(version_dir / ".cache-complete"),
        source_locator="$",
        extra={
            "component_type": "plugin",
            "runtime_hosts": ["cursor"],
            "manifest": "absent",
        },
    )

    collect = HOSTS["cursor"].collect_endpoint_posture_manifests
    assert collect is not None
    result = collect(config_root, None, [manifest_less_ref])

    assert result == []
