import json

from click.testing import CliRunner

from tools.cli import main as openaca_main
from tools.scan import main as scan_main


def test_bom_repo_command_emits_cyclonedx_agent_bom(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "inspector": {
                        "command": "npx",
                        "args": ["@mcpjam/inspector@1.4.2"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(openaca_main, ["bom", "repo", "--target", str(tmp_path)])

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["bomFormat"] == "CycloneDX"
    assert any(c.get("purl") == "pkg:npm/%40mcpjam/inspector@1.4.2" for c in doc["components"])


def test_scan_bom_reuses_matching_without_posture_replay(tmp_path):
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "properties": [
                {"name": "openaca:schema_version", "value": "0.1"},
                {"name": "openaca:target_type", "value": "bom"},
            ]
        },
        "components": [
            {
                "type": "application",
                "bom-ref": "pkg:npm/%40cyanheads/git-mcp-server@1.1.0",
                "name": "@cyanheads/git-mcp-server",
                "version": "1.1.0",
                "purl": "pkg:npm/%40cyanheads/git-mcp-server@1.1.0",
                "properties": [
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    {"name": "openaca:scope", "value": "agent-component"},
                ],
            }
        ],
        "dependencies": [],
    }
    bom_path = tmp_path / "agent.bom.json"
    bom_path.write_text(json.dumps(bom), encoding="utf-8")

    result = CliRunner().invoke(
        scan_main,
        ["bom", "--input", str(bom_path), "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["stats"]["components"] == 1
    assert any(f["id"] == "GHSA-3q26-f695-pp76" for f in payload["findings"])
