"""OpenCode posture (ADR-0083, ADR-0085, docs/specs/opencode-agent-kind.md).

Three rules apply. The MCP posture view comes from the composed servers: a
server's headers merge across its files, each attributed to the file that sets
it, and OpenCode's `{env:…}` and `{file:…}` references are references. The
permission rules and the skill rule do not apply.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openaca.core import collect_installed_agents
from tools.parsers.opencode_config import dollar_references
from tools.posture.rules import insecure_transport, mcp_header_credential, mutable_install

HEADER = mcp_header_credential.RULE_ID
INSECURE = insecure_transport.RULE_ID


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _json(path: Path, data: object) -> Path:
    return _write(path, json.dumps(data))


def _remote(url: str, **headers: str) -> dict:
    entry: dict = {"type": "remote", "url": url}
    if headers:
        entry["headers"] = headers
    return entry


@pytest.fixture
def roots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    config = home / ".config" / "opencode"
    config.mkdir(parents=True)
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    return config, project


def _findings(project: Path) -> tuple:
    (agent,) = collect_installed_agents(project=project, kind_id="opencode")
    return agent.posture_findings


def test_a_header_is_attributed_to_the_file_that_sets_it(roots):
    """The project redefines the server, so the server is the project's; the
    global file's literal header still merges in, and is reported there."""
    config, project = roots
    token = "dummy-opencode-literal"
    _json(
        config / "opencode.json",
        {"mcp": {"docs": _remote("https://old.example.com/mcp", Authorization=f"Bearer {token}")}},
    )
    _json(
        project / "opencode.json",
        {"mcp": {"docs": _remote("https://docs.example.com/mcp", **{"X-Api-Key": "{env:KEY}"})}},
    )

    findings = [f for f in _findings(project) if f.rule_id == HEADER]

    assert len(findings) == 1
    (finding,) = findings
    assert finding.declared_by == {"kind": "manifest", "path": str(config / "opencode.json")}
    assert finding.evidence == {"fields": ["headers.authorization"]}
    assert finding.bom_ref == "project/opencode.json#$.mcp.docs#mcp-remote/docs.example.com/mcp"
    assert token not in json.dumps(finding.evidence)


@pytest.mark.parametrize(
    "value",
    [
        "Bearer {env:MY_API_KEY}",
        "{file:~/.secrets/token}",
        "Bearer {file:secrets/token.txt}",
        "{file:./token}",
    ],
)
def test_opencode_references_are_not_literals(roots, value):
    config, project = roots
    _json(
        config / "opencode.json",
        {"mcp": {"docs": _remote("https://docs.example.com/mcp", Authorization=value)}},
    )

    assert [f for f in _findings(project) if f.rule_id == HEADER] == []


def test_insecure_transport_skips_a_server_switched_off(roots):
    config, project = roots
    _json(
        config / "opencode.json",
        {
            "mcp": {
                "plain": _remote("http://plain.example.com/mcp"),
                "off": _remote("http://off.example.com/mcp"),
            }
        },
    )
    _json(project / "opencode.json", {"mcp": {"off": {"enabled": False}}})

    findings = [f for f in _findings(project) if f.rule_id == INSECURE]

    assert [f.component["name"] for f in findings] == [
        "mcp-server/plain @ http://plain.example.com/mcp"
    ]
    assert findings[0].bom_ref


def test_an_unpinned_launch_is_a_mutable_install(roots):
    config, project = roots
    _json(
        config / "opencode.json",
        {"mcp": {"fetch": {"type": "local", "command": ["uvx", "mcp-server-fetch"]}}},
    )

    findings = [f for f in _findings(project) if f.rule_id == mutable_install.RULE_ID]

    assert len(findings) == 1 and findings[0].bom_ref


def test_permissions_and_skill_tools_report_nothing(roots):
    """ADR-0085: OpenCode allows every tool by default, and ignores a skill's
    `allowed-tools`."""
    config, project = roots
    _json(
        config / "opencode.json",
        {
            "permission": {"bash": "allow", "github_*": "allow", "*": "allow"},
            "mcp": {"github": {"type": "local", "command": ["npx", "-y", "gh-mcp@1.0.0"]}},
        },
    )
    _write(
        project / ".opencode" / "skills" / "deploy" / "SKILL.md",
        "---\nname: deploy\nallowed-tools: Bash\n---\nDeploy.\n",
    )

    assert list(_findings(project)) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Bearer {env:TOKEN}", "Bearer ${env:TOKEN}"),
        ("{file:token.txt}", "${file:./token.txt}"),
        ("{file:/run/secret}", "${file:/run/secret}"),
        ("{file:~/secret}", "${file:~/secret}"),
        ("Bearer ${env:ALREADY}", "Bearer ${env:ALREADY}"),
        ("Bearer literal", "Bearer literal"),
        (123, 123),
    ],
)
def test_dollar_references(value, expected):
    assert dollar_references(value) == expected
