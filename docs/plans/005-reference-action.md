# 005 — Reference Scanner (CLI + Action)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reference scanner exposed two ways from a single Python entrypoint (`tools/scan.py`): (a) the `asve-scan` CLI for local developer use, with human-readable terminal output and smart defaults so `asve-scan .` "just works"; (b) a thin composite GitHub Action at the repo root (`action.yml`, invoked as `open-agent-security/asve@v1`) that wraps the same CLI for CI use. CLI is a first-class V0 deliverable; PyPI publishing is a deliberate follow-up.

**Architecture:** `tools/scan.py` wraps three steps: (1) parse the target repo using `tools.parsers.parse_repo` (Plan 003), (2) match the resulting `ComponentRef` stream against the loaded advisory corpus using a small ranges/identity matcher, (3) emit findings via three sinks selected at runtime — a human-readable terminal table (default for local), GitHub workflow annotations (only when `GITHUB_ACTIONS=true`), and SARIF (when `--sarif` is given). Smart defaults: `--target` defaults to the current directory; `--advisories` resolves explicit-flag → `./advisories` if present → `~/.cache/asve/all.zip` (auto-fetched from `https://asve.dev/all.zip` and cached, with `--no-fetch` opt-out). The `action.yml` invokes the same CLI with explicit args.

**Tech Stack:** Python 3.11+, `packaging` (npm/PyPI version comparison), stdlib `json` for SARIF emission, stdlib `urllib.request` for advisory auto-fetch (no new runtime deps). No JavaScript Action wrapper — pure composite-Python action.

**Depends on:** 001 (project setup), 002 (advisory corpus), 003 (parsers), 004 (static export).

---

## File structure

| File | Purpose |
|---|---|
| `action.yml` | Composite GitHub Action manifest at repo root |
| `tools/matcher.py` | Match a `ComponentRef` against a set of advisories |
| `tools/sarif.py` | Render findings as SARIF v2.1.0 |
| `tools/report.py` | Human-readable terminal report (table) + GitHub annotations (gated on `GITHUB_ACTIONS`) |
| `tools/advisory_source.py` | Resolve `--advisories`: explicit flag → `./advisories` → `~/.cache/asve/all.zip` (auto-fetch + cache) |
| `tools/scan.py` | End-to-end CLI: parse → match → report |
| `tests/test_matcher.py` | Range and identity matching tests |
| `tests/test_sarif.py` | SARIF schema-shape tests |
| `tests/test_report.py` | Terminal report + GitHub-annotation gating tests |
| `tests/test_advisory_source.py` | Source resolution + cache-vs-fetch tests |
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

## Task 4b: Human-readable terminal report; gate GitHub annotations

The CLI is a first-class V0 deliverable, so the terminal experience matters. Refactor the existing GitHub-annotation emission into a `tools/report.py` module, add a human-readable findings table, and only emit GitHub annotations when running inside Actions (`GITHUB_ACTIONS=true`).

**Files:**
- Create: `tools/report.py`
- Create: `tests/test_report.py`
- Modify: `tools/scan.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report.py
import os

import pytest
from click.testing import CliRunner

from tools.component_ref import ComponentRef
from tools.matcher import Finding
from tools.report import emit_github_annotations, emit_terminal_report

ADVISORY_INDEX = {
    "ASVE-2026-0001": {
        "summary": "Command injection in @cyanheads/git-mcp-server",
        "severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N/.../SA:N"}],
    }
}


def make_finding(advisory_id="ASVE-2026-0001", confidence="high") -> Finding:
    ref = ComponentRef(ecosystem="npm", name="@cyanheads/git-mcp-server", version="1.1.0",
                       source_manifest="package.json", source_locator="dependencies")
    return Finding(advisory_id=advisory_id, component=ref, confidence=confidence,
                   reason=f"{ref.name}@{ref.version} matches {advisory_id}")


def test_terminal_report_renders_table(capsys):
    emit_terminal_report([make_finding()], ADVISORY_INDEX)
    out = capsys.readouterr().out
    assert "ASVE-2026-0001" in out
    assert "@cyanheads/git-mcp-server" in out
    assert "1.1.0" in out
    assert "package.json" in out


def test_terminal_report_no_findings(capsys):
    emit_terminal_report([], ADVISORY_INDEX)
    out = capsys.readouterr().out
    assert "no findings" in out.lower()


def test_github_annotations_emit_when_in_actions(capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    emit_github_annotations([make_finding()])
    out = capsys.readouterr().out
    assert "::error" in out
    assert "ASVE-2026-0001" in out


def test_github_annotations_silent_when_not_in_actions(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    emit_github_annotations([make_finding()])
    out = capsys.readouterr().out
    assert out == ""
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_report.py -v`
Expected: fails — `tools.report` does not exist.

- [ ] **Step 3: Implement `tools/report.py`**

```python
"""Output sinks for asve-scan findings."""
from __future__ import annotations

import os

import click

from tools.matcher import Finding


def _severity_style(confidence: str) -> str:
    if confidence == "high":
        return click.style("high", fg="red", bold=True)
    if confidence == "low":
        return click.style("low", fg="yellow")
    return click.style(confidence, fg="cyan")


def emit_terminal_report(findings: list[Finding], advisory_index: dict[str, dict]) -> None:
    """Human-readable findings table for a developer terminal."""
    if not findings:
        click.echo(click.style("no findings", fg="green"))
        return

    headers = ("ADVISORY", "SEVERITY", "COMPONENT", "VERSION", "LOCATION")
    rows = []
    for f in findings:
        component = f.component.name or f.component.component_identity or "-"
        version = f.component.version or "-"
        location = f"{f.component.source_manifest}::{f.component.source_locator}"
        rows.append((f.advisory_id, _severity_style(f.confidence), component, version, location))

    widths = [
        max(len(headers[i]), max(len(_strip_ansi(r[i])) for r in rows))
        for i in range(len(headers))
    ]

    def fmt(values, color: bool = False) -> str:
        return "  ".join(
            v.ljust(widths[i] + (len(v) - len(_strip_ansi(v)) if color else 0))
            for i, v in enumerate(values)
        )

    click.echo(click.style(fmt(headers), bold=True))
    click.echo("-" * sum(widths) + "-" * 8)
    for row in rows:
        click.echo(fmt(row, color=True))

    high_count = sum(1 for f in findings if f.confidence == "high")
    summary = f"\n{len(findings)} finding(s); {high_count} high-confidence"
    click.echo(click.style(summary, bold=True))


def emit_github_annotations(findings: list[Finding]) -> None:
    """GitHub workflow annotation lines, only when running inside Actions."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for f in findings:
        kind = "error" if f.confidence == "high" else "warning"
        click.echo(
            f"::{kind} file={f.component.source_manifest},title={f.advisory_id}::"
            f"{f.reason}"
        )


def _strip_ansi(s: str) -> str:
    """Remove ANSI color escape sequences for width calculation."""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)
```

- [ ] **Step 4: Refactor `tools/scan.py` to use `report.py`**

Remove the local `emit_github_annotations` function from `tools/scan.py` (it lives in `tools/report.py` now). At the top of `tools/scan.py`:

```python
from tools.report import emit_github_annotations, emit_terminal_report
```

Replace the bottom of `main` (the section that handled output and exit codes) with:

```python
    advisory_index = {a["id"]: a for a in corpus}

    if sarif:
        sarif_doc = to_sarif(findings, advisory_index)
        sarif.write_text(json.dumps(sarif_doc, indent=2))
        click.echo(f"sarif: wrote {sarif}", err=True)

    emit_terminal_report(findings, advisory_index)
    emit_github_annotations(findings)

    if not findings:
        sys.exit(0)

    high_count = sum(1 for f in findings if f.confidence == "high")
    if fail_on == "none":
        sys.exit(0)
    if fail_on == "high" and high_count == 0:
        sys.exit(0)
    sys.exit(1)
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/test_report.py tests/test_scan.py -v`
Expected: all pass. (The `test_scan.py` tests still pass because `emit_terminal_report` is additive and `emit_github_annotations` no-ops outside `GITHUB_ACTIONS=true`.)

- [ ] **Step 6: Commit**

```bash
git add tools/report.py tools/scan.py tests/test_report.py
git commit -m "feat: human-readable terminal report; gate GitHub annotations on GITHUB_ACTIONS"
```

---

## Task 4c: Smart defaults for `--target` and `--advisories` (auto-fetch + cache)

For the CLI to "just work" locally, `asve-scan .` should run without spelling out `--target` or `--advisories`. Resolve `--advisories` in this order: explicit flag → `./advisories` if present → `~/.cache/asve/all.zip` (auto-fetched from `https://asve.dev/all.zip` and cached). Provide `--no-fetch` to opt out of the network step.

**Files:**
- Create: `tools/advisory_source.py`
- Create: `tests/test_advisory_source.py`
- Modify: `tools/scan.py`
- Modify: `tests/test_scan.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_advisory_source.py
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.advisory_source import (
    DEFAULT_REMOTE_URL,
    AdvisorySourceError,
    resolve_advisories,
)


def test_explicit_path_wins(tmp_path):
    explicit = tmp_path / "advisories"
    explicit.mkdir()
    (explicit / "ASVE-2026-0001.yaml").write_text("id: ASVE-2026-0001\n")
    resolved = resolve_advisories(explicit, no_fetch=False, cache_dir=tmp_path / "cache")
    assert resolved == explicit


def test_local_advisories_dir_used_when_no_flag(tmp_path, monkeypatch):
    cwd = tmp_path / "project"
    (cwd / "advisories").mkdir(parents=True)
    (cwd / "advisories" / "ASVE-2026-0001.yaml").write_text("id: ASVE-2026-0001\n")
    monkeypatch.chdir(cwd)
    resolved = resolve_advisories(None, no_fetch=False, cache_dir=tmp_path / "cache")
    assert resolved.resolve() == (cwd / "advisories").resolve()


def test_no_fetch_with_no_local_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AdvisorySourceError):
        resolve_advisories(None, no_fetch=True, cache_dir=tmp_path / "cache")


def test_fetches_and_caches_when_no_local(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / "cache"

    def fake_download(url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w") as zf:
            zf.writestr("advisories/2026/ASVE-2026-0001.yaml",
                        "id: ASVE-2026-0001\n")

    with patch("tools.advisory_source._download", side_effect=fake_download):
        resolved = resolve_advisories(None, no_fetch=False, cache_dir=cache_dir)

    assert (resolved / "2026" / "ASVE-2026-0001.yaml").is_file()


def test_cached_zip_reused_on_second_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / "cache"

    def fake_download(url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w") as zf:
            zf.writestr("advisories/2026/ASVE-2026-0001.yaml",
                        "id: ASVE-2026-0001\n")

    with patch("tools.advisory_source._download", side_effect=fake_download) as dl:
        resolve_advisories(None, no_fetch=False, cache_dir=cache_dir)
        resolve_advisories(None, no_fetch=False, cache_dir=cache_dir)

    assert dl.call_count == 1   # second call hits cache
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_advisory_source.py -v`
Expected: fails — `tools.advisory_source` does not exist.

- [ ] **Step 3: Implement `tools/advisory_source.py`**

```python
"""Resolve --advisories: explicit flag → ./advisories → cached static export."""
from __future__ import annotations

import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

DEFAULT_REMOTE_URL = "https://asve.dev/all.zip"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "asve"


class AdvisorySourceError(RuntimeError):
    pass


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=30) as response, target.open("wb") as out:
        out.write(response.read())


def _extract(zip_path: Path, dest: Path) -> Path:
    """Extract zip and return the path to the contained advisories directory."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    advisories = dest / "advisories"
    if not advisories.is_dir():
        raise AdvisorySourceError(
            f"expected 'advisories/' inside {zip_path}; got {sorted(p.name for p in dest.iterdir())}"
        )
    return advisories


def resolve_advisories(
    explicit: Optional[Path],
    *,
    no_fetch: bool,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    remote_url: str = DEFAULT_REMOTE_URL,
) -> Path:
    """Return a path containing advisory YAMLs.

    Resolution order:
        1. explicit (when given) — must exist.
        2. ./advisories — if present in the current working directory.
        3. cached extract of remote_url under cache_dir (only when no_fetch is False).
    """
    if explicit is not None:
        if not explicit.exists():
            raise AdvisorySourceError(f"--advisories path does not exist: {explicit}")
        return explicit

    local = Path("advisories")
    if local.is_dir():
        return local

    if no_fetch:
        raise AdvisorySourceError(
            "no --advisories given, no ./advisories directory, and --no-fetch was set"
        )

    zip_path = cache_dir / "all.zip"
    extracted = cache_dir / "extracted"
    if not zip_path.is_file():
        _download(remote_url, zip_path)
    if not (extracted / "advisories").is_dir():
        _extract(zip_path, extracted)
    return extracted / "advisories"
```

- [ ] **Step 4: Wire into `tools/scan.py`**

In `tools/scan.py`, replace the `--target` and `--advisories` decorators with:

```python
@click.command()
@click.argument("target", type=click.Path(exists=True, file_okay=False, path_type=Path),
                default=".")
@click.option("--advisories", type=click.Path(path_type=Path), default=None,
              help="ASVE advisories directory. Defaults to ./advisories or the cached static export.")
@click.option("--no-fetch", is_flag=True, default=False,
              help="Do not fetch the remote static export when no local advisories are found.")
@click.option("--sarif", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Write SARIF v2.1.0 to this path.")
@click.option("--fail-on", type=click.Choice(["high", "any", "none"]), default="any",
              show_default=True, help="Exit non-zero when findings of this severity are present.")
def main(target: Path, advisories: Path | None, no_fetch: bool,
         sarif: Path | None, fail_on: str) -> None:
    """Scan TARGET for components matching ASVE advisories."""
    from tools.advisory_source import resolve_advisories
    advisories_path = resolve_advisories(advisories, no_fetch=no_fetch)

    refs = parse_repo(target)
    corpus = load_corpus(advisories_path)
    findings = match(refs, corpus)

    # ... rest of main unchanged from Task 4b ...
```

`target` is now a positional argument with a default of `.` so `asve-scan` (no args) means `asve-scan .`.

- [ ] **Step 5: Update existing scan tests for the new positional argument**

In `tests/test_scan.py`, the existing tests pass `--target` explicitly; both styles still work because `--target` is no longer a flag. Update the tests to use the positional form:

```python
def test_scan_finds_exposed_mcp(tmp_path):
    sarif_out = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(main, [
        str(FIXTURES / "repos" / "exposed-mcp"),
        "--advisories", str(REPO_ROOT / "advisories"),
        "--sarif", str(sarif_out),
    ])
    assert result.exit_code == 1, result.output
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
        str(clean),
        "--advisories", str(REPO_ROOT / "advisories"),
        "--sarif", str(sarif_out),
    ])
    assert result.exit_code == 0, result.output
```

Add one more test exercising the bare-defaults form:

```python
def test_scan_defaults_to_cwd_with_local_advisories(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "advisories" / "2026").mkdir(parents=True)
    (project / "advisories" / "2026" / "ASVE-2026-0001.yaml").write_text(
        (REPO_ROOT / "advisories" / "2026" / "ASVE-2026-0001.yaml").read_text()
    )
    (project / "package.json").write_text(
        '{"name":"clean","version":"0","dependencies":{}}'
    )
    monkeypatch.chdir(project)
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code == 0, result.output
```

- [ ] **Step 6: Run all scan + advisory-source tests**

Run: `uv run pytest tests/test_scan.py tests/test_advisory_source.py -v`
Expected: all pass.

- [ ] **Step 7: Manual smoke check**

```bash
cd /path/to/some/repo/with/mcp.json
uv run asve-scan         # uses cwd, fetches advisories on first run, caches them
```

Expected: terminal table of findings (or "no findings" in green); cache populated under `~/.cache/asve/`.

- [ ] **Step 8: Commit**

```bash
git add tools/advisory_source.py tools/scan.py tests/test_advisory_source.py tests/test_scan.py
git commit -m "feat: smart defaults for asve-scan (cwd + auto-fetch advisories)"
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
