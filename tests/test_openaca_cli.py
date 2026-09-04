"""`openaca.cli` publishes the Click group `tools/cli.py` already builds.

What is promised: the group exists, and `scan`, `bom` and `policy` are
reachable on it by those names. Nothing is asserted here about any command's
internal structure, option set or output format — those are the CLI's contract
to its users, which is a looser thing than a library API by design, so adding a
flag must not break a consumer that re-registers a command whole.
"""

from __future__ import annotations

from importlib.metadata import entry_points

import click
from click.testing import CliRunner

import openaca.cli
import tools.cli


def test_the_published_group_is_the_group_tools_cli_defines():
    assert openaca.cli.main is tools.cli.main
    assert isinstance(openaca.cli.main, click.Group)


def test_scan_bom_and_policy_are_reachable_by_name():
    assert set(openaca.cli.main.commands) >= {"scan", "bom", "policy"}


def test_a_command_registered_under_another_group_reports_that_program_name():
    """The whole mechanism a consumer offering OpenACA's commands under its own
    name relies on: Click builds a usage line from the invocation, not from
    where a command was defined. Worth a test precisely because it is a Click
    behaviour rather than an OpenACA one."""
    host = click.Group()
    host.add_command(openaca.cli.main.commands["scan"], name="inspect")

    result = CliRunner().invoke(host, ["inspect", "--help"], prog_name="othertool")

    assert result.exit_code == 0, result.output
    assert result.output.startswith("Usage: othertool inspect")


def test_the_console_script_still_points_at_tools_cli():
    """Running `openaca` must not change; the published import is additive."""
    (script,) = [ep for ep in entry_points(group="console_scripts") if ep.name == "openaca"]
    assert script.value == "tools.cli:main"
    assert script.load() is tools.cli.main
