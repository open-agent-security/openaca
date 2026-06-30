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


def test_bom_lint_accepts_generated_package_dependency(tmp_path):
    bom = build_agent_bom(
        [
            ComponentRef(
                ecosystem="npm",
                name="hono",
                version="4.12.5",
                scope="agent-dependency",
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
                {"name": "openaca:schema_version", "value": "0.2"},
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


def test_bom_lint_accepts_graph_backed_bom_with_target_dependency(tmp_path):
    """Graph-backed BOMs encode the scan target as metadata.component (bom-ref
    `openaca:target`) and emit dependencies[] edges whose parent is that target
    ref. The linter must accept the metadata.component bom-ref as a valid
    dependency endpoint, not reject it as 'does not match any component bom-ref'."""
    from tools.graph import Edge, Graph, Node

    target = Node(key="openaca:target", kind="target", ref=None)
    plugin = Node(
        key="plugin/mp/demo@1",
        kind="plugin",
        ref=ComponentRef(
            name="demo",
            version="1",
            component_identity="plugin/mp/demo",
            extra={"component_type": "plugin"},
        ),
    )
    pkg = Node(
        key="skills/x/package.json#dependencies#pkg:npm/lodash@4.17.20",
        kind="package",
        ref=ComponentRef(ecosystem="npm", name="lodash", version="4.17.20"),
    )
    graph = Graph(
        nodes={n.key: n for n in (target, plugin, pkg)},
        edges=[Edge("openaca:target", "plugin/mp/demo@1"), Edge("plugin/mp/demo@1", pkg.key)],
    )
    bom = build_agent_bom([], target_type="repo", target=".", graph=graph)
    path = tmp_path / "agent.bom.json"
    path.write_text(json.dumps(bom.to_cyclonedx()), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])
    assert result.exit_code == 0, result.output
    assert f"{path}: ok" in result.output


def test_bom_lint_accepts_schema_version_0_1(tmp_path):
    """BOMs produced by OpenACA 0.2.0 carry openaca:schema_version 0.1 (mislabeled
    at the time) and cannot be relabeled. The linter must accept them."""
    doc = _valid_bom_doc()
    for prop in doc["metadata"]["properties"]:
        if prop["name"] == "openaca:schema_version":
            prop["value"] = "0.1"
    path = tmp_path / "v0.1.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 0, result.output
    assert f"{path}: ok" in result.output


def test_bom_lint_handles_non_dict_metadata_without_crashing():
    """A schema-invalid BOM whose `metadata` is not an object (e.g. a list or
    string) must not raise AttributeError in check_semantics — it should return
    error strings, letting `bom lint` report validation errors instead of a
    traceback."""
    from tools.bom_lint import check_semantics

    for bad_metadata in ([], "bad", 42):
        doc = {"metadata": bad_metadata, "components": [], "dependencies": []}
        assert isinstance(check_semantics(doc), list)  # no raise
