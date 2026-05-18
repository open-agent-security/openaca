"""Tests for hooks_json parser (claude-hook ecosystem).

Two input shapes:
- Plugin format: `<plugin-root>/hooks/hooks.json` wrapping `{"description": "...", "hooks": {...}}`.
- Settings format: a `hooks` block inside settings.json (no wrapper).

Identity is payload-based: hook location details live in source metadata and
extra, not in component_identity.
"""

import json
from pathlib import Path

from tools.parsers.hooks_json import (
    parse_plugin_hooks,
    parse_plugin_hooks_inline,
    parse_settings_hooks,
)


def _write_hooks_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_parse_plugin_hooks_emits_one_ref_per_event_index(tmp_path):
    path = _write_hooks_json(
        tmp_path / "hooks" / "hooks.json",
        {
            "description": "Superpowers hooks",
            "hooks": {
                "PreToolUse": [
                    {"type": "command", "command": "echo pre"},
                    {"type": "command", "command": "echo pre2", "matcher": "Bash"},
                ],
                "PostToolUse": [
                    {"type": "command", "command": "echo post"},
                ],
            },
        },
    )
    refs = parse_plugin_hooks(
        path, plugin_name="superpowers", attributed_to="claude-plugin/superpowers@5.1.0"
    )
    assert len(refs) == 3
    identities = sorted(r.component_identity or "" for r in refs)
    assert len(set(identities)) == 3
    assert all(identity.startswith("claude-hook/command:") for identity in identities)
    # Every ref is attributed to the parent plugin.
    assert all(r.attributed_to == "claude-plugin/superpowers@5.1.0" for r in refs)
    # Type, command, and matcher land in extra (not identity).
    pre1 = next(r for r in refs if r.source_locator == "$.hooks.PreToolUse[1]")
    assert pre1.extra["event"] == "PreToolUse"
    assert pre1.extra["index"] == 1
    assert pre1.extra["type"] == "command"
    assert pre1.extra["command"] == "echo pre2"
    assert pre1.extra["matcher"] == "Bash"
    # source_locator carries the JSON path.
    assert pre1.source_locator == "$.hooks.PreToolUse[1]"
    assert pre1.ecosystem == "claude-hook"


def test_parse_plugin_hooks_handles_missing_matcher(tmp_path):
    """`matcher` is optional in hook entries; absent means it applies to everything."""
    path = _write_hooks_json(
        tmp_path / "hooks.json",
        {"hooks": {"PreToolUse": [{"type": "command", "command": "echo x"}]}},
    )
    refs = parse_plugin_hooks(path, plugin_name="p", attributed_to="claude-plugin/p@1.0")
    assert len(refs) == 1
    assert refs[0].extra.get("matcher") is None


def test_parse_plugin_hooks_skips_when_not_object(tmp_path):
    """If the top-level JSON isn't an object, return []."""
    path = tmp_path / "hooks.json"
    path.write_text("[]")
    assert parse_plugin_hooks(path, plugin_name="p", attributed_to="claude-plugin/p@1.0") == []


def test_parse_plugin_hooks_skips_when_hooks_key_missing(tmp_path):
    """Wrapper without a `hooks` key (or non-dict value) → empty."""
    path = _write_hooks_json(tmp_path / "hooks.json", {"description": "no hooks here"})
    assert parse_plugin_hooks(path, plugin_name="p", attributed_to="claude-plugin/p@1.0") == []


def test_parse_plugin_hooks_skips_malformed_json(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text("{not json")
    assert parse_plugin_hooks(path, plugin_name="p", attributed_to="claude-plugin/p@1.0") == []


def test_parse_plugin_hooks_skips_unreadable_file(tmp_path):
    """OS-level read failure (e.g. directory at the path) degrades to []."""
    bad = tmp_path / "hooks.json"
    bad.mkdir()
    assert parse_plugin_hooks(bad, plugin_name="p", attributed_to="claude-plugin/p@1.0") == []


def test_parse_plugin_hooks_skips_non_dict_entries(tmp_path):
    """If an entry in an event array isn't a dict, skip it but keep counting
    its slot in the index (so identity stays stable across entries)."""
    path = _write_hooks_json(
        tmp_path / "hooks.json",
        {
            "hooks": {
                "PreToolUse": [
                    "bad-entry",  # index 0, skipped
                    {"type": "command", "command": "echo x"},  # index 1, kept
                ]
            }
        },
    )
    refs = parse_plugin_hooks(path, plugin_name="p", attributed_to="claude-plugin/p@1.0")
    assert len(refs) == 1
    assert (refs[0].component_identity or "").startswith("claude-hook/command:")
    assert refs[0].source_locator == "$.hooks.PreToolUse[1]"


def test_parse_plugin_hooks_skips_non_list_event_value(tmp_path):
    """Event value must be a list; otherwise skip the event entirely."""
    path = _write_hooks_json(
        tmp_path / "hooks.json",
        {"hooks": {"PreToolUse": "not-a-list", "PostToolUse": [{"command": "x"}]}},
    )
    refs = parse_plugin_hooks(path, plugin_name="p", attributed_to="claude-plugin/p@1.0")
    assert len(refs) == 1
    assert (refs[0].component_identity or "").startswith("claude-hook/hook:")
    assert refs[0].source_locator == "$.hooks.PostToolUse[0]"


def test_parse_settings_hooks_keeps_scope_out_of_identity():
    """Settings scope is observation metadata, not identity."""
    settings_path = Path("/fake/settings.json")
    hooks_block = {
        "PreToolUse": [{"type": "command", "command": "echo a"}],
        "Stop": [{"type": "command", "command": "echo b"}],
    }
    refs = parse_settings_hooks(settings_path, hooks_block, scope="user")
    assert len(refs) == 2
    identities = sorted(r.component_identity or "" for r in refs)
    assert len(set(identities)) == 2
    assert all(identity.startswith("claude-hook/command:") for identity in identities)
    assert {r.extra["scope"] for r in refs} == {"user"}
    assert {r.extra["event"] for r in refs} == {"PreToolUse", "Stop"}
    # Direct hooks are NOT attributed to any plugin.
    assert all(r.attributed_to is None for r in refs)
    assert all(r.source_manifest == str(settings_path) for r in refs)


def test_parse_settings_hooks_per_scope_identity_is_logical():
    """Same hook payload at different settings scopes emits the same identity."""
    settings_path = Path("/fake/project/.claude/settings.json")
    hooks_block = {"PreToolUse": [{"type": "command", "command": "echo p"}]}
    user_refs = parse_settings_hooks(settings_path, hooks_block, scope="user")
    project_refs = parse_settings_hooks(settings_path, hooks_block, scope="project")
    local_refs = parse_settings_hooks(settings_path, hooks_block, scope="local")
    assert user_refs[0].component_identity == project_refs[0].component_identity
    assert project_refs[0].component_identity == local_refs[0].component_identity
    assert user_refs[0].extra["scope"] == "user"
    assert project_refs[0].extra["scope"] == "project"
    assert local_refs[0].extra["scope"] == "local"


def test_parse_settings_hooks_handles_non_dict_block():
    """Non-dict hooks block (e.g., None or list) → empty."""
    settings_path = Path("/fake/settings.json")
    assert parse_settings_hooks(settings_path, None, scope="user") == []  # type: ignore[arg-type]
    assert parse_settings_hooks(settings_path, [], scope="user") == []  # type: ignore[arg-type]


def test_parse_settings_hooks_empty_block_returns_empty():
    assert parse_settings_hooks(Path("/fake/s.json"), {}, scope="user") == []


def test_parse_plugin_hooks_empty_hooks_block_returns_empty(tmp_path):
    path = _write_hooks_json(tmp_path / "hooks.json", {"hooks": {}})
    assert parse_plugin_hooks(path, plugin_name="p", attributed_to="claude-plugin/p@1.0") == []


# parse_plugin_hooks_inline — inline hooks declared in plugin.json["hooks"]


def test_parse_plugin_hooks_inline_emits_refs_with_plugin_identity():
    """Inline plugin.json hooks use payload-based hook identities."""
    hooks_block = {
        "PostToolUse": [{"type": "command", "command": "echo post"}],
        "Stop": [{"type": "command", "command": "echo stop"}],
    }
    refs = parse_plugin_hooks_inline(
        hooks_block=hooks_block,
        plugin_name="superpowers",
        source_manifest="/fake/plugin.json",
        attributed_to="claude-plugin/superpowers@5.1.0",
    )
    assert len(refs) == 2
    identities = sorted(r.component_identity or "" for r in refs)
    assert len(set(identities)) == 2
    assert all(identity.startswith("claude-hook/command:") for identity in identities)
    assert all(r.attributed_to == "claude-plugin/superpowers@5.1.0" for r in refs)
    assert all(r.source_manifest == "/fake/plugin.json" for r in refs)
    assert all(r.ecosystem == "claude-hook" for r in refs)


def test_same_hook_payload_has_same_identity_across_locations(tmp_path):
    path = _write_hooks_json(
        tmp_path / "hooks.json",
        {"hooks": {"PreToolUse": [{"type": "command", "command": "echo same"}]}},
    )
    plugin_ref = parse_plugin_hooks(path, plugin_name="p", attributed_to="claude-plugin/p@1.0")[0]
    settings_ref = parse_settings_hooks(
        Path("/fake/settings.json"),
        {"PostToolUse": [{"type": "command", "command": "echo same"}]},
        scope="project",
    )[0]
    assert plugin_ref.component_identity == settings_ref.component_identity
    assert plugin_ref.extra["event"] == "PreToolUse"
    assert settings_ref.extra["event"] == "PostToolUse"


def test_parse_plugin_hooks_inline_source_locator_uses_hooks_jsonpath():
    """source_locator for inline hooks uses the same $.hooks.* path as hooks/hooks.json."""
    refs = parse_plugin_hooks_inline(
        hooks_block={"PreToolUse": [{"type": "command", "command": "x"}]},
        plugin_name="p",
        source_manifest="/fake/plugin.json",
        attributed_to="claude-plugin/p@1.0",
    )
    assert refs[0].source_locator == "$.hooks.PreToolUse[0]"


def test_parse_plugin_hooks_inline_returns_empty_for_non_dict():
    """Non-dict hooks_block (e.g., list or None) → empty."""
    assert (
        parse_plugin_hooks_inline(
            hooks_block=[],  # type: ignore[arg-type]
            plugin_name="p",
            source_manifest="/fake/plugin.json",
            attributed_to="claude-plugin/p@1.0",
        )
        == []
    )


def test_parse_plugin_hooks_inline_returns_empty_for_empty_block():
    assert (
        parse_plugin_hooks_inline(
            hooks_block={},
            plugin_name="p",
            source_manifest="/fake/plugin.json",
            attributed_to="claude-plugin/p@1.0",
        )
        == []
    )
