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
    assert rows[0].gaps == ("outside allowed root",)
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
