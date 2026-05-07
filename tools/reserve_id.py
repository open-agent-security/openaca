"""Reserve the next free ASVE-YYYY-NNNN identifier."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import click

ID_RE = re.compile(r"^ASVE-(\d{4})-(\d{4})\.yaml$")


def next_id_for_year(advisories_dir: Path, year: int) -> str:
    # Reserve max(used) + 1 — gaps from withdrawn or never-published numbers
    # are intentionally not reused. Predictable monotonic IDs are easier to
    # reason about than gap-filling, and prevent confusion when a withdrawn
    # advisory's number resurfaces under a new vulnerability.
    used: set[int] = set()
    year_dir = advisories_dir / str(year)
    if year_dir.is_dir():
        for path in year_dir.iterdir():
            m = ID_RE.match(path.name)
            if m and int(m.group(1)) == year:
                used.add(int(m.group(2)))
    next_n = (max(used) + 1) if used else 1
    return f"ASVE-{year}-{next_n:04d}"


@click.command()
@click.argument("advisories_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--year", type=int, default=None, help="Year segment; defaults to current year.")
def main(advisories_dir: Path, year: int | None) -> None:
    """Print the next free ASVE-YYYY-NNNN under ADVISORIES_DIR for YEAR."""
    if year is None:
        year = date.today().year
    click.echo(next_id_for_year(advisories_dir, year))


if __name__ == "__main__":
    main()
