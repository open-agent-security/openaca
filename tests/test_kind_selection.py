"""Kind-selection validation, and the command line's parity with it.

The three messages are asserted twice: once against `validate_kind_selection`
directly, and once against `openaca scan endpoint`, using literals captured
from the command line *before* the validation moved out of `tools/cli_kind.py`.
The CLI cases are therefore evidence that the move changed nothing, not a
restatement of the new code.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

import tools.agent_kinds as agent_kinds
from tools.agent_kinds import AgentKind
from tools.cli import main
from tools.cli_kind import require_kind_for_config_dir
from tools.graph import Graph
from tools.kind_selection import KindSelectionError, validate_kind_selection

# Captured from `openaca scan endpoint` on commit 83ec849, before the move.
_UNKNOWN_KIND = "unknown agent kind 'not-a-real-kind'; known kinds: claude-code, codex, cursor"
_CONFIG_DIR_WITHOUT_KIND = (
    "--config-dir requires --kind: with more than one installed agent kind, "
    "--config-dir alone cannot say which kind's root it names."
)
_CURSOR_REFUSAL = (
    "--config-dir is not supported for --kind cursor: an installed Cursor's "
    "composition is gathered from three places — its own root, permissions.json "
    "(relocated independently), and another runtime's skill roots under your home "
    "— and a root override moves only the first, producing a composition stitched "
    "from two homes that the output cannot distinguish from a correct scan."
)


def _synthetic_kind(*, kind_id: str, refusal: str | None) -> AgentKind:
    return AgentKind(
        id=kind_id,
        display_name="Synthetic",
        cardinality="singleton",
        root_label=kind_id,
        coverage_baseline={"installed": "partial", "declared": "partial"},
        discover=lambda ctx: [],
        compose=lambda agent, **_: Graph(nodes={}),
        root_override_refusal=refusal,
    )


def test_a_legal_selection_raises_nothing(tmp_path):
    assert validate_kind_selection(None, None) is None
    assert validate_kind_selection("claude-code", None) is None
    assert validate_kind_selection("claude-code", tmp_path) is None


def test_an_unknown_kind_names_the_known_kinds(tmp_path):
    with pytest.raises(KindSelectionError) as excinfo:
        validate_kind_selection("not-a-real-kind", tmp_path)

    assert str(excinfo.value) == _UNKNOWN_KIND


def test_a_config_root_without_a_kind_is_ambiguous(tmp_path):
    """Never a silent arbitration toward one kind's root."""
    with pytest.raises(KindSelectionError) as excinfo:
        validate_kind_selection(None, tmp_path)

    assert str(excinfo.value) == _CONFIG_DIR_WITHOUT_KIND


def test_a_kind_may_refuse_the_root_override_outright(tmp_path):
    """ADR-0054: a root override is a per-kind capability. Cursor is the real
    refusing kind, and it names its own reason rather than a generic one."""
    with pytest.raises(KindSelectionError) as excinfo:
        validate_kind_selection("cursor", tmp_path)

    assert str(excinfo.value) == _CURSOR_REFUSAL


def test_the_validation_reads_the_registry_live(tmp_path, monkeypatch):
    """`tools/kind_selection.py` reads `agent_kinds.REGISTRY`/`kind_for`
    *through* the module rather than importing the names, so a test that swaps
    in a synthetic registry stays live. Importing them would freeze the
    validation at import time and this test would validate against the real
    registry while appearing to pass."""
    monkeypatch.setattr(
        agent_kinds,
        "REGISTRY",
        (
            _synthetic_kind(kind_id="synthetic-open", refusal=None),
            _synthetic_kind(kind_id="synthetic-refusing", refusal="it resolves its own root"),
        ),
    )

    assert validate_kind_selection("synthetic-open", tmp_path) is None

    with pytest.raises(KindSelectionError) as unknown:
        validate_kind_selection("claude-code", tmp_path)
    assert str(unknown.value) == (
        "unknown agent kind 'claude-code'; known kinds: synthetic-open, synthetic-refusing"
    )

    with pytest.raises(KindSelectionError) as refused:
        validate_kind_selection("synthetic-refusing", tmp_path)
    assert str(refused.value) == (
        "--config-dir is not supported for --kind synthetic-refusing: it resolves its own root."
    )


def test_the_cli_adapter_translates_the_domain_error(tmp_path):
    """`require_kind_for_config_dir` keeps raising Click's exception, so the
    three commands using it are untouched by the move."""
    import click

    with pytest.raises(click.ClickException) as excinfo:
        require_kind_for_config_dir(None, tmp_path)

    assert excinfo.value.format_message() == _CONFIG_DIR_WITHOUT_KIND


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--kind", "not-a-real-kind"], _UNKNOWN_KIND),
        (["--config-dir", "<TMP>"], _CONFIG_DIR_WITHOUT_KIND),
        (["--kind", "cursor", "--config-dir", "<TMP>"], _CURSOR_REFUSAL),
    ],
    ids=["unknown-kind", "config-dir-without-kind", "refusing-kind"],
)
def test_the_command_line_is_byte_identical_to_the_pre_move_capture(tmp_path, argv, message):
    result = CliRunner().invoke(
        main, ["scan", "endpoint", *(str(tmp_path) if a == "<TMP>" else a for a in argv)]
    )

    assert result.exit_code == 1
    assert result.output == f"Error: {message}\n"


def test_the_domain_function_takes_a_path(tmp_path):
    """The signature the facade publishes: an optional kind id and an optional
    `Path`, matching `collect_installed_agents`' own two arguments."""
    assert validate_kind_selection("claude-code", Path(tmp_path)) is None
