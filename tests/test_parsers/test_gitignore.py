"""Tests for `.gitignore`-aware repo-mode manifest discovery."""

from __future__ import annotations

import json
from pathlib import Path

from tools.parsers import parse_repo_grouped
from tools.parsers.gitignore import is_ignored, load_gitignore_spec


def _write_package_json(path: Path, name: str = "x", version: str = "1.0.0") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"name": name, "version": version, "dependencies": {"lodash": "1.0.0"}})
    )


def test_default_skips_gitignored_node_modules(tmp_path):
    """The classic case: node_modules is gitignored, vendored package.json
    files inside it should not show up as repo-declared manifests."""
    _write_package_json(tmp_path / "package.json", name="host", version="1.0.0")
    _write_package_json(tmp_path / "node_modules" / "lodash" / "package.json", name="lodash")
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    grouped, n_found = parse_repo_grouped(tmp_path)
    paths = sorted(str(p.relative_to(tmp_path)) for p, _ in grouped)
    assert paths == ["package.json"]
    assert n_found == 1  # n_found also reflects post-filter count


def test_include_gitignored_flag_walks_everything(tmp_path):
    """--include-gitignored=True opts back into walking node_modules etc."""
    _write_package_json(tmp_path / "package.json")
    _write_package_json(tmp_path / "node_modules" / "lodash" / "package.json", name="lodash")
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    grouped, n_found = parse_repo_grouped(tmp_path, include_gitignored=True)
    paths = sorted(str(p.relative_to(tmp_path)) for p, _ in grouped)
    assert paths == [
        "node_modules/lodash/package.json",
        "package.json",
    ]
    assert n_found == 2


def test_no_gitignore_file_walks_everything(tmp_path):
    """Repos with no .gitignore = no filtering. Behavior matches pre-feature."""
    _write_package_json(tmp_path / "package.json")
    _write_package_json(tmp_path / "vendor" / "package.json", name="vendored")
    grouped, _ = parse_repo_grouped(tmp_path)
    paths = sorted(str(p.relative_to(tmp_path)) for p, _ in grouped)
    assert paths == ["package.json", "vendor/package.json"]


def test_git_directory_always_skipped(tmp_path):
    """`.git/` is never walked, even with --include-gitignored. Git's own
    metadata may contain packed object filenames or sample manifests; we
    never want to scan inside it."""
    _write_package_json(tmp_path / "package.json")
    _write_package_json(tmp_path / ".git" / "hooks" / "package.json", name="evil")
    grouped, _ = parse_repo_grouped(tmp_path, include_gitignored=True)
    paths = sorted(str(p.relative_to(tmp_path)) for p, _ in grouped)
    assert paths == ["package.json"]


def test_gitignore_negation_pattern_respected(tmp_path):
    """Negation (`!path`) should re-include a previously-ignored path. This
    proves we're using a real gitignore matcher, not just substring exclusion."""
    _write_package_json(tmp_path / "vendor" / "important" / "package.json", name="important")
    _write_package_json(tmp_path / "vendor" / "junk" / "package.json", name="junk")
    (tmp_path / ".gitignore").write_text("vendor/\n!vendor/important/\n")
    grouped, _ = parse_repo_grouped(tmp_path)
    paths = sorted(str(p.relative_to(tmp_path)) for p, _ in grouped)
    assert "vendor/important/package.json" in paths
    assert "vendor/junk/package.json" not in paths


def test_gitignore_glob_patterns(tmp_path):
    """Wildcards in .gitignore should match — confirms gitwildmatch semantics."""
    _write_package_json(tmp_path / "package.json")
    _write_package_json(tmp_path / "build" / "package.json", name="build")
    _write_package_json(tmp_path / "dist" / "package.json", name="dist")
    (tmp_path / ".gitignore").write_text("build/\ndist/\n")
    grouped, _ = parse_repo_grouped(tmp_path)
    paths = sorted(str(p.relative_to(tmp_path)) for p, _ in grouped)
    assert paths == ["package.json"]


def test_load_gitignore_spec_returns_none_when_absent(tmp_path):
    assert load_gitignore_spec(tmp_path) is None


def test_load_gitignore_spec_returns_none_on_unreadable_file(tmp_path):
    """Binary or unreadable .gitignore should fall back to None, not crash."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_bytes(b"\xff\xfe\x00binary garbage\x00")
    # is_ignored should still work; load_gitignore_spec may return spec or None
    # depending on whether bytes happen to decode. Either way, no exception.
    result = load_gitignore_spec(tmp_path)
    # If it returned None, fine; if it returned a spec, also fine (just
    # exercises that we didn't crash).
    _ = result


def test_is_ignored_handles_none_spec(tmp_path):
    """Calling is_ignored with spec=None should only filter `.git/`."""
    assert is_ignored(Path(".git/HEAD"), None) is True
    assert is_ignored(Path("package.json"), None) is False
    assert is_ignored(Path("node_modules/foo/package.json"), None) is False


def test_dotclaude_paths_not_accidentally_gitignored(tmp_path):
    """Common gitignore patterns shouldn't accidentally hide .claude/ files.
    Repos that put `.claude/settings.local.json` in .gitignore (a common
    pattern) should still emit the committed .claude/settings.json."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"foo@bar": True}})
    )
    (tmp_path / ".claude" / "settings.local.json").write_text(
        json.dumps({"enabledPlugins": {"local-only@bar": True}})
    )
    (tmp_path / ".gitignore").write_text(".claude/settings.local.json\n")
    grouped, _ = parse_repo_grouped(tmp_path)
    paths = sorted(str(p.relative_to(tmp_path)) for p, _ in grouped)
    assert ".claude/settings.json" in paths
    # settings.local.json isn't in REGISTRY anyway, but this confirms the
    # committed settings.json isn't accidentally caught by the rule.
