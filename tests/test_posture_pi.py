"""Pi posture operates on selected package refs and global trust defaults."""

import json
from pathlib import Path

import pytest

from tools.component_ref import ComponentRef
from tools.posture import run_posture_rules
from tools.posture.rules.project_trust import check_project_trust


@pytest.mark.parametrize(
    "source,mutable",
    [
        ("npm:pi-skills", True),
        ("npm:pi-skills@latest", True),
        ("npm:pi-skills@^1.2.3", True),
        ("npm:pi-skills@1.2.3", False),
        ("npm:pi-skills@v1.2.3", False),
        ("git:github.com/a/b@main", True),
        ("git:github.com/a/b@v1.2.3", True),
        ("git:github.com/a/b@" + "a" * 40, False),
        ("./local", False),
        ("pi-skills", False),
        ("@scope/pkg", False),
    ],
)
def test_selected_package_configuration_not_observed_version(source, mutable):
    ref = ComponentRef(
        name="pi-skills",
        version="1.2.3",
        ecosystem="npm",
        extra={"component_type": "plugin", "install_source": source, "bom_ref": "selected"},
    )
    findings = run_posture_rules([ref], [], agent_kind="pi")
    assert len(findings) == int(mutable)
    if mutable:
        assert findings[0].rule_id == "openaca-posture-mutable-install-reference"
        assert findings[0].bom_ref == "selected"
        assert findings[0].active_in == ["pi"]


def test_global_default_is_not_directory_trust():
    manifests = [(Path("/home/u/.pi/agent/settings.json"), {"default_project_trust": "always"})]
    (finding,) = check_project_trust(manifests, active_in=["pi"])
    assert "default" in finding.title.lower()
    assert finding.component["type"] == "agent"
    assert finding.active_in == ["pi"]
    assert "saved" in finding.remediation


@pytest.mark.parametrize("value", [None, "ask", "never", True, "unknown"])
def test_other_defaults_are_not_trust(value):
    assert check_project_trust([(Path("settings.json"), {"default_project_trust": value})]) == []


def test_codex_default_api_is_unchanged():
    manifests = [(Path("config.toml"), {"projects": {"/repo": "trusted"}})]
    assert check_project_trust(manifests) == check_project_trust(manifests, active_in=["codex"])
    assert check_project_trust(manifests)[0].title == "Directory is marked trusted"


def test_collector_reads_global_settings_only(tmp_path, monkeypatch):
    from tools.posture import collect_pi_project_trust_manifests

    root = tmp_path / "agent"
    root.mkdir()
    settings = root / "settings.json"
    settings.write_text(json.dumps({"defaultProjectTrust": "always"}))
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    manifests = collect_pi_project_trust_manifests(root, tmp_path / "project", [])
    assert reads == [settings]
    assert manifests == [(settings, {"default_project_trust": "always"})]
    findings = run_posture_rules(
        [],
        [],
        agent_kind="pi",
        extra_manifests={
            "openaca-posture-project-trust": manifests,
        },
    )
    assert findings[0].active_in == ["pi"]


@pytest.mark.parametrize("kind", [None, "codex", "claude", "cursor"])
def test_other_agent_source_classification_unchanged(kind):
    from tools.posture.rules.mutable_install import check_mutable_install

    ref = ComponentRef(
        name="pkg", extra={"component_type": "plugin", "install_source": "npm:pkg@1.2.3"}
    )
    baseline = check_mutable_install([ref])
    findings = check_mutable_install([ref], agent_kind=kind)
    assert [f.component for f in findings] == [f.component for f in baseline]


def test_pi_package_without_configured_source_does_not_infer_a_pin():
    ref = ComponentRef(name="pkg", extra={"component_type": "plugin", "gitCommitSha": None})
    assert run_posture_rules([ref], [], agent_kind="pi") == []


@pytest.mark.parametrize("contents", [None, "{", "[]", "{}", '{"defaultProjectTrust": false}'])
def test_missing_or_invalid_global_settings(tmp_path, contents):
    from tools.posture import collect_pi_project_trust_manifests

    if contents is not None:
        (tmp_path / "settings.json").write_text(contents)
    assert collect_pi_project_trust_manifests(tmp_path) == []
