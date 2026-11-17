"""Pi posture (docs/specs/pi-agent-kind.md, Posture): every allowlisted rule
reaches a Pi-owned surface, and the two rule changes the kind needs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tools.agent_kinds import pi
from tools.posture.immutability import is_mutable_reference
from tools.posture.rules.mcp_header_credential import check_mcp_header_credential


def _write(path: Path, content: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _findings(args: list[str]) -> list[dict]:
    from tools.scan import main as scan_main

    result = CliRunner().invoke(scan_main, [*args, "--include-posture", "--format", "json"])
    assert result.exit_code in (0, 1), result.output
    return [
        f
        for f in json.loads(result.stdout)["findings"]
        if f.get("agent", {}).get("kind") == "pi" and f.get("rule_id")
    ]


def test_every_declared_rule_fires_on_pis_own_surfaces(tmp_path):
    _write(
        tmp_path / ".pi" / "mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "unpinned": {"command": "npx", "args": ["-y", "some-mcp-server"]},
                    "plain": {"url": "http://insecure.example.test/mcp"},
                    "literal": {
                        "url": "https://remote.example.test/mcp",
                        "headers": {"Authorization": "Bearer dummy-literal-token"},
                    },
                }
            }
        ),
    )
    _write(
        tmp_path / ".pi" / "skills" / "release" / "SKILL.md",
        "---\nname: release\ndescription: Ship.\nallowed-tools:\n  - bash\n---\nShip.\n",
    )

    findings = _findings(["repo", "--target", str(tmp_path)])

    fired = {f["rule_id"] for f in findings}
    assert pi.KIND.posture_rules is not None
    # `project_trust` reads the agent directory, which a repository scan never does.
    assert fired == pi.KIND.posture_rules - {"openaca-posture-project-trust"}
    assert all(f.get("bom_ref") for f in findings)


def test_an_unpinned_package_declaration_is_a_mutable_install(tmp_path):
    _write(
        tmp_path / ".pi" / "settings.json",
        json.dumps(
            {
                "packages": [
                    "npm:@scope/floating",
                    "npm:@scope/pinned@1.2.3",
                    "git:github.com/owner/branch@main",
                    "git:github.com/owner/commit@" + "a" * 40,
                ]
            }
        ),
    )

    findings = _findings(["repo", "--target", str(tmp_path)])

    names = sorted(
        f["component"]["name"]
        for f in findings
        if f["rule_id"] == "openaca-posture-mutable-install-reference"
    )
    assert names == [
        "github/owner/branch (git+https://github.com/owner/branch@main)",
        "npm/@scope/floating (npm:@scope/floating)",
    ]


def test_installed_trust_records_are_project_trust_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = tmp_path / ".pi" / "agent"
    _write(
        agent / "trust.json",
        json.dumps({"/work/trusted": True, "/work/refused": False, "/work/cleared": None}),
    )

    findings = _findings(["endpoint", "--kind", "pi"])

    trust = [f for f in findings if f["rule_id"] == "openaca-posture-project-trust"]
    assert [f["component"]["name"] for f in trust] == ["/work/trusted"]
    assert trust[0]["active_in"] == ["pi"]


def test_a_disabled_insecure_server_is_not_an_exposure(tmp_path):
    _write(
        tmp_path / ".pi" / "mcp.json",
        json.dumps(
            {"mcpServers": {"off": {"url": "http://insecure.example.test/mcp", "enabled": False}}}
        ),
    )

    findings = _findings(["repo", "--target", str(tmp_path)])

    assert not [f for f in findings if f["rule_id"] == "openaca-posture-insecure-transport"]


# --- The two rule changes ----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "reported"),
    [
        ("Bearer dummy-literal-token", True),
        ("Bearer ${DOCS_TOKEN}", False),
        ("Bearer $DOCS_TOKEN", False),
        ("$DOCS_TOKEN", False),
        ("!echo Bearer $(gh auth token)", False),
        ("Bearer !not-a-command", True),
    ],
)
def test_pi_header_expansions_are_references(value, reported):
    manifest = {
        "mcpServers": {
            "docs": {"url": "https://docs.example.test/mcp", "headers": {"Authorization": value}}
        }
    }

    findings = check_mcp_header_credential([(Path("mcp.json"), manifest)])

    assert bool(findings) is reported


@pytest.mark.parametrize(
    ("reference", "mutable"),
    [
        ("npm:@scope/tools", True),
        ("npm:@scope/tools@^1.2.0", True),
        ("npm:@scope/tools@latest", True),
        ("npm:@scope/tools@1.2.3", False),
        ("npm:tools@1.2.3", False),
    ],
)
def test_the_npm_scheme_is_judged_as_a_package_spec(reference, mutable):
    assert is_mutable_reference(reference) is mutable
