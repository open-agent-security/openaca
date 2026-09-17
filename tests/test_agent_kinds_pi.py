"""Pi discovery and composition contracts."""

import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("settings", [False, True])
@pytest.mark.parametrize("package_repository", [False, True])
@pytest.mark.parametrize("surface", [".pi/extensions", ".pi/skills", ".agents/skills"])
def test_native_resource_manifests_do_not_declare_packages(
    tmp_path, settings, package_repository, surface
):
    from tools.parsers import parse_repo_registry_counts

    root = tmp_path / "repo"
    resource = root / surface / "native"
    put(resource / "package.json", {"name": "native", "pi": {"extensions": ["entry.ts"]}})
    put(resource / "entry.ts", "export default () => {}")
    skill = put(resource / "SKILL.md", "---\nname: native\ndescription: Native skill\n---\n")
    if settings:
        put(root / ".pi/settings.json", {})
    if package_repository:
        put(root / "package.json", {"name": "bundle", "pi": {}})
    graph = declared(root)
    counts, _ = parse_repo_registry_counts(root, {"pi": pi.KIND.manifest_patterns})
    assert counts["pi"] == (int(settings) + int(package_repository), 0)
    assert {ref.name for ref in refs(graph, "plugin")} == (
        {"bundle"} if package_repository else set()
    )
    kind = "extension" if surface.endswith("extensions") else "skill"
    nodes = [node for node in graph.nodes.values() if node.kind == kind]
    assert len(nodes) == 1
    assert graph.lineage(nodes[0]) == [nodes[0], graph.root]
    ref = nodes[0].ref
    assert ref is not None
    assert ref.extra["source_provenance"]["origin"] == "auto"
    if kind == "skill":
        assert ref.source_manifest == str(skill)
        assert not refs(graph, "extension")


def test_native_extension_manifest_can_be_the_only_project_evidence(tmp_path):
    entry = put(tmp_path / "src/entry.ts", "export default () => {}")
    put(
        tmp_path / ".pi/extensions/native/package.json",
        {"pi": {"extensions": ["../../../src/entry.ts"]}},
    )
    graph = declared(tmp_path)
    assert not refs(graph, "plugin")
    assert [Path(ref.source_manifest).resolve() for ref in refs(graph, "extension")] == [
        entry.resolve()
    ]


@pytest.mark.parametrize("source", ["declared", "installed"])
def test_explicit_native_extension_package_has_one_selected_owner(tmp_path, monkeypatch, source):
    root = tmp_path / "repo"
    put(root / ".pi/settings.json", {"packages": ["extensions/native"]})
    put(
        root / ".pi/extensions/native/package.json",
        {"name": "native", "pi": {"extensions": ["entry.ts"]}},
    )
    put(root / ".pi/extensions/native/entry.ts", "export default () => {}")
    put(tmp_path / ".pi/agent/settings.json", {"defaultProjectTrust": "always"})
    graph = declared(root) if source == "declared" else installed(tmp_path, monkeypatch, root)
    assert len(refs(graph, "plugin")) == 1
    (extension,) = [node for node in graph.nodes.values() if node.kind == "extension"]
    assert [node.kind for node in graph.lineage(extension)] == ["extension", "plugin", "target"]
    assert extension.ref is not None
    assert extension.ref.extra["source_provenance"]["origin"] == "package"


@pytest.mark.parametrize("with_repository", [False, True])
def test_all_ancestor_skill_occurrences_are_portable(tmp_path, monkeypatch, with_repository):
    from tools.bom import build_agent_bom

    documents = []
    for machine in ("first", "second"):
        home = tmp_path / machine
        repo = home / "checkout"
        project = repo / "apps/web"
        project.mkdir(parents=True)
        put(home / ".pi/agent/settings.json", {"defaultProjectTrust": "always"})
        if with_repository:
            put(repo / ".git/HEAD", "ref: refs/heads/main")
        for level, ancestor in enumerate((project, repo / "apps", repo)):
            put(
                ancestor / f".agents/skills/level-{level}/SKILL.md",
                f"---\nname: level-{level}\ndescription: Shared skill\n---\n",
            )
        graph = installed(home, monkeypatch, project)
        assert len(refs(graph, "skill")) == 3
        doc = build_agent_bom(
            [],
            graph=graph,
            agent_kind="pi",
            agent_name="Pi",
            composition_source="installed",
            composition_coverage="partial",
        ).to_cyclonedx()
        documents.append({component["bom-ref"] for component in doc["components"]})
        assert all(str(tmp_path) not in key for key in documents[-1])
    assert documents[0] == documents[1]


@pytest.mark.parametrize("package_project", [False, True])
@pytest.mark.parametrize("alias", [False, True])
def test_declared_local_package_selection_replaces_standalone_discovery(
    tmp_path, package_project, alias
):
    package = tmp_path / "pkg"
    put(
        package / "package.json",
        {
            "name": "bundle",
            "version": "1.2.3",
            "pi": {"extensions": ["entry.ts"], "skills": ["skills/shared"]},
        },
    )
    put(package / "entry.ts", "export default () => {}")
    put(package / "skills/shared/SKILL.md", "---\nname: shared\ndescription: Skill\n---\n")
    if package_project:
        put(package / ".pi/extensions/own.ts", "export default () => {}")
    source = "../pkg"
    if alias:
        (tmp_path / "alias").symlink_to(package, target_is_directory=True)
        source = "../alias"
    put(tmp_path / ".pi/settings.json", {"packages": [{"source": source, "skills": []}]})
    graph = declared(tmp_path)
    (plugin,) = refs(graph, "plugin")
    assert plugin.extra["install_source"] == source
    assert plugin.version == "1.2.3"
    assert len(refs(graph, "skill")) == 1
    assert refs(graph, "skill")[0].extra["enabled"] is False
    assert {ref.name for ref in refs(graph, "extension")} == (
        {"entry", "own"} if package_project else {"entry"}
    )


@pytest.mark.parametrize(
    "source", ["npm:bundle@latest", "git:github.com/org/bundle@v1", "./bundle"]
)
def test_every_installed_package_source_preserves_observed_version(tmp_path, monkeypatch, source):
    root = tmp_path / ".pi/agent"
    path = (
        "npm/node_modules/bundle"
        if source.startswith("npm:")
        else "git/github.com/org/bundle"
        if source.startswith("git:")
        else "bundle"
    )
    put(root / "settings.json", {"packages": [source]})
    put(root / path / "package.json", {"name": "bundle", "version": "1.2.3", "pi": {}})
    (package,) = refs(installed(tmp_path, monkeypatch), "plugin")
    assert package.version == "1.2.3"
    assert package.extra["install_source"] == source
    if source.startswith("./"):
        assert package.component_identity is None
        assert package.ecosystem is None


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
    assert "project-ancestor-2/agents/skills/ancestor" in key


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


@pytest.mark.parametrize("explicit_names", [False, True])
@pytest.mark.parametrize("surface", [".pi/skills", ".agents/skills/group"])
@pytest.mark.parametrize("source", ["declared", "installed"])
def test_direct_skill_names_follow_pi_parent_directory_fallback(
    tmp_path, monkeypatch, explicit_names, surface, source
):
    project = tmp_path / "repo"
    for name in ("build", "deploy"):
        frontmatter = f"name: {name}\n" if explicit_names else ""
        put(project / surface / f"{name}.md", f"---\n{frontmatter}description: Skill\n---\n")
    put(tmp_path / ".pi/agent/settings.json", {"defaultProjectTrust": "always"})
    graph = declared(project) if source == "declared" else installed(tmp_path, monkeypatch, project)
    skills = refs(graph, "skill")
    assert {ref.name for ref in skills} == (
        {"build", "deploy"} if explicit_names else {Path(surface).name}
    )
    assert len(skills) == (2 if explicit_names else 1)


@pytest.mark.parametrize("source", ["declared", "installed"])
@pytest.mark.parametrize("entry", ["native", "package", "shared"])
def test_cyclic_resource_paths_preserve_other_inventory(tmp_path, monkeypatch, source, entry):
    project = tmp_path / "repo"
    put(project / ".pi/extensions/valid.ts", "export default () => {}")
    put(tmp_path / ".pi/agent/settings.json", {"defaultProjectTrust": "always"})
    if entry == "native":
        loop = project / ".pi/extensions/loop"
    elif entry == "shared":
        loop = project / ".agents"
    else:
        loop = project / ".pi/loop"
        put(project / ".pi/settings.json", {"packages": ["loop"]})
    loop.symlink_to(loop.name, target_is_directory=True)
    graph = declared(project) if source == "declared" else installed(tmp_path, monkeypatch, project)
    assert [ref.name for ref in refs(graph, "extension")] == ["valid"]
    if entry == "package":
        assert len(refs(graph, "plugin")) == 1
        assert refs(graph, "plugin")[0].extra["installed"] is False
        assert graph.warnings.gaps


def test_cyclic_global_shared_root_preserves_installed_resources(tmp_path, monkeypatch):
    put(tmp_path / ".pi/agent/extensions/valid.ts", "export default () => {}")
    (tmp_path / ".agents").symlink_to(".agents", target_is_directory=True)
    graph = installed(tmp_path, monkeypatch)
    assert [ref.name for ref in refs(graph, "extension")] == ["valid"]
    assert graph.warnings.gaps


@pytest.mark.parametrize("via_package", [False, True])
@pytest.mark.parametrize(
    "resource_type,kind,filename,content",
    [
        ("extensions", "extension", "shared.ts", "export default () => {}"),
        ("skills", "skill", "shared.md", "---\nname: shared\ndescription: Shared\n---\n"),
        ("prompts", "command", "shared.md", "Shared prompt"),
        ("themes", "theme", "shared.json", {"name": "shared"}),
    ],
)
def test_shared_file_has_an_occurrence_for_each_selecting_project(
    tmp_path, via_package, resource_type, kind, filename, content
):
    from tools.bom import build_agent_bom, graph_from_cyclonedx

    keys = []
    for machine in ("one", "two"):
        root = tmp_path / machine
        put(root / "shared/package.json", {"pi": {resource_type: [f"{resource_type}/{filename}"]}})
        put(root / "shared" / resource_type / filename, content)
        for project in ("a", "b"):
            settings = (
                {"packages": ["../../shared"]}
                if via_package
                else {resource_type: [f"../../shared/{resource_type}/{filename}"]}
            )
            put(root / project / ".pi/settings.json", settings)
        graph = declared(root)
        resources = [node for node in graph.nodes.values() if node.kind == kind]
        # A standalone package is also independently declared in the direct-path case.
        assert len(resources) == (2 if via_package else 3)
        declarations = {
            node.ref.extra["source_provenance"].get("declaration")
            for node in resources
            if node.ref is not None
        }
        assert {"a/.pi/settings.json", "b/.pi/settings.json"} <= declarations
        for node in resources:
            assert node.ref is not None
            assert graph.node_for_ref(node.ref) == node
        if via_package:
            for plugin in (node for node in graph.nodes.values() if node.kind == "plugin"):
                assert len([edge for edge in graph.edges if edge.parent == plugin.key]) == 1
        doc = build_agent_bom(
            [],
            graph=graph,
            agent_kind="pi",
            agent_name="Pi",
            composition_source="declared",
            composition_coverage="partial",
        ).to_cyclonedx()
        replay = graph_from_cyclonedx(doc)
        replay.validate()
        for node in replay.nodes.values():
            if node.kind == kind:
                assert node.ref is not None
                assert replay.node_for_ref(node.ref) == node
        keys.append({node.key for node in resources})
        assert {node.key for node in replay.nodes.values() if node.kind == kind} == keys[-1]
    assert keys[0] == keys[1]
