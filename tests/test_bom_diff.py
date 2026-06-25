from __future__ import annotations

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
            }
        ],
        "changed_components": [
            {
                "bom_ref": "plugin/demo",
                "identity": "plugin/demo",
                "component_type": "plugin",
                "name": "plugin/demo",
                "before": {"version": "1.0.0", "purl": None},
                "after": {"version": "1.1.0", "purl": None},
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
    if purl is not None:
        component["purl"] = purl
    return component
