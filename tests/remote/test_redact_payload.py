from pathlib import Path

import pytest

from tools.remote.collector import (
    _is_absolute_path,
    _redact_payload_for_remote,
    _redact_url_for_remote,
    _relativize_path_for_remote,
)
from tools.remote.upload_contract import RemoteUploadContractError, enforce_remote_upload_contract

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


# --- _relativize_path_for_remote ---------------------------------------------


def test_relativize_under_config_dir() -> None:
    cfg = Path("/home/u/.claude")
    assert (
        _relativize_path_for_remote(
            "/home/u/.claude/skills/clerk-cli/SKILL.md", config_dir=cfg, project=None
        )
        == "skills/clerk-cli/SKILL.md"
    )


def test_relativize_under_project_prefixed() -> None:
    cfg = Path("/home/u/.claude")
    proj = Path("/home/u/code/myrepo")
    assert (
        _relativize_path_for_remote(
            "/home/u/code/myrepo/.claude/skills/x.md",
            config_dir=cfg,
            project=proj,
        )
        == "project/.claude/skills/x.md"
    )


def test_relativize_unknown_root_falls_back_to_basename() -> None:
    cfg = Path("/home/u/.claude")
    assert (
        _relativize_path_for_remote("/tmp/random/whatever.md", config_dir=cfg, project=None)
        == "whatever.md"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("C:\\Users\\alice\\.claude\\settings.json", "settings.json"),
        ("D:/Users/alice/projects/x.json", "x.json"),
        ("\\\\server\\share\\config\\settings.json", "settings.json"),
    ],
)
def test_relativize_windows_paths_strip_to_basename(value: str, expected: str) -> None:
    """Windows-style absolute paths (drive-letter or UNC) reach this helper
    even on POSIX runners — they show up as plain strings inside `openaca:*`
    property values. `Path(...)` on POSIX treats them as a single filename
    (`\\` is not a separator), so without explicit handling the function
    would return the whole absolute string and the upload contract would
    reject it. _is_absolute_path already detects these shapes; the
    relativize fallback must be consistent with that classification.
    """
    cfg = Path("/home/u/.claude")
    assert _relativize_path_for_remote(value, config_dir=cfg, project=None) == expected


def test_relativize_non_absolute_unchanged() -> None:
    cfg = Path("/home/u/.claude")
    assert (
        _relativize_path_for_remote("relative/path.md", config_dir=cfg, project=None)
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
        _relativize_path_for_remote("/home/u/.claude/skills/x.md", config_dir=cfg, project=proj)
        == "skills/x.md"
    )


# --- _redact_payload_for_remote ----------------------------------------------


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
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    new_value = payload["bom"]["components"][0]["properties"][0]["value"]
    assert new_value == "skills/clerk-cli/SKILL.md"


def test_redact_uses_basename_for_unknown_root() -> None:
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property("/var/lib/openaca/cache/manifest.json")
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    assert payload["bom"]["components"][0]["properties"][0]["value"] == "manifest.json"


def test_redact_leaves_relative_paths_alone() -> None:
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property("skills/clerk-cli/SKILL.md")
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    assert payload["bom"]["components"][0]["properties"][0]["value"] == "skills/clerk-cli/SKILL.md"


def test_redact_ignores_non_openaca_properties() -> None:
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property("/home/u/.claude/skills/x.md", name="cdx:other:source-path")
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    # Untouched — pass-through CycloneDX content is out of scope (ADR 0003).
    assert (
        payload["bom"]["components"][0]["properties"][0]["value"] == "/home/u/.claude/skills/x.md"
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
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    assert payload["posture_findings"][0]["evidence"]["manifest_path"] == "settings.json"
    assert payload["posture_findings"][0]["evidence"]["transport"] == "http"


# --- contract enforcement: defense-in-depth ---------------------------------


def test_contract_rejects_absolute_openaca_property() -> None:
    payload = _payload_with_property("/Users/alice/.claude/skills/x.md")
    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)
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
    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)
    assert "absolute path" in str(exc.value)
    assert "manifest_path" in str(exc.value)


def test_contract_accepts_relativized_payload() -> None:
    """After `_redact_payload_for_remote`, the contract should be satisfied —
    the round-trip is the contract.
    """
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property("/home/u/.claude/skills/x.md")
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    enforce_remote_upload_contract(payload)  # must not raise


# --- _redact_url_for_remote --------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://api.example.com/mcp/", "https://api.example.com"),
        ("https://api.example.com/mcp?token=abc", "https://api.example.com"),
        ("https://api.example.com#frag", "https://api.example.com"),
        ("http://example.com/some/path", "http://example.com"),
        ("https://example.com", "https://example.com"),  # bare host kept
        ("not-a-url", "not-a-url"),
        # userinfo (credentials) must be stripped
        ("https://alice:s3cr3t@example.com/mcp", "https://example.com"),
        ("https://alice:s3cr3t@example.com", "https://example.com"),
        ("https://token@api.example.com/v1/mcp", "https://api.example.com"),
        # scheme is case-insensitive — uppercase must be redacted too, with the
        # original scheme casing preserved
        ("HTTPS://api.example.com/mcp/", "HTTPS://api.example.com"),
        ("HtTp://example.com/some/path", "HtTp://example.com"),
        ("HTTPS://alice:s3cr3t@api.example.com/mcp?token=ABC", "HTTPS://api.example.com"),
    ],
)
def test_redact_url_for_remote(value: str, expected: str) -> None:
    assert _redact_url_for_remote(value) == expected


def test_redact_uppercase_url_path_in_openaca_property() -> None:
    """An uppercase URL scheme must still be reduced to the bare host — not
    fall through to identity redaction (which would leak the query string).
    """
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property(
        "HTTPS://api.example.com/mcp/secret?token=ABC", name="openaca:install_source"
    )
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    assert payload["bom"]["components"][0]["properties"][0]["value"] == "HTTPS://api.example.com"


def test_contract_rejects_uppercase_url_with_path() -> None:
    """Defense-in-depth: the contract's URL-with-path check must also be
    case-insensitive, or an uppercase-scheme URL with a path slips past it.
    """
    payload = _payload_with_property("HTTPS://api.example.com/mcp/", name="openaca:install_source")
    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)
    assert "URL with a path" in str(exc.value)


def test_redact_replaces_url_paths_in_openaca_properties() -> None:
    cfg = Path("/home/u/.claude")
    payload = _payload_with_property(
        "https://api.githubcopilot.com/mcp/", name="openaca:install_source"
    )
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    assert (
        payload["bom"]["components"][0]["properties"][0]["value"] == "https://api.githubcopilot.com"
    )


def test_contract_rejects_url_with_path_in_openaca_property() -> None:
    payload = _payload_with_property("https://api.example.com/mcp/", name="openaca:install_source")
    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)
    assert "URL with a path" in str(exc.value)


def test_contract_accepts_bare_host_url() -> None:
    payload = _payload_with_property("https://example.test", name="openaca:source_provenance")
    enforce_remote_upload_contract(payload)  # must not raise


# --- JSON-embedded path redaction -------------------------------------------


def test_redact_declared_by_json_embedded_path() -> None:
    """openaca:declared_by is JSON-encoded; the path field inside must be redacted."""
    import json

    cfg = Path("/home/u/.claude")
    declared_by_json = json.dumps(
        {"kind": "manifest", "path": "/home/u/.claude/mcp.json"}, sort_keys=True
    )
    payload = _payload_with_property(declared_by_json, name="openaca:declared_by")
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    result = json.loads(payload["bom"]["components"][0]["properties"][0]["value"])
    assert result["path"] == "mcp.json"
    assert result["kind"] == "manifest"


def test_redact_source_provenance_embedded_paths() -> None:
    """openaca:source_provenance can contain lockfile_path and resolved_path;
    both are absolute and must be relativized."""
    import json

    cfg = Path("/home/u/.claude")
    provenance = {
        "lockfile_path": "/home/u/.claude/skills/.skill-lock.json",
        "resolved_path": "/home/u/.claude/skills/clerk-cli",
        "source": "github",
        "source_type": "git",
        "status": "known",
    }
    payload = _payload_with_property(
        json.dumps(provenance, sort_keys=True), name="openaca:source_provenance"
    )
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    result = json.loads(payload["bom"]["components"][0]["properties"][0]["value"])
    assert result["lockfile_path"] == "skills/.skill-lock.json"
    assert result["resolved_path"] == "skills/clerk-cli"
    assert result["source"] == "github"


def test_redact_declared_by_plugin_with_absolute_path() -> None:
    """Plugin-declared-by shape has name + path; path must be redacted."""
    import json

    cfg = Path("/home/u/.claude")
    declared_by_json = json.dumps(
        {"kind": "plugin", "name": "my-plugin", "path": "/home/u/.claude/plugins/my-plugin"},
        sort_keys=True,
    )
    payload = _payload_with_property(declared_by_json, name="openaca:declared_by")
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    result = json.loads(payload["bom"]["components"][0]["properties"][0]["value"])
    assert result["path"] == "plugins/my-plugin"
    assert result["name"] == "my-plugin"
    assert result["kind"] == "plugin"


def test_redact_json_url_with_path_inside_value() -> None:
    """A URL-with-path embedded in a JSON property value must be stripped to bare host."""
    import json

    cfg = Path("/home/u/.claude")
    source_json = json.dumps(
        {"kind": "http", "url": "https://api.example.com/mcp/"}, sort_keys=True
    )
    payload = _payload_with_property(source_json, name="openaca:source")
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    result = json.loads(payload["bom"]["components"][0]["properties"][0]["value"])
    assert result["url"] == "https://api.example.com"


def test_redact_json_relative_path_unchanged() -> None:
    """Relative paths inside JSON values are not touched."""
    import json

    cfg = Path("/home/u/.claude")
    declared_by_json = json.dumps({"kind": "manifest", "path": "skills/x/SKILL.md"}, sort_keys=True)
    payload = _payload_with_property(declared_by_json, name="openaca:declared_by")
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    result = json.loads(payload["bom"]["components"][0]["properties"][0]["value"])
    assert result["path"] == "skills/x/SKILL.md"


def test_contract_accepts_payload_after_json_path_redaction() -> None:
    """After redacting JSON-embedded paths, the upload contract must be satisfied."""
    import json

    cfg = Path("/home/u/.claude")
    declared_by_json = json.dumps(
        {"kind": "manifest", "path": "/home/u/.claude/mcp.json"}, sort_keys=True
    )
    payload = _payload_with_property(declared_by_json, name="openaca:declared_by")
    _redact_payload_for_remote(payload, config_dir=cfg, project=None)
    enforce_remote_upload_contract(payload)  # must not raise
