# 005 — Reference GitHub Action

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A thin, local-first GitHub Action that consumers invoke via `open-agent-security/asve@v1`. The Action parses agent-installation manifests in the consumer's repo (using the parsers from Plan 003), looks up matching advisories from the ASVE static export, and reports findings as both SARIF and GitHub annotations.

**Architecture:** A single Python entrypoint (`tools/scan.py`) wraps three steps: (1) parse the target repo using `tools.parsers.parse_repo`, (2) match the resulting `ComponentRef` stream against the loaded advisory corpus using a small ranges/identity matcher, (3) emit SARIF (for code-scanning UIs) and GitHub annotations (for inline PR review). The `action.yml` at the repo root invokes the same script.

**Tech Stack:** Python 3.11+, `packaging` (npm/PyPI version comparison), stdlib `json` for SARIF emission. No JavaScript Action wrapper — pure composite-Python action.

**Depends on:** 001 (project setup), 002 (advisory corpus), 003 (parsers), 004 (static export).

---

## File structure

| File | Purpose |
|---|---|
| `action.yml` | Composite GitHub Action manifest at repo root |
| `tools/matcher.py` | Match a `ComponentRef` against a set of advisories |
| `tools/sarif.py` | Render findings as SARIF v2.1.0 |
| `tools/scan.py` | End-to-end CLI: parse → match → report |
| `tests/test_matcher.py` | Range and identity matching tests |
| `tests/test_sarif.py` | SARIF schema-shape tests |
| `tests/test_scan.py` | End-to-end scan against fixture repos |
| `tests/fixtures/repos/exposed-mcp/...` | Fixture repo that should match ASVE-2026-0001 |

---

## Task 1: Add `packaging` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `packaging` to runtime deps**

```toml
[project]
dependencies = [
    "click>=8.1",
    "jsonschema>=4.21",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "packaging>=24.0",
]
```

Sync deps: `uv sync`

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add packaging for version-range comparisons"
```

---

## Task 2: Matcher — versioned range matches

**Files:**
- Create: `tools/matcher.py`
- Create: `tests/test_matcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_matcher.py
from tools.component_ref import ComponentRef
from tools.matcher import Finding, match


def make_advisory(asve_id: str, ecosystem: str, name: str, fixed: str) -> dict:
    return {
        "id": asve_id,
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-06T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": ecosystem, "name": name},
                "ranges": [
                    {"type": "ECOSYSTEM",
                     "events": [{"introduced": "0"}, {"fixed": fixed}]}
                ],
            }
        ],
    }


def test_match_npm_in_range():
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(ecosystem="npm", name="@cyanheads/git-mcp-server", version="1.1.0",
                       source_manifest="package.json", source_locator="dependencies")
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].advisory_id == "ASVE-2026-0001"
    assert findings[0].component is ref


def test_match_npm_at_fixed_version_excluded():
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(ecosystem="npm", name="@cyanheads/git-mcp-server", version="1.2.3",
                       source_manifest="package.json", source_locator="dependencies")
    findings = match(refs=[ref], advisories=advisories)
    assert findings == []


def test_match_unknown_version_returns_finding_with_warning():
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(ecosystem="npm", name="@cyanheads/git-mcp-server", version="^1.0.0",
                       source_manifest="package.json", source_locator="dependencies")
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].confidence == "low"   # range-vs-range: cannot resolve precisely
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_matcher.py -v`
Expected: fails — module does not exist.

- [ ] **Step 3: Implement `tools/matcher.py`**

```python
"""Match ComponentRefs against ASVE advisories."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from packaging.version import InvalidVersion, Version

from tools.component_ref import ComponentRef


@dataclass(frozen=True)
class Finding:
    advisory_id: str
    component: ComponentRef
    confidence: str  # "high" if version is concrete; "low" if range-vs-range; "identity" for ASVE-native
    reason: str = ""


def _parse_version(value: Optional[str]) -> Optional[Version]:
    if value is None:
        return None
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _match_range(version: Optional[Version], events: list[dict]) -> Optional[bool]:
    """Return True if version is within range (introduced..fixed], False if not, None if can't decide."""
    if version is None:
        return None
    introduced = None
    fixed = None
    for ev in events:
        if "introduced" in ev:
            introduced = ev["introduced"]
        elif "fixed" in ev:
            fixed = ev["fixed"]
    intro_v = _parse_version(introduced) if introduced not in (None, "0") else Version("0")
    fixed_v = _parse_version(fixed)
    if intro_v is None or fixed_v is None:
        return None
    return (version >= intro_v) and (version < fixed_v)


def _match_versioned(ref: ComponentRef, advisories: list[dict]) -> list[Finding]:
    findings: list[Finding] = []
    if not (ref.ecosystem and ref.name):
        return findings
    for advisory in advisories:
        for entry in advisory.get("affected") or []:
            pkg = entry.get("package") or {}
            if pkg.get("ecosystem") != ref.ecosystem or pkg.get("name") != ref.name:
                continue
            ranges = entry.get("ranges") or []
            version = _parse_version(ref.version)
            for rng in ranges:
                events = rng.get("events") or []
                in_range = _match_range(version, events)
                if in_range is True:
                    findings.append(Finding(
                        advisory_id=advisory["id"],
                        component=ref,
                        confidence="high",
                        reason=f"{ref.name}@{ref.version} matches {advisory['id']}",
                    ))
                elif in_range is None:
                    findings.append(Finding(
                        advisory_id=advisory["id"],
                        component=ref,
                        confidence="low",
                        reason=f"{ref.name}@{ref.version!r} could not be precisely resolved against {advisory['id']}",
                    ))
            if not ranges and entry.get("versions") and ref.version in entry["versions"]:
                findings.append(Finding(
                    advisory_id=advisory["id"], component=ref, confidence="high",
                    reason=f"{ref.name}@{ref.version} listed in {advisory['id']} versions",
                ))
    return findings


def _match_identity(ref: ComponentRef, advisories: list[dict]) -> list[Finding]:
    findings: list[Finding] = []
    if not ref.component_identity:
        return findings
    for advisory in advisories:
        hints = (advisory.get("database_specific") or {}).get("asve", {}).get("detection_hints") or {}
        for manifest_hint in hints.get("manifests") or []:
            for arg_match in manifest_hint.get("match_args") or []:
                # Crude substring check; the parsers normalize commands and args
                if arg_match in (ref.extra.get("raw_command", "") + " " + " ".join(ref.extra.get("raw_args", []))):
                    findings.append(Finding(
                        advisory_id=advisory["id"], component=ref, confidence="identity",
                        reason=f"{ref.component_identity} matches detection hint",
                    ))
    return findings


def match(refs: list[ComponentRef], advisories: list[dict]) -> list[Finding]:
    findings: list[Finding] = []
    for ref in refs:
        findings += _match_versioned(ref, advisories)
        findings += _match_identity(ref, advisories)
    return findings
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_matcher.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tools/matcher.py tests/test_matcher.py
git commit -m "feat: matcher pairs ComponentRefs with ASVE advisories"
```

---

## Task 3: SARIF emission

**Files:**
- Create: `tools/sarif.py`
- Create: `tests/test_sarif.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sarif.py
from tools.component_ref import ComponentRef
from tools.matcher import Finding
from tools.sarif import to_sarif


def test_sarif_envelope():
    ref = ComponentRef(ecosystem="npm", name="@cyanheads/git-mcp-server", version="1.1.0",
                       source_manifest="package.json", source_locator="dependencies")
    findings = [Finding(advisory_id="ASVE-2026-0001", component=ref, confidence="high",
                        reason="matched range")]
    advisory_index = {
        "ASVE-2026-0001": {"summary": "Command injection in @cyanheads/git-mcp-server",
                            "details": "see advisory"},
    }
    sarif = to_sarif(findings, advisory_index)
    assert sarif["version"] == "2.1.0"
    assert sarif["$schema"].startswith("https://json.schemastore.org/sarif")
    runs = sarif["runs"]
    assert runs[0]["tool"]["driver"]["name"] == "asve"
    rule_ids = {r["id"] for r in runs[0]["tool"]["driver"]["rules"]}
    assert "ASVE-2026-0001" in rule_ids
    result = runs[0]["results"][0]
    assert result["ruleId"] == "ASVE-2026-0001"
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "package.json"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sarif.py -v`
Expected: fails.

- [ ] **Step 3: Implement `tools/sarif.py`**

```python
"""Render ASVE findings as SARIF v2.1.0."""
from __future__ import annotations

from tools.matcher import Finding

LEVEL_BY_CONFIDENCE = {"high": "error", "low": "warning", "identity": "warning"}


def to_sarif(findings: list[Finding], advisory_index: dict[str, dict]) -> dict:
    rule_ids = sorted({f.advisory_id for f in findings})
    rules = []
    for advisory_id in rule_ids:
        meta = advisory_index.get(advisory_id, {})
        rules.append({
            "id": advisory_id,
            "name": advisory_id,
            "shortDescription": {"text": meta.get("summary", advisory_id)},
            "fullDescription": {"text": meta.get("details", meta.get("summary", advisory_id))},
            "helpUri": f"https://asve.dev/advisories/{advisory_id.split('-')[1]}/{advisory_id}.html",
        })
    results = []
    for f in findings:
        results.append({
            "ruleId": f.advisory_id,
            "level": LEVEL_BY_CONFIDENCE.get(f.confidence, "warning"),
            "message": {"text": f.reason or f.advisory_id},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": f.component.source_manifest},
                    "region": {"startLine": 1, "snippet": {"text": f.component.source_locator}},
                }
            }],
        })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "asve", "informationUri": "https://asve.dev",
                                 "rules": rules}},
            "results": results,
        }],
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_sarif.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tools/sarif.py tests/test_sarif.py
git commit -m "feat: SARIF v2.1.0 emission for ASVE findings"
```

---

## Task 4: `tools/scan.py` end-to-end

**Files:**
- Create: `tools/scan.py`
- Create: `tests/test_scan.py`
- Create: `tests/fixtures/repos/exposed-mcp/package.json`
- Create: `tests/fixtures/repos/exposed-mcp/.claude/settings.json`

- [ ] **Step 1: Write fixtures**

`tests/fixtures/repos/exposed-mcp/package.json`:

```json
{
  "name": "exposed",
  "version": "0.0.0",
  "dependencies": {
    "@cyanheads/git-mcp-server": "1.1.0"
  }
}
```

`tests/fixtures/repos/exposed-mcp/.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "deployment-tools@1.2.0": true
  }
}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_scan.py
import json
from pathlib import Path

from click.testing import CliRunner

from tools.scan import main

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def test_scan_finds_exposed_mcp(tmp_path):
    sarif_out = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(main, [
        "--target", str(FIXTURES / "repos" / "exposed-mcp"),
        "--advisories", str(REPO_ROOT / "advisories"),
        "--sarif", str(sarif_out),
    ])
    assert result.exit_code == 1, result.output    # findings → exit 1
    sarif = json.loads(sarif_out.read_text())
    rule_ids = {r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
    assert "ASVE-2026-0001" in rule_ids


def test_scan_clean_repo_exits_zero(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "package.json").write_text('{"name":"clean","version":"0","dependencies":{}}')
    sarif_out = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(main, [
        "--target", str(clean),
        "--advisories", str(REPO_ROOT / "advisories"),
        "--sarif", str(sarif_out),
    ])
    assert result.exit_code == 0, result.output
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_scan.py -v`
Expected: fails — `tools.scan` does not exist.

- [ ] **Step 4: Implement `tools/scan.py`**

```python
"""End-to-end ASVE scan: parse → match → report (SARIF + annotations)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml

from tools.matcher import Finding, match
from tools.parsers import parse_repo
from tools.sarif import to_sarif


def load_corpus(advisories_root: Path) -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(advisories_root.rglob("*.yaml"))]


def emit_github_annotations(findings: list[Finding]) -> None:
    """GitHub workflow annotation lines on stdout."""
    for f in findings:
        kind = "error" if f.confidence == "high" else "warning"
        click.echo(
            f"::{kind} file={f.component.source_manifest},title={f.advisory_id}::"
            f"{f.reason}"
        )


@click.command()
@click.option("--target", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Repo to scan.")
@click.option("--advisories", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="ASVE advisories directory (YAML records).")
@click.option("--sarif", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Write SARIF v2.1.0 to this path.")
@click.option("--fail-on", type=click.Choice(["high", "any", "none"]), default="any",
              show_default=True, help="Exit non-zero when findings of this severity are present.")
def main(target: Path, advisories: Path, sarif: Path | None, fail_on: str) -> None:
    """Scan TARGET for components matching ASVE advisories."""
    refs = parse_repo(target)
    corpus = load_corpus(advisories)
    findings = match(refs, corpus)

    advisory_index = {a["id"]: a for a in corpus}

    if sarif:
        sarif_doc = to_sarif(findings, advisory_index)
        sarif.write_text(json.dumps(sarif_doc, indent=2))
        click.echo(f"sarif: wrote {sarif}", err=True)

    emit_github_annotations(findings)

    if not findings:
        click.echo("no findings", err=True)
        sys.exit(0)

    high_count = sum(1 for f in findings if f.confidence == "high")
    click.echo(f"{len(findings)} finding(s); {high_count} high-confidence", err=True)

    if fail_on == "none":
        sys.exit(0)
    if fail_on == "high" and high_count == 0:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Register console script**

Add to `pyproject.toml`:

```toml
asve-scan = "tools.scan:main"
```

Sync deps: `uv sync`

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_scan.py -v`
Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add tools/scan.py tests/test_scan.py tests/fixtures/repos/exposed-mcp/ pyproject.toml
git commit -m "feat: end-to-end asve-scan CLI"
```

---

## Task 5: `action.yml` at repo root

**Files:**
- Create: `action.yml`

- [ ] **Step 1: Write the action**

```yaml
name: ASVE Scan
description: Scan agent-installation manifests for ASVE advisories.
author: ASVE
branding:
  icon: shield
  color: gray-dark

inputs:
  target:
    description: Path to scan (defaults to GITHUB_WORKSPACE).
    required: false
    default: ${{ github.workspace }}
  advisories:
    description: |
      Path to the advisories directory. Defaults to the action's bundled
      copy. Set to a vendored mirror if you need air-gapped operation.
    required: false
    default: ${{ github.action_path }}/advisories
  sarif:
    description: SARIF output path.
    required: false
    default: asve-results.sarif
  fail-on:
    description: Severity threshold for non-zero exit (high | any | none).
    required: false
    default: any

outputs:
  sarif-path:
    description: Path to the generated SARIF file.
    value: ${{ steps.scan.outputs.sarif-path }}

runs:
  using: composite
  steps:
    - uses: astral-sh/setup-uv@v3
      with:
        enable-cache: true
    - shell: bash
      working-directory: ${{ github.action_path }}
      run: uv sync --frozen
    - id: scan
      shell: bash
      run: |
        cd "${{ github.action_path }}"
        uv run asve-scan \
          --target "${{ inputs.target }}" \
          --advisories "${{ inputs.advisories }}" \
          --sarif "${{ inputs.sarif }}" \
          --fail-on "${{ inputs.fail-on }}"
        echo "sarif-path=${{ inputs.sarif }}" >> "$GITHUB_OUTPUT"
```

- [ ] **Step 2: Smoke-test locally**

Run: `uv run asve-scan --target tests/fixtures/repos/exposed-mcp --advisories advisories --sarif /tmp/asve-out.sarif`
Expected: exits non-zero; `/tmp/asve-out.sarif` written; ASVE-2026-0001 listed.

- [ ] **Step 3: Commit**

```bash
git add action.yml
git commit -m "feat: composite GitHub Action at repo root"
```

---

## Task 6: Self-scan workflow

A workflow that runs `asve-scan` on this repo's own manifests. Useful as both dogfooding and CI smoke-test.

**Files:**
- Create: `.github/workflows/self-scan.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Self-scan

on:
  pull_request:
  push:
    branches: [main]

jobs:
  asve-scan:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v4
      - uses: ./
        with:
          target: ${{ github.workspace }}
          advisories: ${{ github.workspace }}/advisories
          fail-on: none      # this repo is the database; findings would be self-references
      - uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: asve-results.sarif
        if: always()
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/self-scan.yml
git commit -m "ci: self-scan workflow exercises action.yml on every PR"
```

---

## Verification

```bash
uv run asve-scan --target tests/fixtures/repos/exposed-mcp --advisories advisories --sarif /tmp/o.sarif
# Expected: exit 1; SARIF lists ASVE-2026-0001
uv run pytest tests/test_matcher.py tests/test_sarif.py tests/test_scan.py -v
# Expected: all pass
```

End-to-end (after pushing to GitHub): the self-scan workflow runs on every PR; SARIF output uploads to the Security tab.

---

## Self-review checklist

- [ ] **Matcher** handles both versioned (range/list) and identity (detection_hints) cases.
- [ ] **SARIF v2.1.0** envelope is correct: `version`, `runs[0].tool.driver`, `rules`, `results`, `locations`.
- [ ] **GitHub annotations** on stdout follow `::error file=…,title=…::` syntax.
- [ ] **Exit code policy** matches `--fail-on` (`any`/`high`/`none`).
- [ ] **Composite action** uses Python entrypoint; no Node wrapper. Fewer moving parts.
- [ ] **Self-scan workflow** uses `fail-on: none` because this repo *is* the database.
- [ ] **No commercial / competitor framing** in `action.yml` description, branding, or output messages.
