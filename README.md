# OpenACA - Open Agent Composition Analysis

Your dependency scanner can see your libraries. It usually cannot see the
plugins, MCP servers, skills, hooks, commands, and bundled dependencies that
compose your AI agent stack.

**OpenACA** is the open reference scanner for **Agent Composition Analysis
(ACA)**. It resolves stable identities for agent-stack components, builds an
Agent BOM, and matches those components against known security advisories
(OSV / GHSA / CVE / MAL).

> **Status:** V0 - early and evolving, available on
> [PyPI](https://pypi.org/project/openaca/).
> Start with the [Quickstart](#quickstart), then see the
> [docs](https://github.com/open-agent-security/openaca/blob/main/docs/README.md)
> for scan modes, coverage, CLI reference, and schema details.

## What OpenACA does

- **Identity Resolution** - normalize agent config such as `npx
  @scope/foo@1.4.0`, Git-backed skills, and plugin marketplace refs into
  stable component identities.
- **Composition Graph** - show how components enter the stack:
  host -> plugin -> skill / MCP server / hook -> dependency.
- **Risk Attribution** - trace a vulnerable dependency back to the plugin,
  skill, or MCP server that introduced it.
- **Advisory Intelligence** - match components against upstream OSV / GHSA /
  CVE / MAL records, enriched with agent-specific context where OpenACA has
  overlays.

OpenACA builds on upstream advisory records rather than minting its own IDs.
It contributes agent-component identity, composition, and context on top.

## Why OpenACA

Agent components are installed and activated through files most
general-purpose SCA scanners do not read: `mcp.json`, `.mcp.json`,
`claude_desktop_config.json`, `.claude-plugin/plugin.json`,
`.claude/settings.json`, `SKILL.md`, and related host-specific state.

ACA is the AI-agent analogue of Software Composition Analysis (SCA):

| Layer | Inventories | From these manifests |
|---|---|---|
| **SCA** | Your library tree | `package.json`, `requirements.txt`, lockfiles |
| **ACA** | Your agent composition | `mcp.json`, `.claude-plugin/plugin.json`, `.claude/settings.json`, marketplace registries |

The two work together. Use a general-purpose SCA scanner for normal software
dependencies, and OpenACA for the agent-installation surface those tools do not
parse today.

## Quickstart

### Install

```bash
curl -fsSL https://raw.githubusercontent.com/open-agent-security/openaca/main/scripts/install.sh | sh
```

This bootstraps [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
if needed, then installs OpenACA as an isolated CLI tool.

### Scan your endpoint

```bash
openaca scan endpoint
```

This scans your user-level Claude Code config (`~/.claude`). Add
`--project <path>` to include project-local skills, MCP servers, commands,
agents, hooks, and plugin manifests.

### Try it on a sample project

Drop a sample `mcp.json` in any empty directory and scan it:

```bash
mkdir openaca-demo && cd openaca-demo
cat > mcp.json <<'EOF'
{
  "mcpServers": {
    "git": {
      "command": "npx",
      "args": ["@cyanheads/git-mcp-server@1.1.0"]
    }
  }
}
EOF
openaca scan repo --target . --fail-on none
```

Expected output, abbreviated:

```text
Inventory

repo .
└── direct components/
    └── MCPs/ (1)
        └── @cyanheads/git-mcp-server@1.1.0 (stdio via npx) (from mcp.json)  [! GHSA-3q26-f695-pp76]

Findings

Found 1 vulnerability in 1 package.

@cyanheads/git-mcp-server 1.1.0
  location: mcp.json
  fix:      upgrade to >=2.1.5

  HIGH  GHSA-3q26-f695-pp76  fixed in 2.1.5  @cyanheads/git-mcp-server vulnerable to command injection in several tools  [osv.dev]

Next
  emit Agent BOM: openaca bom repo --target . --output openaca-bom.json
```

For clean scans, posture examples, and expected output, clone the
[openaca-demo](https://github.com/open-agent-security/openaca-demo) repo.

## Scan modes

OpenACA has two primary scan modes:

- `openaca scan repo` - review agent components declared in a repository,
  usually in CI or a PR check.
- `openaca scan endpoint` - review agent components installed on a machine,
  such as a developer laptop or managed runner.

Both modes produce inventory and findings. The mode tells you what observation
context the result came from: declared-in-source-control vs.
installed-on-this-machine.

See [Scan Modes](https://github.com/open-agent-security/openaca/blob/main/docs/concepts/scan-modes.md)
for the details, including `--project <path>`.

## GitHub Action

Add to `.github/workflows/openaca.yml`:

```yaml
name: OpenACA
on: [push, pull_request]
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: open-agent-security/openaca@v1
        with:
          fail-on: high           # high | any | none (default: any)
          # target: .             # path to scan (default: workspace)
          # sarif: results.sarif  # output path (default: openaca-results.sarif)
```

Findings appear as GitHub annotations on the PR. With GitHub Advanced
Security, upload the SARIF to the Security tab via
`github/codeql-action/upload-sarif@v3`.

## Claude Code plugin

Prefer staying inside Claude Code? The
[OpenACA plugin](https://github.com/open-agent-security/openaca-claude-plugin)
wraps the scanner in slash commands:

```text
/plugin marketplace add open-agent-security/openaca-claude-plugin
/plugin install openaca@openaca
```

- `/openaca:scan` - run an endpoint or repo scan
- `/openaca:bom` - generate an Agent BOM
- `/openaca:explain` - explain a finding in conversation
- `/openaca:triage` - guided review after agent config changes

The plugin is explicit-invocation only: no hooks, no background monitors, and
no modification of your Claude Code settings.

## Current coverage

OpenACA V0 focuses on declared and installed agent composition for Claude Code
and Claude-family filesystem conventions.

Today it reads:

- host-specific agent config such as `.claude/settings.json`, `.mcp.json`,
  `mcp.json`, `claude_desktop_config.json`, `installed_plugins.json`,
  `SKILL.md`, hooks, commands, and subagents;
- package manifests and lockfiles when they belong to agent components, such as
  dependencies bundled by a Claude Code plugin.

Use `--include-posture` to include configuration-hygiene findings such as
unpinned installs, insecure MCP endpoints, endpoint overrides, and MCP
auto-approval.

See [Coverage](https://github.com/open-agent-security/openaca/blob/main/docs/reference/coverage.md),
[CLI Reference](https://github.com/open-agent-security/openaca/blob/main/docs/reference/cli.md),
and [Posture Findings](https://github.com/open-agent-security/openaca/blob/main/docs/posture/README.md)
for the full details.

## Limitations

OpenACA V0 does not yet see:

- programmatic SDK configuration embedded directly in source code;
- non-Claude agent-host local state such as Codex CLI, Cursor, Windsurf, or VS
  Code agent-mode config;
- vulnerabilities for local-only or source-less components that do not provide
  a package, Git, or external match coordinate;
- live tool invocations or runtime blocking.

The Agent BOM format is pre-1.0. Field names, identities, and CLI output may
change before the first stable schema release.

## Docs

- [Getting Started](https://github.com/open-agent-security/openaca/blob/main/docs/getting-started.md)
- [Scan Modes](https://github.com/open-agent-security/openaca/blob/main/docs/concepts/scan-modes.md)
- [Identity Model](https://github.com/open-agent-security/openaca/blob/main/docs/concepts/identities.md)
- [CLI Reference](https://github.com/open-agent-security/openaca/blob/main/docs/reference/cli.md)
- [Coverage](https://github.com/open-agent-security/openaca/blob/main/docs/reference/coverage.md)
- [Overlay Reference](https://github.com/open-agent-security/openaca/blob/main/docs/reference/overlays.md)
- [Agent BOM Schema](https://github.com/open-agent-security/openaca/blob/main/docs/openaca-bom-schema.md)
- [Posture Findings](https://github.com/open-agent-security/openaca/blob/main/docs/posture/README.md)

## Status

V0, in development. See
[`docs/specs/openaca-thesis.md`](https://github.com/open-agent-security/openaca/blob/main/docs/specs/openaca-thesis.md)
for the thesis and V0 -> V1 roadmap,
[`docs/adrs/`](https://github.com/open-agent-security/openaca/blob/main/docs/adrs/INDEX.md)
for architecture decisions, and
[`docs/plans/`](https://github.com/open-agent-security/openaca/blob/main/docs/plans/README.md)
for implementation plans.

## Contributing

See [`CONTRIBUTING.md`](https://github.com/open-agent-security/openaca/blob/main/CONTRIBUTING.md)
for contribution guidance.

## Coordinated disclosure

OpenACA does not mint vulnerability IDs. Vulnerabilities in agent components
are filed upstream (CVE / GHSA / OSV / PYSEC / MAL); once an upstream record is
public, contribute an OpenACA overlay per
[`CONTRIBUTING.md`](https://github.com/open-agent-security/openaca/blob/main/CONTRIBUTING.md).

For security issues in **OpenACA's own code**, see
[`SECURITY.md`](https://github.com/open-agent-security/openaca/blob/main/SECURITY.md).
Do not file public issues for unembargoed vulnerabilities.

## License

- **Code**: [Apache License 2.0](https://github.com/open-agent-security/openaca/blob/main/LICENSE).
- **Overlay data** (YAML under `overlays/` and the static exports derived from
  them): [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)
  (CC-BY-4.0) - matches OSV.dev. Attribution: *OpenACA - Open Agent Composition
  Analysis, <https://openaca.dev>*.
