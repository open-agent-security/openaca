"""Build the static ASVE export.

Loads every advisory YAML under `advisories/`, emits OSV-shaped JSON per
advisory under `dist/advisories/<year>/<id>.json`, copies the canonical
schema to `dist/schema/asve.schema.json`, and produces three top-level
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

import yaml


def load_corpus(advisories_root: Path) -> list[dict]:
    corpus: list[dict] = []
    for path in sorted(advisories_root.rglob("*.yaml")):
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


def build(advisories_root: Path, schema_path: Path, dist: Path) -> None:
    _ensure_clean(dist)
    corpus = load_corpus(advisories_root)
    for advisory in corpus:
        year = advisory["id"].split("-")[1]
        target = dist / "advisories" / year / f"{advisory['id']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_dump_json(advisory))
    schema_target = dist / "schema" / "asve.schema.json"
    schema_target.parent.mkdir(parents=True, exist_ok=True)
    schema_target.write_text(schema_path.read_text())
    _emit_modified_csv(corpus, dist)
    _emit_index_json(corpus, dist)
    _bundle_zip(dist)
