"""Devin CLI's `permissions` lists feed two posture rule ids (ADR-0073).

`Exec(…)` and bare `exec` allows report under `command_policy_allow`, `mcp__…`
allows under `mcp_auto_approve`, and nothing else reports. The lists merge
across user, project and project-local levels before reporting: a covering
deny anywhere in an allow's chain, or a covering ask at a higher-precedence
level, suppresses it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.posture import (
    collect_devin_endpoint_permissions_manifests,
    collect_devin_permissions_manifests,
    run_posture_rules,
)
from tools.posture.devin_permissions import covers
from tools.posture.rules import command_policy_allow, mcp_auto_approve

COMMAND = command_policy_allow.RULE_ID
MCP = mcp_auto_approve.RULE_ID


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _permissions(path: Path, **lists: list[str]) -> Path:
    return _write(path, json.dumps({"permissions": lists}))


def _findings(manifests, *, agent_kind: str | None = "devin") -> list:
    return run_posture_rules([], [], manifests, agent_kind=agent_kind)


def _by_rule(findings, rule_id: str) -> list[str]:
    return sorted(f.component["name"] for f in findings if f.rule_id == rule_id)


def _endpoint(tmp_path: Path, project: Path | None = None) -> list:
    return _findings(collect_devin_endpoint_permissions_manifests(tmp_path / "config", project))


# --- Splitting one list by entry ---------------------------------------------------


def test_each_allow_entry_reports_under_the_rule_for_its_subject(tmp_path):
    _permissions(
        tmp_path / "config" / "config.json",
        allow=[
            "Exec(git push)",
            "exec",
            "mcp__github__list_issues",
            "mcp__linear__*",
            "mcp__*",
            "Read(**)",
            "Write(src/**)",
            "Fetch(domain:npmjs.org)",
            "grep",
            "edit",
        ],
    )

    findings = _endpoint(tmp_path)

    assert _by_rule(findings, COMMAND) == ["*", "git push"]
    assert _by_rule(findings, MCP) == [
        "mcp-server/* autoApprove",
        "mcp-server/github autoApprove",
        "mcp-server/linear autoApprove",
    ]
    assert {f.rule_id for f in findings} == {COMMAND, MCP}


def test_findings_name_the_file_to_edit_and_the_entry(tmp_path):
    config = _permissions(tmp_path / "config" / "config.json", allow=["mcp__github__create_issue"])

    (finding,) = _endpoint(tmp_path)

    assert finding.declared_by == {"kind": "permissions", "path": str(config)}
    assert finding.evidence == {"permissions": ["mcp__github__create_issue"]}
    assert finding.component_path == [{"type": "mcp_server", "name": "mcp-server/github"}]


def test_active_in_is_the_scanning_kind(tmp_path):
    _permissions(tmp_path / "config" / "config.json", allow=["Exec(git)", "mcp__*"])

    assert {tuple(f.active_in) for f in _endpoint(tmp_path)} == {("devin",)}


def test_the_rules_alone_name_devin_for_its_own_shape(tmp_path):
    """Without a scanning agent the shape still identifies the runtime —
    never a hardcoded other kind."""
    _permissions(tmp_path / "config" / "config.json", allow=["Exec(git)", "mcp__*"])
    manifests = collect_devin_endpoint_permissions_manifests(tmp_path / "config", None)

    mcp = mcp_auto_approve.check_mcp_auto_approve(manifests)
    command = command_policy_allow.check_command_policy_allow(manifests)

    assert [f.active_in for f in mcp + command] == [["devin"], ["devin"]]


def test_unparseable_or_undocumented_entries_report_nothing(tmp_path):
    _permissions(
        tmp_path / "config" / "config.json",
        allow=["Exec()", "Exec(", "mcp__github", "mcp__", 42, None],  # type: ignore[list-item]
    )

    assert _endpoint(tmp_path) == []


def test_config_json_is_read_as_json_with_comments(tmp_path):
    _write(
        tmp_path / "config" / "config.json",
        '{\n  // personal grants\n  "permissions": {"allow": ["Exec(npm run)",],},\n}\n',
    )

    assert _by_rule(_endpoint(tmp_path), COMMAND) == ["npm run"]


# --- Merging across levels -----------------------------------------------------------


def test_lists_merge_across_user_project_and_local(tmp_path):
    project = tmp_path / "project"
    _permissions(tmp_path / "config" / "config.json", allow=["Exec(git)"])
    _permissions(project / ".devin" / "config.json", allow=["Exec(npm run)"])
    _permissions(project / ".devin" / "config.local.json", allow=["Exec(docker compose)"])

    assert _by_rule(_endpoint(tmp_path, project), COMMAND) == ["docker compose", "git", "npm run"]


def test_a_deny_at_any_level_suppresses_a_covering_allow(tmp_path):
    project = tmp_path / "project"
    _permissions(
        tmp_path / "config" / "config.json",
        allow=["Exec(git push)", "Exec(git status)", "mcp__github__delete_repo"],
    )
    # A lower level's deny still wins: "a deny rule always wins".
    _permissions(project / ".devin" / "config.local.json", allow=["Exec(rm -rf)"])
    _permissions(
        project / ".devin" / "config.json",
        deny=["Exec(git push)", "mcp__github__*", "Exec(rm)"],
    )

    findings = _endpoint(tmp_path, project)

    assert _by_rule(findings, COMMAND) == ["git status"]
    assert _by_rule(findings, MCP) == []


def test_a_narrower_deny_leaves_a_broader_allow_reported(tmp_path):
    """`Exec(git)` still approves `git status` unattended when only
    `git push` is denied."""
    _permissions(
        tmp_path / "config" / "config.json",
        allow=["Exec(git)", "mcp__github__*"],
        deny=["Exec(git push)", "mcp__github__delete_repo"],
    )

    findings = _endpoint(tmp_path)

    assert _by_rule(findings, COMMAND) == ["git"]
    assert _by_rule(findings, MCP) == ["mcp-server/github autoApprove"]


def test_a_higher_precedence_ask_suppresses_a_lower_allow(tmp_path):
    """ "A lower-level allow cannot override an ask from a higher-precedence
    level" (Devin's permissions reference)."""
    project = tmp_path / "project"
    _permissions(tmp_path / "config" / "config.json", allow=["Exec(git)", "mcp__linear__list"])
    _permissions(project / ".devin" / "config.json", ask=["exec", "mcp__linear__*"])

    assert _endpoint(tmp_path, project) == []


def test_a_same_or_lower_level_ask_does_not_suppress_an_allow(tmp_path):
    """At one level a more specific allow carves an exception out of a
    broader ask; a lower-precedence ask cannot reach a higher allow."""
    project = tmp_path / "project"
    _permissions(tmp_path / "config" / "config.json", ask=["Exec(npm)"])
    _permissions(project / ".devin" / "config.json", allow=["Exec(git status)"], ask=["exec"])
    _permissions(project / ".devin" / "config.local.json", allow=["Exec(npm run)"])

    assert _by_rule(_endpoint(tmp_path, project), COMMAND) == ["git status", "npm run"]


def test_without_a_project_only_the_user_config_is_read(tmp_path):
    _permissions(tmp_path / "config" / "config.json", allow=["Exec(make)"])

    assert _by_rule(_endpoint(tmp_path), COMMAND) == ["make"]


def test_no_permissions_yields_no_manifest(tmp_path):
    _write(tmp_path / "config" / "config.json", "{}")

    assert collect_devin_endpoint_permissions_manifests(tmp_path / "config", None) == []


# --- Declared --------------------------------------------------------------------


def test_declared_reads_every_project_config_in_the_tree(tmp_path):
    _permissions(tmp_path / ".devin" / "config.json", allow=["Exec(make)"])
    _permissions(tmp_path / ".devin" / "config.local.json", allow=["mcp__github__*"])
    _permissions(tmp_path / "pkg" / ".devin" / "config.json", allow=["Exec(cargo)"])

    findings = _findings(collect_devin_permissions_manifests([tmp_path]))

    assert _by_rule(findings, COMMAND) == ["cargo", "make"]
    assert _by_rule(findings, MCP) == ["mcp-server/github autoApprove"]


def test_declared_an_ancestor_deny_reaches_a_nested_allow(tmp_path):
    """Devin loads the ancestors' configs when it runs in a nested project,
    so their denies apply there; a nested deny does not reach an ancestor."""
    _permissions(tmp_path / ".devin" / "config.json", allow=["Exec(npm)"], deny=["Exec(rm)"])
    _permissions(
        tmp_path / "pkg" / ".devin" / "config.json",
        allow=["Exec(rm -rf build)", "Exec(cargo)"],
        deny=["Exec(npm)"],
    )

    findings = _findings(collect_devin_permissions_manifests([tmp_path]))

    assert _by_rule(findings, COMMAND) == ["cargo", "npm"]


def test_declared_honours_gitignore_unless_asked(tmp_path):
    _write(tmp_path / ".gitignore", "vendor/\n")
    _permissions(tmp_path / "vendor" / ".devin" / "config.json", allow=["Exec(make)"])

    assert collect_devin_permissions_manifests([tmp_path], include_gitignored=False) == []
    assert collect_devin_permissions_manifests([tmp_path], include_gitignored=True)


def test_declared_skips_a_config_inside_a_realized_plugin(tmp_path):
    from tools.component_ref import ComponentRef

    plugin = tmp_path / "plugin"
    _permissions(plugin / ".devin" / "config.json", allow=["Exec(make)"])
    ref = ComponentRef(
        name="p",
        source_manifest=str(plugin / ".devin-plugin" / "plugin.json"),
        extra={"component_type": "plugin"},
    )

    assert collect_devin_permissions_manifests([tmp_path], refs=[ref]) == []


def test_a_raw_settings_file_cannot_forge_devin_permissions(tmp_path):
    """The merged view travels as a typed object, which raw JSON cannot be."""
    forged = {"devin_permissions": {"allows": [{"entry": "Exec(git)", "path": "x"}]}}

    assert _findings([(tmp_path / "settings.json", forged)], agent_kind=None) == []


# --- Coverage relation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "allow", "expected"),
    [
        ("Exec(git)", "Exec(git push)", True),
        ("Exec(git)", "Exec(git)", True),
        ("Exec(git push)", "Exec(git)", False),
        ("Exec(git)", "Exec(gitk)", False),
        ("exec", "Exec(anything here)", True),
        ("exec", "exec", True),
        ("Exec(git)", "exec", False),
        ("mcp__*", "mcp__github__x", True),
        ("mcp__*", "mcp__github__*", True),
        ("mcp__github__*", "mcp__github__x", True),
        ("mcp__github__*", "mcp__*", False),
        ("mcp__github__x", "mcp__github__*", False),
        ("mcp__github__x", "mcp__github__x", True),
        ("mcp__github__*", "mcp__githubber__x", False),
        ("Exec(git)", "mcp__git__x", False),
        ("Read(**)", "Read(src/**)", False),
    ],
)
def test_covers(rule, allow, expected):
    assert covers(rule, allow) is expected


def test_endpoint_an_ancestor_layer_deny_reaches_a_nested_allow(tmp_path):
    """Installed scans load every `.devin/` from the project up to the
    repository root, so a repo-root deny governs a nested package's allow."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    app = repo / "packages" / "app"
    _permissions(repo / ".devin" / "config.json", deny=["Exec(git push)"])
    _permissions(app / ".devin" / "config.json", allow=["Exec(git push)", "Exec(npm test)"])

    assert _by_rule(_endpoint(tmp_path, app), COMMAND) == ["npm test"]


def test_endpoint_an_ancestor_layer_allow_is_reported(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    app = repo / "packages" / "app"
    app.mkdir(parents=True)
    _permissions(repo / ".devin" / "config.json", allow=["mcp__github__*"])

    assert _by_rule(_endpoint(tmp_path, app), MCP) == ["mcp-server/github autoApprove"]
