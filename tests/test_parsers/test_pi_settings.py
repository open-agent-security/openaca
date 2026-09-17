import json

from tools.parsers.pi_settings import expand_resources, resolve_resources


def write(path, value=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def test_project_wins():
    rows = resolve_resources({"packages": ["npm:a@1.0.0"]}, {"packages": ["npm:a@2.0.0"]})
    assert len(rows) == 1
    assert rows[0].source.ref == "2.0.0"


def test_delta_fills_unmentioned_global_files(tmp_path):
    root = tmp_path / "agent"
    install = root / "npm/node_modules/a"
    write(install / "skills/a/SKILL.md")
    write(install / "skills/b/SKILL.md")
    rows = resolve_resources(
        {"packages": ["npm:a@1.0.0"]},
        {"packages": [{"source": "npm:a", "autoload": False, "skills": ["!a"]}]},
        agent_root=root,
        project_root=tmp_path,
    )
    files = expand_resources(rows)
    assert {f.path.parent.name: f.enabled for f in files} == {"a": False, "b": True}
    assert rows[0].delta_base is not None
    assert rows[0].delta_base.source.ref == "1.0.0"


def test_version_is_independent_and_name_checked(tmp_path):
    install = tmp_path / "a"
    write(install / "package.json", json.dumps({"name": "a", "version": "3.1.0"}))
    (row,) = resolve_resources({"packages": ["npm:a"]}, install_root=tmp_path)
    assert not row.source.pinned
    assert row.installed_version == "3.1.0"
    write(install / "package.json", json.dumps({"name": "other", "version": "3.1.0"}))
    (row,) = resolve_resources({"packages": ["npm:a"]}, install_root=tmp_path)
    assert row.installed_version is None


def test_git_source_reads_installed_version_without_touching_ref(tmp_path):
    root = tmp_path / "agent"
    install = root / "git/github.com/acme/tool"
    write(install / "package.json", json.dumps({"name": "tool", "version": "2.3.1"}))
    (row,) = resolve_resources({"packages": ["git:github:acme/tool#v2"]}, agent_root=root)
    assert row.source.ref == "v2"
    assert row.installed_version == "2.3.1"


def test_git_source_without_manifest_version_leaves_installed_version_unset(tmp_path):
    root = tmp_path / "agent"
    install = root / "git/github.com/acme/tool"
    write(install / "package.json", json.dumps({"name": "tool"}))
    (row,) = resolve_resources({"packages": ["git:github:acme/tool"]}, agent_root=root)
    assert row.installed_version is None


def test_native_and_boundary(tmp_path):
    for path in ["extensions/a.ts", "skills/a/SKILL.md", "prompts/a.md", "themes/a.json"]:
        write(tmp_path / ".pi" / path)
    files = expand_resources([], project_root=tmp_path, allowed_root=tmp_path)
    assert {f.resource_type for f in files} == {"extensions", "skills", "prompts", "themes"}


def test_empty_delta_keeps_base(tmp_path):
    install = tmp_path / "a"
    write(install / "skills/a/SKILL.md")
    rows = resolve_resources(
        {"packages": ["npm:a"]},
        {"packages": [{"source": "npm:a", "autoload": False, "skills": []}]},
        install_root=tmp_path,
    )
    assert all(f.enabled for f in expand_resources(rows))


def test_omitted_and_empty_filters(tmp_path):
    write(tmp_path / "a/extensions/a.ts")
    (omitted,) = resolve_resources({"packages": ["npm:a"]}, install_root=tmp_path)
    (empty,) = resolve_resources(
        {"packages": [{"source": "npm:a", "extensions": []}]}, install_root=tmp_path
    )
    assert omitted.filters is None
    assert empty.filters == {"extensions": []}
    assert expand_resources([omitted])[0].enabled
    assert not expand_resources([empty])[0].enabled


def test_manifest_and_explicit_local_scope(tmp_path):
    write(tmp_path / ".pi/pkg/package.json", json.dumps({"pi": {"prompts": ["custom/*.md"]}}))
    wanted = write(tmp_path / ".pi/pkg/custom/a.md")
    write(tmp_path / ".pi/pkg/extensions/unused.ts")
    local = write(tmp_path / ".pi/other.md")
    rows = resolve_resources(
        {}, {"packages": ["./pkg"], "prompts": ["other.md"]}, project_root=tmp_path
    )
    assert {f.path for f in expand_resources(rows)} == {wanted, local}
    assert rows[1].source.kind == "local"


def test_boundary_blocks_symlink_and_retains_declaration(tmp_path):
    repo = tmp_path / "repo"
    outside = write(tmp_path / "outside/SKILL.md")
    (repo / ".pi/skills").mkdir(parents=True)
    (repo / ".pi/skills/linked").symlink_to(outside.parent)
    rows = resolve_resources({}, {"skills": [str(outside)]}, project_root=repo, allowed_root=repo)
    assert rows[0].gaps == ("outside allowed root or path could not be resolved",)
    assert expand_resources(rows, project_root=repo, allowed_root=repo) == []


def test_shared_skills_and_single_file_package(tmp_path):
    skill = write(tmp_path / "shared/a/SKILL.md")
    extension = write(tmp_path / "single.ts")
    rows = resolve_resources({"packages": [str(extension)]})
    files = expand_resources(rows, shared_skill_roots=(tmp_path / "shared",))
    assert {f.path for f in files} == {skill, extension}


def test_native_ignore_hidden_cycles_and_shallow_prompts(tmp_path):
    root = tmp_path / ".pi"
    wanted = write(root / "skills/a/SKILL.md")
    write(root / "skills/.hidden/SKILL.md")
    write(root / "skills/ignored/SKILL.md")
    write(root / "skills/.ignore", "ignored/\n")
    write(root / "prompts/nested/no.md")
    (root / "skills/loop").symlink_to(root / "skills")
    assert {f.path for f in expand_resources([], project_root=tmp_path)} == {wanted}


def test_force_patterns_have_exact_slash_semantics(tmp_path):
    write(tmp_path / "a/skills/one/SKILL.md")
    write(tmp_path / "a/skills/two/SKILL.md")
    rows = resolve_resources(
        {"packages": [{"source": "npm:a", "skills": ["!**", "+skills/one", "-skills/two"]}]},
        install_root=tmp_path,
    )
    assert {f.path.parent.name: f.enabled for f in expand_resources(rows)} == {
        "one": True,
        "two": False,
    }


def test_scoped_shared_roots_override_and_output_priority(tmp_path):
    native = write(tmp_path / ".pi/skills/project/SKILL.md")
    shared = write(tmp_path / "home/.agents/skills/global/SKILL.md")
    package = write(tmp_path / "pkg/skills/package/SKILL.md")
    rows = resolve_resources({"packages": [str(tmp_path / "pkg")]})
    files = expand_resources(
        rows,
        project_root=tmp_path,
        global_settings={"skills": ["!global"]},
        shared_skill_roots=((shared.parent.parent, "global"),),
    )
    assert [f.path for f in files] == [native, shared, package]
    assert files[1].scope == "global"
    assert not files[1].enabled


def test_alias_precedence_preserves_project_local_disabled_state(tmp_path):
    target = write(tmp_path / "pkg/skills/a/SKILL.md")
    alias = tmp_path / ".pi/alias.md"
    alias.parent.mkdir()
    alias.symlink_to(target)
    settings = {"skills": ["alias.md", "!alias.md"]}
    rows = resolve_resources({"packages": [str(tmp_path / "pkg")]}, settings, project_root=tmp_path)
    files = expand_resources(rows, project_root=tmp_path, project_settings=settings)
    assert len(files) == 1
    assert files[0].path == alias
    assert files[0].scope == "project"
    assert files[0].origin == "local"
    assert files[0].enabled is False


def test_manifest_star_does_not_match_nested_basename(tmp_path):
    write(tmp_path / "package.json", json.dumps({"pi": {"prompts": ["*.md"]}}))
    wanted = write(tmp_path / "root.md")
    write(tmp_path / "nested/extra.md")
    rows = resolve_resources({"packages": [str(tmp_path)]})
    assert [f.path for f in expand_resources(rows)] == [wanted]


def test_standalone_local_directory_is_extension(tmp_path):
    write(tmp_path / "ext/index.ts")
    rows = resolve_resources({"packages": [str(tmp_path / "ext")]})
    (file,) = expand_resources(rows)
    assert file.path == tmp_path / "ext"
    assert file.resource_type == "extensions"
    assert file.owner is rows[0]
    assert file.enabled


def test_nested_ignore_negation_reincludes_skill(tmp_path):
    root = tmp_path / ".pi/skills"
    wanted = write(root / "a/SKILL.md")
    write(root / ".gitignore", "*.md\n")
    write(root / "a/.gitignore", "!SKILL.md\n")
    assert [f.path for f in expand_resources([], project_root=tmp_path)] == [wanted]


def test_excluded_parent_is_not_reopened_by_child_ignore(tmp_path):
    root = tmp_path / ".pi/skills"
    write(root / "a/SKILL.md")
    write(root / ".gitignore", "a/\n")
    write(root / "a/.gitignore", "!SKILL.md\n")
    assert expand_resources([], project_root=tmp_path) == []


def test_exact_path_keeps_package_first_write_before_canonical_ranking(tmp_path):
    target = write(tmp_path / "pkg/skills/a/SKILL.md")
    settings = {"skills": [str(target), "!SKILL.md"]}
    rows = resolve_resources({"packages": [str(tmp_path / "pkg")]}, settings, project_root=tmp_path)
    (file,) = expand_resources(rows, project_settings=settings)
    assert file.path == target
    assert file.origin == "package"
    assert file.enabled


def test_absolute_manifest_glob_stays_anchored_and_within_boundary(tmp_path):
    package = tmp_path / "pkg"
    wanted = write(package / "root.md")
    write(package / "nested/extra.md")
    outside = write(tmp_path / "outside/leak.md")
    write(
        package / "package.json",
        json.dumps({"pi": {"prompts": [str(package / "*.md"), str(outside.parent / "*.md")]}}),
    )
    rows = resolve_resources({"packages": [str(package)]}, allowed_root=package)
    assert [f.path for f in expand_resources(rows, allowed_root=package)] == [wanted]
