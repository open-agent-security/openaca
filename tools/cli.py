"""Top-level `openaca` CLI: composes scan, lint, export, promote, seed."""

from __future__ import annotations

import click

from tools.bom_cli import main as bom_cmd
from tools.export import main as export_cmd
from tools.lint import main as lint_cmd
from tools.promote import main as promote_cmd
from tools.scan import main as scan_cmd
from tools.seed.__main__ import main as seed_cmd


@click.group()
@click.version_option(package_name="openaca", prog_name="openaca")
def main() -> None:
    """OpenACA: agent composition analysis tooling."""


scan_cmd.short_help = "Scan a repository or endpoint for agent-composition findings."
bom_cmd.short_help = "Generate an Agent BOM for a repository or endpoint."
lint_cmd.short_help = "Validate overlay YAML against the schema."
export_cmd.short_help = "Build the static overlay export."
promote_cmd.short_help = "Promote a reviewed candidate into the corpus."
seed_cmd.short_help = "Generate review candidates from an OSV dump."

main.add_command(scan_cmd, name="scan")
main.add_command(bom_cmd, name="bom")
main.add_command(lint_cmd, name="lint")
main.add_command(export_cmd, name="export")
main.add_command(promote_cmd, name="promote")
main.add_command(seed_cmd, name="seed")


if __name__ == "__main__":
    main()
