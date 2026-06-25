from __future__ import annotations

import json

from tools.bom_diff import diff_boms


def test_diff_boms_reports_component_and_edge_changes():
    before = _bom(
        components=[
            _component("plugin/demo", "plugin/demo", "plugin", version="1.0.0"),
            _component("skill/deploy", "skill/deploy", "skill"),
            _component("pkg/npm/lodash", "package/npm/lodash", "package", version="4.17.20"),
            _component("mcp/old", "mcp-server/old", "mcp_server"),
        ],
        dependencies=[
            {"ref": "openaca:target", "dependsOn": ["plugin/demo", "mcp/old"]},
            {"ref": "plugin/demo", "dependsOn": ["skill/deploy"]},
            {"ref": "skill/deploy", "dependsOn": ["pkg/npm/lodash"]},
        ],
    )
    after = _bom(
        components=[
            _component("plugin/demo", "plugin/demo", "plugin", version="1.1.0"),
            _component("skill/deploy", "skill/deploy", "skill"),
            _component("pkg/npm/lodash", "package/npm/lodash", "package", version="4.17.20"),
            _component("mcp/new", "mcp-server/new", "mcp_server"),
        ],
        dependencies=[
            {"ref": "openaca:target", "dependsOn": ["plugin/demo", "mcp/new"]},
            {"ref": "plugin/demo", "dependsOn": ["skill/deploy", "pkg/npm/lodash"]},
        ],
    )

    result = diff_boms(before, after)

    assert [c.bom_ref for c in result.added_components] == ["mcp/new"]
    assert [c.bom_ref for c in result.removed_components] == ["mcp/old"]
    assert [(c.before.version, c.after.version) for c in result.changed_components] == [
        ("1.0.0", "1.1.0")
    ]
    assert result.added_edges == [
        ("openaca:target", "mcp/new"),
        ("plugin/demo", "pkg/npm/lodash"),
    ]
    assert result.removed_edges == [
        ("openaca:target", "mcp/old"),
        ("skill/deploy", "pkg/npm/lodash"),
    ]
    assert result.to_json() == {
        "added_components": [
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
            }
        ],
        "removed_components": [
            {
                "bom_ref": "mcp/old",
                "identity": "mcp-server/old",
                "component_type": "mcp_server",
                "name": "mcp-server/old",
                "version": None,
                "purl": None,
                "git_commit_sha": None,
                "artifact_coordinates": None,
                "url": None,
                "install_source": None,
                "git_ref": None,
            }
        ],
        "changed_components": [
            {
                "bom_ref": "plugin/demo",
                "identity": "plugin/demo",
                "component_type": "plugin",
                "name": "plugin/demo",
                "before": {
                    "version": "1.0.0",
                    "purl": None,
                    "git_commit_sha": None,
                    "artifact_coordinates": None,
                    "url": None,
                    "install_source": None,
                    "git_ref": None,
                },
                "after": {
                    "version": "1.1.0",
                    "purl": None,
                    "git_commit_sha": None,
                    "artifact_coordinates": None,
                    "url": None,
                    "install_source": None,
                    "git_ref": None,
                },
            }
        ],
        "added_edges": [
            {"parent": "openaca:target", "child": "mcp/new"},
            {"parent": "plugin/demo", "child": "pkg/npm/lodash"},
        ],
        "removed_edges": [
            {"parent": "openaca:target", "child": "mcp/old"},
            {"parent": "skill/deploy", "child": "pkg/npm/lodash"},
        ],
    }


def test_diff_boms_rejects_non_object_bom():
    try:
        diff_boms([], _bom())
    except ValueError as exc:
        assert "before BOM must be a JSON object" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def _bom(*, components: list[dict] | None = None, dependencies: list[dict] | None = None) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "metadata": {"component": {"bom-ref": "openaca:target", "type": "application"}},
        "components": components or [],
        "dependencies": dependencies or [],
    }


def _component(
    bom_ref: str,
    identity: str,
    component_type: str,
    *,
    version: str | None = None,
    purl: str | None = None,
    git_commit_sha: str | None = None,
    artifact_coordinates: str | None = None,
    url: str | None = None,
    install_source: str | None = None,
    git_ref: str | None = None,
) -> dict:
    component: dict = {
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
    if purl is not None:
        component["purl"] = purl
    if git_commit_sha is not None:
        component["properties"].append({"name": "openaca:git_commit_sha", "value": git_commit_sha})
    if artifact_coordinates is not None:
        component["properties"].append(
            {"name": "openaca:artifact_coordinates", "value": artifact_coordinates}
        )
    if url is not None:
        component["properties"].append({"name": "openaca:url", "value": url})
    if install_source is not None:
        component["properties"].append({"name": "openaca:install_source", "value": install_source})
    if git_ref is not None:
        component["properties"].append({"name": "openaca:git_ref", "value": git_ref})
    return component


def test_diff_boms_detects_artifact_coordinates_change():
    old_coords = json.dumps(
        [{"algorithm": "sha256", "kind": "skill-content-hash", "value": "sha256:aabbcc"}],
        sort_keys=True,
    )
    new_coords = json.dumps(
        [{"algorithm": "sha256", "kind": "skill-content-hash", "value": "sha256:ddeeff"}],
        sort_keys=True,
    )
    before = _bom(
        components=[
            _component("skill/helper", "skill/helper", "skill", artifact_coordinates=old_coords)
        ],
    )
    after = _bom(
        components=[
            _component("skill/helper", "skill/helper", "skill", artifact_coordinates=new_coords)
        ],
    )

    result = diff_boms(before, after)

    assert result.added_components == []
    assert result.removed_components == []
    assert len(result.changed_components) == 1
    changed = result.changed_components[0]
    assert changed.before.artifact_coordinates == old_coords
    assert changed.after.artifact_coordinates == new_coords
    assert changed.to_json()["before"]["artifact_coordinates"] == old_coords
    assert changed.to_json()["after"]["artifact_coordinates"] == new_coords


def test_diff_boms_detects_git_commit_sha_change():
    before = _bom(
        components=[
            _component(
                "plugin/local",
                "plugin/local",
                "plugin",
                git_commit_sha="aabbcc112233",
            )
        ],
    )
    after = _bom(
        components=[
            _component(
                "plugin/local",
                "plugin/local",
                "plugin",
                git_commit_sha="ddeeff445566",
            )
        ],
    )

    result = diff_boms(before, after)

    assert result.added_components == []
    assert result.removed_components == []
    assert len(result.changed_components) == 1
    changed = result.changed_components[0]
    assert changed.before.git_commit_sha == "aabbcc112233"
    assert changed.after.git_commit_sha == "ddeeff445566"
    assert changed.to_json()["before"]["git_commit_sha"] == "aabbcc112233"
    assert changed.to_json()["after"]["git_commit_sha"] == "ddeeff445566"


def test_diff_boms_detects_mcp_endpoint_change():
    before = _bom(
        components=[
            _component(
                "mcp/api",
                "mcp/api",
                "mcp_server",
                url="https://old-host.example.com/mcp",
                install_source="remote",
                git_ref="main",
            )
        ],
    )
    after = _bom(
        components=[
            _component(
                "mcp/api",
                "mcp/api",
                "mcp_server",
                url="https://new-host.example.com/mcp",
                install_source="remote",
                git_ref="main",
            )
        ],
    )

    result = diff_boms(before, after)

    assert result.added_components == []
    assert result.removed_components == []
    assert len(result.changed_components) == 1
    changed = result.changed_components[0]
    assert changed.before.url == "https://old-host.example.com/mcp"
    assert changed.after.url == "https://new-host.example.com/mcp"
    assert changed.to_json()["before"]["url"] == "https://old-host.example.com/mcp"
    assert changed.to_json()["after"]["url"] == "https://new-host.example.com/mcp"


def test_diff_boms_detects_mcp_install_source_change():
    before = _bom(
        components=[
            _component(
                "mcp/local",
                "mcp/local",
                "mcp_server",
                install_source="local",
                git_ref="v1.0.0",
            )
        ],
    )
    after = _bom(
        components=[
            _component(
                "mcp/local",
                "mcp/local",
                "mcp_server",
                install_source="local",
                git_ref="v2.0.0",
            )
        ],
    )

    result = diff_boms(before, after)

    assert result.added_components == []
    assert result.removed_components == []
    assert len(result.changed_components) == 1
    changed = result.changed_components[0]
    assert changed.before.git_ref == "v1.0.0"
    assert changed.after.git_ref == "v2.0.0"
    assert changed.to_json()["before"]["git_ref"] == "v1.0.0"
    assert changed.to_json()["after"]["git_ref"] == "v2.0.0"
