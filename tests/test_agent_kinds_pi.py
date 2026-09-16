"""Pi discovery and composition contracts."""

import json
from pathlib import Path

from tools.agent_kinds import DiscoveryContext, kind_for, pi


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)
    return path


def installed(home, monkeypatch, project=None):
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    return pi.KIND.compose(
        pi.discover(DiscoveryContext(source="installed", project_root=project))[0]
    )


def declared(root, **kwargs):
    return pi.KIND.compose(
        pi.discover(DiscoveryContext(source="declared", scan_root=root, **kwargs))[0], **kwargs
    )


def refs(graph, kind=None):
    return [n.ref for n in graph.nodes.values() if n.ref and (kind is None or n.kind == kind)]


def test_registered_and_two_packages(tmp_path, monkeypatch):
    root = tmp_path / ".pi/agent"
    put(root / "settings.json", {"packages": ["npm:one@latest", "npm:two@1.0.0"]})
    for name in ("one", "two"):
        put(
            root / f"npm/node_modules/{name}/package.json",
            {"name": name, "version": "2.0.0", "pi": {}},
        )
    graph = installed(tmp_path, monkeypatch)
    assert kind_for("pi") is pi.KIND
    assert len(refs(graph, "plugin")) == 2
    one = next(r for r in refs(graph) if r.name == "one")
    assert one.component_identity == "plugin/npm/one"
    assert one.version == "2.0.0"
    assert one.extra["install_source"] == "npm:one@latest"


def test_project_trust_gates_installed_only(tmp_path, monkeypatch):
    put(tmp_path / ".pi/agent/settings.json", {"defaultProjectTrust": "ask"})
    project = tmp_path / "repo"
    put(project / ".pi/extensions/test.ts", "export default () => {}")
    assert len(refs(declared(project), "extension")) == 1
    graph = installed(tmp_path, monkeypatch, project)
    assert not refs(graph, "extension")
    assert any("unresolved" in s for s in graph.warnings.gaps)
    put(tmp_path / ".pi/agent/trust.json", {str(project): True})
    assert len(refs(installed(tmp_path, monkeypatch, project), "extension")) == 1


def test_source_package_and_no_native_mcp(tmp_path):
    put(
        tmp_path / "package.json",
        {
            "name": "bundle",
            "version": "1.0.0",
            "pi": {"extensions": ["extensions/a.ts"], "themes": ["themes/a.json"]},
        },
    )
    put(tmp_path / "extensions/a.ts", "export default () => {}")
    put(tmp_path / "themes/a.json", {"name": "night"})
    put(tmp_path / "examples/.pi/extensions/fixture.ts", "fixture")
    put(tmp_path / ".mcp.json", {"mcpServers": {"phantom": {"command": "npx x"}}})
    graph = declared(tmp_path)
    assert sorted(n.kind for n in graph.nodes.values()) == [
        "extension",
        "plugin",
        "target",
        "theme",
    ]


def test_package_repository_keeps_its_own_project_surface(tmp_path):
    put(
        tmp_path / "package.json",
        {"name": "bundle", "version": "1.0.0", "pi": {"extensions": ["extensions/a.ts"]}},
    )
    put(tmp_path / "extensions/a.ts", "export default () => {}")
    put(tmp_path / ".pi/extensions/own.ts", "export default () => {}")
    put(tmp_path / "examples/.pi/extensions/fixture.ts", "fixture")
    graph = declared(tmp_path)
    extensions = {r.name: r for r in refs(graph, "extension")}
    assert set(extensions) == {"a", "own"}
    assert extensions["own"].extra["source_provenance"]["origin"] == "auto"
    assert len(refs(graph, "plugin")) == 1


def test_package_repository_keeps_its_own_shared_skills(tmp_path):
    put(
        tmp_path / "package.json",
        {"name": "bundle", "version": "1.0.0", "pi": {"extensions": ["extensions/a.ts"]}},
    )
    put(tmp_path / "extensions/a.ts", "export default () => {}")
    put(
        tmp_path / ".agents/skills/deploy/SKILL.md",
        "---\nname: deploy\ndescription: deploys stuff\n---\nbody",
    )
    put(
        tmp_path / "examples/.agents/skills/fixture/SKILL.md",
        "---\nname: fixture\ndescription: fixture\n---\nbody",
    )
    graph = declared(tmp_path)
    skills = {r.name: r for r in refs(graph, "skill")}
    assert set(skills) == {"deploy"}
    assert len(refs(graph, "plugin")) == 1


def test_package_repository_project_skill_shadows_package_skill(tmp_path):
    put(
        tmp_path / "package.json",
        {"name": "bundle", "version": "1.0.0", "pi": {"skills": ["skills/dup"]}},
    )
    put(
        tmp_path / "skills/dup/SKILL.md",
        "---\nname: dup\ndescription: package skill\n---\nbody",
    )
    put(
        tmp_path / ".pi/skills/dup/SKILL.md",
        "---\nname: dup\ndescription: project skill\n---\nbody",
    )
    graph = declared(tmp_path)
    dups = [r for r in refs(graph, "skill") if r.name == "dup"]
    assert len(dups) == 1
    assert dups[0].extra["source_provenance"]["origin"] == "auto"


def test_declared_containment_and_ignore(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    put(repo / ".pi/settings.json", {"extensions": ["../../outside.ts", "ignored.ts"]})
    put(tmp_path / "outside.ts", "outside")
    put(repo / ".pi/ignored.ts", "ignored")
    put(repo / ".gitignore", ".pi/ignored.ts\n")
    monkeypatch.setattr(Path, "home", lambda: (_ for _ in ()).throw(AssertionError("read home")))
    assert not refs(declared(repo), "extension")
    assert len(refs(declared(repo, include_gitignored=True), "extension")) == 1


def test_package_glob_outside_boundary_is_recorded_as_gap(tmp_path):
    put(
        tmp_path / "repo/package.json",
        {"name": "bundle", "version": "1.0.0", "pi": {"prompts": ["../../secrets/*.md"]}},
    )
    graph = declared(tmp_path / "repo")
    assert len(refs(graph, "plugin")) == 1
    assert not refs(graph, "command")
    assert any("resource outside allowed root" in g for g in graph.warnings.gaps)


def test_package_glob_through_symlink_outside_boundary_does_not_crash(tmp_path):
    repo = tmp_path / "repo"
    put(repo / "skills/real/x.md", "hello")
    outside_link = tmp_path / "outside_link"
    outside_link.symlink_to(repo / "skills/real", target_is_directory=True)
    put(
        repo / "package.json",
        {
            "name": "bundle",
            "version": "1.0.0",
            "pi": {"prompts": [f"{outside_link}/*.md"]},
        },
    )
    graph = declared(repo)
    assert len(refs(graph, "plugin")) == 1
    assert len(refs(graph, "command")) == 1


def test_missing_package_is_inventory_with_gap(tmp_path, monkeypatch):
    put(tmp_path / ".pi/agent/settings.json", {"packages": ["npm:missing@1.0.0"]})
    graph = installed(tmp_path, monkeypatch)
    row = refs(graph, "plugin")[0]
    assert row.extra["installed"] is False
    assert row.version is None
    assert graph.warnings.gaps


def test_unrelated_manifests_are_not_evidence(tmp_path):
    put(tmp_path / "package.json", {"name": "plain"})
    put(tmp_path / ".mcp.json", {})
    put(tmp_path / ".pi/npm/node_modules/package/package.json", {"pi": {}})
    assert pi.discover(DiscoveryContext(source="declared", scan_root=tmp_path)) == []


def test_delta_keeps_one_base_container_and_file_override_provenance(tmp_path, monkeypatch):
    root = tmp_path / ".pi/agent"
    project = tmp_path / "repo"
    put(
        root / "settings.json", {"packages": ["npm:bundle@latest"], "defaultProjectTrust": "always"}
    )
    put(
        root / "npm/node_modules/bundle/package.json",
        {"name": "bundle", "version": "2.0.0", "pi": {"extensions": ["a.ts", "b.ts"]}},
    )
    for name in ("a", "b"):
        put(root / f"npm/node_modules/bundle/{name}.ts", "export default () => {}")
    put(
        project / ".pi/settings.json",
        {"packages": [{"source": "npm:bundle@next", "autoload": False, "extensions": ["!a.ts"]}]},
    )
    graph = installed(tmp_path, monkeypatch, project)
    package = refs(graph, "plugin")
    assert len(package) == 1
    assert package[0].extra["install_source"] == "npm:bundle@latest"
    children = {ref.name: ref for ref in refs(graph, "extension")}
    assert children["a"].extra["enabled"] is False
    assert children["b"].extra["enabled"] is True
    assert children["a"].extra["source_provenance"]["configured_source"] == "npm:bundle@next"
    assert children["b"].component_identity == "extension/plugin/npm/bundle/b"
    assert all("install_source" not in r.extra for r in children.values())


def test_shared_skills_and_name_precedence(tmp_path, monkeypatch):
    root = tmp_path / ".pi/agent"
    project = tmp_path / "repo"
    put(root / "settings.json", {"defaultProjectTrust": "always"})
    put(project / ".git/HEAD", "ref: refs/heads/main")
    for path in (
        tmp_path / ".agents/skills/global/SKILL.md",
        project / ".agents/skills/project/SKILL.md",
    ):
        put(path, "---\nname: same\ndescription: something\n---\nbody")
    graph = installed(tmp_path, monkeypatch, project)
    assert len(refs(graph, "skill")) == 1
    assert refs(graph, "skill")[0].source_manifest.startswith(str(project))
    assert "project/" in next(n.key for n in graph.nodes.values() if n.kind == "skill")


def test_ancestor_shared_skill_normalizes_relative_to_repo_root(tmp_path, monkeypatch):
    root = tmp_path / ".pi/agent"
    repo = tmp_path / "repo"
    project = repo / "apps" / "web"
    put(root / "settings.json", {"defaultProjectTrust": "always"})
    put(repo / ".git/HEAD", "ref: refs/heads/main")
    put(
        repo / ".agents/skills/ancestor/SKILL.md",
        "---\nname: ancestor\ndescription: hi\n---\nbody",
    )
    graph = installed(tmp_path, monkeypatch, project)
    assert len(refs(graph, "skill")) == 1
    key = next(n.key for n in graph.nodes.values() if n.kind == "skill")
    assert not key.startswith(str(tmp_path))
    assert "repo/skills/ancestor" in key


def test_invalid_skill_is_retained_with_gap(tmp_path):
    put(tmp_path / ".pi/skills/broken/SKILL.md", "no frontmatter")
    graph = declared(tmp_path)
    assert len(refs(graph, "skill")) == 1
    assert refs(graph, "skill")[0].extra["enabled"] is False
    assert graph.warnings.gaps


def test_relocated_root_keeps_home_shared_skills(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "relocated"
    put(root / "settings.json", {})
    put(home / ".agents/skills/shared/SKILL.md", "---\nname: shared\ndescription: hello\n---\n")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(root))
    agent = pi.discover(DiscoveryContext(source="installed"))[0]
    assert agent.config_root == root
    assert pi.KIND.root_override_refusal
    graph = pi.KIND.compose(agent)
    assert len(refs(graph, "skill")) == 1
    assert "agents/skills/shared" in next(n.key for n in graph.nodes.values() if n.kind == "skill")


def test_malformed_settings_counts_parse_failure(tmp_path):
    from tools.parsers import parse_repo_registry_counts

    put(tmp_path / ".pi/settings.json", "[")
    counts, _ = parse_repo_registry_counts(tmp_path, {"pi": pi.KIND.manifest_patterns})
    assert counts["pi"] == (1, 1)
    assert declared(tmp_path).warnings.gaps


def test_extension_theme_bom_lint_and_roundtrip(tmp_path):
    from jsonschema import Draft202012Validator

    from tools.bom import build_agent_bom, component_refs_from_cyclonedx, graph_from_cyclonedx
    from tools.bom_lint import lint_bom, load_schema

    put(tmp_path / ".pi/extensions/a.ts", "export default () => {}")
    put(tmp_path / ".pi/themes/a.json", {"name": "night"})
    graph = declared(tmp_path)
    doc = build_agent_bom(
        [],
        graph=graph,
        agent_kind="pi",
        agent_name="Pi",
        composition_source="declared",
        composition_coverage="partial",
    ).to_cyclonedx()
    assert lint_bom(doc, Draft202012Validator(load_schema())) == []
    assert {r.extra["component_type"] for r in component_refs_from_cyclonedx(doc)} == {
        "extension",
        "theme",
    }
    graph_from_cyclonedx(doc).validate()


def test_ignored_package_manifest_does_not_hide_project_declaration(tmp_path):
    put(tmp_path / "package.json", {"pi": {}})
    put(tmp_path / ".gitignore", "package.json\n")
    put(tmp_path / ".pi/extensions/a.ts", "export default () => {}")
    assert len(refs(declared(tmp_path), "extension")) == 1


def test_unloaded_default_files_do_not_declare_pi(tmp_path):
    put(tmp_path / ".pi/extensions/README.md", "readme")
    put(tmp_path / ".agents/skills/README.txt", "readme")
    assert pi.declared_evidence(tmp_path) is None


def test_declared_does_not_read_external_ignore_file(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    put(root / ".pi/extensions/a.ts", "export default () => {}")
    outside = put(tmp_path / "outside-ignore", "*.ts")
    (root / ".gitignore").symlink_to(outside)
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert path.resolve() != outside, "read external ignore file"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    assert len(refs(declared(root), "extension")) == 1


def test_malformed_resource_entries_count_as_parse_failures(tmp_path):
    from tools.parsers import parse_repo_registry_counts

    put(tmp_path / ".pi/settings.json", {"extensions": [123]})
    counts, _ = parse_repo_registry_counts(tmp_path, {"pi": pi.KIND.manifest_patterns})
    assert counts["pi"] == (1, 1)


def test_skill_frontmatter_matches_pi_boundaries_and_normalization(tmp_path):
    samples = {
        "delimiter": (
            '---\nname: delimiter\ndescription: "Use --- to separate sections"\n---\nbody',
            True,
        ),
        "bom": ("\ufeff---\nname: bom\ndescription: Valid skill\n---\nbody", True),
        "unterminated": ("---\nname: unterminated\ndescription: No closing delimiter\n", False),
        "cr": ("---\rname: cr\rdescription: Valid skill\r---\rbody", True),
        "crlf": ("---\r\nname: crlf\r\ndescription: Valid skill\r\n---\r\nbody", True),
    }
    for name, (content, _) in samples.items():
        put(tmp_path / f".pi/skills/{name}/SKILL.md", content)
    graph = declared(tmp_path)
    assert {ref.name: ref.extra["enabled"] for ref in refs(graph, "skill")} == {
        name: enabled for name, (_, enabled) in samples.items()
    }
    assert len(graph.warnings.gaps) == 1
    assert "unterminated" in graph.warnings.gaps[0]
