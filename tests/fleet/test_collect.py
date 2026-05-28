from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from tools.cli import main as openaca_main
from tools.component_ref import ComponentRef
from tools.fleet.client import (
    BomUploadResult,
    DriftResult,
    RegisterAssetResult,
)
from tools.fleet.collector import (
    CollectError,
    EndpointCollection,
    _relativize_bom_paths,
    _relativize_declared_by,
    _relativize_path,
    _relativize_source_provenance,
    build_endpoint_collection,
    collect_endpoint,
    upload_bom_file,
)
from tools.fleet.config import load_fleet_config
from tools.fleet.redaction import validate_fleet_upload_payload
from tools.posture.finding import PostureFinding, Standards


def test_build_endpoint_collection_uses_endpoint_bom_and_posture_engine(tmp_path, monkeypatch):
    ref = ComponentRef(
        ecosystem="npm",
        name="@example/mcp",
        version=None,
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        component_identity="mcp-server/example",
        extra={"component_type": "mcp_server", "install_source": "@example/mcp"},
    )
    calls: list[tuple[str, object]] = []

    def fake_parse_install(**kwargs):
        calls.append(("parse_install", kwargs))
        return [ref], []

    def fake_run_posture_rules(refs, manifests, settings_manifests):
        calls.append(("run_posture_rules", refs))
        assert manifests == [("mcp", {})]
        assert settings_manifests == [("settings", {})]
        return [_posture("openaca-posture-mutable-install-reference")]

    monkeypatch.setattr("tools.fleet.collector.parse_install", fake_parse_install)
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [("mcp", {})],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [("settings", {})],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", fake_run_posture_rules)

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    assert calls[0][0] == "parse_install"
    assert calls[1] == ("run_posture_rules", [ref])
    assert collection.bom["metadata"]["properties"][1] == {
        "name": "openaca:target_type",
        "value": "endpoint",
    }
    assert {"name": "openaca:target", "value": "endpoint:user-scope"} in collection.bom["metadata"][
        "properties"
    ]
    assert collection.posture_findings == [
        {
            "rule_id": "openaca-posture-mutable-install-reference",
            "rule_version": "1",
            "severity": "LOW",
            "scope": "component",
            "component_identity": "mcp-server/example",
            "summary": "Mutable install",
            "fix": "Pin the install reference.",
            "evidence": {"install_ref": "@example/mcp", "manifest_path": ".mcp.json"},
        }
    ]


def test_collect_endpoint_registers_asset_uploads_bom_and_saves_asset_id(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id=None)
    pending_dir = tmp_path / "pending"
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr("tools.fleet.collector.socket.gethostname", lambda: "demo-host")
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            calls.append(("init", {"api_url": api_url, "token": token}))

        def register_asset(self, payload):
            calls.append(("register_asset", payload))
            return RegisterAssetResult(
                asset_id="asset-123", dashboard_url="https://app/assets/asset-123"
            )

        def upload_bom(self, payload):
            calls.append(("upload_bom", payload))
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    result = collect_endpoint(config_dir=tmp_path, project=None)

    assert result.asset_id == "asset-123"
    assert [name for name, _ in calls] == ["init", "register_asset", "upload_bom"]
    assert calls[1][1]["asset_type"] == "endpoint"
    assert calls[1][1]["external_id"] == "demo-host"
    assert calls[2][1]["asset_id"] == "asset-123"
    assert calls[2][1]["content_hash"].startswith("sha256:")
    assert calls[2][1]["posture_findings"][0]["rule_id"] == "openaca-posture-insecure-transport"
    assert load_fleet_config(config_path).asset_id == "asset-123"


def test_collect_endpoint_uses_existing_asset_id(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    calls: list[str] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            calls.append("init")

        def register_asset(self, payload):
            raise AssertionError("asset should not be re-registered")

        def upload_bom(self, payload):
            calls.append(payload["asset_id"])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert calls == ["init", "asset-existing"]


def test_collect_endpoint_caches_redacted_payload_on_interactive_offline_failure(
    tmp_path, monkeypatch
):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert exc.value.exit_code == 2
    pending = list(pending_dir.glob("pending-bom-*.json"))
    assert len(pending) == 1
    cached = json.loads(pending[0].read_text(encoding="utf-8"))
    assert cached["asset_id"] == "asset-existing"
    assert "/Users/" not in pending[0].read_text(encoding="utf-8")


def test_collect_endpoint_quiet_offline_failure_exits_zero_after_cache(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None, quiet=True)

    assert exc.value.exit_code == 0


def test_collect_endpoint_replays_pending_cache_before_current_upload(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    old_payload = _payload(asset_id="asset-existing", content_hash="sha256:old")
    (pending_dir / "pending-bom-1.json").write_text(json.dumps(old_payload), encoding="utf-8")
    uploads: list[str] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload["content_hash"])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert uploads[0] == "sha256:old"
    assert len(uploads) == 2
    assert not list(pending_dir.glob("pending-bom-*.json"))


def test_collect_endpoint_continues_current_collection_when_replay_fails(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    old_payload = _payload(content_hash="sha256:old")
    (pending_dir / "pending-bom-1.json").write_text(json.dumps(old_payload), encoding="utf-8")

    collection_built: list[bool] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: (collection_built.append(True), _collection())[1],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None, allow_offline_cache=True)

    assert exc.value.exit_code == 0
    assert collection_built, "current endpoint collection must run even when replay fails"
    assert (pending_dir / "pending-bom-1.json").exists(), "old pending file kept for next attempt"
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 2, "new pending file written"


def test_collect_endpoint_skips_and_removes_corrupt_pending_file(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    (pending_dir / "pending-bom-bad.json").write_text("not-json!!!", encoding="utf-8")

    uploads: list[str] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload["content_hash"])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert not (pending_dir / "pending-bom-bad.json").exists(), "corrupt file removed"
    assert len(uploads) == 1, "only the current upload ran, not the corrupt pending one"


def test_upload_bom_file_uploads_existing_bom_without_collecting_endpoint(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    bom_path = tmp_path / "bom.json"
    bom_path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": []}), encoding="utf-8")
    uploads: list[dict[str, Any]] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: pytest.fail("endpoint collection should not run"),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    upload_bom_file(bom_path)

    assert uploads[0]["asset_id"] == "asset-existing"
    assert uploads[0]["source"] == "manual"
    assert uploads[0]["bom"] == {"bomFormat": "CycloneDX", "components": []}
    assert uploads[0]["posture_findings"] == []


def test_collect_endpoint_cli_prints_upload_summary(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_collect_endpoint(**kwargs):
        calls.append(kwargs)
        return _upload_result(asset_id="asset-123")

    monkeypatch.setattr("tools.fleet.cli.collect_endpoint", fake_collect_endpoint)

    result = CliRunner().invoke(
        openaca_main,
        ["fleet", "collect", "endpoint", "--config-dir", str(tmp_path), "--quiet"],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "config_dir": tmp_path,
            "project": None,
            "quiet": True,
            "allow_offline_cache": False,
        }
    ]
    assert "bom-123" in result.output
    assert "https://app/boms/bom-123" in result.output


def test_upload_cli_prints_upload_summary(tmp_path, monkeypatch):
    bom_path = tmp_path / "bom.json"
    bom_path.write_text("{}", encoding="utf-8")
    calls: list[Path] = []

    def fake_upload_bom_file(path: Path):
        calls.append(path)
        return _upload_result(asset_id="asset-123")

    monkeypatch.setattr("tools.fleet.cli.upload_bom_file", fake_upload_bom_file)

    result = CliRunner().invoke(openaca_main, ["fleet", "upload", str(bom_path)])

    assert result.exit_code == 0
    assert calls == [bom_path]
    assert "bom-123" in result.output


def test_relativize_path_makes_absolute_subpath_relative(tmp_path):
    config_dir = tmp_path / "dot-claude"
    path = str(config_dir / "settings.json")
    assert _relativize_path(path, config_dir) == "settings.json"


def test_relativize_path_nested_under_config_dir(tmp_path):
    config_dir = tmp_path / "dot-claude"
    path = str(config_dir / "projects" / "myproj" / ".claude" / "settings.json")
    assert _relativize_path(path, config_dir) == str(
        Path("projects") / "myproj" / ".claude" / "settings.json"
    )


def test_relativize_path_outside_config_dir_returns_filename(tmp_path):
    config_dir = tmp_path / "dot-claude"
    path = str(tmp_path / "myproject" / ".mcp.json")
    assert _relativize_path(path, config_dir) == ".mcp.json"


def test_relativize_path_relative_unchanged(tmp_path):
    config_dir = tmp_path / "dot-claude"
    assert _relativize_path("settings.json", config_dir) == "settings.json"
    assert _relativize_path("", config_dir) == ""


def test_relativize_declared_by_sanitizes_path_field(tmp_path):
    config_dir = tmp_path / "dot-claude"
    raw = f'{{"kind": "skill_lock", "path": "{config_dir / "installed_plugins.json"}"}}'
    result = _relativize_declared_by(raw, config_dir)
    obj = json.loads(result)
    assert obj["path"] == "installed_plugins.json"
    assert obj["kind"] == "skill_lock"


def test_relativize_declared_by_leaves_non_json_unchanged(tmp_path):
    config_dir = tmp_path / "dot-claude"
    assert _relativize_declared_by("not-json", config_dir) == "not-json"


def test_relativize_source_provenance_relativizes_lockfile_and_resolved_path(tmp_path):
    config_dir = tmp_path / "dot-claude"
    lockfile = str(config_dir / ".skill-lock.json")
    resolved = str(config_dir / "skills" / "my-skill" / "SKILL.md")
    raw = json.dumps(
        {
            "status": "known",
            "source": "github",
            "source_type": "git",
            "lockfile_path": lockfile,
            "resolved_path": resolved,
        },
        sort_keys=True,
    )
    result = _relativize_source_provenance(raw, config_dir)
    obj = json.loads(result)
    assert obj["lockfile_path"] == ".skill-lock.json"
    assert obj["resolved_path"] == str(Path("skills") / "my-skill" / "SKILL.md")
    assert obj["source"] == "github"


def test_relativize_source_provenance_lockfile_outside_config_dir_returns_filename(tmp_path):
    config_dir = tmp_path / "dot-claude"
    lockfile = str(tmp_path / ".agents" / ".skill-lock.json")
    raw = json.dumps({"status": "known", "lockfile_path": lockfile}, sort_keys=True)
    result = _relativize_source_provenance(raw, config_dir)
    obj = json.loads(result)
    assert obj["lockfile_path"] == ".skill-lock.json"


def test_relativize_source_provenance_symlink_target_only(tmp_path):
    config_dir = tmp_path / "dot-claude"
    resolved = str(tmp_path / "skills-repo" / "my-skill" / "SKILL.md")
    raw = json.dumps({"status": "symlink-target", "resolved_path": resolved}, sort_keys=True)
    result = _relativize_source_provenance(raw, config_dir)
    obj = json.loads(result)
    assert obj["resolved_path"] == "SKILL.md"


def test_relativize_source_provenance_leaves_non_json_unchanged(tmp_path):
    config_dir = tmp_path / "dot-claude"
    assert _relativize_source_provenance("not-json", config_dir) == "not-json"


def test_relativize_bom_paths_strips_home_paths_from_source_provenance(tmp_path):
    config_dir = tmp_path / "dot-claude"
    lockfile = str(config_dir / ".skill-lock.json")
    resolved = str(config_dir / "skills" / "my-skill" / "SKILL.md")
    provenance = json.dumps(
        {
            "status": "known",
            "source": "github",
            "source_type": "git",
            "lockfile_path": lockfile,
            "resolved_path": resolved,
        },
        sort_keys=True,
    )
    bom = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "type": "application",
                "bom-ref": "ref1",
                "name": "my-skill",
                "properties": [
                    {"name": "openaca:source_provenance", "value": provenance},
                    {"name": "openaca:scope", "value": "agent-component"},
                ],
            }
        ],
    }
    result = _relativize_bom_paths(bom, config_dir)
    props = {p["name"]: p["value"] for p in result["components"][0]["properties"]}
    obj = json.loads(props["openaca:source_provenance"])
    assert obj["lockfile_path"] == ".skill-lock.json"
    assert obj["resolved_path"] == str(Path("skills") / "my-skill" / "SKILL.md")
    assert props["openaca:scope"] == "agent-component"


def test_relativize_bom_paths_strips_home_paths_from_source_manifest(tmp_path):
    config_dir = tmp_path / "dot-claude"
    abs_path = str(config_dir / "settings.json")
    bom = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "type": "application",
                "bom-ref": "ref1",
                "name": "test-hook",
                "properties": [
                    {"name": "openaca:source_manifest", "value": abs_path},
                    {"name": "openaca:scope", "value": "agent-component"},
                ],
            }
        ],
    }
    result = _relativize_bom_paths(bom, config_dir)
    props = {p["name"]: p["value"] for p in result["components"][0]["properties"]}
    assert props["openaca:source_manifest"] == "settings.json"
    assert props["openaca:scope"] == "agent-component"


def test_relativize_bom_paths_strips_home_paths_from_declared_by(tmp_path):
    config_dir = tmp_path / "dot-claude"
    declared_by = json.dumps(
        {"kind": "skill_lock", "path": str(config_dir / "installed_plugins.json")},
        sort_keys=True,
    )
    bom = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "type": "application",
                "bom-ref": "ref1",
                "name": "my-plugin",
                "properties": [
                    {"name": "openaca:declared_by", "value": declared_by},
                ],
            }
        ],
    }
    result = _relativize_bom_paths(bom, config_dir)
    props = {p["name"]: p["value"] for p in result["components"][0]["properties"]}
    obj = json.loads(props["openaca:declared_by"])
    assert obj["path"] == "installed_plugins.json"


def test_build_endpoint_collection_bom_passes_validation_with_home_paths(tmp_path, monkeypatch):
    """BOM with home-directory source_manifest paths must pass validate_fleet_upload_payload."""
    config_dir = tmp_path / "dot-claude"
    abs_manifest = str(config_dir / "settings.json")
    abs_declared_by = json.dumps(
        {"kind": "skill_lock", "path": str(config_dir / "installed_plugins.json")},
        sort_keys=True,
    )
    ref = ComponentRef(
        name="my-plugin",
        component_identity="claude-plugin/mktplace/my-plugin",
        source_manifest=abs_manifest,
        source_locator="$.plugins[0]",
        extra={
            "component_type": "plugin",
            "declared_by": json.loads(abs_declared_by),
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *a, **kw: [])

    collection = build_endpoint_collection(config_dir=config_dir, project=None)

    payload = {
        "asset_id": "asset-123",
        "source": "endpoint",
        "openaca_version": "unknown",
        "target_locator": "endpoint:user-scope",
        "content_hash": "sha256:abc",
        "bom": collection.bom,
        "posture_findings": [],
    }
    validate_fleet_upload_payload(payload)

    props_by_name = {}
    for comp in collection.bom.get("components") or []:
        for p in comp.get("properties") or []:
            props_by_name[p["name"]] = p["value"]

    assert props_by_name["openaca:source_manifest"] == "settings.json"
    declared = json.loads(props_by_name["openaca:declared_by"])
    assert declared["path"] == "installed_plugins.json"


def test_build_endpoint_collection_bom_passes_validation_with_source_provenance_home_paths(
    tmp_path, monkeypatch
):
    """BOM components with home-directory lockfile_path/resolved_path in source_provenance
    must pass validate_fleet_upload_payload after relativization."""
    config_dir = tmp_path / "dot-claude"
    lockfile_path = str(config_dir / ".skill-lock.json")
    resolved_path = str(config_dir / "skills" / "my-skill" / "SKILL.md")
    ref = ComponentRef(
        name="my-skill",
        component_identity="claude-skill/my-skill",
        source_manifest=str(config_dir / "skills" / "my-skill" / "SKILL.md"),
        source_locator="skills.my-skill",
        extra={
            "component_type": "skill",
            "source_provenance": {
                "status": "known",
                "source": "github.com/example/my-skill",
                "source_type": "git",
                "lockfile_path": lockfile_path,
                "resolved_path": resolved_path,
            },
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *a, **kw: [])

    collection = build_endpoint_collection(config_dir=config_dir, project=None)

    payload = {
        "asset_id": "asset-123",
        "source": "endpoint",
        "openaca_version": "unknown",
        "target_locator": "endpoint:user-scope",
        "content_hash": "sha256:abc",
        "bom": collection.bom,
        "posture_findings": [],
    }
    validate_fleet_upload_payload(payload)

    props_by_name = {}
    for comp in collection.bom.get("components") or []:
        for p in comp.get("properties") or []:
            props_by_name[p["name"]] = p["value"]

    provenance = json.loads(props_by_name["openaca:source_provenance"])
    assert provenance["lockfile_path"] == ".skill-lock.json"
    assert provenance["resolved_path"] == str(Path("skills") / "my-skill" / "SKILL.md")


def _write_config(tmp_path: Path, *, asset_id: str | None) -> Path:
    config_path = tmp_path / "fleet.toml"
    lines = [
        "[fleet]",
        'api_url = "http://fleet.test"',
        'token = "ot_TEST"',
    ]
    if asset_id is not None:
        lines.append(f'asset_id = "{asset_id}"')
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def _collection() -> EndpointCollection:
    return EndpointCollection(
        bom={"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []},
        posture_findings=[
            {
                "rule_id": "openaca-posture-insecure-transport",
                "rule_version": "1",
                "severity": "MEDIUM",
                "scope": "component",
                "component_identity": "mcp-server/test",
                "summary": "Insecure transport",
                "fix": "Use https.",
                "evidence": {"transport": "http", "manifest_path": ".mcp.json"},
            }
        ],
        component_count=0,
    )


def _payload(**overrides) -> dict[str, Any]:
    payload = {
        "asset_id": "asset-existing",
        "source": "endpoint",
        "openaca_version": "0.1.0b5",
        "target_locator": "endpoint:user-scope",
        "content_hash": "sha256:abc",
        "bom": {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []},
        "posture_findings": [],
    }
    payload.update(overrides)
    return payload


def _upload_result(*, asset_id: str) -> BomUploadResult:
    return BomUploadResult(
        bom_id="bom-123",
        asset_id=asset_id,
        component_count=0,
        finding_count=0,
        policy_violation_count=0,
        drift=DriftResult(added=0, removed=0, changed=0),
        dashboard_url="https://app/boms/bom-123",
    )


def _posture(rule_id: str) -> PostureFinding:
    return PostureFinding(
        rule_id=rule_id,
        title="Mutable install",
        severity="low",
        confidence="high",
        component={"type": "mcp_server", "name": "mcp-server/example (@example/mcp)"},
        active_in=["claude-code"],
        declared_by={"kind": "manifest", "path": ".mcp.json"},
        component_path=[{"type": "mcp_server", "name": "mcp-server/example"}],
        standards=Standards(),
        remediation="Pin the install reference.",
    )
