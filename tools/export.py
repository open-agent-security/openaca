"""Build the static ASVE overlay export.

Loads every overlay YAML under `overlays/`, emits JSON per overlay under
`dist/overlays/<id>.json`, copies the canonical schema to
`dist/schema/asve.schema.json`, and produces three top-level
index artifacts for downstream consumers:

- `all.zip` — every JSON file + the schema, for offline mirroring (the
  pattern OSV-Scanner consumes).
- `modified_id.csv` — `id,modified` rows sorted by `id`, for sync
  clients that diff against their last-pulled timestamp.
- `index.json` — flat array of `{id, modified, summary,
  affected_ecosystems}` for tooling that wants a single fetch + local
  filter (the static replacement for an HTTP query API in V0).

Output format conventions (locked in V0):
- UTF-8 (no `\\uXXXX` escapes) so non-ASCII summaries render as written.
- 2-space indent for human readability and stable diffs.
- Field order preserved from the YAML source — matches OSV.dev's
  natural-order convention. Don't `sort_keys`; sorting reorders OSV
  records away from the form their consumers expect.
- Trailing newline so POSIX tools (`cat`, `wc -l`) treat each file as
  a complete line-terminated record.
"""

from __future__ import annotations

import csv
import json
import shutil
import zipfile
from pathlib import Path

import click
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates"


def load_corpus(overlays_root: Path) -> list[dict]:
    corpus: list[dict] = []
    for path in sorted(overlays_root.rglob("*.yaml")):
        corpus.append(yaml.safe_load(path.read_text()))
    return corpus


def _ensure_clean(dist: Path) -> None:
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)


def _dump_json(record: object) -> str:
    return json.dumps(record, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _affected_ecosystems(advisory: dict) -> list[str]:
    """Return de-duplicated ecosystems from advisory.affected[*].package.ecosystem."""
    seen: list[str] = []
    for entry in advisory.get("affected") or []:
        eco = (entry.get("package") or {}).get("ecosystem")
        if eco and eco not in seen:
            seen.append(eco)
    return seen


def _emit_modified_csv(corpus: list[dict], dist: Path) -> None:
    """Emit dist/modified_id.csv sorted by id for stable diffs."""
    csv_path = dist / "modified_id.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "modified"])
        writer.writeheader()
        for advisory in sorted(corpus, key=lambda a: a["id"]):
            writer.writerow({"id": advisory["id"], "modified": advisory["modified"]})


def _emit_index_json(corpus: list[dict], dist: Path) -> None:
    """Emit dist/index.json: compact summary array for tooling consumers."""
    index = [
        {
            "id": advisory["id"],
            "modified": advisory["modified"],
            "summary": advisory.get("summary", ""),
            "affected_ecosystems": _affected_ecosystems(advisory),
        }
        for advisory in sorted(corpus, key=lambda a: a["id"])
    ]
    (dist / "index.json").write_text(_dump_json(index))


def _bundle_zip(dist: Path) -> None:
    """Bundle every file under dist/ (except the zip itself) into all.zip."""
    zip_path = dist / "all.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(dist.rglob("*")):
            if path.is_file() and path != zip_path:
                zf.write(path, arcname=path.relative_to(dist))


def _render_html(corpus: list[dict], dist: Path) -> None:
    """Render per-overlay pages and index."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    advisory_tmpl = env.get_template("advisory.html.j2")
    index_tmpl = env.get_template("index.html.j2")

    for advisory in corpus:
        target = dist / "overlays" / f"{advisory['id']}.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(advisory_tmpl.render(advisory=advisory), encoding="utf-8")

    sorted_corpus = sorted(corpus, key=lambda a: a["id"])
    (dist / "index.html").write_text(
        index_tmpl.render(advisories_by_year=[("Overlays", sorted_corpus)]),
        encoding="utf-8",
    )

    css_src = TEMPLATES_DIR / "style.css"
    (dist / "style.css").write_text(css_src.read_text(encoding="utf-8"), encoding="utf-8")


def build(overlays_root: Path, schema_path: Path, dist: Path) -> None:
    _ensure_clean(dist)
    corpus = load_corpus(overlays_root)
    for overlay in corpus:
        target = dist / "overlays" / f"{overlay['id']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_dump_json(overlay))
    schema_target = dist / "schema" / "asve.schema.json"
    schema_target.parent.mkdir(parents=True, exist_ok=True)
    schema_target.write_text(schema_path.read_text())
    _emit_modified_csv(corpus, dist)
    _emit_index_json(corpus, dist)
    _render_html(corpus, dist)
    _bundle_zip(dist)


@click.command()
@click.option(
    "--schema",
    default="schema/asve.schema.json",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--dist",
    default="dist",
    show_default=True,
    type=click.Path(path_type=Path),
)
def main(schema: Path, dist: Path) -> None:
    """Build the ASVE static export under DIST."""
    build(Path("overlays"), schema_path=schema, dist=dist)
    click.echo(f"wrote export to {dist}")


if __name__ == "__main__":
    main()
