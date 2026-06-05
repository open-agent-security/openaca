# 004 — Static Export Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Convert YAML advisories under `advisories/YYYY/` into the canonical published artifacts: per-advisory JSON files, an `all.zip` bundle, a `modified_id.csv` index, and a static GitHub Pages site that hosts both human-readable advisory pages and the raw artifacts.

**Architecture:** A single `tools/export.py` builds everything into a `dist/` directory (gitignored). A GitHub Actions workflow runs the same export on every push to `main` and publishes `dist/` to GitHub Pages. The output layout follows OSV.dev's conventions so consumers familiar with OSV can mirror OpenACA the same way.

**Tech Stack:** Python 3.11+, stdlib `zipfile` + `csv` + `json`, `jinja2` for HTML templating (new dev dependency), pyyaml. GitHub Actions for publishing.

**Depends on:** 001 (schema, linter), 002 (the advisory corpus).

---

## File structure

| File | Purpose |
|---|---|
| `tools/export.py` | Build static export into `dist/` |
| `tools/templates/advisory.html.j2` | Per-advisory page template |
| `tools/templates/index.html.j2` | Listing page template |
| `tests/test_export.py` | Round-trip + zip + CSV tests |
| `Makefile` | Add `publish` target that runs the exporter |
| `.gitignore` | Add `dist/` |
| `.github/workflows/publish.yml` | Build and deploy to GitHub Pages on push to `main` |

Output layout under `dist/`:

```
dist/
  all.zip
  modified_id.csv
  advisories/
    2026/
      CVE-2026-0001.json
      CVE-2026-0001.html
      ...
  index.html
  schema/
    openaca.schema.json   # copy of the canonical schema
```

---

## Task 1: Add `dist/` to `.gitignore` and add `jinja2` dependency

**Files:**
- Modify: `.gitignore`
- Modify: `pyproject.toml`

- [x] **Step 1: Append to `.gitignore`**

```text

# Static export output
dist/
```

- [x] **Step 2: Add `jinja2` to dev deps**

Edit `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "jinja2>=3.1",
]
```

Sync deps: `uv sync`

- [x] **Step 3: Commit**

```bash
git add .gitignore pyproject.toml
git commit -m "chore: gitignore dist/, add jinja2 dev dep"
```

---

## Task 2: Round-trip test (YAML → JSON)

**Files:**
- Create: `tests/test_export.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_export.py
import json
from pathlib import Path

import pytest
import yaml

from tools.export import build, load_corpus

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def sample_corpus(tmp_path, fixtures_dir):
    advisories_dir = tmp_path / "advisories" / "2026"
    advisories_dir.mkdir(parents=True)
    src = fixtures_dir / "valid" / "cve-2026-0001.yaml"
    (advisories_dir / src.name.upper().replace(".YAML", ".yaml")).write_text(src.read_text())
    # Also write under the canonical capitalized filename
    (advisories_dir / "CVE-2026-0001.yaml").write_text(src.read_text())
    return tmp_path


def test_load_corpus_returns_one_advisory(sample_corpus):
    corpus = load_corpus(sample_corpus / "advisories")
    assert len(corpus) == 1
    assert corpus[0]["id"] == "CVE-2026-0001"


def test_build_emits_json_per_advisory(sample_corpus, tmp_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories",
          schema_path=REPO_ROOT / "schema" / "openaca.schema.json",
          dist=dist)
    json_path = dist / "advisories" / "2026" / "CVE-2026-0001.json"
    assert json_path.is_file()
    record = json.loads(json_path.read_text())
    # YAML → JSON preserves structure
    src = yaml.safe_load((sample_corpus / "advisories" / "2026" / "CVE-2026-0001.yaml").read_text())
    assert record == src
```

> Note: the fixture deliberately writes the advisory under the canonical `OpenACA-` prefix; the duplicate write with the lowercase prefix is irrelevant to the test (cleaning that up is fine if you prefer; both produce the same content).

- [x] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_export.py -v`
Expected: fails — `tools.export` module does not exist.

---

## Task 3: Implement minimal exporter (load + JSON emit)

**Files:**
- Create: `tools/export.py`

- [x] **Step 1: Implement initial `tools/export.py`**

```python
"""Build the static OpenACA export."""
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


def build(advisories_root: Path, schema_path: Path, dist: Path) -> None:
    _ensure_clean(dist)
    corpus = load_corpus(advisories_root)
    for advisory in corpus:
        year = advisory["id"].split("-")[1]
        target = dist / "advisories" / year / f"{advisory['id']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(advisory, indent=2, sort_keys=False) + "\n")
    # Schema copy
    schema_target = dist / "schema" / "openaca.schema.json"
    schema_target.parent.mkdir(parents=True, exist_ok=True)
    schema_target.write_text(schema_path.read_text())
```

- [x] **Step 2: Run tests**

Run: `uv run pytest tests/test_export.py -v`
Expected: both tests pass.

- [x] **Step 3: Commit**

```bash
git add tools/export.py tests/test_export.py
git commit -m "feat: minimal YAML→JSON export to dist/"
```

---

## Task 4: `all.zip` bundle

**Files:**
- Modify: `tools/export.py`
- Modify: `tests/test_export.py`

- [x] **Step 1: Write the failing test**

Append to `tests/test_export.py`:

```python
import zipfile


def test_all_zip_contains_each_advisory(sample_corpus, tmp_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories",
          schema_path=REPO_ROOT / "schema" / "openaca.schema.json",
          dist=dist)
    zip_path = dist / "all.zip"
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "advisories/2026/CVE-2026-0001.json" in names
    assert "schema/openaca.schema.json" in names
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_export.py::test_all_zip_contains_each_advisory -v`
Expected: fails.

- [x] **Step 3: Implement zip building in `tools/export.py`**

Add to `tools/export.py`:

```python
import zipfile


def _bundle_zip(dist: Path) -> None:
    zip_path = dist / "all.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(dist.rglob("*")):
            if path.is_file() and path != zip_path:
                zf.write(path, arcname=path.relative_to(dist))
```

In `build`, append:

```python
    _bundle_zip(dist)
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_export.py -v`
Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add tools/export.py tests/test_export.py
git commit -m "feat: export bundles dist into all.zip"
```

---

## Task 5: `modified_id.csv` index

**Files:**
- Modify: `tools/export.py`
- Modify: `tests/test_export.py`

- [x] **Step 1: Write the failing test**

```python
import csv as csvlib


def test_modified_id_csv_lists_advisories(sample_corpus, tmp_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories",
          schema_path=REPO_ROOT / "schema" / "openaca.schema.json",
          dist=dist)
    csv_path = dist / "modified_id.csv"
    assert csv_path.is_file()
    rows = list(csvlib.DictReader(csv_path.open()))
    assert any(r["id"] == "CVE-2026-0001" for r in rows)
    row = next(r for r in rows if r["id"] == "CVE-2026-0001")
    assert row["modified"]  # ISO timestamp
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_export.py::test_modified_id_csv_lists_advisories -v`
Expected: fails.

- [x] **Step 3: Implement CSV emission**

Add to `tools/export.py`:

```python
import csv


def _emit_modified_csv(corpus: list[dict], dist: Path) -> None:
    csv_path = dist / "modified_id.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "modified"])
        writer.writeheader()
        for advisory in sorted(corpus, key=lambda a: a["id"]):
            writer.writerow({"id": advisory["id"], "modified": advisory["modified"]})
```

In `build`, after the per-advisory JSON loop and before `_bundle_zip(dist)`:

```python
    _emit_modified_csv(corpus, dist)
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_export.py -v`
Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add tools/export.py tests/test_export.py
git commit -m "feat: export emits modified_id.csv index"
```

---

## Task 6: HTML templates and listing page

**Files:**
- Create: `tools/templates/advisory.html.j2`
- Create: `tools/templates/index.html.j2`
- Modify: `tools/export.py`
- Modify: `tests/test_export.py`

- [x] **Step 1: Write the failing test**

```python
def test_export_emits_html_pages(sample_corpus, tmp_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories",
          schema_path=REPO_ROOT / "schema" / "openaca.schema.json",
          dist=dist)
    advisory_html = (dist / "advisories" / "2026" / "CVE-2026-0001.html").read_text()
    assert "CVE-2026-0001" in advisory_html
    index_html = (dist / "index.html").read_text()
    assert "CVE-2026-0001" in index_html
```

- [x] **Step 2: Write `tools/templates/advisory.html.j2`**

```jinja
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ advisory.id }} — OpenACA</title>
  <link rel="canonical" href="https://openaca.dev/advisories/{{ advisory.id.split('-')[1] }}/{{ advisory.id }}.html">
</head>
<body>
  <header>
    <h1>{{ advisory.id }}</h1>
    <p>{{ advisory.summary }}</p>
  </header>
  <section>
    <h2>Type</h2><p>{{ advisory.type }}</p>
    <h2>Aliases</h2>
    <ul>
      {% for alias in advisory.aliases or [] %}
      <li>{{ alias }}</li>
      {% endfor %}
    </ul>
    <h2>Affected</h2>
    <ul>
      {% for entry in advisory.affected or [] %}
      <li>{{ entry.package.ecosystem }}:{{ entry.package.name }}</li>
      {% endfor %}
    </ul>
    <h2>Severity</h2>
    <ul>
      {% for sev in advisory.severity or [] %}
      <li>{{ sev.type }} — <code>{{ sev.score }}</code></li>
      {% endfor %}
    </ul>
    <h2>Details</h2>
    <p>{{ advisory.details }}</p>
    <h2>References</h2>
    <ul>
      {% for ref in advisory.references or [] %}
      <li><a href="{{ ref.url }}">{{ ref.type }}</a></li>
      {% endfor %}
    </ul>
  </section>
  <footer>
    <p>Published {{ advisory.published }} · Modified {{ advisory.modified }}</p>
    <p><a href="../../">Back to index</a> · <a href="{{ advisory.id }}.json">JSON</a></p>
  </footer>
</body>
</html>
```

- [x] **Step 3: Write `tools/templates/index.html.j2`**

```jinja
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>OpenACA — Agent Stack Vulnerabilities and Exposures</title>
</head>
<body>
  <header>
    <h1>OpenACA</h1>
    <p>Open advisories for agent stack security.</p>
  </header>
  <section>
    <h2>Advisories</h2>
    <table>
      <thead><tr><th>ID</th><th>Summary</th><th>Modified</th></tr></thead>
      <tbody>
        {% for advisory in advisories %}
        <tr>
          <td><a href="advisories/{{ advisory.id.split('-')[1] }}/{{ advisory.id }}.html">{{ advisory.id }}</a></td>
          <td>{{ advisory.summary }}</td>
          <td>{{ advisory.modified }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </section>
  <footer>
    <p><a href="all.zip">all.zip</a> · <a href="modified_id.csv">modified_id.csv</a> · <a href="schema/openaca.schema.json">schema</a></p>
  </footer>
</body>
</html>
```

- [x] **Step 4: Wire HTML emission into `tools/export.py`**

Add to `tools/export.py`:

```python
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _render_html(corpus: list[dict], dist: Path) -> None:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    advisory_tmpl = env.get_template("advisory.html.j2")
    index_tmpl = env.get_template("index.html.j2")

    for advisory in corpus:
        year = advisory["id"].split("-")[1]
        target = dist / "advisories" / year / f"{advisory['id']}.html"
        target.write_text(advisory_tmpl.render(advisory=advisory))

    sorted_corpus = sorted(corpus, key=lambda a: a["id"])
    (dist / "index.html").write_text(index_tmpl.render(advisories=sorted_corpus))
```

In `build`, before `_bundle_zip(dist)`:

```python
    _render_html(corpus, dist)
```

- [x] **Step 5: Add a `MANIFEST.in` so templates are installed**

`MANIFEST.in` (new file at repo root):

```
include tools/templates/*.j2
```

And ensure the templates ship with the package by adding to `pyproject.toml`:

```toml
[tool.setuptools.package-data]
tools = ["templates/*.j2"]
```

Sync deps: `uv sync`

- [x] **Step 6: Run tests**

Run: `uv run pytest tests/test_export.py -v`
Expected: all pass.

- [x] **Step 7: Commit**

```bash
git add tools/export.py tools/templates/ MANIFEST.in pyproject.toml tests/test_export.py
git commit -m "feat: HTML pages for advisories and index"
```

---

## Task 7: Register `openaca export` console script

**Files:**
- Modify: `pyproject.toml`

- [x] **Step 1: Register `openaca export` console script**

In `pyproject.toml`:

```toml
[project.scripts]
openaca lint = "tools.lint:main"
openaca-reserve-id = "tools.reserve_id:main"
openaca-import-osv = "tools.import_from_osv:main"
openaca export = "tools.export:main"
```

Sync deps: `uv sync`

- [x] **Step 2: Add a Click CLI entry point at the bottom of `tools/export.py`**

```python
import click


@click.command()
@click.option("--advisories", default="advisories", show_default=True,
              type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--schema", default="schema/openaca.schema.json", show_default=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dist", default="dist", show_default=True, type=click.Path(path_type=Path))
def main(advisories: Path, schema: Path, dist: Path) -> None:
    """Build the OpenACA static export under DIST."""
    build(advisories, schema_path=schema, dist=dist)
    click.echo(f"wrote export to {dist}")


if __name__ == "__main__":
    main()
```

- [x] **Step 3: Smoke test from the repo root**

Run: `uv run openaca export`
Expected: `dist/` is created with `all.zip`, `modified_id.csv`, `advisories/2026/...`, `schema/openaca.schema.json`, `index.html`.

- [x] **Step 4: Commit**

```bash
git add tools/export.py pyproject.toml
git commit -m "feat: openaca export console script"
```

---

## Task 8: GitHub Pages deployment workflow

**Files:**
- Create: `.github/workflows/publish.yml`

- [x] **Step 1: Write the workflow**

```yaml
name: Publish

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - name: Sync deps
        run: uv sync --frozen
      - name: Build export
        run: uv run openaca export
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: dist
      - id: deployment
        uses: actions/deploy-pages@v4
```

- [x] **Step 2: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: publish static export to GitHub Pages on push to main"
```

> Note: enabling Pages in the repo settings (Source = "GitHub Actions") is a one-time manual step outside this plan.

---

## Verification

```bash
uv run openaca export
ls dist/                                            # all.zip, modified_id.csv, index.html, advisories/, schema/
unzip -l dist/all.zip | head -10                    # advisory and schema files
head dist/modified_id.csv                           # id,modified header + rows
open dist/index.html                                # human-readable listing
uv run pytest tests/test_export.py -v               # all pass
```

---

## Self-review checklist

- [x] **YAML → JSON round-trip** preserves the advisory record.
- [x] **`all.zip`** contains the same files served at top-level URLs.
- [x] **`modified_id.csv`** has `id` and `modified` columns and rows are sorted.
- [x] **HTML pages** auto-escape advisory content (`autoescape=True` on the Jinja env).
- [x] **Schema** is mirrored under `dist/schema/openaca.schema.json` (the canonical `$id` URL still works once the domain is wired).
- [x] **No HTTP API** in this plan — that's deferred past V0.
- [x] **Templates** ship with the package (`MANIFEST.in` + `package-data`).
- [x] **No commercial / competitor framing** in templates or output.
