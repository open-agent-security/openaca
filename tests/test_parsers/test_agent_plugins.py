"""Tests for the Agent Plugins parser (agent-plugins.org/specification).

Covers `is_agent_plugins_manifest` (§5.2 version allowlist),
`validate_manifest` (§5.3/§5.5 fatal-vs-non-fatal split), and `parse`'s three
emissions: the plugin self-identity ref, skills (§7.1), and portable MCP
(§7.2.1/§7.2.2).
"""

import json
from pathlib import Path

import pytest

from tools.parsers.agent_plugins import (
    is_agent_plugins_manifest,
    parse,
    validate_manifest,
)

MANIFEST_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


def _write_manifest(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps(data))
    return path


def _write_skill(root: Path, rel_dir: str, name: str = "demo-skill") -> None:
    skill_dir = root / rel_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: a demo skill\n---\nbody\n"
    )


def _component_types(refs) -> list[str]:
    return [r.extra["component_type"] for r in refs]


# --- is_agent_plugins_manifest ---------------------------------------------


def test_is_agent_plugins_manifest_accepts_1_0_0(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    assert is_agent_plugins_manifest(path) is True


def test_is_agent_plugins_manifest_rejects_1_1_0_working_draft(tmp_path):
    path = _write_manifest(
        tmp_path,
        {"$schema": "https://agent-plugins.org/schemas/1.1.0/plugin.schema.json", "name": "demo"},
    )
    assert is_agent_plugins_manifest(path) is False


def test_is_agent_plugins_manifest_rejects_2_0_0(tmp_path):
    path = _write_manifest(
        tmp_path,
        {"$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json", "name": "demo"},
    )
    assert is_agent_plugins_manifest(path) is False


def test_is_agent_plugins_manifest_rejects_same_origin_non_schema_url(tmp_path):
    path = _write_manifest(
        tmp_path,
        {"$schema": "https://agent-plugins.org/schemas/1.0.0/", "name": "demo"},
    )
    assert is_agent_plugins_manifest(path) is False


def test_is_agent_plugins_manifest_rejects_non_dict_json(tmp_path):
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps(["not", "an", "object"]))
    assert is_agent_plugins_manifest(path) is False


def test_is_agent_plugins_manifest_rejects_malformed_json(tmp_path):
    path = tmp_path / "plugin.json"
    path.write_text("{not json")
    assert is_agent_plugins_manifest(path) is False


# --- validate_manifest: name rules (§5.5) -----------------------------------


def test_validate_manifest_accepts_valid_name():
    assert validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "demo-plugin.v1"}) is True


def test_validate_manifest_rejects_uppercase_name():
    assert validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "My-Plugin"}) is False


def test_validate_manifest_rejects_leading_hyphen():
    assert validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "-start"}) is False


def test_validate_manifest_rejects_consecutive_hyphens():
    assert validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "a--b"}) is False


def test_validate_manifest_rejects_65_char_name():
    assert validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "a" * 65}) is False


def test_validate_manifest_rejects_non_string_name():
    assert validate_manifest({"$schema": MANIFEST_SCHEMA, "name": 123}) is False


def test_validate_manifest_rejects_missing_name():
    assert validate_manifest({"$schema": MANIFEST_SCHEMA}) is False


# --- validate_manifest: fatal vs non-fatal split (§5.2) ---------------------


def test_validate_manifest_accepts_unknown_top_level_field():
    assert (
        validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "demo", "totally-unknown": 1})
        is True
    )


def test_validate_manifest_accepts_non_object_extensions():
    assert (
        validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "demo", "extensions": "nope"})
        is True
    )


def test_validate_manifest_rejects_wrong_typed_known_field():
    assert validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "demo", "version": 1}) is False


def test_validate_manifest_rejects_non_object_author():
    assert (
        validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "demo", "author": "nope"}) is False
    )


def test_validate_manifest_rejects_non_string_keyword():
    assert (
        validate_manifest({"$schema": MANIFEST_SCHEMA, "name": "demo", "keywords": ["ok", 1]})
        is False
    )


# --- parse: manifest-level rejection (fatal) --------------------------------


def test_parse_rejects_unsupported_schema_version(tmp_path):
    path = _write_manifest(
        tmp_path,
        {"$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json", "name": "demo"},
    )
    _write_skill(tmp_path, "skills/demo-skill")
    assert parse(path) == []


def test_parse_rejects_invalid_name_with_zero_skills_and_zero_servers(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "My-Plugin"})
    _write_skill(tmp_path, "skills/demo-skill")
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {"$schema": MCP_SCHEMA, "mcpServers": {"a": {"command": "npx", "args": ["x@1.0.0"]}}}
        )
    )
    assert parse(path) == []


def test_parse_strict_raises_on_fatal_validation_failure(tmp_path):
    """`_realize_agent_plugins_root` (tools/graph_build_cursor.py) calls with
    `strict=True` so `safe_parse` records this as a warning/evidence gap
    instead of a scan silently reporting a clean, empty composition for a
    plugin.json it already committed to treating as Agent Plugins."""
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "My-Plugin"})

    with pytest.raises(ValueError):
        parse(path, strict=True)


def test_parse_non_strict_default_unaffected_by_strict_failure(tmp_path):
    """The registry-driven route (`tools/parsers/__init__.py`) still calls
    with the `strict=False` default, since its guard runs against arbitrary
    `plugin.json` files that are usually not Agent Plugins manifests at
    all — a guard miss must stay silent, not raise."""
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "My-Plugin"})
    assert parse(path) == []


def test_parse_accepts_unknown_top_level_field_and_still_loads_skills(tmp_path):
    path = _write_manifest(
        tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo", "totally-unknown": True}
    )
    _write_skill(tmp_path, "skills/demo-skill")
    refs = parse(path)
    assert _component_types(refs) == ["plugin", "skill"]


def test_parse_accepts_non_object_extensions_and_still_loads_skills(tmp_path):
    path = _write_manifest(
        tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo", "extensions": "nope"}
    )
    _write_skill(tmp_path, "skills/demo-skill")
    refs = parse(path)
    assert _component_types(refs) == ["plugin", "skill"]


# --- parse: skills (§7.1) ---------------------------------------------------


def test_parse_finds_immediate_child_skill(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    _write_skill(tmp_path, "skills/demo-skill")
    refs = parse(path)
    assert [r.name for r in refs] == ["demo", "demo-skill"]


def test_parse_does_not_recurse_two_levels_deep(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    _write_skill(tmp_path, "skills/nested/too-deep")
    _write_skill(tmp_path, "skills/sibling", name="sibling")
    refs = parse(path)
    assert [r.name for r in refs] == ["demo", "sibling"]


def test_parse_ignores_bundled_commands(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "do-thing.md").write_text("# a command\n")
    refs = parse(path)
    assert _component_types(refs) == ["plugin"]


def test_parse_skill_symlink_escape_realizes_nothing(tmp_path):
    outside = tmp_path.parent / "outside-skill"
    outside.mkdir(exist_ok=True)
    (outside / "SKILL.md").write_text("---\nname: evil\ndescription: escapee\n---\n")

    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "escapee").symlink_to(outside, target_is_directory=True)

    refs = parse(path)
    assert _component_types(refs) == ["plugin"]


# --- parse: portable MCP (§7.2.1/§7.2.2) ------------------------------------


def test_parse_finds_valid_mcp_servers(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}},
            }
        )
    )
    refs = parse(path)
    assert _component_types(refs) == ["plugin", "mcp_server"]
    assert refs[0].component_identity == "plugin/demo"
    assert refs[1].name == "weather-mcp"


def test_parse_emits_plugin_self_ref_matching_claude_plugin_shape(tmp_path):
    path = _write_manifest(
        tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo", "version": "2.0.0"}
    )
    refs = parse(path)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.name == "demo"
    assert ref.version == "2.0.0"
    assert ref.component_identity == "plugin/demo"
    assert ref.source_manifest == str(path)
    assert ref.source_locator == "$"
    assert ref.extra == {"component_type": "plugin"}


def test_parse_fatal_manifest_rejection_emits_no_self_ref(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "My-Plugin"})
    assert parse(path) == []


def test_parse_missing_mcp_schema_disables_mcp_but_keeps_plugin_and_skills(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    _write_skill(tmp_path, "skills/demo-skill")
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    refs = parse(path)
    assert _component_types(refs) == ["plugin", "skill"]


def test_parse_wrong_mcp_schema_disables_mcp_but_keeps_plugin_and_skills(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    _write_skill(tmp_path, "skills/demo-skill")
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MANIFEST_SCHEMA,
                "mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}},
            }
        )
    )
    refs = parse(path)
    assert _component_types(refs) == ["plugin", "skill"]


def test_parse_mcp_version_mismatch_against_plugin_json_disables_mcp(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    _write_skill(tmp_path, "skills/demo-skill")
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.1.0/mcp.schema.json",
                "mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}},
            }
        )
    )
    refs = parse(path)
    assert _component_types(refs) == ["plugin", "skill"]


def test_parse_mcp_extra_top_level_field_disables_mcp(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    _write_skill(tmp_path, "skills/demo-skill")
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}},
                "extra": True,
            }
        )
    )
    refs = parse(path)
    assert _component_types(refs) == ["plugin", "skill"]


def test_parse_mcp_invalid_json_disables_mcp(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    _write_skill(tmp_path, "skills/demo-skill")
    (tmp_path / "mcp.json").write_text("{not json")
    refs = parse(path)
    assert _component_types(refs) == ["plugin", "skill"]


def test_parse_mcp_malformed_entry_drops_only_that_entry(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {
                    "good": {"command": "npx", "args": ["weather-mcp@1.0.0"]},
                    "bad": "not-an-object",
                },
            }
        )
    )
    refs = parse(path)
    server_refs = [r for r in refs if r.extra["component_type"] == "mcp_server"]
    assert [r.name for r in server_refs] == ["weather-mcp"]


def test_parse_mcp_symlink_escape_realizes_nothing(tmp_path):
    outside = tmp_path.parent / "outside-mcp"
    outside.write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}},
            }
        )
    )
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    (tmp_path / "mcp.json").symlink_to(outside)

    refs = parse(path)
    assert _component_types(refs) == ["plugin"]


def test_parse_empty_mcp_servers_object_is_valid(tmp_path):
    path = _write_manifest(tmp_path, {"$schema": MANIFEST_SCHEMA, "name": "demo"})
    (tmp_path / "mcp.json").write_text(json.dumps({"$schema": MCP_SCHEMA, "mcpServers": {}}))
    refs = parse(path)
    assert _component_types(refs) == ["plugin"]
