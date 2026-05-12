"""Tests for `tools/resync_from_aliases.py`.

Unit-level tests use synthetic OSV records and assert merge behavior;
the CLI end-to-end path is exercised against an in-tmp advisories
directory with `fetch_alias` monkeypatched (no network).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from tools import resync_from_aliases as resync


def _asve_skeleton() -> dict:
    return {
        "schema_version": "1.7.5",
        "id": "ASVE-2026-9999",
        "type": "vulnerability",
        "aliases": ["GHSA-fake-aaaa-bbbb"],
        "summary": "Test",
        "details": "Test",
        "published": "2026-01-01T00:00:00Z",
        "modified": "2026-01-01T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "demo-pkg"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0.0"}]}
                ],
            }
        ],
        "severity": [
            {
                "type": "CVSS_V4",
                "score": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
            }
        ],
        "database_specific": {
            "asve": {
                "component_type": "mcp_server",
                "surfaces": ["tool_invocation"],
                "agent_impact": {"code_execution": True},
                "evidence_level": "confirmed",
            }
        },
    }


def _upstream() -> dict:
    return {
        "id": "GHSA-fake-aaaa-bbbb",
        "modified": "2026-04-01T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "demo-pkg"},
                "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "2.5.0"}]}],
            }
        ],
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:H/A:H"}],
    }


def test_merge_replaces_severity_with_upstream():
    asve = _asve_skeleton()
    merged = resync.merge_upstream(asve, _upstream())
    assert merged["severity"] == [
        {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:H/A:H"}
    ]


def test_merge_replaces_matching_package_ranges_with_upstream():
    asve = _asve_skeleton()
    merged = resync.merge_upstream(asve, _upstream())
    npm_entry = next(e for e in merged["affected"] if e["package"]["ecosystem"] == "npm")
    fixed_events = [ev for r in npm_entry["ranges"] for ev in r["events"] if "fixed" in ev]
    assert fixed_events == [{"fixed": "2.5.0"}]


def test_merge_preserves_agent_overlay():
    """database_specific.asve.* is the ASVE wedge; resync must never touch it."""
    asve = _asve_skeleton()
    merged = resync.merge_upstream(asve, _upstream())
    assert merged["database_specific"]["asve"]["component_type"] == "mcp_server"
    assert merged["database_specific"]["asve"]["agent_impact"]["code_execution"] is True
    assert merged["database_specific"]["asve"]["evidence_level"] == "confirmed"


def test_merge_preserves_asve_only_affected_entries():
    """An ASVE-native ecosystem (claude-plugin/*) with no upstream counterpart
    should pass through untouched — upstream doesn't track those, and
    deleting them would silently lose advisory coverage."""
    asve = _asve_skeleton()
    asve["affected"].append(
        {
            "package": {"ecosystem": "claude-plugin", "name": "demo-plugin"},
            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}]}],
        }
    )
    merged = resync.merge_upstream(asve, _upstream())
    cp_entry = next(e for e in merged["affected"] if e["package"]["ecosystem"] == "claude-plugin")
    assert cp_entry["package"]["name"] == "demo-plugin"
    assert cp_entry["ranges"]


def test_merge_bumps_modified_timestamp():
    asve = _asve_skeleton()
    merged = resync.merge_upstream(asve, _upstream())
    assert merged["modified"] != asve["modified"]
    assert merged["modified"].endswith("Z")


def test_merge_drops_stale_severity_when_upstream_has_none():
    """If upstream has no severity, don't keep a stale hand-authored one —
    'unknown' downstream is more honest than a wrong CVSS."""
    asve = _asve_skeleton()
    upstream = _upstream()
    del upstream["severity"]
    merged = resync.merge_upstream(asve, upstream)
    assert "severity" not in merged


def test_resync_record_skips_when_no_aliases(monkeypatch):
    record = _asve_skeleton()
    record["aliases"] = []
    resynced, reason = resync.resync_record(record)
    assert resynced is None
    assert reason is not None and "no aliases" in reason


def test_resync_record_tries_aliases_in_order(monkeypatch):
    """First alias resolves to None (404); second resolves successfully.
    Verifies fallthrough rather than aborting on the first miss."""
    record = _asve_skeleton()
    record["aliases"] = ["GHSA-doesnotexist", "CVE-2025-99999"]
    upstream = _upstream()
    calls: list[str] = []

    def fake_fetch(alias_id: str):
        calls.append(alias_id)
        return upstream if alias_id == "CVE-2025-99999" else None

    monkeypatch.setattr(resync, "fetch_alias", fake_fetch)
    resynced, used = resync.resync_record(record)
    assert used == "CVE-2025-99999"
    assert calls == ["GHSA-doesnotexist", "CVE-2025-99999"]
    assert resynced is not None


def test_cli_writes_resynced_file(tmp_path: Path, monkeypatch):
    advisories = tmp_path / "advisories"
    advisories.mkdir()
    target = advisories / "ASVE-2026-9999.yaml"
    target.write_text(yaml.safe_dump(_asve_skeleton(), sort_keys=False))

    monkeypatch.setattr(resync, "fetch_alias", lambda _: _upstream())

    runner = CliRunner()
    result = runner.invoke(resync.main, [str(advisories)])
    assert result.exit_code == 0, result.output

    after = yaml.safe_load(target.read_text())
    npm_entry = next(e for e in after["affected"] if e["package"]["ecosystem"] == "npm")
    fixed_events = [ev for r in npm_entry["ranges"] for ev in r["events"] if "fixed" in ev]
    assert fixed_events == [{"fixed": "2.5.0"}]
    assert after["severity"][0]["type"] == "CVSS_V3"


def test_cli_check_mode_exits_nonzero_on_drift(tmp_path: Path, monkeypatch):
    advisories = tmp_path / "advisories"
    advisories.mkdir()
    target = advisories / "ASVE-2026-9999.yaml"
    original = _asve_skeleton()
    target.write_text(yaml.safe_dump(original, sort_keys=False))

    monkeypatch.setattr(resync, "fetch_alias", lambda _: _upstream())

    runner = CliRunner()
    result = runner.invoke(resync.main, [str(advisories), "--check"])
    assert result.exit_code == 1, result.output
    assert "DRIFT" in result.output or "drift" in result.output

    # --check must NOT modify the file.
    on_disk = yaml.safe_load(target.read_text())
    assert on_disk == original


def test_cli_check_mode_exits_zero_when_in_sync(tmp_path: Path, monkeypatch):
    advisories = tmp_path / "advisories"
    advisories.mkdir()
    target = advisories / "ASVE-2026-9999.yaml"

    # Pre-synced: severity + ranges already match upstream.
    in_sync = _asve_skeleton()
    upstream = _upstream()
    in_sync["severity"] = upstream["severity"]
    in_sync["affected"][0]["ranges"] = upstream["affected"][0]["ranges"]
    target.write_text(yaml.safe_dump(in_sync, sort_keys=False))

    monkeypatch.setattr(resync, "fetch_alias", lambda _: upstream)

    runner = CliRunner()
    result = runner.invoke(resync.main, [str(advisories), "--check"])
    assert result.exit_code == 0, result.output


def test_cli_id_filter_limits_scope(tmp_path: Path, monkeypatch):
    advisories = tmp_path / "advisories"
    advisories.mkdir()
    a = advisories / "ASVE-2026-9998.yaml"
    b = advisories / "ASVE-2026-9999.yaml"
    rec_a = _asve_skeleton()
    rec_a["id"] = "ASVE-2026-9998"
    rec_b = _asve_skeleton()
    rec_b["id"] = "ASVE-2026-9999"
    a.write_text(yaml.safe_dump(rec_a, sort_keys=False))
    b.write_text(yaml.safe_dump(rec_b, sort_keys=False))

    monkeypatch.setattr(resync, "fetch_alias", lambda _: _upstream())

    runner = CliRunner()
    result = runner.invoke(resync.main, [str(advisories), "--id", "ASVE-2026-9999"])
    assert result.exit_code == 0, result.output

    # Only the targeted file changed.
    after_a = yaml.safe_load(a.read_text())
    assert after_a["affected"][0]["ranges"][0]["events"][-1] == {"fixed": "1.0.0"}
    after_b = yaml.safe_load(b.read_text())
    assert after_b["affected"][0]["ranges"][0]["events"][-1] == {"fixed": "2.5.0"}
