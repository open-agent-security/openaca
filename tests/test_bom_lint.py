import json

from click.testing import CliRunner

from tools.bom import build_agent_bom
from tools.bom_cli import main as bom_main
from tools.cli import main as openaca_main
from tools.component_ref import ComponentRef


def test_bom_lint_accepts_generated_bom(tmp_path):
    bom = build_agent_bom(
        [
            ComponentRef(
                ecosystem="npm",
                name="@mcpjam/inspector",
                version="1.4.2",
                extra={"component_type": "mcp_server"},
            )
        ],
        target_type="repo",
        target=".",
    )
    path = tmp_path / "agent.bom.json"
    path.write_text(json.dumps(bom.to_cyclonedx()), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 0, result.output
    assert f"{path}: ok" in result.output


def test_bom_lint_rejects_duplicate_bom_refs(tmp_path):
    doc = _valid_bom_doc()
    doc["components"].append(dict(doc["components"][0]))
    path = tmp_path / "duplicate.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert "duplicate bom-ref" in result.output


def test_bom_lint_rejects_dangling_dependency_refs(tmp_path):
    doc = _valid_bom_doc()
    doc["dependencies"] = [{"ref": "pkg:npm/%40mcpjam/inspector@1.4.2", "dependsOn": ["missing"]}]
    path = tmp_path / "dangling.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert "dependency target 'missing' does not match any component bom-ref" in result.output


def test_bom_lint_rejects_component_without_identity(tmp_path):
    doc = _valid_bom_doc()
    component = doc["components"][0]
    component["properties"] = [
        prop for prop in component["properties"] if prop["name"] != "openaca:identity"
    ]
    path = tmp_path / "missing-identity.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert "must have openaca:identity" in result.output


def test_bom_lint_rejects_invalid_openaca_component_type(tmp_path):
    doc = _valid_bom_doc()
    for prop in doc["components"][0]["properties"]:
        if prop["name"] == "openaca:component_type":
            prop["value"] = "database"
    path = tmp_path / "bad-type.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert "openaca:component_type 'database' is not recognized" in result.output


def test_bom_lint_rejects_schema_errors(tmp_path):
    doc = _valid_bom_doc()
    doc["bomFormat"] = "SPDX"
    path = tmp_path / "bad-schema.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert "schema:" in result.output
    assert "'CycloneDX' was expected" in result.output


def _valid_bom_doc() -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "properties": [
                {"name": "openaca:schema_version", "value": "0.1"},
                {"name": "openaca:target_type", "value": "repo"},
            ]
        },
        "components": [
            {
                "type": "application",
                "bom-ref": "pkg:npm/%40mcpjam/inspector@1.4.2",
                "name": "@mcpjam/inspector",
                "version": "1.4.2",
                "purl": "pkg:npm/%40mcpjam/inspector@1.4.2",
                "properties": [
                    {"name": "openaca:identity", "value": "mcp-server/inspector"},
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    {"name": "openaca:scope", "value": "agent-component"},
                ],
            }
        ],
        "dependencies": [{"ref": "pkg:npm/%40mcpjam/inspector@1.4.2", "dependsOn": []}],
    }
