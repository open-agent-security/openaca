import json
from pathlib import Path

from click.testing import CliRunner

from tests.fixtures.agent_kinds import register_synthetic_kind
from tools.bom_cli import _write_new_temp_file
from tools.bom_cli import main as bom_main


def test_single_agent_stdout_is_one_json_line(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        bom_main, ["endpoint", "--kind", "claude-code", "--config-dir", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["metadata"]["component"]["bom-ref"] == "root/claude-code"


def test_many_agents_stream_as_ndjson(monkeypatch, tmp_path):
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer", "critic"])

    result = CliRunner().invoke(
        bom_main, ["endpoint", "--kind", "synthetic", "--config-dir", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    docs = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert [d["metadata"]["component"]["bom-ref"] for d in docs] == [
        "root/synthetic/researcher",
        "root/synthetic/writer",
        "root/synthetic/critic",
    ]
    props = {p["name"]: p["value"] for p in docs[0]["metadata"]["component"]["properties"]}
    assert props["openaca:agent_id"] == "researcher"


_MANIFEST_NAME = ".openaca-bom-manifest.json"


def test_output_dir_writes_one_file_per_agent(monkeypatch, tmp_path):
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer"])
    out = tmp_path / "boms"

    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == [
        _MANIFEST_NAME,
        "synthetic--researcher.cdx.json",
        "synthetic--writer.cdx.json",
    ]


def test_output_dir_manifest_write_does_not_follow_a_planted_symlink(monkeypatch, tmp_path):
    """A checked-out repository could contain a symlink at the well-known
    `.openaca-bom-manifest.json` name pointing outside `--output-dir`. Writing
    the ownership manifest must not follow that symlink and truncate its
    target — the manifest write has to replace the directory entry itself,
    the same way the emitted `*.cdx.json` documents already do via
    temp-file-then-replace."""
    out = tmp_path / "boms"
    out.mkdir()
    outside_target = tmp_path / "outside.txt"
    outside_target.write_text("do not touch", encoding="utf-8")
    (out / _MANIFEST_NAME).symlink_to(outside_target)
    register_synthetic_kind(monkeypatch, agent_ids=["researcher"])

    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert outside_target.read_text(encoding="utf-8") == "do not touch"
    manifest_path = out / _MANIFEST_NAME
    assert not manifest_path.is_symlink()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == [
        "synthetic--researcher.cdx.json"
    ]


def test_output_dir_temp_file_write_does_not_follow_a_planted_symlink(monkeypatch, tmp_path):
    """Before this fix, the write-then-replace temp files (the per-document
    `<name>.cdx.json.tmp` and `.openaca-bom-manifest.json.tmp`) had
    predictable names, so a symlink planted at one of those exact names was
    followed by `write_text` and its target truncated -- before the atomic
    `Path.replace` that made the *previous* symlink fix work ever ran.
    Exercise both temp files this command creates."""
    out = tmp_path / "boms"
    out.mkdir()
    outside_doc_target = tmp_path / "outside-doc.txt"
    outside_doc_target.write_text("do not touch (doc)", encoding="utf-8")
    outside_manifest_target = tmp_path / "outside-manifest.txt"
    outside_manifest_target.write_text("do not touch (manifest)", encoding="utf-8")
    (out / "synthetic--researcher.cdx.json.tmp").symlink_to(outside_doc_target)
    (out / f"{_MANIFEST_NAME}.tmp").symlink_to(outside_manifest_target)
    register_synthetic_kind(monkeypatch, agent_ids=["researcher"])

    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert outside_doc_target.read_text(encoding="utf-8") == "do not touch (doc)"
    assert outside_manifest_target.read_text(encoding="utf-8") == "do not touch (manifest)"
    doc_path = out / "synthetic--researcher.cdx.json"
    assert not doc_path.is_symlink()
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    assert doc["metadata"]["component"]["bom-ref"] == "root/synthetic/researcher"
    manifest_path = out / _MANIFEST_NAME
    assert not manifest_path.is_symlink()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == [
        "synthetic--researcher.cdx.json"
    ]


def test_output_dir_drops_a_stale_file_it_previously_wrote(monkeypatch, tmp_path):
    """A consumer reading `--output-dir` after a rerun must not see an agent
    that no longer resolves — the directory holds this run's `*.cdx.json`
    set, not every set this tool has ever written to it. A non-`.cdx.json`
    file the user placed there is left alone."""
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer"])
    CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )
    (out / "notes.txt").write_text("kept", encoding="utf-8")

    register_synthetic_kind(monkeypatch, agent_ids=["writer"])
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == [
        _MANIFEST_NAME,
        "notes.txt",
        "synthetic--writer.cdx.json",
    ]
    assert (out / "notes.txt").read_text(encoding="utf-8") == "kept"


def test_output_dir_leaves_a_foreign_cdx_json_file_alone(monkeypatch, tmp_path):
    """A `*.cdx.json` file this tool never wrote — hand-authored, produced by
    another tool, or left by a previous scan pointed at this directory by a
    different invocation — is not owned data and must survive a rerun even
    though its name matches the extension this tool emits."""
    out = tmp_path / "boms"
    out.mkdir()
    (out / "third-party.cdx.json").write_text("{}", encoding="utf-8")
    register_synthetic_kind(monkeypatch, agent_ids=["writer"])

    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "third-party.cdx.json").read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in out.iterdir()) == [
        _MANIFEST_NAME,
        "synthetic--writer.cdx.json",
        "third-party.cdx.json",
    ]


def test_output_dir_ignores_a_manifest_entry_that_traverses_out_of_the_directory(
    monkeypatch, tmp_path
):
    """A manifest entry like `../important.cdx.json` (or an absolute path) must
    never become a deletion candidate outside `--output-dir` — whether it got
    there from a hand-edited manifest or a planted one."""
    out = tmp_path / "boms"
    out.mkdir()
    sibling = tmp_path / "important.cdx.json"
    sibling.write_text("keep me", encoding="utf-8")
    (out / _MANIFEST_NAME).write_text(
        json.dumps(["../important.cdx.json", "/etc/passwd"]), encoding="utf-8"
    )
    register_synthetic_kind(monkeypatch, agent_ids=["writer"])

    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert sibling.read_text(encoding="utf-8") == "keep me"
    assert sorted(p.name for p in out.iterdir()) == [
        _MANIFEST_NAME,
        "synthetic--writer.cdx.json",
    ]


def test_output_dir_ignores_a_manifest_entry_that_is_not_a_bom_filename(monkeypatch, tmp_path):
    """A manifest entry naming a direct child of `--output-dir` that this
    emitter could never itself have produced (a non-`.cdx.json` name, or the
    manifest's own filename) must not become a deletion candidate — a
    hand-edited or planted manifest containing `["notes.txt"]` must not cause
    an unrelated `notes.txt` to be deleted on the next run."""
    out = tmp_path / "boms"
    out.mkdir()
    (out / "notes.txt").write_text("keep me", encoding="utf-8")
    (out / _MANIFEST_NAME).write_text(json.dumps(["notes.txt", _MANIFEST_NAME]), encoding="utf-8")
    register_synthetic_kind(monkeypatch, agent_ids=["writer"])

    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert sorted(p.name for p in out.iterdir()) == [
        _MANIFEST_NAME,
        "notes.txt",
        "synthetic--writer.cdx.json",
    ]


def test_output_dir_refuses_to_overwrite_an_unowned_name_collision(monkeypatch, tmp_path):
    """A foreign file whose name happens to match a basename this run would
    generate is not owned data and must not be silently overwritten."""
    out = tmp_path / "boms"
    out.mkdir()
    (out / "synthetic--writer.cdx.json").write_text("not ours", encoding="utf-8")
    register_synthetic_kind(monkeypatch, agent_ids=["writer"])

    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code != 0
    assert "refusing to overwrite" in result.output
    assert (out / "synthetic--writer.cdx.json").read_text(encoding="utf-8") == "not ours"


def test_output_dir_publish_failure_keeps_manifest_consistent_with_disk(monkeypatch, tmp_path):
    """A failure during the publish (replace) phase — after staging succeeded —
    must not leave a manifest that claims a file is current when it wasn't
    published, or that a stale file was removed when it wasn't touched."""
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer"])
    CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    register_synthetic_kind(monkeypatch, agent_ids=["a", "b"])
    real_replace = Path.replace
    calls = {"n": 0}

    def flaky_replace(self, target):
        if self.suffix == ".tmp":
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("permission denied")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code != 0
    manifest = json.loads((out / _MANIFEST_NAME).read_text(encoding="utf-8"))
    on_disk = {p.name for p in out.iterdir() if p.name != _MANIFEST_NAME}
    assert set(manifest) == on_disk
    assert not list(out.glob("*.tmp"))


def test_output_dir_publish_failure_with_unchanged_agent_set_keeps_old_file_owned(
    monkeypatch, tmp_path
):
    """A publish failure on a rerun that resolves the *same* agent set (so the
    failing name was already owned from the previous run) must not drop that
    untouched, still-current file from the recovery manifest — its old content
    is still on disk. Dropping it would make the next run see its own,
    previously-written file as an unowned collision and refuse to overwrite it
    forever."""
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer"])
    CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )
    writer_before = (out / "synthetic--writer.cdx.json").read_text(encoding="utf-8")

    real_replace = Path.replace
    calls = {"n": 0}

    def flaky_replace(self, target):
        if self.suffix == ".tmp":
            calls["n"] += 1
            if calls["n"] == 2:  # the second document staged is "writer"
                raise OSError("permission denied")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )
    assert result.exit_code != 0
    manifest = json.loads((out / _MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "synthetic--writer.cdx.json" in manifest
    assert (out / "synthetic--writer.cdx.json").read_text(encoding="utf-8") == writer_before

    monkeypatch.setattr(Path, "replace", real_replace)
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output


def test_output_dir_write_failure_preserves_the_prior_complete_set(monkeypatch, tmp_path):
    """If serializing the new set fails partway through, the previous run's
    complete set must still be on disk afterward — never a mix of old and
    new files, and never an empty directory."""
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer"])
    CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )
    before = sorted(p.name for p in out.iterdir())

    register_synthetic_kind(monkeypatch, agent_ids=["a", "b", "c"])
    real_write = _write_new_temp_file
    calls = {"n": 0}

    def flaky_write(directory, content):
        # A document's serialized content is a JSON object; the manifest's is
        # a JSON list of names (`json.dumps(names)`) — that leading character
        # is what distinguishes the two without depending on call order.
        if content.startswith("{"):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
        return real_write(directory, content)

    monkeypatch.setattr("tools.bom_cli._write_new_temp_file", flaky_write)
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code != 0
    assert sorted(p.name for p in out.iterdir()) == before
    assert not list(out.glob("*.tmp"))


def test_output_dir_normal_manifest_write_failure_is_reported(monkeypatch, tmp_path):
    """If every document publishes cleanly but the final manifest write itself
    fails, the CLI must report a `ClickException`, not let a raw `OSError`
    escape — the successfully written BOMs are real; only the ownership
    record failed to update."""
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["writer"])
    real_write = _write_new_temp_file

    def flaky_write(directory, content):
        # The manifest's serialized content is a JSON list of names
        # (`json.dumps(names)`); a document's is a JSON object.
        if content.startswith("["):
            raise OSError("disk full")
        return real_write(directory, content)

    monkeypatch.setattr("tools.bom_cli._write_new_temp_file", flaky_write)
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code != 0
    assert "manifest" in result.output
    assert (out / "synthetic--writer.cdx.json").exists()


def test_output_dir_publish_failure_reports_when_recovery_manifest_write_also_fails(
    monkeypatch, tmp_path
):
    """A publish failure followed by a failure to write the recovery manifest
    must still surface a `ClickException` describing both problems, not let
    the second `OSError` mask the first or escape unhandled."""
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer"])
    CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    register_synthetic_kind(monkeypatch, agent_ids=["a", "b"])
    real_replace = Path.replace
    replace_calls = {"n": 0}

    def flaky_replace(self, target):
        if self.suffix == ".tmp":
            replace_calls["n"] += 1
            if replace_calls["n"] == 2:
                raise OSError("permission denied")
        return real_replace(self, target)

    real_write = _write_new_temp_file

    def flaky_write(directory, content):
        # The manifest's serialized content is a JSON list of names
        # (`json.dumps(names)`); a document's is a JSON object.
        if content.startswith("["):
            raise OSError("disk full")
        return real_write(directory, content)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr("tools.bom_cli._write_new_temp_file", flaky_write)
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code != 0
    assert "failed to publish" in result.output
    assert "manifest" in result.output


def test_output_dir_stale_cleanup_failure_reports_when_recovery_manifest_write_also_fails(
    monkeypatch, tmp_path
):
    """A stale-file removal failure followed by a failure to write the recovery
    manifest must still surface a `ClickException` describing both problems."""
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer"])
    CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    register_synthetic_kind(monkeypatch, agent_ids=["writer"])
    real_unlink = Path.unlink

    def flaky_unlink(self, missing_ok=False):
        if self.name == "synthetic--researcher.cdx.json":
            raise OSError("permission denied")
        return real_unlink(self, missing_ok=missing_ok)

    real_write = _write_new_temp_file

    def flaky_write(directory, content):
        # The manifest's serialized content is a JSON list of names
        # (`json.dumps(names)`); a document's is a JSON object.
        if content.startswith("["):
            raise OSError("disk full")
        return real_write(directory, content)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    monkeypatch.setattr("tools.bom_cli._write_new_temp_file", flaky_write)
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code != 0
    assert "failed to remove stale" in result.output
    assert "manifest" in result.output


def test_output_errors_only_when_more_than_one_agent_resolves(monkeypatch, tmp_path):
    register_synthetic_kind(monkeypatch, agent_ids=["a", "b"])

    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output",
            str(tmp_path / "one.json"),
        ],
    )

    assert result.exit_code != 0
    assert "--output-dir" in result.output


def test_repo_with_no_declaration_emits_no_document(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["repo", "--target", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""


def test_repo_output_dir_clears_prior_boms_when_no_agent_resolves(monkeypatch, tmp_path):
    """A rerun that resolves zero agents must not leave a previous run's
    `--output-dir` contents behind for a consumer to misread as current."""
    target = tmp_path / "target"
    target.mkdir()
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher"])
    first = CliRunner().invoke(
        bom_main, ["repo", "--target", str(target), "--output-dir", str(out)]
    )
    assert first.exit_code == 0, first.output
    assert (out / "synthetic--researcher.cdx.json").exists()

    register_synthetic_kind(monkeypatch, agent_ids=[])
    second = CliRunner().invoke(
        bom_main, ["repo", "--target", str(target), "--output-dir", str(out)]
    )

    assert second.exit_code == 0, second.output
    assert sorted(p.name for p in out.iterdir()) == [_MANIFEST_NAME]


def test_endpoint_output_dir_clears_prior_boms_when_no_agent_resolves(monkeypatch, tmp_path):
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher"])
    first = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )
    assert first.exit_code == 0, first.output
    assert (out / "synthetic--researcher.cdx.json").exists()

    register_synthetic_kind(monkeypatch, agent_ids=[])
    second = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert second.exit_code == 0, second.output
    assert sorted(p.name for p in out.iterdir()) == [_MANIFEST_NAME]


def test_repo_output_clears_stale_file_when_no_agent_resolves(monkeypatch, tmp_path):
    """A rerun that resolves zero agents must not leave a previous `--output`
    file's document behind for a consumer to misread as current — the same
    contract `--output-dir` already enforces via its ownership manifest."""
    target = tmp_path / "target"
    target.mkdir()
    output_path = tmp_path / "openaca-bom.json"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher"])
    first = CliRunner().invoke(
        bom_main, ["repo", "--target", str(target), "--output", str(output_path)]
    )
    assert first.exit_code == 0, first.output
    assert output_path.exists()

    register_synthetic_kind(monkeypatch, agent_ids=[])
    second = CliRunner().invoke(
        bom_main, ["repo", "--target", str(target), "--output", str(output_path)]
    )

    assert second.exit_code == 0, second.output
    assert not output_path.exists()


def test_bom_repo_reads_the_agent_s_own_manifest_registry(tmp_path, monkeypatch):
    """`bom repo` must walk each agent's own `manifest_patterns`, not always the
    global registry — otherwise a repo declaring two different kinds counts one
    kind's manifests against the other's evidence gaps."""
    from dataclasses import replace

    import tools.agent_kinds as agent_kinds
    import tools.bom_cli
    from tools.parsers.mcp_json import parse as parse_mcp

    kind = register_synthetic_kind(monkeypatch, agent_ids=["a"])
    # `AgentKind` is frozen — build the surface-bearing kind directly rather
    # than mutating the fixture's instance, then re-register it.
    kind = replace(kind, manifest_patterns=((".mcp.json", parse_mcp),))
    monkeypatch.setattr(agent_kinds, "REGISTRY", (kind,))

    seen_registries = []
    real_counts = tools.bom_cli.parse_repo_registry_counts

    def spy(root, registries, include_gitignored=False, **kwargs):
        seen_registries.append(registries)
        return real_counts(root, registries, include_gitignored=include_gitignored, **kwargs)

    monkeypatch.setattr("tools.bom_cli.parse_repo_registry_counts", spy)

    result = CliRunner().invoke(bom_main, ["repo", "--target", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert seen_registries == [{kind.id: kind.manifest_patterns}]


def test_bom_repo_two_kinds_share_one_filesystem_walk(tmp_path, monkeypatch):
    """Task 9 Step 5 (bom_cli parity): two different kinds declared over the
    same `--target` must be counted from one filesystem walk of that root,
    not one walk per kind — each kind's own (n_found, n_failed) still comes
    out of that single walk unmixed with the other kind's."""
    import tools.agent_kinds as agent_kinds
    import tools.bom_cli
    from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext
    from tools.graph import Graph, Node
    from tools.parsers import HOST_AGNOSTIC_REGISTRY, ManifestPattern

    def make_kind(kind_id: str) -> AgentKind:
        def discover(ctx: DiscoveryContext) -> list[AgentInstance]:
            if ctx.scan_root is None:
                return []
            return [
                AgentInstance(
                    kind_id=kind_id,
                    display_name=kind_id,
                    source="declared",
                    root_label=kind_id,
                    coverage_baseline="complete",
                    scan_root=ctx.scan_root,
                )
            ]

        def compose(agent, *, include_gitignored=False, warnings=None) -> Graph:
            root = Node(key=agent.bom_ref, kind="target", ref=None)
            return Graph(nodes={root.key: root})

        own_pattern = ManifestPattern(f"{kind_id}-only.json", lambda _p: [])
        return AgentKind(
            id=kind_id,
            display_name=kind_id,
            cardinality="singleton",
            root_label=kind_id,
            coverage_baseline={"installed": "complete", "declared": "complete"},
            discover=discover,
            compose=compose,
            manifest_patterns=(*HOST_AGNOSTIC_REGISTRY, own_pattern),
        )

    monkeypatch.setattr(agent_kinds, "REGISTRY", (make_kind("kind-a"), make_kind("kind-b")))
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")

    walk_calls = []
    real_counts = tools.bom_cli.parse_repo_registry_counts

    def spy(root, registries, include_gitignored=False, **kwargs):
        walk_calls.append(registries)
        return real_counts(root, registries, include_gitignored=include_gitignored, **kwargs)

    monkeypatch.setattr("tools.bom_cli.parse_repo_registry_counts", spy)

    result = CliRunner().invoke(bom_main, ["repo", "--target", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert len(walk_calls) == 1
    assert set(walk_calls[0]) == {"kind-a", "kind-b"}

    docs = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert len(docs) == 2
    for doc in docs:
        props = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
        # 5 host-agnostic manifests + this kind's own — package.json matches
        # once (the walker's one-file-one-route rule), the kind's own pattern
        # matches nothing on disk.
        assert props["openaca:source_unit_count"] == "1"


def _agent_doc(kind: str, agent_id: str | None, component: str) -> dict:
    ref = f"root/{kind}" if agent_id is None else f"root/{kind}/{agent_id}"
    props = [
        {"name": "openaca:agent_kind", "value": kind},
        {"name": "openaca:composition_source", "value": "installed"},
        {"name": "openaca:composition_coverage", "value": "complete"},
    ]
    if agent_id is not None:
        props.insert(1, {"name": "openaca:agent_id", "value": agent_id})
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "properties": [{"name": "openaca:schema_version", "value": "0.5"}],
            "component": {"type": "application", "bom-ref": ref, "name": kind, "properties": props},
        },
        "components": [
            {
                "type": "application",
                "bom-ref": f"{kind}/x#y#mcp-server/{component}",
                "name": component,
                "properties": [{"name": "openaca:identity", "value": f"mcp-server/{component}"}],
            }
        ],
        "dependencies": [{"ref": ref, "dependsOn": [f"{kind}/x#y#mcp-server/{component}"]}],
    }


def _write_ndjson(path, docs):
    path.write_text("".join(json.dumps(d) + "\n" for d in docs), encoding="utf-8")


def test_bom_diff_reads_ndjson_and_pairs_on_the_agent_key(tmp_path):
    """`bom endpoint` emits NDJSON by default, so `bom endpoint > before.json`
    then `bom diff --before before.json` is the natural workflow. The diff
    primitive stays singular: the caller pairs on (kind, agent id) and diffs
    each pair."""
    before, after = tmp_path / "before.ndjson", tmp_path / "after.ndjson"
    _write_ndjson(
        before, [_agent_doc("synthetic", "a", "git"), _agent_doc("synthetic", "b", "keep")]
    )
    _write_ndjson(
        after, [_agent_doc("synthetic", "a", "git2"), _agent_doc("synthetic", "b", "keep")]
    )

    result = CliRunner().invoke(bom_main, ["diff", "--before", str(before), "--after", str(after)])

    assert result.exit_code == 0, result.output
    # Agent a changed; agent b did not.
    assert "synthetic/a" in result.output
    assert "git2" in result.output


def test_bom_diff_reports_an_unpaired_document_as_an_added_or_removed_agent(tmp_path):
    before, after = tmp_path / "before.ndjson", tmp_path / "after.ndjson"
    _write_ndjson(before, [_agent_doc("synthetic", "a", "git")])
    _write_ndjson(after, [_agent_doc("synthetic", "a", "git"), _agent_doc("synthetic", "b", "new")])

    result = CliRunner().invoke(bom_main, ["diff", "--before", str(before), "--after", str(after)])

    assert result.exit_code == 0, result.output
    assert "added agent" in result.output.lower()
    assert "synthetic/b" in result.output


def test_bom_diff_single_document_output_is_unchanged(tmp_path):
    """One document each side keeps today's exact output — no per-agent
    headings, so existing consumers are unaffected."""
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(_agent_doc("claude-code", None, "git")), encoding="utf-8")
    after.write_text(json.dumps(_agent_doc("claude-code", None, "git2")), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["diff", "--before", str(before), "--after", str(after)])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("BOM diff:")


def test_bom_diff_single_document_each_side_but_different_agents_reports_add_and_remove(tmp_path):
    """One document each side is not automatically "the same agent" — pair on
    identity first. Replacing `synthetic/a` with `synthetic/b` must report one
    agent removed and one added, not component churn between unrelated
    agents."""
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(_agent_doc("synthetic", "a", "git")), encoding="utf-8")
    after.write_text(json.dumps(_agent_doc("synthetic", "b", "git")), encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["diff", "--before", str(before), "--after", str(after)])

    assert result.exit_code == 0, result.output
    assert not result.output.startswith("BOM diff:")
    assert "removed agent synthetic/a" in result.output
    assert "added agent synthetic/b" in result.output


def test_bom_diff_accepts_an_empty_snapshot(tmp_path):
    """`bom endpoint`/`bom repo` write nothing to stdout when they resolve zero
    agents, so an empty file is a valid (empty) snapshot for `bom diff`, not a
    malformed input."""
    before, after = tmp_path / "before.ndjson", tmp_path / "after.ndjson"
    before.write_text("", encoding="utf-8")
    _write_ndjson(after, [_agent_doc("synthetic", "a", "git")])

    result = CliRunner().invoke(bom_main, ["diff", "--before", str(before), "--after", str(after)])

    assert result.exit_code == 0, result.output
    assert "added agent synthetic/a" in result.output


def test_output_dir_distrusts_a_manifest_in_a_sticky_shared_directory(tmp_path, monkeypatch):
    """A planted ownership manifest normally grants an attacker nothing: writing
    it requires write access to the directory, and on POSIX that is exactly the
    permission needed to unlink or replace the files it names — so they could
    destroy them directly.

    The exception is a sticky-bit directory shared with other users (`/tmp` and
    friends), where a non-owner may create their own files but may *not* unlink
    anyone else's. There a planted manifest does escalate: it gets the owner's
    own tool to destroy the owner's file. In that configuration the manifest is
    not treated as ownership proof, so nothing is deleted and a colliding name
    is refused rather than overwritten.
    """
    import os
    import stat

    out = tmp_path / "shared"
    out.mkdir()
    os.chmod(out, 0o1777)  # world-writable + sticky, like /tmp
    assert os.stat(out).st_mode & stat.S_ISVTX

    victim = out / "synthetic--victim.cdx.json"
    victim.write_text("important", encoding="utf-8")
    (out / ".openaca-bom-manifest.json").write_text(
        json.dumps(["synthetic--victim.cdx.json"]), encoding="utf-8"
    )

    register_synthetic_kind(monkeypatch, agent_ids=["writer"])
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert victim.read_text(encoding="utf-8") == "important"
    assert "synthetic--writer.cdx.json" in [p.name for p in out.iterdir()]


def test_output_dir_refuses_a_planted_collision_in_a_sticky_shared_directory(tmp_path, monkeypatch):
    """Same configuration, the overwrite half: a manifest naming the file we are
    about to write must not license overwriting it."""
    import os

    out = tmp_path / "shared"
    out.mkdir()
    os.chmod(out, 0o1777)

    target = out / "synthetic--writer.cdx.json"
    target.write_text("important", encoding="utf-8")
    (out / ".openaca-bom-manifest.json").write_text(
        json.dumps(["synthetic--writer.cdx.json"]), encoding="utf-8"
    )

    register_synthetic_kind(monkeypatch, agent_ids=["writer"])
    result = CliRunner().invoke(
        bom_main,
        [
            "endpoint",
            "--kind",
            "synthetic",
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(out),
        ],
    )

    assert result.exit_code != 0
    assert "not written by a previous run" in result.output
    assert target.read_text(encoding="utf-8") == "important"
