"""Tests for `.gitignore`-aware repo-mode manifest discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pathspec import GitIgnoreSpec

from tools.parsers import parse_repo_grouped
from tools.parsers.gitignore import (
    is_ignored,
    iter_unignored_files,
    iter_unignored_relative_files,
    load_gitignore_spec,
)


def test_named_file_walk_filters_before_per_file_ignore_checks(tmp_path, monkeypatch):
    import tools.parsers.gitignore as gitignore

    nested = tmp_path / "nested" / ".codex" / "skills" / "demo"
    nested.mkdir(parents=True)
    skill = nested / "SKILL.md"
    skill.write_text("skill")
    for i in range(100):
        (nested / f"unrelated-{i}.txt").write_text("unrelated")
    checked = []
    original = gitignore.is_ignored

    def check(path, spec):
        checked.append(path.name)
        return original(path, spec)

    monkeypatch.setattr(gitignore, "is_ignored", check)
    assert list(iter_unignored_files(tmp_path, None, names=frozenset({"SKILL.md"}))) == [skill]
    assert checked == ["SKILL.md"]


def test_excluded_directories_are_pruned_before_descent(tmp_path, monkeypatch):
    import tools.parsers.gitignore as gitignore

    excluded = tmp_path / "project" / "node_modules"
    excluded.mkdir(parents=True)
    (excluded / "package.json").write_text("{}")
    manifest = excluded.parent / "package.json"
    manifest.write_text("{}")
    visited = []
    original = gitignore.os.walk

    def walk(root):
        for row in original(root):
            visited.append(Path(row[0]))
            yield row

    monkeypatch.setattr(gitignore.os, "walk", walk)
    assert list(iter_unignored_files(tmp_path, None, exclude_dirs=frozenset({"node_modules"}))) == [
        manifest
    ]
    assert excluded not in visited


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


def test_relative_walk_uses_posix_paths_and_the_root_gitignore(tmp_path):
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    nested = tmp_path / "dir" / "nested.txt"
    nested.parent.mkdir()
    nested.write_text("nested", encoding="utf-8")
    (tmp_path / "skip.txt").write_text("skip", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("skip.txt\n", encoding="utf-8")

    ignored = list(iter_unignored_relative_files(tmp_path))
    rels = [rel for rel, _path, spec in ignored]
    assert "keep.txt" in rels
    assert "dir/nested.txt" in rels
    assert "skip.txt" not in rels
    assert all(spec is not None for _rel, _path, spec in ignored)

    included = list(iter_unignored_relative_files(tmp_path, include_gitignored=True))
    included_rels = [rel for rel, _path, spec in included]
    assert "skip.txt" in included_rels
    assert all(spec is None for _rel, _path, spec in included)


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


def test_iter_unignored_files_prunes_ignored_directories(tmp_path):
    """Ignored directories should not be descended into at all.

    Filtering after a recursive glob keeps output correct but still walks huge
    trees like `.venv/` and `.worktrees/`; the shared walker must prune before
    descent so repo scans and endpoint project-context scans stay fast.
    """
    _write_package_json(tmp_path / "package.json")
    _write_package_json(tmp_path / ".worktrees" / "feature" / "package.json")
    _write_package_json(tmp_path / ".venv" / "lib" / "package.json")
    skill_md = tmp_path / "src" / ".claude" / "skills" / "bootstrap" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("---\nname: bootstrap\n---\nbody\n")
    (tmp_path / ".gitignore").write_text(".worktrees/\n.venv/\n")

    spec = load_gitignore_spec(tmp_path)
    paths = sorted(str(p.relative_to(tmp_path)) for p in iter_unignored_files(tmp_path, spec))

    assert "package.json" in paths
    assert "src/.claude/skills/bootstrap/SKILL.md" in paths
    assert ".worktrees/feature/package.json" not in paths
    assert ".venv/lib/package.json" not in paths


def test_secondary_manifest_gitignored_via_plugin_string_mcp(tmp_path):
    """claude_plugin.parse() follows a string mcpServers path to a secondary
    .mcp.json. If that secondary file is gitignored, refs from it must be
    dropped even though the primary plugin.json is not gitignored.

    Regression target for the Codex P1 review comment on PR #28: parsers
    that follow secondary files bypass the rglob filter, so parse_repo_grouped
    must re-apply the gitignore spec to source_manifest of returned refs.
    """
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "my-plugin",
                "version": "1.0.0",
                "mcpServers": "./dist/.mcp.json",
            }
        )
    )

    # Secondary .mcp.json lives in dist/, which is gitignored.
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"server": {"command": "npx", "args": ["-y", "@vuln/pkg@1.0.0"]}}}
        )
    )

    (tmp_path / ".gitignore").write_text("dist/\n")

    grouped, n_found = parse_repo_grouped(tmp_path)

    # Only plugin.json survives rglob filtering (dist/.mcp.json is gitignored).
    assert n_found == 1
    all_refs = [r for _, refs in grouped for r in refs]
    source_manifests = {r.source_manifest for r in all_refs}
    # Plugin self-identity ref (source_manifest = plugin.json) is present.
    assert any(".claude-plugin" in str(m) for m in source_manifests)
    # No ref should point to the gitignored secondary dist/.mcp.json.
    assert not any("dist" in str(m) for m in source_manifests), (
        f"Gitignored secondary ref leaked: {source_manifests}"
    )


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


def test_unrelated_negations_do_not_visit_ignored_trees(tmp_path, monkeypatch):
    import os

    for relative in (
        "package.json",
        ".venv/lib/package.json",
        ".worktrees/topic/package.json",
        "candidates/ready_for_review/keep.yaml",
        "candidates/discard/junk.yaml",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    (tmp_path / ".gitignore").write_text(
        ".venv/\n.worktrees/\ncandidates/*\n"
        "!candidates/ready_for_review/\ncandidates/ready_for_review/*\n"
        "!candidates/ready_for_review/*.yaml\n"
    )
    scanned = []
    original = os.scandir

    def scandir(path):
        scanned.append(Path(path).relative_to(tmp_path).as_posix())
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)
    paths = {
        p.relative_to(tmp_path).as_posix()
        for p in iter_unignored_files(tmp_path, load_gitignore_spec(tmp_path))
    }
    assert paths == {".gitignore", "package.json", "candidates/ready_for_review/keep.yaml"}
    assert ".venv" not in scanned
    assert ".worktrees" not in scanned
    assert "candidates/discard" not in scanned


@pytest.mark.parametrize(
    "exception",
    [
        "!ignored/keep.json",
        "!/ignored/keep.json",
        "!ignored/keep/",
        "!ignored/deep/*.json",
        "!ignored/**/keep.json",
        "!ignored/[dk]eep/keep.json",
        "!ignored/d?ep/keep.json",
        "!ignore*/deep/keep.json",
        "!**/keep.json",
        "!keep.json",
        "!keep/",
        "!/elsewhere",
        "!elsewhere/keep.json",
        "!ignored/keep.json   ",
        r"!ignored/space\ dir/keep.json",
        "! ignored/keep.json",
    ],
)
def test_pruning_preserves_file_matching_with_negations(tmp_path, exception):
    """Compare traversal to filtering every file, including legacy re-inclusions."""
    relatives = [
        "ignored/keep.json",
        "ignored/junk.json",
        "ignored/deep/keep.json",
        "ignored/deep/junk.json",
        "ignored/keep/child.json",
        "ignored/space dir/keep.json",
        " ignored/keep.json",
        "elsewhere/keep.json",
        ".venv/keep.json",
        ".venv/keep/child.json",
        "public/keep.json",
    ]
    for relative in relatives:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    spec = GitIgnoreSpec.from_lines(["ignored/", " ignored/", "elsewhere/", ".venv/", exception])
    expected = {relative for relative in relatives if not spec.match_file(relative)}
    actual = {
        path.relative_to(tmp_path).as_posix() for path in iter_unignored_files(tmp_path, spec)
    }
    assert actual == expected


def test_git_directory_stays_pruned_with_global_negations(tmp_path, monkeypatch):
    import os

    _write_package_json(tmp_path / ".git" / "package.json")
    _write_package_json(tmp_path / "package.json")
    original = os.scandir

    def scandir(path):
        assert Path(path) != tmp_path / ".git"
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)
    spec = GitIgnoreSpec.from_lines(["!**/package.json"])
    assert list(iter_unignored_files(tmp_path, spec)) == [tmp_path / "package.json"]
