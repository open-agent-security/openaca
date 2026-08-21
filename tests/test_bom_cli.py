import json
from pathlib import Path

from click.testing import CliRunner

from tools.bom import build_agent_bom, component_refs_from_cyclonedx
from tools.cli import main as openaca_main
from tools.component_ref import ComponentRef
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


def test_bom_repo_output_writes_cyclonedx_agent_bom_to_file(tmp_path):
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
    output = tmp_path / "openaca.bom.json"

    result = CliRunner().invoke(
        openaca_main,
        ["bom", "repo", "--target", str(tmp_path), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert result.output == ""
    doc = json.loads(output.read_text(encoding="utf-8"))
    assert doc["bomFormat"] == "CycloneDX"
    assert any(c.get("purl") == "pkg:npm/%40mcpjam/inspector@1.4.2" for c in doc["components"])


def test_bom_endpoint_short_output_writes_cyclonedx_agent_bom_to_file(tmp_path):
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
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
    output = tmp_path / "endpoint.bom.json"

    result = CliRunner().invoke(
        openaca_main,
        ["bom", "endpoint", "--config-dir", str(config_dir), "-o", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert result.output == ""
    doc = json.loads(output.read_text(encoding="utf-8"))
    assert doc["bomFormat"] == "CycloneDX"
    assert any(c.get("purl") == "pkg:npm/%40mcpjam/inspector@1.4.2" for c in doc["components"])


def test_bom_diff_command_renders_text_summary(tmp_path):
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(
        json.dumps(
            _diff_bom(
                components=[
                    _diff_component("plugin/demo", "plugin/demo", "plugin", version="1.0.0")
                ],
                dependencies=[],
            )
        ),
        encoding="utf-8",
    )
    after_path.write_text(
        json.dumps(
            _diff_bom(
                components=[
                    _diff_component("plugin/demo", "plugin/demo", "plugin", version="1.1.0"),
                    _diff_component("mcp/new", "mcp-server/new", "mcp_server"),
                ],
                dependencies=[{"ref": "openaca:target", "dependsOn": ["plugin/demo", "mcp/new"]}],
            )
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        openaca_main,
        ["bom", "diff", "--before", str(before_path), "--after", str(after_path)],
    )

    assert result.exit_code == 0, result.output
    assert "BOM diff: 1 added, 0 removed, 1 changed" in result.output
    assert "+ mcp-server/new (mcp_server) [mcp/new]" in result.output
    assert "~ plugin/demo (plugin) version 1.1.0 [plugin/demo]" in result.output
    assert "version: 1.0.0 -> 1.1.0" in result.output
    assert "+ openaca:target -> mcp/new" in result.output


def test_bom_diff_command_can_emit_json(tmp_path):
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(_diff_bom()), encoding="utf-8")
    after_path.write_text(
        json.dumps(
            _diff_bom(
                components=[_diff_component("mcp/new", "mcp-server/new", "mcp_server")],
                dependencies=[{"ref": "openaca:target", "dependsOn": ["mcp/new"]}],
            )
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        openaca_main,
        [
            "bom",
            "diff",
            "--before",
            str(before_path),
            "--after",
            str(after_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["added_components"] == [
        {
            "bom_ref": "mcp/new",
            "identity": "mcp-server/new",
            "component_type": "mcp_server",
            "name": "mcp-server/new",
            "version": None,
            "purl": None,
            "git_commit_sha": None,
            "artifact_coordinates": None,
            "url": None,
            "install_source": None,
            "git_ref": None,
            "transport": None,
            "source_provenance": None,
            "match_coordinate": None,
            "scope": None,
            "capabilities": None,
            "capability_coverage": None,
            "runtime_hosts": None,
        }
    ]
    assert payload["added_edges"] == [{"parent": "openaca:target", "child": "mcp/new"}]


def test_scan_bom_verbose_renders_repo_inventory_from_bom(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["@modelcontextprotocol/server-filesystem"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    bom_path = tmp_path / "openaca.bom.json"
    bom_result = CliRunner().invoke(
        openaca_main,
        ["bom", "repo", "--target", str(tmp_path), "--output", str(bom_path)],
    )
    assert bom_result.exit_code == 0, bom_result.output

    direct = CliRunner().invoke(scan_main, ["repo", "--target", str(tmp_path), "-v"])
    from_bom = CliRunner().invoke(scan_main, ["bom", "--input", str(bom_path), "-v"])

    assert direct.exit_code == 0, direct.output
    assert from_bom.exit_code == 0, from_bom.output
    expected = "@modelcontextprotocol/server-filesystem (stdio via npx, unpinned) (from .mcp.json)"
    # The inventory tree (root + component leaf) and the count now live in the
    # default stdout card; the BOM-sourced scan reconstructs the same tree as a
    # direct repo scan.
    assert f"repo {tmp_path}" in direct.output
    assert f"repo {tmp_path}" in from_bom.output
    assert "Scanned 1 manifest, 1 component" in direct.output
    assert "Scanned 1 manifest, 1 component" in from_bom.output
    assert expected in direct.output
    assert expected in from_bom.output


def test_scan_bom_verbose_renders_endpoint_inventory_from_bom(tmp_path):
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["@modelcontextprotocol/server-filesystem"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    bom_path = tmp_path / "endpoint.bom.json"
    bom_result = CliRunner().invoke(
        openaca_main,
        ["bom", "endpoint", "--config-dir", str(config_dir), "--output", str(bom_path)],
    )
    assert bom_result.exit_code == 0, bom_result.output

    direct = CliRunner().invoke(scan_main, ["endpoint", "--config-dir", str(config_dir), "-v"])
    from_bom = CliRunner().invoke(scan_main, ["bom", "--input", str(bom_path), "-v"])

    assert direct.exit_code == 0, direct.output
    assert from_bom.exit_code == 0, from_bom.output
    expected = "@modelcontextprotocol/server-filesystem (stdio via npx, unpinned)"
    assert "0 active plugins, 1 direct component, 1 total component" in direct.output
    assert "0 active plugins, 1 direct component, 1 total component" in from_bom.output
    assert "Scanned 0 active plugins, 1 component" in direct.output
    assert "Scanned 0 active plugins, 1 component" in from_bom.output
    assert expected in direct.output
    assert expected in from_bom.output


def test_scan_bom_reuses_matching_without_posture_replay(tmp_path):
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "properties": [
                {"name": "openaca:schema_version", "value": "0.3"},
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


def test_scan_bom_flat_package_keeps_stored_agent_dependency_scope(tmp_path):
    # [bug-fixed] A flat BOM (no metadata.component, no edges) carrying a
    # package-kind component stored as agent-dependency must still produce its
    # finding. graph_from_cyclonedx would synthesize a target and attach the
    # package directly under it, so scope_of re-derives software-dependency and
    # _filter_agent_scope_refs drops the ref — silently losing the finding.
    # scan_bom now reads the stored openaca:scope for flat BOMs instead. Before
    # the fix this BOM matched 0 findings (exit 0); after, the finding is present.
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "properties": [
                {"name": "openaca:schema_version", "value": "0.3"},
                {"name": "openaca:target_type", "value": "bom"},
            ]
        },
        "components": [
            {
                "type": "library",
                "bom-ref": "pkg:npm/mcp-remote@0.1.0",
                "name": "mcp-remote",
                "version": "0.1.0",
                "purl": "pkg:npm/mcp-remote@0.1.0",
                "properties": [
                    {"name": "openaca:component_type", "value": "package"},
                    {"name": "openaca:scope", "value": "agent-dependency"},
                ],
            }
        ],
        "dependencies": [],
    }
    bom_path = tmp_path / "flat.bom.json"
    bom_path.write_text(json.dumps(bom), encoding="utf-8")

    result = CliRunner().invoke(
        scan_main,
        ["bom", "--input", str(bom_path), "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["stats"]["components"] == 1
    assert any(f["id"] == "GHSA-6xpm-ggf7-wc3p" for f in payload["findings"])
    finding = next(f for f in payload["findings"] if f["id"] == "GHSA-6xpm-ggf7-wc3p")
    assert finding["bom_ref"] == "pkg:npm/mcp-remote@0.1.0"


def test_scan_bom_nontarget_metadata_component_keeps_stored_scope(tmp_path):
    # [bug-fixed] metadata.component is a standard CycloneDX field; a flat or
    # externally-produced BOM can carry one with a bom-ref that is NOT the
    # OpenACA graph target key. Gating the graph-backed path on mere presence of
    # metadata.component sent such BOMs through graph_from_cyclonedx, which
    # synthesizes a target, re-derives the top-level agent-dependency package as
    # software-dependency, and drops it (0 components, 0 findings). scan_bom now
    # gates on metadata.component bom-ref == "openaca:target", so this flat BOM
    # takes the stored-scope path and the finding survives.
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": "my-app@2.0.0",
                "name": "my-app",
                "version": "2.0.0",
            },
            "properties": [
                {"name": "openaca:schema_version", "value": "0.3"},
                {"name": "openaca:target_type", "value": "bom"},
            ],
        },
        "components": [
            {
                "type": "library",
                "bom-ref": "pkg:npm/mcp-remote@0.1.0",
                "name": "mcp-remote",
                "version": "0.1.0",
                "purl": "pkg:npm/mcp-remote@0.1.0",
                "properties": [
                    {"name": "openaca:component_type", "value": "package"},
                    {"name": "openaca:scope", "value": "agent-dependency"},
                ],
            }
        ],
        "dependencies": [],
    }
    bom_path = tmp_path / "flat-with-component.bom.json"
    bom_path.write_text(json.dumps(bom), encoding="utf-8")

    result = CliRunner().invoke(
        scan_main,
        ["bom", "--input", str(bom_path), "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["stats"]["components"] == 1
    assert any(f["id"] == "GHSA-6xpm-ggf7-wc3p" for f in payload["findings"])


def test_scan_bom_graph_backed_uses_graph_path(tmp_path):
    # A genuinely graph-backed BOM (metadata.component bom-ref == "openaca:target"
    # plus dependencies[] edges) must still reconstruct via the graph path: a
    # top-level agent-dependency package reached through an agent ancestor keeps
    # agent scope and produces its finding.
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": "openaca:target",
                "name": "endpoint",
                "properties": [{"name": "openaca:component_type", "value": "target"}],
            },
            "properties": [
                {"name": "openaca:schema_version", "value": "0.3"},
                {"name": "openaca:target_type", "value": "bom"},
            ],
        },
        "components": [
            {
                "type": "application",
                "bom-ref": "endpoint/mcp/server",
                "name": "server",
                "properties": [
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    {"name": "openaca:scope", "value": "agent-component"},
                    {"name": "openaca:identity", "value": "endpoint/mcp/server"},
                ],
            },
            {
                "type": "library",
                "bom-ref": "pkg:npm/mcp-remote@0.1.0",
                "name": "mcp-remote",
                "version": "0.1.0",
                "purl": "pkg:npm/mcp-remote@0.1.0",
                "properties": [
                    {"name": "openaca:component_type", "value": "package"},
                    {"name": "openaca:scope", "value": "agent-dependency"},
                ],
            },
        ],
        "dependencies": [
            {"ref": "openaca:target", "dependsOn": ["endpoint/mcp/server"]},
            {"ref": "endpoint/mcp/server", "dependsOn": ["pkg:npm/mcp-remote@0.1.0"]},
        ],
    }
    bom_path = tmp_path / "graph.bom.json"
    bom_path.write_text(json.dumps(bom), encoding="utf-8")

    result = CliRunner().invoke(
        scan_main,
        ["bom", "--input", str(bom_path), "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert any(f["id"] == "GHSA-6xpm-ggf7-wc3p" for f in payload["findings"])


def test_scan_bom_rejects_include_posture(tmp_path):
    bom_path = tmp_path / "agent.bom.json"
    bom_path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.7",
                "version": 1,
                "components": [],
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        scan_main,
        ["--include-posture", "bom", "--input", str(bom_path)],
    )

    assert result.exit_code != 0
    assert "--include-posture is not supported for scan bom" in result.output


def test_bom_repo_excludes_bare_package_json_as_software_dependency(tmp_path):
    """bom repo must not include bare package.json deps (software-dependency scope)."""
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}',
        encoding="utf-8",
    )
    result = CliRunner().invoke(openaca_main, ["bom", "repo", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    purls = {c.get("purl") for c in doc.get("components", [])}
    assert not any("lodash" in (p or "") for p in purls), (
        "bare package.json deps are software-dependency and must not appear in the agent BOM"
    )


def test_bom_cli_repo_emits_capability_properties(tmp_path):
    """The direct `bom repo` CLI path (no tools/scan.py) annotates + emits capabilities."""
    skill_dir = tmp_path / ".claude" / "skills" / "bashful"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bashful\ndescription: runs bash\nallowed-tools: Bash(*)\n---\nbody\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(openaca_main, ["bom", "repo", "--target", str(tmp_path)])

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    skill = next(
        c
        for c in doc["components"]
        if any(
            p["name"] == "openaca:component_type" and p["value"] == "skill"
            for p in c.get("properties", [])
        )
    )
    props = {p["name"]: p["value"] for p in skill["properties"]}
    assert props["openaca:capability_coverage"] in {"partial", "complete"}
    assert any(c["name"] == "shell_exec" for c in json.loads(props["openaca:capabilities"]))


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


def test_git_ref_survives_bom_round_trip():
    ref = ComponentRef(
        ecosystem="github",
        name="oraios/serena",
        version=None,
        source_manifest=".mcp.json",
        source_locator="mcpServers/serena",
        extra={"git_ref": "v1.0.0"},
    )
    bom = build_agent_bom([ref], target_type="repo")
    doc = bom.to_cyclonedx()
    restored = component_refs_from_cyclonedx(doc)
    assert len(restored) == 1
    assert restored[0].extra.get("git_ref") == "v1.0.0"


def test_scan_bom_rejects_non_utf8_input(tmp_path):
    """scan bom exits with a controlled CLI error when the BOM file is not valid UTF-8."""
    bom_path = tmp_path / "latin1.bom.json"
    # Write a byte sequence that is valid Latin-1 but not valid UTF-8.
    bom_path.write_bytes(b'{"key": "\xff\xfe"}')

    result = CliRunner().invoke(
        scan_main,
        ["bom", "--input", str(bom_path)],
    )

    assert result.exit_code != 0
    assert "not valid UTF-8" in result.output
    assert isinstance(result.exception, SystemExit)


def test_bom_endpoint_surfaces_resolver_warnings(tmp_path):
    """`bom endpoint` must surface endpoint resolver warnings (parity with the
    old parse_install path). An enabled plugin absent from installed_plugins.json
    produces an 'enabled but missing' warning that must reach stderr."""
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"ghost@mp": True}}), encoding="utf-8"
    )
    (config_dir / "plugins").mkdir()
    (config_dir / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}}), encoding="utf-8"
    )
    output = tmp_path / "endpoint.bom.json"
    result = CliRunner().invoke(
        openaca_main,
        ["bom", "endpoint", "--config-dir", str(config_dir), "-o", str(output)],
    )
    assert result.exit_code == 0, result.output
    assert "ghost@mp enabled but missing from installed_plugins.json" in result.output


def test_bom_endpoint_host_cursor_stub_component_wires_request_and_root_map(tmp_path, monkeypatch):
    """Proves `--host cursor` resolution, root map, and BOM emit are wired
    through `resolve_endpoint_request` — before Task 17 registers a real
    `HOSTS["cursor"].seed_endpoint`, a bare `bom endpoint --host cursor`
    would legitimately emit zero Cursor components, so this stubs the
    adapter (the `_with_detect`-style `monkeypatch.setitem(HOSTS, ...)`
    pattern, substituting a stub `seed_endpoint`) to prove the plumbing
    without asserting on real Cursor composition."""
    import dataclasses

    from tools.component_ref import ComponentRef
    from tools.graph import Node
    from tools.graph_build import _add_child, occurrence_key
    from tools.hosts import HOSTS

    def _stub_cursor_seed(graph, target, config_root, project_root, normalize, *, warnings=None):
        ref = ComponentRef(
            name="stub-server",
            source_manifest=str(config_root / "mcp.json"),
            source_locator="$.mcpServers.stub",
            extra={"component_type": "mcp_server", "runtime_hosts": ["cursor"]},
        )
        _add_child(
            graph, target, Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
        )

    monkeypatch.setitem(
        HOSTS, "cursor", dataclasses.replace(HOSTS["cursor"], seed_endpoint=_stub_cursor_seed)
    )

    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()

    result = CliRunner().invoke(
        openaca_main,
        ["bom", "endpoint", "--host", "cursor", "--config-dir", str(cursor_root)],
    )

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    stub_components = [c for c in doc["components"] if c.get("name") == "stub-server"]
    assert len(stub_components) == 1
    component = stub_components[0]
    assert {"name": "openaca:runtime_hosts", "value": '["cursor"]'} in component["properties"]
    assert component["bom-ref"].startswith("endpoint-cursor/")


def test_bom_endpoint_cursor_dev_linked_plugin_has_no_enabled_property(tmp_path):
    """Task 17's real Cursor `seed_endpoint`, against a dev-linked plugin
    fixture: the plugin component is host-tagged `cursor` and carries no
    enabled/active property (ADR-0045 Decision #7 — no such state exists
    for a dev-linked plugin, so none is asserted)."""
    cursor_root = tmp_path / "cursor"
    plugin_dir = cursor_root / "plugins" / "local" / "demo" / ".cursor-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")

    result = CliRunner().invoke(
        openaca_main,
        ["bom", "endpoint", "--host", "cursor", "--config-dir", str(cursor_root)],
    )

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    plugin_components = [c for c in doc["components"] if c.get("name") == "demo"]
    assert len(plugin_components) == 1
    component = plugin_components[0]
    assert {"name": "openaca:runtime_hosts", "value": '["cursor"]'} in component["properties"]
    prop_names = {p["name"] for p in component["properties"]}
    assert not any(name.endswith(":enabled") or name.endswith(":active") for name in prop_names)


def _bom_metadata_properties(doc: dict) -> dict[str, str]:
    return {p["name"]: p["value"] for p in doc["metadata"]["properties"]}


def test_bom_endpoint_claude_only_metadata_is_byte_identical_pinned(tmp_path):
    """Claude-only `bom endpoint` must keep emitting exactly today's metadata
    property set (no `openaca:scanned_hosts`/`openaca:host_config_roots`, and
    `source_unit_label` stays `"active plugin"`). Pinned as an exact-set
    assertion so a future change can't silently widen single-host output."""
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "endpoint", "--config-dir", str(config_dir)])

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    props = _bom_metadata_properties(doc)
    assert props == {
        "openaca:schema_version": doc["metadata"]["properties"][0]["value"],
        "openaca:target_type": "endpoint",
        "openaca:target": str(config_dir),
        "openaca:source_unit_count": "0",
        "openaca:source_unit_label": "active plugin",
    }


def test_bom_endpoint_cursor_only_metadata_labels_plugin_not_active(tmp_path):
    """Cursor is presence-only (ADR-0045 Decision #7): a Cursor-only endpoint BOM must not
    claim "active plugin", and single-host output carries no scanned_hosts/
    host_config_roots (those are multi-host-gated)."""
    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()

    result = CliRunner().invoke(
        openaca_main, ["bom", "endpoint", "--host", "cursor", "--config-dir", str(cursor_root)]
    )

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    props = _bom_metadata_properties(doc)
    assert props["openaca:source_unit_label"] == "plugin"
    assert props["openaca:target"] == str(cursor_root)
    assert "openaca:scanned_hosts" not in props
    assert "openaca:host_config_roots" not in props


def test_bom_endpoint_two_hosts_metadata_scanned_hosts_and_config_roots(tmp_path, monkeypatch):
    """A two-host endpoint BOM must append `openaca:scanned_hosts` (host ids,
    root-map order) and `openaca:host_config_roots` (host id -> config root).
    `openaca:target` becomes the neutral `endpoint:user-scope` locator since no
    single host's root is authoritative for a multi-host scan; the label
    reflects Cursor's presence-only posture."""
    import dataclasses

    from tools.component_ref import ComponentRef
    from tools.graph import Node
    from tools.graph_build import _add_child, occurrence_key
    from tools.hosts import HOSTS

    def _stub_cursor_seed(graph, target, config_root, project_root, normalize, *, warnings=None):
        ref = ComponentRef(
            name="stub-server",
            source_manifest=str(config_root / "mcp.json"),
            source_locator="$.mcpServers.stub",
            extra={"component_type": "mcp_server", "runtime_hosts": ["cursor"]},
        )
        _add_child(
            graph, target, Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
        )

    monkeypatch.setitem(
        HOSTS, "cursor", dataclasses.replace(HOSTS["cursor"], seed_endpoint=_stub_cursor_seed)
    )

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_root = tmp_path / ".claude"
    claude_root.mkdir()
    (claude_root / "settings.json").write_text("{}", encoding="utf-8")
    cursor_root = tmp_path / ".cursor"
    cursor_root.mkdir()

    result = CliRunner().invoke(openaca_main, ["bom", "endpoint"])

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    props = _bom_metadata_properties(doc)
    assert props["openaca:target"] == "endpoint:user-scope"
    assert json.loads(props["openaca:scanned_hosts"]) == ["claude-code", "cursor"]
    assert json.loads(props["openaca:host_config_roots"]) == {
        "claude-code": str(claude_root),
        "cursor": str(cursor_root),
    }
    assert props["openaca:source_unit_label"] == "plugin"


def test_scan_bom_verbose_renders_multi_host_target_locator(tmp_path, monkeypatch):
    """`scan bom` round-tripping a multi-host endpoint BOM must show the
    neutral `endpoint:user-scope` locator in the "original target" card row,
    not either host's config root."""
    import dataclasses

    from tools.component_ref import ComponentRef
    from tools.graph import Node
    from tools.graph_build import _add_child, occurrence_key
    from tools.hosts import HOSTS

    def _stub_cursor_seed(graph, target, config_root, project_root, normalize, *, warnings=None):
        ref = ComponentRef(
            name="stub-server",
            source_manifest=str(config_root / "mcp.json"),
            source_locator="$.mcpServers.stub",
            extra={"component_type": "mcp_server", "runtime_hosts": ["cursor"]},
        )
        _add_child(
            graph, target, Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
        )

    monkeypatch.setitem(
        HOSTS, "cursor", dataclasses.replace(HOSTS["cursor"], seed_endpoint=_stub_cursor_seed)
    )

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_root = tmp_path / ".claude"
    claude_root.mkdir()
    (claude_root / "settings.json").write_text("{}", encoding="utf-8")
    cursor_root = tmp_path / ".cursor"
    cursor_root.mkdir()

    bom_path = tmp_path / "multi-host.bom.json"
    bom_result = CliRunner().invoke(openaca_main, ["bom", "endpoint", "--output", str(bom_path)])
    assert bom_result.exit_code == 0, bom_result.output

    from_bom = CliRunner().invoke(scan_main, ["bom", "--input", str(bom_path), "-v"])

    assert from_bom.exit_code == 0, from_bom.output
    assert "original target: endpoint endpoint:user-scope" in from_bom.output


def _two_host_endpoint_bom(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """Build a two-host endpoint BOM (claude-code has one MCP server, Cursor
    a stubbed one) and return (bom_path, claude_root)."""
    import dataclasses

    from tools.component_ref import ComponentRef
    from tools.graph import Node
    from tools.graph_build import _add_child, occurrence_key
    from tools.hosts import HOSTS

    def _stub_cursor_seed(graph, target, config_root, project_root, normalize, *, warnings=None):
        ref = ComponentRef(
            name="stub-server",
            source_manifest=str(config_root / "mcp.json"),
            source_locator="$.mcpServers.stub",
            extra={"component_type": "mcp_server", "runtime_hosts": ["cursor"]},
        )
        _add_child(
            graph, target, Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
        )

    monkeypatch.setitem(
        HOSTS, "cursor", dataclasses.replace(HOSTS["cursor"], seed_endpoint=_stub_cursor_seed)
    )

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_root = tmp_path / ".claude"
    claude_root.mkdir()
    claude_root.joinpath("settings.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["@modelcontextprotocol/server-filesystem"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cursor_root = tmp_path / ".cursor"
    cursor_root.mkdir()

    bom_path = tmp_path / "multi-host.bom.json"
    bom_result = CliRunner().invoke(openaca_main, ["bom", "endpoint", "--output", str(bom_path)])
    assert bom_result.exit_code == 0, bom_result.output
    return bom_path, claude_root


def test_scan_bom_two_host_shows_per_host_tags_and_breakdown(tmp_path, monkeypatch):
    """`scan bom --input` on a two-host endpoint BOM must show the same
    per-host display a live `scan endpoint` shows: `[host]` tags on
    top-level inventory entries, an (host: N, ...) stats breakdown, and
    `components_by_host` in JSON stats -- reading `openaca:scanned_hosts`
    off the round-tripped BOM to know the scan spanned two hosts."""
    bom_path, _ = _two_host_endpoint_bom(tmp_path, monkeypatch)

    text_result = CliRunner().invoke(scan_main, ["bom", "--input", str(bom_path)])
    assert text_result.exit_code == 0, text_result.output
    assert "(claude-code: 1, cursor: 1)" in text_result.output
    assert "[claude-code]" in text_result.output
    assert "[cursor]" in text_result.output

    json_result = CliRunner().invoke(
        scan_main, ["bom", "--input", str(bom_path), "--format", "json"]
    )
    assert json_result.exit_code == 0, json_result.output
    doc = json.loads("\n".join(json_result.output.splitlines()[:-1]))
    assert doc["stats"]["components_by_host"] == {"claude-code": 1, "cursor": 1}


def test_scan_bom_multi_host_refs_without_scanned_hosts_falls_back(tmp_path, monkeypatch):
    """A BOM whose `openaca:scanned_hosts` metadata property is missing (pre-
    Stage-4 BOM, or the property stripped) but whose components still carry
    per-ref host attribution must still show the per-host breakdown, derived
    from the refs themselves rather than the metadata property."""
    bom_path, _ = _two_host_endpoint_bom(tmp_path, monkeypatch)

    doc = json.loads(bom_path.read_text(encoding="utf-8"))
    doc["metadata"]["properties"] = [
        p for p in doc["metadata"]["properties"] if p["name"] != "openaca:scanned_hosts"
    ]
    bom_path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(scan_main, ["bom", "--input", str(bom_path)])
    assert result.exit_code == 0, result.output
    assert "(claude-code: 1, cursor: 1)" in result.output
    assert "[claude-code]" in result.output
    assert "[cursor]" in result.output


def _diff_bom(
    *, components: list[dict] | None = None, dependencies: list[dict] | None = None
) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "metadata": {"component": {"bom-ref": "openaca:target", "type": "application"}},
        "components": components or [],
        "dependencies": dependencies or [],
    }


def _diff_component(
    bom_ref: str,
    identity: str,
    component_type: str,
    *,
    version: str | None = None,
) -> dict:
    component = {
        "type": "application",
        "bom-ref": bom_ref,
        "name": identity,
        "properties": [
            {"name": "openaca:identity", "value": identity},
            {"name": "openaca:component_type", "value": component_type},
        ],
    }
    if version is not None:
        component["version"] = version
    return component
