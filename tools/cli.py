"""Top-level `openaca` CLI: composes scan, lint, export, promote, seed."""

from __future__ import annotations

import click

from tools.export import main as export_cmd
from tools.lint import main as lint_cmd
from tools.promote import main as promote_cmd
from tools.scan import main as scan_cmd
from tools.seed.__main__ import main as seed_cmd


@click.group()
def main() -> None:
    """OpenACA: agent composition analysis tooling."""


main.add_command(scan_cmd, name="scan")
main.add_command(lint_cmd, name="lint")
main.add_command(export_cmd, name="export")
main.add_command(promote_cmd, name="promote")
main.add_command(seed_cmd, name="seed")


if __name__ == "__main__":
    main()
