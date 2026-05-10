# ASVE

**Agent Stack Vulnerabilities and Exposures** — an open, OSV-compatible
advisory database for AI agent infrastructure: plugins, MCP servers,
skills, agent frameworks, model proxies, and runtime components.

> Open advisories for agent stack security.

## Why ASVE

Traditional Software Composition Analysis (SCA) reads `package.json`
and lockfiles. Agents install components a different way: an MCP
server invoked from `mcp.json` via `uvx package==1.4.0`, a Claude
Code plugin declared in `.claude-plugin/plugin.json`, a skill bundle
referenced by stable identifier. None of those manifests are parsed
by Snyk, Dependabot, or OSV-Scanner today.

ASVE fills two gaps:

1. **Manifest coverage** for agent-installation files SCA tools
   don't parse — `mcp.json`, `.mcp.json`,
   `claude_desktop_config.json`, `.claude-plugin/plugin.json`,
   `.claude/settings.json`, `pyproject.toml`, `package.json`.
2. **Agent-context metadata** layered on top of existing
   CVE/GHSA/OSV records: `component_type`, `surfaces`,
   `agent_impact` (e.g., `repo_write`, `credential_exfiltration`,
   `tool_hijack`), and OWASP Agentic Top 10 (ASI) category mapping.

ASVE aliases upstream identifiers wherever they exist; it does not
duplicate authority. The wedge is the agent-stack overlay and the
manifest parsers, not a parallel CVE database.

## Quickstart

Two ways to run the scanner. Both produce SARIF v2.1.0 output and
the same set of findings.

### GitHub Action

Add to `.github/workflows/asve.yml`:

```yaml
name: ASVE
on: [push, pull_request]
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: open-agent-security/asve@v1
        with:
          fail-on: high           # high | any | none (default: any)
          # target: .             # path to scan (default: workspace)
          # sarif: results.sarif  # output path (default: asve-results.sarif)
```

Findings appear as GitHub annotations on the PR. With GitHub
Advanced Security, upload the SARIF to the Security tab via
`github/codeql-action/upload-sarif@v3`.

### Standalone CLI

The scanner is a normal Python package; run it against any local
checkout without GitHub Actions.

```bash
git clone https://github.com/open-agent-security/asve.git
cd asve
uv sync
uv run asve-scan \
    --target /path/to/your/repo \
    --advisories advisories/ \
    --sarif results.sarif \
    --fail-on any
```

Or via `uvx`, which clones, builds, and runs in one shot (no manual
checkout):

```bash
uvx --from git+https://github.com/open-agent-security/asve asve-scan \
    --target /path/to/your/repo \
    --advisories advisories/ \
    --sarif results.sarif
```

`asve-scan --help` lists all options. Exit codes: `0` clean (or
findings below `--fail-on` threshold), `1` findings at or above the
threshold.

## How it works

```
   Your repo                    ASVE corpus
       |                             |
       v                             v
  Manifest parsers  --->  Three-tier matcher  --->  SARIF + GitHub annotations
  (package.json,          (high / low /
   mcp.json, ...)          unknown confidence)
```

1. **Parse** every supported manifest under `--target`. Each parser
   emits component identifiers — standard PURLs (`pkg:npm/...`,
   `pkg:pypi/...`) where possible, ASVE-native identifiers
   (`mcp-stdio/uvx-unpinned:<package>`) where standard PURLs don't
   apply.
2. **Match** each identifier against advisories under
   `--advisories/`. Confidence tiers:
   - **high** — concrete pinned version inside an OSV ECOSYSTEM
     range (`introduced` / `fixed` / `last_affected` / `limit`).
   - **low** — version present but unparseable (e.g., `^1.0.0`).
   - **unknown** — unpinned manifest reference (e.g., `npx pkg`,
     `uvx pkg`) that names a package with a known advisory.
3. **Emit** SARIF v2.1.0 (severity mapped from confidence) and
   GitHub annotations for the PR.

## What gets scanned

| Manifest | Detects | Identifier emitted |
|---|---|---|
| `package.json` | npm dependencies (deps + devDeps) | `pkg:npm/<name>@<version>` |
| `pyproject.toml` | PEP 621 deps, optional-deps, PEP 735 dependency-groups | `pkg:pypi/<name>@<version>` |
| `mcp.json` / `.mcp.json` / `claude_desktop_config.json` | MCP server launches via `npx`, `uvx`, `python -m`, etc. | PURL when pinned; `mcp-stdio/...` otherwise |
| `.claude-plugin/plugin.json` | Claude Code plugin identity | `claude-plugin/<author>/<name>@<version>` |
| `.claude/settings.json` | Installed plugin enumeration | same as plugin manifest |

Cursor and Windsurf manifests are deferred to V1.

## Schema and IDs

- **ID format**: `ASVE-YYYY-NNNN` (single namespace).
- **Type-tagged records**: `type: vulnerability` is the only public
  V0 record type. `type: exposure` and `type: config` are reserved
  in the schema for V1.
- **Severity**: CVSS v4 base + environmental.
- **Category**: OWASP Agentic Top 10 (`asi01`–`asi10`).
- **Aliasing**: every record aliases existing CVE/GHSA/OSV
  identifiers where available. ASVE adds the agent-context overlay;
  it does not duplicate upstream authority.

Sample advisory:
[`advisories/2026/ASVE-2026-0001.yaml`](advisories/2026/ASVE-2026-0001.yaml).
Schema source of truth:
[`schema/asve.schema.json`](schema/asve.schema.json).

## Status

V0, in development. See
[`docs/specs/asve-v0-design.md`](docs/specs/asve-v0-design.md) for
the canonical V0 design and [`docs/plans/`](docs/plans/) for
implementation plans.

## License

- **Code**: [Apache License 2.0](LICENSE).
- **Advisory data**: [CC-BY-4.0](LICENSE-DATA) (matches OSV.dev).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the advisory authoring
guide, linter discipline, ID reservation flow, and PR workflow.

## Coordinated disclosure

ASVE follows the [OpenSSF coordinated disclosure
guidance](https://openssf.org/) with project-specific defaults
documented in [`docs/disclosure-policy.md`](docs/disclosure-policy.md).
Report security issues per that policy; do not file public issues
for unembargoed vulnerabilities.
