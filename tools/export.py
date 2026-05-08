"""Build the static ASVE export.

Loads every advisory YAML under `advisories/`, emits OSV-shaped JSON per
advisory under `dist/advisories/<year>/<id>.json`, and copies the
canonical schema to `dist/schema/asve.schema.json`.

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

import json
import shutil
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


def _dump_json(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


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
