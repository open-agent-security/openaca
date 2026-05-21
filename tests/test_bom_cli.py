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


def test_bom_repo_warns_on_parse_failures(tmp_path):
    """bom repo emits a stderr warning when manifests fail to parse."""
    (tmp_path / ".mcp.json").write_text("not valid json{{{", encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "repo", "--target", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "warning:" in result.output
    assert "failed to parse" in result.output


def test_scan_bom_rejects_non_object_json(tmp_path):
    """scan bom exits with a controlled error when the BOM file is a JSON array."""
    bom_path = tmp_path / "bad.bom.json"
    bom_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    result = CliRunner().invoke(
        scan_main,
        ["bom", "--input", str(bom_path)],
    )

    assert result.exit_code != 0
    assert "BOM must be a JSON object" in result.output


def test_scan_bom_rejects_malformed_json(tmp_path):
    """scan bom exits with a controlled CLI error (not a traceback) for unparseable JSON."""
    bom_path = tmp_path / "malformed.bom.json"
    bom_path.write_text("{not valid json", encoding="utf-8")

    result = CliRunner().invoke(
        scan_main,
        ["bom", "--input", str(bom_path)],
    )

    assert result.exit_code != 0
    assert "invalid JSON" in result.output
    assert isinstance(result.exception, SystemExit)
