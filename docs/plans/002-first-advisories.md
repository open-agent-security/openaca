# 002 — First V0 Advisories and OSV Importer

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the first 5 V0 advisories under `advisories/2026/` — four straightforward CVE/GHSA aliases for known MCP server vulnerabilities, plus one *enriched* record that demonstrates OpenACA catches the same upstream vuln via agent-installation-manifest detection rather than lockfile detection. Add an OSV importer (`tools/import_from_osv.py`) that produces OpenACA skeletons from upstream OSV records.

**Architecture:** The importer fetches an OSV record from `osv.dev` (or accepts a JSON path), maps it into OpenACA's schema, and emits a YAML skeleton. A human author then fills in `database_specific.openaca` (component_type, surfaces, agent_impact, OWASP ASI mapping). Advisories alias their upstream IDs and add agent-context overlay; one advisory carries an additional `database_specific.openaca.detection_hints` block describing the manifest pattern that surfaces it.

**Tech Stack:** Python 3.11+, `requests` for OSV fetch (lazy import; only required when `--fetch` is used), Click, PyYAML, the linter from Plan 001.

**Depends on:** 001 (schema, linter, ID reservation, ADRs).

---

## File structure

| File | Purpose |
|---|---|
| `LICENSE-DATA` | CC-BY-4.0 text covering the advisory corpus |
| `tools/import_from_osv.py` | OSV → OpenACA skeleton generator |
| `tests/test_import_from_osv.py` | Importer tests (golden-file mapping) |
| `tests/fixtures/osv/ghsa-3q26-f695-pp76.json` | Captured OSV record for tests |
| `advisories/2026/CVE-2026-0001.yaml` | `@cyanheads/git-mcp-server` alias |
| `advisories/2026/CVE-2026-0002.yaml` | `mcp-remote` alias |
| `advisories/2026/CVE-2026-0003.yaml` | `@akoskm/create-mcp-server-stdio` alias (enriched) |
| `advisories/2026/CVE-2026-0004.yaml` | `aws-mcp-server` alias |
| `advisories/2026/CVE-2026-0005.yaml` | `serverless-mcp-server` alias |

CVE-2026-0003 is the **enriched** record per the V0 spec (§8 of `docs/specs/openaca-v0-design.md`): same upstream CVE/GHSA, but the OpenACA record adds `detection_hints` that the reference Action will use to flag installations declared in `mcp.json` rather than `package.json`.

---

## Task 1: CC-BY-4.0 license for advisory data

**Files:**
- Create: `LICENSE-DATA`
- Modify: `README.md` (already references `LICENSE-DATA` from Plan 001 work)

- [x] **Step 1: Write `LICENSE-DATA`**

```text
OpenACA Advisory Data is licensed under the
Creative Commons Attribution 4.0 International License (CC-BY-4.0).

You are free to:
- Share — copy and redistribute the material in any medium or format.
- Adapt — remix, transform, and build upon the material for any purpose,
  even commercially.

Under the following terms:
- Attribution — You must give appropriate credit, provide a link to the
  license, and indicate if changes were made. You may do so in any
  reasonable manner, but not in any way that suggests the licensor
  endorses you or your use.

Full license text: https://creativecommons.org/licenses/by/4.0/legalcode

Attribution should reference: "OpenACA — Agent Stack Vulnerabilities and
Exposures, https://openaca.dev"
```

- [x] **Step 2: Commit**

```bash
git add LICENSE-DATA
git commit -m "docs: add CC-BY-4.0 license for advisory data"
```

---

## Task 2: OSV record fixture

**Files:**
- Create: `tests/fixtures/osv/ghsa-3q26-f695-pp76.json`

- [x] **Step 1: Capture a real OSV record verbatim**

Save this JSON (this is a faithful subset of the GHSA record; trim or expand if the live record differs at implementation time, but keep this snapshot for stable tests):

```json
{
  "schema_version": "1.7.5",
  "id": "GHSA-3q26-f695-pp76",
  "aliases": ["CVE-2025-53107"],
  "summary": "Command injection in @cyanheads/git-mcp-server",
  "details": "@cyanheads/git-mcp-server is vulnerable to command injection in several tools, caused by unsanitized use of input parameters within a child-process invocation, enabling an attacker to inject arbitrary system commands.",
  "published": "2025-09-01T12:00:00Z",
  "modified": "2025-09-15T12:00:00Z",
  "affected": [
    {
      "package": {
        "ecosystem": "npm",
        "name": "@cyanheads/git-mcp-server",
        "purl": "pkg:npm/%40cyanheads/git-mcp-server"
      },
      "ranges": [
        {
          "type": "ECOSYSTEM",
          "events": [
            {"introduced": "0"},
            {"fixed": "1.2.3"}
          ]
        }
      ]
    }
  ],
  "references": [
    {"type": "ADVISORY", "url": "https://github.com/advisories/GHSA-3q26-f695-pp76"}
  ]
}
```

- [x] **Step 2: Commit**

```bash
git add tests/fixtures/osv/ghsa-3q26-f695-pp76.json
git commit -m "test: capture OSV fixture for importer tests"
```

---

## Task 3: OSV importer — failing test

**Files:**
- Create: `tests/test_import_from_osv.py`

- [x] **Step 1: Write the test**

```python
# tests/test_import_from_osv.py
import json

import pytest
import yaml
from click.testing import CliRunner

from tools.import_from_osv import main, osv_to_openaca_skeleton


@pytest.fixture
def osv_record(fixtures_dir):
    return json.loads((fixtures_dir / "osv" / "ghsa-3q26-f695-pp76.json").read_text())


def test_skeleton_aliases_upstream_ids(osv_record):
    skeleton = osv_to_openaca_skeleton(osv_record, openaca_id="CVE-2026-0001")
    assert skeleton["id"] == "CVE-2026-0001"
    assert "GHSA-3q26-f695-pp76" in skeleton["aliases"]
    assert "CVE-2025-53107" in skeleton["aliases"]


def test_skeleton_carries_affected(osv_record):
    skeleton = osv_to_openaca_skeleton(osv_record, openaca_id="CVE-2026-0001")
    assert skeleton["affected"][0]["package"]["ecosystem"] == "npm"
    assert skeleton["affected"][0]["package"]["name"] == "@cyanheads/git-mcp-server"


def test_skeleton_includes_openaca_extension_placeholder(osv_record):
    skeleton = osv_to_openaca_skeleton(osv_record, openaca_id="CVE-2026-0001")
    openaca = skeleton["database_specific"]["openaca"]
    assert "component_type" in openaca
    # placeholder value the author replaces:
    assert openaca["component_type"] == "TODO"


def test_cli_writes_yaml(tmp_path, fixtures_dir):
    src = fixtures_dir / "osv" / "ghsa-3q26-f695-pp76.json"
    dst = tmp_path / "CVE-2026-0001.yaml"
    runner = CliRunner()
    result = runner.invoke(main, ["--osv-file", str(src), "--openaca-id", "CVE-2026-0001",
                                  "--out", str(dst)])
    assert result.exit_code == 0, result.output
    advisory = yaml.safe_load(dst.read_text())
    assert advisory["id"] == "CVE-2026-0001"
```

- [x] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_import_from_osv.py -v`
Expected: fails — module does not exist yet.

---

## Task 4: OSV importer — implementation

**Files:**
- Create: `tools/import_from_osv.py`

- [x] **Step 1: Implement the importer**

```python
"""Generate an OpenACA advisory skeleton from an upstream OSV record."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import click
import yaml


def osv_to_openaca_skeleton(osv: dict, openaca_id: str) -> dict:
    """Map an OSV record into an OpenACA skeleton (TODOs for human-author fields)."""
    aliases: list[str] = []
    if osv.get("id"):
        aliases.append(osv["id"])
    aliases.extend(a for a in osv.get("aliases") or [] if a not in aliases)

    skeleton: dict = {
        "schema_version": osv.get("schema_version", "1.7.5"),
        "id": openaca_id,
        "type": "vulnerability",
        "aliases": aliases,
        "summary": osv.get("summary", "TODO"),
        "details": osv.get("details", "TODO"),
        "published": osv.get("published", "TODO"),
        "modified": osv.get("modified", "TODO"),
        "affected": copy.deepcopy(osv.get("affected") or []),
        "references": copy.deepcopy(osv.get("references") or []),
        "database_specific": {
            "openaca": {
                "component_type": "TODO",
                "surfaces": [],
                "agent_impact": {
                    "repo_read": False,
                    "repo_write": False,
                    "credential_exfiltration": False,
                    "tool_hijack": False,
                    "memory_poisoning": False,
                    "pr_manipulation": False,
                    "code_execution": False,
                },
                "owasp_agentic_top10": [],
                "evidence_level": "likely",
            }
        },
    }
    return skeleton


def fetch_osv(osv_id: str) -> dict:
    """Fetch a single OSV record. Lazy-imports requests to keep the dep optional for non-fetch use."""
    import requests  # type: ignore

    response = requests.get(f"https://api.osv.dev/v1/vulns/{osv_id}", timeout=15)
    response.raise_for_status()
    return response.json()


@click.command()
@click.option("--osv-id", help="Fetch this OSV/GHSA/CVE ID from osv.dev.")
@click.option("--osv-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Read OSV JSON from this file instead of fetching.")
@click.option("--openaca-id", required=True, help="Target OpenACA-YYYY-NNNN identifier.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True,
              help="Write the OpenACA YAML skeleton to this path.")
def main(osv_id: str | None, osv_file: Path | None, openaca_id: str, out: Path) -> None:
    """Generate an OpenACA advisory skeleton from an OSV record."""
    if not osv_id and not osv_file:
        raise click.UsageError("specify --osv-id or --osv-file")
    if osv_file:
        osv = json.loads(osv_file.read_text())
    else:
        osv = fetch_osv(osv_id)
    skeleton = osv_to_openaca_skeleton(osv, openaca_id=openaca_id)
    out.write_text(yaml.safe_dump(skeleton, sort_keys=False))
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
```

- [x] **Step 2: Register console script**

Add to `pyproject.toml` under `[project.scripts]`:

```toml
openaca-import-osv = "tools.import_from_osv:main"
```

Sync deps: `uv sync`

- [x] **Step 3: Run tests**

Run: `uv run pytest tests/test_import_from_osv.py -v`
Expected: all four pass.

- [x] **Step 4: Commit**

```bash
git add tools/import_from_osv.py pyproject.toml tests/test_import_from_osv.py
git commit -m "feat: OSV importer generates OpenACA skeletons"
```

---

## Task 5: Author CVE-2026-0001 (`@cyanheads/git-mcp-server`)

**Files:**
- Create: `advisories/2026/CVE-2026-0001.yaml`

- [x] **Step 1: Generate the skeleton**

```bash
mkdir -p advisories/2026
uv run openaca-import-osv --osv-file tests/fixtures/osv/ghsa-3q26-f695-pp76.json \
                --openaca-id CVE-2026-0001 \
                --out advisories/2026/CVE-2026-0001.yaml
```

- [x] **Step 2: Fill in the `database_specific.openaca` block**

Edit `advisories/2026/CVE-2026-0001.yaml`. Replace the `database_specific.openaca` block with:

```yaml
database_specific:
  openaca:
    component_type: mcp_server
    surfaces:
      - tool_invocation
      - stdio
      - repo_context
    agent_impact:
      repo_read: true
      repo_write: false
      credential_exfiltration: true
      tool_hijack: true
      memory_poisoning: false
      pr_manipulation: false
      code_execution: true
    owasp_agentic_top10:
      - asi02
      - asi05
    evidence_level: confirmed
```

- [x] **Step 3: Add a CVSS v4 severity entry**

Insert before `references:`:

```yaml
severity:
  - type: CVSS_V4
    score: "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
```

- [x] **Step 4: Lint**

Run: `uv run openaca lint advisories/2026/CVE-2026-0001.yaml`
Expected: exits 0.

- [x] **Step 5: Commit**

```bash
git add advisories/2026/CVE-2026-0001.yaml
git commit -m "advisory: CVE-2026-0001 — @cyanheads/git-mcp-server command injection"
```

---

## Task 6: Author CVE-2026-0002 (`mcp-remote`)

**Files:**
- Create: `tests/fixtures/osv/ghsa-6xpm-ggf7-wc3p.json`
- Create: `advisories/2026/CVE-2026-0002.yaml`

- [x] **Step 1: Capture the OSV record**

```json
{
  "schema_version": "1.7.5",
  "id": "GHSA-6xpm-ggf7-wc3p",
  "aliases": ["CVE-2025-6514"],
  "summary": "OS command injection in mcp-remote when connecting to untrusted MCP servers",
  "details": "mcp-remote is exposed to OS command injection when connecting to untrusted MCP servers due to unsanitized input from the authorization_endpoint response URL.",
  "published": "2025-09-10T12:00:00Z",
  "modified": "2025-09-20T12:00:00Z",
  "affected": [
    {
      "package": {"ecosystem": "npm", "name": "mcp-remote", "purl": "pkg:npm/mcp-remote"},
      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "0.4.5"}]}]
    }
  ],
  "references": [
    {"type": "ADVISORY", "url": "https://github.com/advisories/GHSA-6xpm-ggf7-wc3p"}
  ]
}
```

- [x] **Step 2: Generate skeleton and fill `database_specific.openaca`**

```bash
uv run openaca-import-osv --osv-file tests/fixtures/osv/ghsa-6xpm-ggf7-wc3p.json \
                --openaca-id CVE-2026-0002 \
                --out advisories/2026/CVE-2026-0002.yaml
```

Replace the openaca extension block with:

```yaml
database_specific:
  openaca:
    component_type: mcp_proxy
    surfaces:
      - network_egress
      - tool_invocation
    agent_impact:
      repo_read: false
      repo_write: false
      credential_exfiltration: true
      tool_hijack: true
      memory_poisoning: false
      pr_manipulation: false
      code_execution: true
    owasp_agentic_top10:
      - asi04
      - asi05
    evidence_level: confirmed
```

Add CVSS v4 severity:

```yaml
severity:
  - type: CVSS_V4
    score: "CVSS:4.0/AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:H/VA:L/SC:N/SI:N/SA:N"
```

- [x] **Step 3: Lint and commit**

```bash
uv run openaca lint advisories/2026/CVE-2026-0002.yaml
git add tests/fixtures/osv/ghsa-6xpm-ggf7-wc3p.json advisories/2026/CVE-2026-0002.yaml
git commit -m "advisory: CVE-2026-0002 — mcp-remote OS command injection"
```

---

## Task 7: Author CVE-2026-0003 (`@akoskm/create-mcp-server-stdio`) — enriched

This is the **enriched** record per the V0 spec. Same upstream CVE/GHSA, but OpenACA adds a `detection_hints` block that the reference Action (Plan 005) will use to flag installations declared via `mcp.json` `command:` strings rather than `package.json`.

**Files:**
- Create: `tests/fixtures/osv/ghsa-3ch2-jxxc-v4xf.json`
- Create: `advisories/2026/CVE-2026-0003.yaml`

- [x] **Step 1: Capture the OSV record**

```json
{
  "schema_version": "1.7.5",
  "id": "GHSA-3ch2-jxxc-v4xf",
  "aliases": ["CVE-2025-54994"],
  "summary": "Command injection in @akoskm/create-mcp-server-stdio",
  "details": "@akoskm/create-mcp-server-stdio is vulnerable to MCP server command injection through unsafe child-process invocation when handling tool inputs.",
  "published": "2025-10-05T12:00:00Z",
  "modified": "2025-10-15T12:00:00Z",
  "affected": [
    {
      "package": {"ecosystem": "npm", "name": "@akoskm/create-mcp-server-stdio",
                  "purl": "pkg:npm/%40akoskm/create-mcp-server-stdio"},
      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0.4"}]}]
    }
  ],
  "references": [
    {"type": "ADVISORY", "url": "https://github.com/advisories/GHSA-3ch2-jxxc-v4xf"}
  ]
}
```

- [x] **Step 2: Generate skeleton, fill extension, add detection_hints**

```bash
uv run openaca-import-osv --osv-file tests/fixtures/osv/ghsa-3ch2-jxxc-v4xf.json \
                --openaca-id CVE-2026-0003 \
                --out advisories/2026/CVE-2026-0003.yaml
```

Replace `database_specific.openaca` with:

```yaml
database_specific:
  openaca:
    component_type: mcp_server
    surfaces:
      - tool_invocation
      - stdio
    agent_impact:
      repo_read: false
      repo_write: false
      credential_exfiltration: false
      tool_hijack: true
      memory_poisoning: false
      pr_manipulation: false
      code_execution: true
    owasp_agentic_top10:
      - asi02
      - asi05
    evidence_level: confirmed
    detection_hints:
      manifests:
        - file: "mcp.json"
          path: "$.mcpServers.*.command"
          match_args:
            - "npx @akoskm/create-mcp-server-stdio"
            - "npx -y @akoskm/create-mcp-server-stdio"
        - file: ".claude-plugin/plugin.json"
          path: "$.mcpServers.*.command"
          match_args:
            - "npx @akoskm/create-mcp-server-stdio"
```

Add CVSS:

```yaml
severity:
  - type: CVSS_V4
    score: "CVSS:4.0/AV:L/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
```

- [x] **Step 3: Note that `detection_hints` is currently outside the V0 schema**

The V0 schema's `openaca_extension` block uses `additionalProperties` open by default for unknown keys. Verify the schema does not reject `detection_hints` — JSON Schema's default behavior under `properties` permits unknown keys. If your Plan 001 schema closed `additionalProperties` on `openaca_extension`, open it (set `"additionalProperties": true` explicitly or remove the closure).

If a schema change is required, do it under a separate commit:

```bash
# only if the schema needs adjusting:
# edit schema/openaca.schema.json — ensure openaca_extension allows additionalProperties
git add schema/openaca.schema.json
git commit -m "schema: allow forward-compat fields under database_specific.openaca"
```

- [x] **Step 4: Lint and commit advisory**

```bash
uv run openaca lint advisories/2026/CVE-2026-0003.yaml
git add tests/fixtures/osv/ghsa-3ch2-jxxc-v4xf.json advisories/2026/CVE-2026-0003.yaml
git commit -m "advisory: CVE-2026-0003 — enriched record with mcp.json detection_hints"
```

---

## Task 8: Author CVE-2026-0004 (`aws-mcp-server`)

**Files:**
- Create: `tests/fixtures/osv/ghsa-m4qw-j7mx-qv6h.json`
- Create: `advisories/2026/CVE-2026-0004.yaml`

- [x] **Step 1: Capture OSV fixture**

```json
{
  "schema_version": "1.7.5",
  "id": "GHSA-m4qw-j7mx-qv6h",
  "aliases": ["CVE-2025-5277"],
  "summary": "Command injection in aws-mcp-server",
  "details": "aws-mcp-server is vulnerable to command injection through unsafe handling of tool inputs that flow into shell-style invocation.",
  "published": "2025-08-12T12:00:00Z",
  "modified": "2025-08-30T12:00:00Z",
  "affected": [
    {
      "package": {"ecosystem": "PyPI", "name": "aws-mcp-server", "purl": "pkg:pypi/aws-mcp-server"},
      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "0.3.2"}]}]
    }
  ],
  "references": [
    {"type": "ADVISORY", "url": "https://github.com/advisories/GHSA-m4qw-j7mx-qv6h"}
  ]
}
```

- [x] **Step 2: Generate, fill extension, add severity**

```bash
uv run openaca-import-osv --osv-file tests/fixtures/osv/ghsa-m4qw-j7mx-qv6h.json \
                --openaca-id CVE-2026-0004 \
                --out advisories/2026/CVE-2026-0004.yaml
```

Extension:

```yaml
database_specific:
  openaca:
    component_type: mcp_server
    surfaces:
      - tool_invocation
      - stdio
    agent_impact:
      repo_read: false
      repo_write: false
      credential_exfiltration: true
      tool_hijack: true
      memory_poisoning: false
      pr_manipulation: false
      code_execution: true
    owasp_agentic_top10:
      - asi02
      - asi05
    evidence_level: confirmed
```

Severity:

```yaml
severity:
  - type: CVSS_V4
    score: "CVSS:4.0/AV:L/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
```

- [x] **Step 3: Lint and commit**

```bash
uv run openaca lint advisories/2026/CVE-2026-0004.yaml
git add tests/fixtures/osv/ghsa-m4qw-j7mx-qv6h.json advisories/2026/CVE-2026-0004.yaml
git commit -m "advisory: CVE-2026-0004 — aws-mcp-server command injection"
```

---

## Task 9: Author CVE-2026-0005 (`serverless-mcp-server`)

**Files:**
- Create: `tests/fixtures/osv/ghsa-rwc2-f344-q6w6.json`
- Create: `advisories/2026/CVE-2026-0005.yaml`

- [x] **Step 1: Capture OSV fixture**

```json
{
  "schema_version": "1.7.5",
  "id": "GHSA-rwc2-f344-q6w6",
  "aliases": ["CVE-2025-69256"],
  "summary": "Command injection in serverless MCP Server's list-projects tool",
  "details": "serverless MCP Server is vulnerable to command injection in the list-projects tool.",
  "published": "2025-11-02T12:00:00Z",
  "modified": "2025-11-12T12:00:00Z",
  "affected": [
    {
      "package": {"ecosystem": "npm", "name": "@serverless/mcp-server",
                  "purl": "pkg:npm/%40serverless/mcp-server"},
      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "0.5.1"}]}]
    }
  ],
  "references": [
    {"type": "ADVISORY", "url": "https://github.com/advisories/GHSA-rwc2-f344-q6w6"}
  ]
}
```

- [x] **Step 2: Generate, fill, lint, commit**

```bash
uv run openaca-import-osv --osv-file tests/fixtures/osv/ghsa-rwc2-f344-q6w6.json \
                --openaca-id CVE-2026-0005 \
                --out advisories/2026/CVE-2026-0005.yaml
```

Extension:

```yaml
database_specific:
  openaca:
    component_type: mcp_server
    surfaces:
      - tool_invocation
      - stdio
    agent_impact:
      repo_read: false
      repo_write: false
      credential_exfiltration: false
      tool_hijack: true
      memory_poisoning: false
      pr_manipulation: false
      code_execution: true
    owasp_agentic_top10:
      - asi02
      - asi05
    evidence_level: confirmed
```

Severity:

```yaml
severity:
  - type: CVSS_V4
    score: "CVSS:4.0/AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
```

- [x] **Step 3: Lint and commit**

```bash
uv run openaca lint advisories/2026/CVE-2026-0005.yaml
git add tests/fixtures/osv/ghsa-rwc2-f344-q6w6.json advisories/2026/CVE-2026-0005.yaml
git commit -m "advisory: CVE-2026-0005 — serverless MCP server command injection"
```

---

## Task 10: Lint the full corpus

- [x] **Step 1: Run linter against `advisories/`**

Run: `uv run openaca lint advisories/`
Expected: all five advisories report `ok`; exit 0.

- [x] **Step 2: Run reserve-id**

Run: `uv run openaca-reserve-id advisories/ --year 2026`
Expected: prints `CVE-2026-0006`.

- [x] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: every test passes.

---

## Verification

```bash
uv run openaca lint advisories/                         # exit 0
uv run openaca-reserve-id advisories/ --year 2026   # CVE-2026-0006
ls advisories/2026/                           # CVE-2026-0001.yaml … 0005.yaml
```

The corpus has:
- 4 records that traditional SCA tools also catch (lockfile-based, T1).
- 1 enriched record (CVE-2026-0003) with `detection_hints` for `mcp.json` and `.claude-plugin/plugin.json` — the agent-installation-manifest detection wedge.

---

## Self-review checklist

- [ ] **Five advisories** under `advisories/2026/`: 0001–0005, all `type: vulnerability`.
- [ ] **One enriched record** (0003) with `detection_hints` for non-lockfile manifests.
- [ ] **Aliases**: every record carries the upstream GHSA + CVE IDs.
- [ ] **CVSS v4** present on every record; vector parses.
- [ ] **OWASP ASI** mapping present on every record.
- [ ] **Linter passes** for the full corpus.
- [ ] **OSV importer** is tested with golden fixtures and a CLI roundtrip.
- [ ] **No commercial / competitor framing** in any advisory description or summary (CLAUDE.md scope rule).
