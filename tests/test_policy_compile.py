"""Unit tests for `tools/policy_compile.py` — the compilation, below the CLI.

Every expected input and evaluation failure is a `PolicyValidationError` or a
`PolicyEvaluationError`, never a `click` exception: this module sits below the
command layer and must not raise the command layer's type. The one failure the
two do not cover is the artifact write, which is documented as part of the
contract rather than converted.

Each branch is tested twice — here for the domain type and the message, and in
`tests/test_policy.py` for the stderr line and exit code `openaca policy
compile` still produces. The CLI cases were captured before the move.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click
import pytest

from tools.policy import PolicyEvaluationError, PolicyValidationError, loads
from tools.policy_compile import compile_endpoint_policy, render_policy_report

_ADMIT_ALL = """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
  skills:
    default: allowed
"""

_BLOCK_MCPS = """\
version: 1
admission:
  mcps:
    default: blocked
  plugins:
    default: allowed
  skills:
    default: allowed
"""

_VULN_GATE = (
    _ADMIT_ALL
    + """\
risk_gates:
  vulnerabilities:
    severity_at_least: high
"""
)


def _endpoint(root: Path) -> Path:
    (root / "settings.json").write_text("{}", encoding="utf-8")
    return root


def _skill(root: Path, name: str) -> None:
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


def _compile(target: Path, body: str, **kwargs):
    defaults: dict = {
        "target": target,
        "project": None,
        "output": None,
        "managed_settings_dir": target / "managed",
        "dry_run": True,
    }
    defaults.update(kwargs)
    return compile_endpoint_policy(loads(body), **defaults)


def test_no_module_level_click_dependency():
    """After the move this module imports no `click` at all — the structural
    form of "a module below the command layer must not raise the command
    layer's exception type"."""
    import tools.policy_compile

    assert not hasattr(tools.policy_compile, "click")
    source = Path(tools.policy_compile.__file__).read_text(encoding="utf-8")
    assert "import click" not in source


def test_output_required_unless_dry_run_is_a_validation_error(tmp_path):
    with pytest.raises(PolicyValidationError) as exc:
        _compile(_endpoint(tmp_path), _ADMIT_ALL, dry_run=False)

    assert str(exc.value) == "--output is required unless --dry-run is set"
    assert not isinstance(exc.value, click.ClickException)


def test_no_installed_agent_at_the_target_is_an_evaluation_error(tmp_path):
    target = tmp_path / "nowhere"

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _ADMIT_ALL, managed_settings_dir=tmp_path / "managed")

    assert str(exc.value) == f"no installed agent found at {target}"
    assert not isinstance(exc.value, click.ClickException)


def test_an_incomplete_inventory_is_an_evaluation_error(tmp_path):
    """Graph warnings mean `components` is an incomplete endpoint inventory:
    admission and risk gates would be evaluated as if the missing component
    did not exist, silently implying a complete artifact."""
    target = _endpoint(tmp_path)
    (target / ".mcp.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _ADMIT_ALL)

    assert ".mcp.json" in str(exc.value)
    assert "could not parse" in str(exc.value)


def test_a_non_queryable_component_under_a_vulnerability_gate_is_an_evaluation_error(tmp_path):
    target = _endpoint(tmp_path)
    _skill(target, "deploy")

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _VULN_GATE)

    assert str(exc.value) == (
        "vulnerability gates cannot evaluate non-queryable component(s): deploy"
    )


def test_the_non_queryable_message_truncates_past_three(tmp_path):
    target = _endpoint(tmp_path)
    for name in ("a", "b", "c", "d"):
        _skill(target, name)

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _VULN_GATE)

    assert str(exc.value) == (
        "vulnerability gates cannot evaluate non-queryable component(s): a, b, c, ..."
    )


def test_an_osv_load_warning_is_an_evaluation_error(tmp_path, monkeypatch):
    target = _endpoint(tmp_path)
    (target / ".mcp.json").write_text(
        '{"mcpServers":{"gh":{"command":"npx","args":["-y","@x/gh@1.0.0"]}}}', encoding="utf-8"
    )
    monkeypatch.setattr(
        "tools.policy_compile._load_osv_with_overlays",
        lambda refs: ([], ["osv.dev federation failed: boom"], 0, {}),
    )

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _VULN_GATE)

    assert str(exc.value) == "osv.dev federation failed: boom"


# --- `_managed_key_collisions`' seven distinct failures, in source order ------


def test_managed_settings_key_collision_is_an_evaluation_error(tmp_path):
    target = _endpoint(tmp_path)
    dropins = target / "managed" / "managed-settings.d"
    dropins.mkdir(parents=True)
    (dropins / "10-security.json").write_text(
        '{"allowManagedMcpServersOnly": true}', encoding="utf-8"
    )

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _BLOCK_MCPS)

    assert str(exc.value) == (
        "managed settings key collision: allowManagedMcpServersOnly in "
        f"{dropins / '10-security.json'}"
    )


def test_branch_1_the_directory_stat_failing(tmp_path, monkeypatch):
    """An `OSError` that is not `FileNotFoundError`. A missing path is the
    early return, not a failure, so this has to be induced."""
    target = _endpoint(tmp_path)
    managed = target / "managed"
    managed.mkdir()
    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self == managed:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _ADMIT_ALL)

    assert str(exc.value) == (
        f"cannot read managed settings directory {managed}: [Errno 13] Permission denied"
    )


def test_branch_2_the_directory_path_is_not_a_directory(tmp_path):
    """Unreachable from `openaca policy compile`: click's
    `Path(file_okay=False)` rejects a file with its own usage error and exit
    code 2 before the compilation runs, so this branch has a direct test only.
    It stays reachable for a programmatic caller, which is why it stays."""
    target = _endpoint(tmp_path)
    managed = target / "managed"
    managed.write_text("not a directory", encoding="utf-8")

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _ADMIT_ALL)

    assert str(exc.value) == f"managed settings path is not a directory: {managed}"


def test_branch_3_the_dropin_stat_failing(tmp_path, monkeypatch):
    target = _endpoint(tmp_path)
    dropins = target / "managed" / "managed-settings.d"
    dropins.mkdir(parents=True)
    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self == dropins:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _ADMIT_ALL)

    assert str(exc.value) == (
        f"cannot read managed settings drop-in path {dropins}: [Errno 13] Permission denied"
    )


def test_branch_4_the_dropin_path_is_not_a_directory(tmp_path):
    target = _endpoint(tmp_path)
    managed = target / "managed"
    managed.mkdir()
    dropins = managed / "managed-settings.d"
    dropins.write_text("not a directory", encoding="utf-8")

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _ADMIT_ALL)

    assert str(exc.value) == f"managed settings drop-in path is not a directory: {dropins}"


def test_branch_5_the_dropin_glob_failing(tmp_path, monkeypatch):
    target = _endpoint(tmp_path)
    dropins = target / "managed" / "managed-settings.d"
    dropins.mkdir(parents=True)
    real_glob = Path.glob

    def flaky_glob(self, pattern, *args, **kwargs):
        if self == dropins:
            raise PermissionError(13, "Permission denied")
        return real_glob(self, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", flaky_glob)

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _ADMIT_ALL)

    assert str(exc.value) == (
        f"cannot read managed settings drop-in path {dropins}: [Errno 13] Permission denied"
    )


def test_branch_6_a_settings_file_that_cannot_be_parsed(tmp_path):
    target = _endpoint(tmp_path)
    managed = target / "managed"
    managed.mkdir()
    settings = managed / "managed-settings.json"
    settings.write_text("{not json", encoding="utf-8")

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _ADMIT_ALL)

    assert str(exc.value).startswith(f"cannot read managed settings file {settings}: ")


def test_branch_7_a_settings_file_whose_json_is_not_an_object(tmp_path):
    target = _endpoint(tmp_path)
    managed = target / "managed"
    managed.mkdir()
    settings = managed / "managed-settings.json"
    settings.write_text("[]", encoding="utf-8")

    with pytest.raises(PolicyEvaluationError) as exc:
        _compile(target, _ADMIT_ALL)

    assert str(exc.value) == f"managed settings file {settings} must contain a JSON object"


# --- the artifact write, which is not a domain error -------------------------


def test_an_unwritable_output_directory_raises_oserror_not_a_policy_error(tmp_path):
    """Deliberately untranslated: today the `OSError` escapes `openaca policy
    compile` uncaught, so wrapping it in a domain error the command catches
    would turn a traceback into `Error: ...` with exit code 1."""
    target = _endpoint(tmp_path)
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    os.chmod(readonly, 0o500)
    try:
        with pytest.raises(OSError) as exc:
            _compile(
                target,
                _BLOCK_MCPS,
                output=readonly / "artifact.json",
                dry_run=False,
            )
    finally:
        os.chmod(readonly, 0o700)

    assert not isinstance(exc.value, PolicyValidationError)
    assert not isinstance(exc.value, PolicyEvaluationError)


def test_a_successful_compile_writes_the_artifact_and_reports_its_digest(tmp_path):
    import hashlib

    target = _endpoint(tmp_path)
    output = tmp_path / "artifact.json"

    report = _compile(target, _BLOCK_MCPS, output=output, dry_run=False)

    assert report["artifact"]["written"] is True
    assert report["artifact"]["output"] == str(output)
    assert report["artifact"]["digest"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert json.loads(output.read_text(encoding="utf-8"))["allowManagedMcpServersOnly"] is True


# --- the extracted renderer --------------------------------------------------

_REPORT = {
    "expected_policy": {"allowManagedMcpServersOnly": True},
    "decisions": [
        {"result": "blocked", "component": "mcp-server/npm/x", "reasons": ["mcps default: blocked"]}
    ],
    "limitations": ["skill/y: posture openaca-posture-x is not enforceable"],
}


def test_render_policy_report_text_is_what_the_command_printed():
    """Newline for newline, including the blank line before `Components:`."""
    assert render_policy_report(_REPORT, "text") == (
        "Expected Claude policy:\n"
        "{\n"
        '  "allowManagedMcpServersOnly": true\n'
        "}\n"
        "\n"
        "Components: 1\n"
        "  blocked: mcp-server/npm/x (mcps default: blocked)\n"
        "  not enforceable: skill/y: posture openaca-posture-x is not enforceable"
    )


def test_render_policy_report_json_is_sorted_and_compact():
    assert render_policy_report(_REPORT, "json") == json.dumps(_REPORT, sort_keys=True)
