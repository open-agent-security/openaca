"""`openaca-posture-project-trust` (plan 043 Task 10A)."""

from __future__ import annotations

from pathlib import Path

from tools.posture.rules.project_trust import RULE_ID, check_project_trust


def _manifest(projects):
    return [(Path("/x/config.toml"), {"projects": projects})]


def test_a_trusted_project_produces_a_finding():
    findings = check_project_trust(_manifest({"/home/u/repo": "trusted"}))

    assert len(findings) == 1
    assert findings[0].rule_id == RULE_ID
    assert findings[0].component["name"] == "/home/u/repo"


def test_an_untrusted_project_produces_nothing():
    assert check_project_trust(_manifest({"/home/u/repo": "untrusted"})) == []


def test_an_absent_trust_level_produces_nothing():
    """Inventing a meaning for a missing value would report a posture the
    runtime does not have."""
    assert check_project_trust(_manifest({"/home/u/repo": None})) == []


def test_an_unrecognised_trust_level_produces_nothing():
    assert check_project_trust(_manifest({"/home/u/repo": "somewhat"})) == []


def test_no_manifests_is_no_findings():
    assert check_project_trust([]) == []


def test_findings_are_stable_in_path_order():
    findings = check_project_trust(_manifest({"/b": "trusted", "/a": "trusted"}))

    assert [f.component["name"] for f in findings] == ["/a", "/b"]
