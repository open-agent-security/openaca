import json

from click.testing import CliRunner

from tools.agent_kinds import AgentKind
from tools.bom import build_agent_bom
from tools.bom_cli import main as bom_main
from tools.cli import main as openaca_main
from tools.component_ref import ComponentRef
from tools.graph import Graph


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


def test_bom_lint_accepts_schema_0_4_component_without_identity(tmp_path):
    doc = _valid_bom_doc()
    component = doc["components"][0]
    component["properties"] = [
        prop for prop in component["properties"] if prop["name"] != "openaca:identity"
    ]
    path = tmp_path / "missing-identity.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 0, result.output


def test_bom_lint_rejects_pre_0_4_component_without_identity(tmp_path):
    doc = _valid_bom_doc()
    for prop in doc["metadata"]["properties"]:
        if prop["name"] == "openaca:schema_version":
            prop["value"] = "0.3"
    component = doc["components"][0]
    component["properties"] = [
        prop for prop in component["properties"] if prop["name"] != "openaca:identity"
    ]
    path = tmp_path / "legacy-missing-identity.bom.json"
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
                {"name": "openaca:schema_version", "value": "0.4"},
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


def test_bom_lint_rejects_invalid_capability_coverage(tmp_path):
    doc = _valid_bom_doc()
    doc["components"][0]["properties"].append(
        {"name": "openaca:capability_coverage", "value": "bogus"}
    )
    path = tmp_path / "bad-coverage.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert "openaca:capability_coverage 'bogus' is not recognized" in result.output


def test_bom_lint_rejects_non_json_capabilities(tmp_path):
    doc = _valid_bom_doc()
    doc["components"][0]["properties"].append({"name": "openaca:capabilities", "value": "not json"})
    path = tmp_path / "bad-capabilities-json.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert "openaca:capabilities is not valid JSON" in result.output


def test_bom_lint_rejects_malformed_capability_entry(tmp_path):
    doc = _valid_bom_doc()
    doc["components"][0]["properties"].append(
        {"name": "openaca:capabilities", "value": json.dumps([{"name": "shell_exec"}])}
    )
    path = tmp_path / "bad-capability-entry.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert "openaca:capabilities[0] is invalid" in result.output


def test_bom_lint_rejects_capability_evidence_missing_kind(tmp_path):
    doc = _valid_bom_doc()
    doc["components"][0]["properties"].extend(
        [
            {
                "name": "openaca:capabilities",
                "value": json.dumps(
                    [
                        {
                            "name": "shell_exec",
                            "execution_locus": "local",
                            "method": "declared",
                            "source": "hook",
                            "source_version": "1",
                            "confidence": "high",
                            "evidence": [{}],
                        }
                    ]
                ),
            },
            {"name": "openaca:capability_coverage", "value": "partial"},
        ]
    )
    path = tmp_path / "bad-evidence-kind.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert "openaca:capabilities[0] is invalid" in result.output


def test_bom_lint_rejects_coverage_without_capabilities(tmp_path):
    doc = _valid_bom_doc()
    doc["components"][0]["properties"].append(
        {"name": "openaca:capability_coverage", "value": "partial"}
    )
    path = tmp_path / "coverage-only.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert (
        "openaca:capabilities and openaca:capability_coverage must both be present or both absent"
        in result.output
    )


def test_bom_lint_rejects_capabilities_without_coverage(tmp_path):
    doc = _valid_bom_doc()
    doc["components"][0]["properties"].append(
        {
            "name": "openaca:capabilities",
            "value": json.dumps(
                [
                    {
                        "name": "shell_exec",
                        "execution_locus": "local",
                        "method": "declared",
                        "source": "hook",
                        "source_version": "1",
                        "confidence": "high",
                        "evidence": [{"kind": "manifest_field"}],
                    }
                ]
            ),
        }
    )
    path = tmp_path / "capabilities-only.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 1
    assert (
        "openaca:capabilities and openaca:capability_coverage must both be present or both absent"
        in result.output
    )


def test_bom_lint_accepts_valid_capability_descriptors(tmp_path):
    doc = _valid_bom_doc()
    doc["components"][0]["properties"].extend(
        [
            {
                "name": "openaca:capabilities",
                "value": json.dumps(
                    [
                        {
                            "name": "shell_exec",
                            "execution_locus": "local",
                            "method": "declared",
                            "source": "hook",
                            "source_version": "1",
                            "confidence": "high",
                            "evidence": [{"kind": "manifest_field"}],
                        }
                    ]
                ),
            },
            {"name": "openaca:capability_coverage", "value": "partial"},
        ]
    )
    path = tmp_path / "good-capabilities.bom.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["lint", str(path)])

    assert result.exit_code == 0, result.output


def test_bom_lint_handles_non_dict_metadata_without_crashing():
    """A schema-invalid BOM whose `metadata` is not an object (e.g. a list or
    string) must not raise AttributeError in check_semantics — it should return
    error strings, letting `bom lint` report validation errors instead of a
    traceback."""
    from tools.bom_lint import check_semantics

    for bad_metadata in ([], "bad", 42):
        doc = {"metadata": bad_metadata, "components": [], "dependencies": []}
        assert isinstance(check_semantics(doc), list)  # no raise


def _agent_doc(**overrides):
    """A minimal 0.5 agent-rooted document."""
    props = {
        "openaca:agent_kind": "claude-code",
        "openaca:composition_source": "installed",
        "openaca:composition_coverage": "complete",
    }
    props.update(overrides.pop("metadata_component_props", {}))
    for key in overrides.pop("drop", ()):
        props.pop(key, None)
    bom_ref = overrides.pop("bom_ref", "root/claude-code")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "tools": [{"vendor": "OpenACA", "name": "openaca"}],
            "properties": [{"name": "openaca:schema_version", "value": "0.5"}],
            "component": {
                "type": "application",
                "bom-ref": bom_ref,
                "name": "Claude Code",
                "properties": [{"name": k, "value": v} for k, v in props.items()],
            },
        },
        "components": [],
        "dependencies": [{"ref": overrides.pop("dep_ref", bom_ref), "dependsOn": []}],
    }


def test_lint_accepts_agent_rooted_bom(tmp_path):
    path = tmp_path / "agent.cdx.json"
    path.write_text(json.dumps(_agent_doc()), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 0, result.output


def test_lint_still_accepts_stored_0_4_target_type(tmp_path):
    doc = _agent_doc()
    doc["metadata"]["properties"] = [
        {"name": "openaca:schema_version", "value": "0.4"},
        {"name": "openaca:target_type", "value": "endpoint"},
    ]
    doc["metadata"]["component"] = {
        "type": "application",
        "bom-ref": "openaca:target",
        "name": "/home/u/.claude",
        "properties": [{"name": "openaca:component_type", "value": "target"}],
    }
    doc["dependencies"] = [{"ref": "openaca:target", "dependsOn": []}]
    path = tmp_path / "legacy.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 0, result.output


def test_lint_rejects_schema_0_5_without_an_agent_root(tmp_path):
    """`check_agent_metadata` only fires once `metadata.component` already has a
    `root/`-prefixed bom-ref, so a `0.5` document missing `metadata.component`
    entirely — the shape a graphless `build_agent_bom(...).to_cyclonedx()` call
    would have produced before it was made to fall back to `0.4` — must be
    rejected some other way, since schema `0.5` is defined as the agent-rooted
    shape (ADR-0044/0045)."""
    doc = _agent_doc()
    del doc["metadata"]["component"]
    doc["dependencies"] = []
    path = tmp_path / "no-root.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "root/" in result.output


def test_lint_rejects_schema_0_5_with_a_non_agent_root(tmp_path):
    doc = _agent_doc(bom_ref="openaca:target", dep_ref="openaca:target")
    doc["metadata"]["component"]["properties"] = [
        {"name": "openaca:component_type", "value": "target"}
    ]
    path = tmp_path / "place-root.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "root/" in result.output


def test_lint_rejects_bad_composition_source(tmp_path):
    doc = _agent_doc(metadata_component_props={"openaca:composition_source": "sandbox"})
    path = tmp_path / "bad.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "openaca:composition_source" in result.output


def test_lint_rejects_duplicate_openaca_property(tmp_path):
    doc = _agent_doc()
    doc["components"] = [
        {
            "type": "application",
            "bom-ref": "claude-code/x#y#skill/x",
            "name": "x",
            "properties": [
                {"name": "openaca:identity", "value": "skill/x"},
                {"name": "openaca:identity", "value": "skill/x"},
            ],
        }
    ]
    doc["dependencies"].append({"ref": "claude-code/x#y#skill/x", "dependsOn": []})
    path = tmp_path / "dup.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "appears more than once" in result.output


def test_lint_rejects_agent_id_on_a_singleton_kind(tmp_path):
    doc = _agent_doc(
        bom_ref="root/claude-code/x",
        metadata_component_props={"openaca:agent_id": "x"},
    )
    path = tmp_path / "singleton.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "is singleton" in result.output


def test_lint_rejects_missing_agent_id_on_a_multiplicity_kind(tmp_path, monkeypatch):
    # Inline stand-in for a many-per-place kind — the shared synthetic-kind
    # fixture in `tests/fixtures/agent_kinds.py` does not exist until Task 7,
    # and this task's test suite must not depend forward on it.
    fake_kind = AgentKind(
        id="synthetic",
        display_name="Synthetic",
        cardinality="many_per_place",
        root_label="synthetic",
        coverage_baseline={"installed": "partial", "declared": "partial"},
        discover=lambda ctx: [],
        compose=lambda agent, **_: Graph(nodes={}),
    )
    monkeypatch.setattr("tools.bom_lint.REGISTRY", (fake_kind,))
    doc = _agent_doc(
        bom_ref="root/synthetic",
        metadata_component_props={"openaca:agent_kind": "synthetic"},
    )
    path = tmp_path / "missing_id.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "same-kind multiplicity" in result.output


def test_lint_accepts_an_unknown_kind_without_a_cardinality_opinion(tmp_path):
    doc = _agent_doc(
        bom_ref="root/third-party-kind",
        metadata_component_props={"openaca:agent_kind": "third-party-kind"},
    )
    path = tmp_path / "unknown_kind.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 0, result.output
