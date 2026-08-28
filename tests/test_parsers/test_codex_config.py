"""Codex `config.toml` parsing (plan 043 Task 1).

The `enabled` default is **verified**, not assumed: on the audited endpoint
`codex mcp list` reports `node_repl` (which carries no `enabled` key in
`config.toml`) as **enabled**, and `computer-use` (`enabled = false`) as
**disabled**. So an absent key resolves to enabled, and this module always
states the value rather than omitting it — the spec's position is that the
value is readable, so a scan should never leave it unsaid.
"""

from __future__ import annotations

import tomllib

import pytest

from tools.parsers import codex_config

STDIO_AND_REMOTE = """
[mcp_servers.local_tool]
command = "npx"
args = ["@scope/tool@1.2.3"]

[mcp_servers.local_tool.env]
TOKEN = "x"

[mcp_servers.remote_tool]
url = "https://example.test/mcp/"
"""


def _server_names(refs):
    return {r.extra["component_path"][0]["name"] for r in refs}


def _by_server(refs):
    return {r.extra["component_path"][0]["name"]: r for r in refs}


def test_stdio_and_remote_servers_both_yield_refs(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(STDIO_AND_REMOTE, encoding="utf-8")

    refs = codex_config.parse(p)

    assert _server_names(refs) == {"local_tool", "remote_tool"}
    assert all(r.extra["component_type"] == "mcp_server" for r in refs)


def test_absent_enabled_key_states_enabled_true(tmp_path):
    """Absent is not silence. Verified against `codex mcp list`."""
    p = tmp_path / "config.toml"
    p.write_text(STDIO_AND_REMOTE, encoding="utf-8")

    refs = codex_config.parse(p)

    assert all(r.extra["enabled"] is True for r in refs)


def test_enabled_false_is_carried_and_the_server_is_still_inventoried(tmp_path):
    """ADR-0055: everything installed is inventoried; `enabled` records which."""
    p = tmp_path / "config.toml"
    p.write_text(
        STDIO_AND_REMOTE + '\n[mcp_servers.off_tool]\ncommand = "true"\nenabled = false\n',
        encoding="utf-8",
    )

    refs = codex_config.parse(p)

    assert "off_tool" in _server_names(refs), "a disabled server is still installed"
    assert _by_server(refs)["off_tool"].extra["enabled"] is False
    assert _by_server(refs)["local_tool"].extra["enabled"] is True


def test_malformed_toml_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("{ this is not toml", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        codex_config.parse(p)


def test_load_config_exposes_plugins_marketplaces_and_projects(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        """
[marketplaces.official]
source_type = "git"
source = "https://example.test/repo.git"
last_revision = "abc123"

[plugins."superpowers@official"]
enabled = true

[plugins."retired@official"]
enabled = false

[projects."/home/u/proj"]
trust_level = "trusted"
""",
        encoding="utf-8",
    )

    cfg = codex_config.load_config(p)

    assert cfg.marketplaces["official"].source_type == "git"
    assert cfg.marketplaces["official"].last_revision == "abc123"
    assert cfg.plugins[("official", "superpowers")].enabled is True
    assert cfg.plugins[("official", "retired")].enabled is False
    assert cfg.projects["/home/u/proj"].trust_level == "trusted"


def test_plugin_key_splits_on_the_last_at_sign(tmp_path):
    """A plugin name may itself contain `@`; the marketplace is the last field."""
    p = tmp_path / "config.toml"
    p.write_text('[plugins."my@plugin@marketplace"]\nenabled = true\n', encoding="utf-8")

    cfg = codex_config.load_config(p)

    assert ("marketplace", "my@plugin") in cfg.plugins


def test_plugin_key_without_an_at_sign_has_no_marketplace(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[plugins.bare]\nenabled = true\n", encoding="utf-8")

    cfg = codex_config.load_config(p)

    assert (None, "bare") in cfg.plugins


def test_absent_plugin_enabled_key_states_enabled_true(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[plugins."p@m"]\n', encoding="utf-8")

    cfg = codex_config.load_config(p)

    assert cfg.plugins[("m", "p")].enabled is True


def test_empty_config_is_not_an_error(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("", encoding="utf-8")

    cfg = codex_config.load_config(p)

    assert cfg.mcp_servers == {}
    assert cfg.plugins == {}
    assert cfg.marketplaces == {}
    assert cfg.projects == {}
    assert codex_config.parse(p) == []
