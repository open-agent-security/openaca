from pathlib import Path

import pytest

from tools.fleet.collector import (
    _is_absolute_path,
    _redact_payload_for_fleet,
    _redact_url_for_fleet,
    _relativize_path_for_fleet,
)
from tools.fleet.upload_contract import FleetUploadContractError, enforce_fleet_upload_contract


# --- _is_absolute_path -------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "/Users/foo/.claude/skills/x/SKILL.md",
        "/",
        "/tmp/x",
        "\\\\server\\share\\x",
        "C:\\Users\\foo\\x",
        "D:/Users/foo/x",
    ],
)
def test_is_absolute_path_true(value: str) -> None:
    assert _is_absolute_path(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "skills/x/SKILL.md",
        "relative.md",
        "C",  # too short to be drive-letter
        "AB:foo",  # colon in wrong position
    ],
)
def test_is_absolute_path_false(value: str) -> None:
    assert not _is_absolute_path(value)


# --- _relativize_path_for_fleet ---------------------------------------------


def test_relativize_under_config_dir() -> None:
    cfg = Path("/home/u/.claude")
    assert (
        _relativize_path_for_fleet(
            "/home/u/.claude/skills/clerk-cli/SKILL.md", config_dir=cfg, project=None
        )
        == "skills/clerk-cli/SKILL.md"
    )


def test_relativize_under_project_prefixed() -> None:
    cfg = Path("/home/u/.claude")
    proj = Path("/home/u/code/myrepo")
    assert (
        _relativize_path_for_fleet(
            "/home/u/code/myrepo/.claude/skills/x.md",
            config_dir=cfg,
            project=proj,
        )
        == "project/.claude/skills/x.md"
    )


def test_relativize_unknown_root_falls_back_to_basename() -> None:
    cfg = Path("/home/u/.claude")
    assert (
        _relativize_path_for_fleet("/tmp/random/whatever.md", config_dir=cfg, project=None)
        == "whatever.md"
    )


def test_relativize_non_absolute_unchanged() -> None:
    cfg = Path("/home/u/.claude")
    assert (
        _relativize_path_for_fleet("relative/path.md", config_dir=cfg, project=None)
        == "relative/path.md"
    )


def test_relativize_prefers_config_dir_when_both_match() -> None:
    """If a path is inside config_dir AND inside project (unusual but
    possible if config_dir IS project), prefer the config_dir relativization
    (no `project/` prefix), since config_dir is the more specific anchor for
    endpoint composition.
    """
    cfg = Path("/home/u/.claude")
    proj = Path("/home/u")
    assert (
        _relativize_path_for_fleet(
            "/home/u/.claude/skills/x.md", config_dir=cfg, project=proj
        )
        == "skills/x.md"
    )


# --- _redact_payload_for_fleet ----------------------------------------------


def _payload_with_property(value: str, name: str = "openaca:source_manifest") -> dict:
    return {
        "asset_id": "ast_T",
        "source": "endpoint",
        "openaca_version": "0.1.0b5",
        "target_locator": "endpoint:user-scope",
        "content_hash": "sha256:abc",
        "bom": {
            "bomFormat": "CycloneDX",
            "specVersion": "1.7",
            "components": [
                {
                    "type": "application",
                    "name": "clerk-cli",
                    "properties": [{"name": name, "value": value}],
                }
            ],
        },
        "posture_findings": [],
    }


def test_redact_replaces_absolute_path_under_config_dir() -> None:
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property("/home/u/.claude/skills/clerk-cli/SKILL.md")
    _redact_payload_for_fleet(payload, config_dir=cfg, project=None)
    new_value = payload["bom"]["components"][0]["properties"][0]["value"]
    assert new_value == "skills/clerk-cli/SKILL.md"


def test_redact_uses_basename_for_unknown_root() -> None:
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property("/var/lib/openaca/cache/manifest.json")
    _redact_payload_for_fleet(payload, config_dir=cfg, project=None)
    assert payload["bom"]["components"][0]["properties"][0]["value"] == "manifest.json"


def test_redact_leaves_relative_paths_alone() -> None:
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property("skills/clerk-cli/SKILL.md")
    _redact_payload_for_fleet(payload, config_dir=cfg, project=None)
    assert (
        payload["bom"]["components"][0]["properties"][0]["value"]
        == "skills/clerk-cli/SKILL.md"
    )


def test_redact_ignores_non_openaca_properties() -> None:
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property(
        "/home/u/.claude/skills/x.md", name="cdx:other:source-path"
    )
    _redact_payload_for_fleet(payload, config_dir=cfg, project=None)
    # Untouched — pass-through CycloneDX content is out of scope (ADR 0003).
    assert (
        payload["bom"]["components"][0]["properties"][0]["value"]
        == "/home/u/.claude/skills/x.md"
    )


def test_redact_handles_posture_evidence() -> None:
    cfg = Path("/home/u/.claude")
    payload = {
        "asset_id": "ast_T",
        "source": "endpoint",
        "openaca_version": "0.1.0b5",
        "target_locator": "endpoint:user-scope",
        "content_hash": "sha256:abc",
        "bom": {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []},
        "posture_findings": [
            {
                "rule_id": "openaca-posture-insecure-transport",
                "evidence": {
                    "transport": "http",
                    "manifest_path": "/home/u/.claude/settings.json",
                },
            }
        ],
    }
    _redact_payload_for_fleet(payload, config_dir=cfg, project=None)
    assert (
        payload["posture_findings"][0]["evidence"]["manifest_path"]
        == "settings.json"
    )
    assert payload["posture_findings"][0]["evidence"]["transport"] == "http"


# --- contract enforcement: defense-in-depth ---------------------------------


def test_contract_rejects_absolute_openaca_property() -> None:
    payload = _payload_with_property("/Users/vinodkone/.claude/skills/x.md")
    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)
    assert "absolute path" in str(exc.value)
    assert "openaca:source_manifest" in str(exc.value)


def test_contract_rejects_absolute_in_posture_evidence() -> None:
    payload = {
        "asset_id": "ast_T",
        "source": "endpoint",
        "openaca_version": "0.1.0b5",
        "target_locator": "endpoint:user-scope",
        "content_hash": "sha256:abc",
        "bom": {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []},
        "posture_findings": [
            {
                "rule_id": "openaca-posture-insecure-transport",
                "evidence": {
                    "transport": "http",
                    "manifest_path": "/etc/openaca/settings.json",
                },
            }
        ],
    }
    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)
    assert "absolute path" in str(exc.value)
    assert "manifest_path" in str(exc.value)


def test_contract_accepts_relativized_payload() -> None:
    """After `_redact_payload_for_fleet`, the contract should be satisfied —
    the round-trip is the contract.
    """
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property("/home/u/.claude/skills/x.md")
    _redact_payload_for_fleet(payload, config_dir=cfg, project=None)
    enforce_fleet_upload_contract(payload)  # must not raise


# --- _redact_url_for_fleet --------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://api.example.com/mcp/", "https://api.example.com"),
        ("https://api.example.com/mcp?token=abc", "https://api.example.com"),
        ("https://api.example.com#frag", "https://api.example.com"),
        ("http://example.com/some/path", "http://example.com"),
        ("https://example.com", "https://example.com"),  # bare host kept
        ("not-a-url", "not-a-url"),
    ],
)
def test_redact_url_for_fleet(value: str, expected: str) -> None:
    assert _redact_url_for_fleet(value) == expected


def test_redact_replaces_url_paths_in_openaca_properties() -> None:
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property(
        "https://api.githubcopilot.com/mcp/", name="openaca:install_source"
    )
    _redact_payload_for_fleet(payload, config_dir=cfg, project=None)
    assert (
        payload["bom"]["components"][0]["properties"][0]["value"]
        == "https://api.githubcopilot.com"
    )


def test_contract_rejects_url_with_path_in_openaca_property() -> None:
    payload = _payload_with_property(
        "https://api.example.com/mcp/", name="openaca:install_source"
    )
    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)
    assert "URL with a path" in str(exc.value)


def test_contract_accepts_bare_host_url() -> None:
    payload = _payload_with_property(
        "https://example.test", name="openaca:source_provenance"
    )
    enforce_fleet_upload_contract(payload)  # must not raise
