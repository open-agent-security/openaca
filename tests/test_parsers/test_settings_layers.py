import json
from pathlib import Path

from tools.parsers.settings_layers import SettingsLayers, load


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_array_union_dedupes_across_scopes():
    layers = SettingsLayers(
        user={"permissions": {"allow": ["Bash(git:*)"]}},
        project={"permissions": {"allow": ["Bash(npm:*)", "Bash(git:*)"]}},
    )
    merged = layers.merged("repo")
    # First-seen order preserved; duplicates collapsed.
    assert merged["permissions"]["allow"] == ["Bash(git:*)", "Bash(npm:*)"]


def test_object_deep_merge_per_key():
    """Project should override user per-key, not replace the whole object."""
    layers = SettingsLayers(
        user={"enabledPlugins": {"foo": True, "bar": True}},
        project={"enabledPlugins": {"foo": False}},
    )
    merged = layers.merged("repo")
    assert merged["enabledPlugins"] == {"foo": False, "bar": True}


def test_scalar_override():
    layers = SettingsLayers(user={"theme": "dark"}, project={"theme": "light"})
    assert layers.merged("repo")["theme"] == "light"


def test_repo_mode_skips_local_scope():
    """settings.local.json is machine-local; CI/repo scans should ignore it."""
    layers = SettingsLayers(
        user={"theme": "dark"},
        local={"theme": "light"},
    )
    assert layers.merged("repo")["theme"] == "dark"
    assert layers.merged("fs")["theme"] == "light"


def test_local_overrides_project_in_fs_mode():
    layers = SettingsLayers(
        user={"theme": "dark"},
        project={"theme": "light"},
        local={"theme": "neon"},
    )
    assert layers.merged("fs")["theme"] == "neon"


def test_by_scope_preserves_provenance():
    """Hooks need scope-of-origin for identity; merging would lose it."""
    layers = SettingsLayers(
        user={"hooks": {"PreToolUse": [{"command": "user-hook"}]}},
        project={"hooks": {"PreToolUse": [{"command": "project-hook"}]}},
    )
    by_scope = layers.by_scope()
    assert by_scope["user"]["hooks"]["PreToolUse"][0]["command"] == "user-hook"
    assert by_scope["project"]["hooks"]["PreToolUse"][0]["command"] == "project-hook"
    assert by_scope["managed"] == {}
    assert by_scope["local"] == {}


def test_load_user_only(tmp_path):
    _write(tmp_path / "settings.json", {"theme": "dark"})
    layers = load(install_root=tmp_path)
    assert layers.user == {"theme": "dark"}
    assert layers.project is None
    assert layers.local is None


def test_load_user_plus_project(tmp_path):
    install_root = tmp_path / "install"
    project_root = tmp_path / "project"
    _write(install_root / "settings.json", {"theme": "dark"})
    _write(project_root / ".claude" / "settings.json", {"theme": "light"})
    _write(project_root / ".claude" / "settings.local.json", {"theme": "neon"})
    layers = load(install_root=install_root, project_root=project_root)
    assert layers.user == {"theme": "dark"}
    assert layers.project == {"theme": "light"}
    assert layers.local == {"theme": "neon"}
    assert layers.merged("repo")["theme"] == "light"
    assert layers.merged("fs")["theme"] == "neon"


def test_load_silently_skips_malformed_json(tmp_path):
    """One malformed file should not abort the whole resolver."""
    (tmp_path / "settings.json").write_text("{not json")
    layers = load(install_root=tmp_path)
    assert layers.user == {}


def test_load_silently_skips_non_object_user_settings(tmp_path):
    """Valid JSON that isn't a dict should not crash merged()."""
    (tmp_path / "settings.json").write_text("[1, 2, 3]")
    layers = load(install_root=tmp_path)
    assert layers.user == {}
    # Must not raise AttributeError in _deep_merge.
    assert layers.merged("fs") == {}


def test_load_silently_skips_non_object_project_settings(tmp_path):
    install_root = tmp_path / "install"
    install_root.mkdir()
    project_root = tmp_path / "project"
    project_dir = project_root / ".claude"
    project_dir.mkdir(parents=True)
    (project_dir / "settings.json").write_text("true")
    (project_dir / "settings.local.json").write_text("null")
    layers = load(install_root=install_root, project_root=project_root)
    assert layers.project is None
    assert layers.local is None
    # Must not raise when merging.
    assert layers.merged("fs") == {}
    assert layers.merged("repo") == {}


def test_empty_install_root_returns_empty_layers(tmp_path):
    layers = load(install_root=tmp_path)
    assert layers.user == {}
    assert layers.project is None
    assert layers.local is None
    assert layers.merged("fs") == {}
    assert layers.merged("repo") == {}


def test_load_silently_skips_unreadable_settings(tmp_path):
    """An unreadable settings file (e.g., a directory at the settings path,
    or a permission-locked file) must not abort load(); it should degrade
    to partial settings like other malformed cases."""
    # Use a directory at the user settings path — raises IsADirectoryError
    # (a concrete OSError subclass) on read_text(), portable across CIs.
    (tmp_path / "settings.json").mkdir()
    layers = load(install_root=tmp_path)
    assert layers.user == {}
    assert layers.merged("fs") == {}


def test_load_silently_skips_non_utf8_settings(tmp_path):
    """Non-UTF-8 bytes (UTF-16 BOM, corrupted file) must not abort load()."""
    (tmp_path / "settings.json").write_bytes(b'\xff\xfe{\x00"\x00t\x00')
    layers = load(install_root=tmp_path)
    assert layers.user == {}
    assert layers.merged("fs") == {}


def test_load_silently_skips_non_utf8_project_settings(tmp_path):
    install_root = tmp_path / "install"
    install_root.mkdir()
    project_root = tmp_path / "project"
    project_dir = project_root / ".claude"
    project_dir.mkdir(parents=True)
    (project_dir / "settings.json").write_bytes(b"\xff\xfe{\x00")
    (project_dir / "settings.local.json").write_bytes(b"\xff\xfe[\x00")
    layers = load(install_root=install_root, project_root=project_root)
    assert layers.project is None
    assert layers.local is None
    assert layers.merged("fs") == {}


def test_load_silently_skips_unreadable_project_settings(tmp_path):
    install_root = tmp_path / "install"
    install_root.mkdir()
    project_root = tmp_path / "project"
    project_dir = project_root / ".claude"
    project_dir.mkdir(parents=True)
    (project_dir / "settings.json").mkdir()
    (project_dir / "settings.local.json").mkdir()
    layers = load(install_root=install_root, project_root=project_root)
    assert layers.project is None
    assert layers.local is None
    assert layers.merged("fs") == {}


def test_merged_does_not_mutate_scope_provenance():
    """After a `merged()` call, `by_scope()` must still return the raw
    per-scope dicts — `_deep_merge` must not alias nested scope values
    into the merged result and mutate them across scopes."""
    layers = SettingsLayers(
        user={"enabledPlugins": {"a": True, "b": True}},
        project={"enabledPlugins": {"a": False}},
    )
    # Force a merge that would alias if the implementation is wrong.
    _ = layers.merged("repo")
    by_scope = layers.by_scope()
    # User scope must still have its original values; project's override
    # must not have leaked into user's dict.
    assert by_scope["user"]["enabledPlugins"] == {"a": True, "b": True}
    assert by_scope["project"]["enabledPlugins"] == {"a": False}


def test_merged_does_not_mutate_scope_arrays():
    """Same provenance guarantee for arrays — array union must not append
    later-scope items into the earlier scope's original list."""
    layers = SettingsLayers(
        user={"permissions": {"allow": ["Bash(git:*)"]}},
        project={"permissions": {"allow": ["Bash(npm:*)"]}},
    )
    _ = layers.merged("repo")
    by_scope = layers.by_scope()
    assert by_scope["user"]["permissions"]["allow"] == ["Bash(git:*)"]
    assert by_scope["project"]["permissions"]["allow"] == ["Bash(npm:*)"]


def test_array_dedupe_handles_dict_items():
    """Permissions / hooks may carry dict entries; first-seen order wins."""
    layers = SettingsLayers(
        user={"hooks": {"X": [{"cmd": "a"}, {"cmd": "b"}]}},
        project={"hooks": {"X": [{"cmd": "b"}, {"cmd": "c"}]}},
    )
    merged = layers.merged("repo")
    assert merged["hooks"]["X"] == [{"cmd": "a"}, {"cmd": "b"}, {"cmd": "c"}]
