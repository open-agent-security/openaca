from __future__ import annotations

import dataclasses
from pathlib import Path

import click
import pytest

from tools.endpoint_request import (
    endpoint_auxiliary_roots,
    endpoint_discovery_roots,
    resolve_endpoint_request,
    shared_agents_root,
)
from tools.hosts import HOSTS


def _with_detect(monkeypatch, host_id: str, value: bool) -> None:
    # HostAdapter is frozen; replace the registry entry rather than setattr.
    monkeypatch.setitem(HOSTS, host_id, dataclasses.replace(HOSTS[host_id], detect=lambda: value))


def test_default_selection_is_every_detected_host(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".cursor").mkdir()
    selected, roots = resolve_endpoint_request((), None)
    assert selected == ["claude-code", "cursor"]
    assert roots == {"claude-code": tmp_path / ".claude", "cursor": tmp_path / ".cursor"}


def test_default_selection_skips_undetected_host(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    selected, roots = resolve_endpoint_request((), None)
    assert selected == ["claude-code"]
    assert roots == {"claude-code": tmp_path / ".claude"}


def test_nothing_detected_is_a_hard_error(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(click.ClickException) as exc:
        resolve_endpoint_request((), None)
    assert "claude-code" in str(exc.value)
    assert "cursor" in str(exc.value)


def test_explicit_hosts_are_validated_deduped_and_registry_ordered(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".cursor").mkdir()
    selected, _ = resolve_endpoint_request(("cursor,claude-code", "cursor"), None)
    assert selected == ["claude-code", "cursor"]
    with pytest.raises(click.BadParameter):
        resolve_endpoint_request(("typo",), None)


def test_explicit_host_not_detected_without_override_is_an_error(tmp_path, monkeypatch):
    _with_detect(monkeypatch, "cursor", False)
    with pytest.raises(click.ClickException) as exc:
        resolve_endpoint_request(("cursor",), None)
    assert "cursor" in str(exc.value)


def test_config_dir_with_single_host_does_not_consult_detect(tmp_path, monkeypatch):
    # The supplied directory IS the root: requiring the default ~/.cursor to
    # also exist would reject a valid override.
    _with_detect(monkeypatch, "cursor", False)
    override = tmp_path / "cursor-config"
    override.mkdir()
    selected, roots = resolve_endpoint_request(("cursor",), override)
    assert selected == ["cursor"]
    assert roots == {"cursor": override}


def test_config_dir_without_host_applies_to_the_default_host(tmp_path, monkeypatch):
    _with_detect(monkeypatch, "claude-code", False)
    _with_detect(monkeypatch, "cursor", True)
    override = tmp_path / "claude-config"
    override.mkdir()
    selected, roots = resolve_endpoint_request((), override)
    assert selected == ["claude-code"]
    assert roots == {"claude-code": override}


def test_config_dir_with_two_hosts_is_a_hard_error(tmp_path):
    override = tmp_path / "shared"
    override.mkdir()
    with pytest.raises(click.ClickException) as exc:
        resolve_endpoint_request(("claude-code", "cursor"), override)
    assert "--host" in str(exc.value)


def test_shared_agents_root_follows_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert shared_agents_root() == tmp_path / ".agents"


def test_auxiliary_roots_empty_for_claude_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert endpoint_auxiliary_roots(["claude-code"], {"claude-code": tmp_path}) == {}


def test_auxiliary_roots_for_cursor_only_include_claude_compat(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-compat"))
    aux = endpoint_auxiliary_roots(["cursor"], {"cursor": tmp_path / "cursor"})
    assert aux == {
        "shared-agents": tmp_path / ".agents",
        "claude-compat": tmp_path / "claude-compat",
    }


def test_auxiliary_roots_drop_claude_compat_when_claude_selected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    aux = endpoint_auxiliary_roots(
        ["claude-code", "cursor"],
        {"claude-code": tmp_path / "claude", "cursor": tmp_path / "cursor"},
    )
    assert aux == {"shared-agents": tmp_path / ".agents"}


def test_discovery_roots_claude_only_is_byte_identical_to_today(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert endpoint_discovery_roots(["claude-code"], {"claude-code": tmp_path / "claude"}) == {
        "endpoint": tmp_path / "claude"
    }


def test_discovery_roots_label_every_host_and_auxiliary_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-compat"))
    roots = {"cursor": tmp_path / "cursor"}
    assert endpoint_discovery_roots(["cursor"], roots) == {
        "endpoint-cursor": tmp_path / "cursor",
        "endpoint-shared-agents": tmp_path / ".agents",
        "endpoint-claude-compat": tmp_path / "claude-compat",
    }


def test_discovery_roots_reject_two_labels_on_one_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # `--host cursor --config-dir ~/.claude` would give the cursor root and the
    # claude-compat auxiliary root the same directory.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "shared"))
    (tmp_path / "shared").mkdir()
    with pytest.raises(click.ClickException):
        endpoint_discovery_roots(["cursor"], {"cursor": tmp_path / "shared"})


def test_endpoint_request_returns_paths_not_strings(tmp_path, monkeypatch):
    _with_detect(monkeypatch, "cursor", False)
    override = tmp_path / "claude-config"
    override.mkdir()
    _, roots = resolve_endpoint_request(("claude-code",), override)
    assert all(isinstance(v, Path) for v in roots.values())
