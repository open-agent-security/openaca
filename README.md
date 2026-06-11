# OpenACA — Open Agent Composition Analysis

**Agent Composition Analysis (ACA)** — a reference scanner that resolves
stable identities for the plugins, MCP servers, skills, hooks, and
dependencies that compose an AI agent, builds the composition graph, and
matches it against known security advisories (OSV / GHSA / CVE / MAL).

> Identity resolution and composition analysis for AI agent stacks.

> **Status:** V0 — early and evolving, available on
> [PyPI](https://pypi.org/project/openaca/).
> The fastest path is the [Quickstart](#quickstart) below; for worked
> examples with expected output, see the
> [openaca-demo](https://github.com/open-agent-security/openaca-demo) repo.

## What OpenACA does

OpenACA resolves stable identities for agent-stack components, builds a
composition graph across hosts, plugins, skills, MCP servers, hooks,
commands, and dependencies, then matches known advisories across that
graph.

- **Identity Resolution** — normalize messy agent config (an MCP server
  launched via `npx @scope/foo@1.4.0`, a skill at a git subpath, a
  plugin keyed `name@marketplace`) into stable, matchable component
  identities, including components that have no package coordinates.
- **Composition Graph** — show how components enter the agent stack:
  host → plugin → skill / MCP server / hook → dependency.
- **Risk Attribution** — when a component is vulnerable, trace it back
  through the graph to the plugin, skill, or MCP server that introduced
  it (a plugin is flagged when something it bundles is vulnerable).
- **Advisory Intelligence** — match graph components against upstream
  OSV / GHSA / CVE / MAL records at scan time, enriched with
  agent-specific context (impact, taxonomy, evidence level) where it
  changes how a record should be read.

OpenACA builds on upstream advisory records (OSV / GHSA / CVE / MAL)
rather than minting its own — it contributes the identity resolution,
composition graph, and agent-context enrichment on top.

## Why OpenACA (the category)

OpenACA is the open category and reference implementation for **Agent
Composition Analysis (ACA)**: identifying the versioned plugins, MCP
servers, skills, and framework components that make up an AI agent,
and matching them against known security records.

ACA is the AI-agent analogue of Software Composition Analysis (SCA):

| Layer | Inventories | From these manifests |
|---|---|---|
| **SCA** | Your library tree | `package.json`, `requirements.txt`, lockfiles |
| **ACA** | Your agent's composition | `mcp.json`, `.claude-plugin/plugin.json`, `.claude/settings.json`, marketplace registries |

The two stack — they answer different questions about different
artifacts. For general software dependency scans (your app's transitive
npm/PyPI tree, your container image, etc.), use a general-purpose SCA
scanner alongside OpenACA.

Traditional SCA tooling reads `package.json` and lockfiles. Agents
install components a different way: an MCP server invoked from
`mcp.json` via `uvx package==1.4.0`, a Claude Code plugin declared in
`.claude-plugin/plugin.json`, a skill bundle referenced by stable
identifier. Most general-purpose SCA scanners do not parse those
manifests today.

OpenACA's parsers cover the agent-installation files general-purpose SCA
tools don't read — `mcp.json`, `.mcp.json`, `claude_desktop_config.json`,
`.claude-plugin/plugin.json`, `.claude/settings.json` — and interpret
package manifests (`pyproject.toml`, `package.json`) when they belong to
agent components. That's what makes the identity resolution and
composition graph above possible.

## Two scan modes

OpenACA scans two distinct surfaces, named via explicit subcommands.
The same advisory matches in both, but the surface tells you *what
question you're asking*:

| Mode | Question | Audience | Where it runs |
|---|---|---|---|
| `openaca scan repo` | *"What agent components are declared in this repository?"* | AppSec / platform security | CI gate, PR check |
| `openaca scan endpoint` | *"What agent components are installed on this machine right now?"* | Endpoint security / IT | Developer laptop, CI runner, MDM-managed device |

What `repo` actually covers: (a) **committed project-host config** —
`.claude/settings.json`, `.claude/skills`, `.claude/commands`,
`.claude/agents`, etc., which describes what Claude Code will load
when run *in this repo*; and (b) **manifest-backed SDK config** like
a root `.mcp.json` an app loads via Claude Agent SDK's
`query({ options: { mcpConfig: "..." } })`. It does **not** cover
SDK-inline definitions (`query({ mcpServers: { ... } })`,
`Agent(tools=[...])`), tools registered programmatically, or anything
extracted from source code — those are V1, gated on SDK-aware
extraction. Treat `repo` findings as *declared* composition, not
deployed-app composition.

The same identifier (e.g., `@modelcontextprotocol/server-filesystem@1.0.0`)
means different things in each context — declared-in-source-control
exposure vs. installed-on-this-machine exposure. The scanner output
makes the distinction explicit.

Unpinned components (e.g. an MCP launched via `npx pkg@latest` with no
version) are inventoried but cannot be advisory-matched — OSV needs an
exact version. Lockfile-pinned transitive dependencies
(`package-lock.json`, `uv.lock`, `bun.lock`) carry exact versions and are
matched.

## Quickstart

Two ways to run the scanner. Both produce the same set of findings —
component manifests are read locally, then matched against live
advisories from OSV.dev (`api.osv.dev`). By default it prints text
output; add `--sarif <path>` when you want a SARIF v2.1.0 artifact.

### Install

```bash
curl -fsSL https://raw.githubusercontent.com/open-agent-security/openaca/main/scripts/install.sh | sh
```

This bootstraps [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
if it isn't already on your machine, then installs OpenACA as an
isolated CLI tool.

### Scan Your Endpoint

```bash
openaca scan endpoint
```

This scans your **user-level** Claude config (`~/.claude`). Skills, MCP
servers, and plugin manifests that live inside a project are opt-in —
add `--project <path>` (or `--project .` from inside the repo) to
include them.

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

Expected output:

```
Target
  host surface: repository
  path: .

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

Summary
  Scanned 1 manifest, 1 component · advisories: 1 · posture: skipped
  sources: osv.dev

Next
  emit Agent BOM: openaca bom repo --target . --output openaca-bom.json
```

For more scenarios (clean scan, configuration-hygiene checks via
`--include-posture`), clone the
[openaca-demo](https://github.com/open-agent-security/openaca-demo)
repo and try each of its fixtures.

### Standalone CLI

The scanner is a normal Python package; run it against any local
checkout. Two modes via subcommands.

**Pin a specific version (recommended for remote sync / MDM / CI):**

```bash
curl -fsSL https://raw.githubusercontent.com/open-agent-security/openaca/main/scripts/install.sh | OPENACA_VERSION=<version> sh
```

Pinning matters for reproducible deployments — every machine getting
"whatever latest was that day" is bad for debugging remote sync behavior.

**Manual install (if you prefer not to pipe to sh, or want to control
the path yourself):**

```bash
# With uv (recommended; handles Python version + isolation; uv's default
# resolution picks openaca's beta releases — no pre-release flag needed):
uv tool install openaca

# Or with pip (Python 3.11+ in your existing workflow; pip needs --pre
# while OpenACA is in beta):
pip install --pre openaca
```

(Avoid `uv … --prerelease allow` — it applies to the entire resolution
and can pull *dependencies* onto their pre-releases too. Pin a specific
build with `openaca==<version>` if you need to reproduce a bug report
against an exact version.)

**Install from source (for contributors):**

```bash
git clone https://github.com/open-agent-security/openaca.git
cd openaca
uv sync
```

Run the scanner:

```bash
# Repo mode: walks declared manifests in a code repo (today's behavior;
# what the GitHub Action uses).
openaca scan repo \
    --target /path/to/your/repo \
    --sarif results.sarif \
    --fail-on any

# Endpoint mode: install-state-aware scan of an installed Claude Code
# endpoint. Defaults to $CLAUDE_CONFIG_DIR (else ~/.claude) for user
# config. Project context is opt-in via --project.
openaca scan endpoint \
    --fail-on any

# Add project-local skills/MCPs/plugin manifests via --project.
# Pass `--project .` for the current directory:
openaca scan endpoint \
    --config-dir ~/.claude \
    --project /path/to/your/repo
```

A subcommand is required. Shared options (`-v`, `--fail-on`, `--sarif`,
`--format`, `--no-color`) can sit before or after the subcommand name —
`openaca scan -v repo --target X ...` is equivalent to
`openaca scan repo --target X ... -v`.

### Output formats

`openaca scan` emits three formats; pick with `--format`:

- **`text`** *(default)* — grouped human-readable output. One block per
  affected package, severity per finding, ANSI-colored when stdout is a
  TTY. Add `-v` for per-finding component/source/container context.
- **`github`** — GitHub workflow annotation lines (`::error file=...::`).
  Auto-selected when `GITHUB_ACTIONS=true` so the included Action keeps
  working without configuration. Use explicitly to emit annotations
  outside CI.
- **`json`** — structured per-finding records plus a `stats` block. For
  programmatic consumption.

`--sarif <path>` is orthogonal and writes a SARIF 2.1.0 artifact in
addition to the chosen stdout format. `--no-color` disables ANSI in text
output (color is also off automatically when stdout isn't a TTY).

JSON output uses one top-level `findings[]` array. Vulnerability entries
carry `finding_type: "vulnerability"` and posture entries carry
`finding_type: "posture"`. Each finding includes:

- `component` — the vulnerable or risky agent component being reported.
- `component.source` — the package/Git/source coordinate used for matching
  or explanation.
- `active_in` — runtime host IDs observed by the scanner, when known.
- `declared_by` — manifest, plugin, or lock entry that introduced the
  component.
- `component_path` — containment path such as `plugin -> mcp_server`.
- `matched_advisory` — advisory identity for vulnerability findings.

Overlay records remain advisory data. They do not store local scan
context such as `active_in`, `declared_by`, or `component_path`.

### GitHub Action

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

Findings appear as GitHub annotations on the PR. With GitHub
Advanced Security, upload the SARIF to the Security tab via
`github/codeql-action/upload-sarif@v3`.

`openaca scan --help` lists all options. Exit codes: `0` clean (or
findings below `--fail-on` threshold), `1` findings at or above the
threshold.

### Claude Code plugin

Prefer staying inside Claude Code? The
[OpenACA plugin](https://github.com/open-agent-security/openaca-claude-plugin)
wraps the scanner in four slash commands:

```text
/plugin marketplace add open-agent-security/openaca-claude-plugin
/plugin install openaca@openaca
```

- `/openaca:scan` — run an endpoint or repo scan
- `/openaca:bom` — generate an Agent BOM
- `/openaca:explain` — explain a finding in conversation
- `/openaca:triage` — guided review after agent config changes

The plugin is explicit-invocation only — no hooks, no background
monitors, no modification of your Claude Code settings.

### Posture findings (`--include-posture`)

A corpus-driven scan returns "no findings" on most clean repos and
endpoints. Pass `--include-posture` to also emit configuration-hygiene
checks: unpinned MCP/plugin installs, `http://` MCP endpoints, Claude API
endpoint overrides, and MCP auto-approval.
Posture findings carry
their own `standards{}` block (CWE / OpenSSF Scorecard / SLSA / OWASP
App / OWASP Agentic / OWASP MCP), render in their own section in text
output, share the JSON `findings[]` array, and emit as separate SARIF rules.
They never affect
`--fail-on` exit codes — they're signal, not gate. See
[`docs/posture/`](https://github.com/open-agent-security/openaca/blob/main/docs/posture/README.md) for the V0 rule list and
per-rule remediation pages.

Pass `-v` / `--verbose` for the per-manifest breakdown (repo mode) or
the resolved active-plugin tree (endpoint mode):

```text
# repo mode -v
loaded 6 OpenACA overlay(s)
loaded 1 OSV advisory record(s)
scanned 87 manifest(s), 70 component(s):
  external_plugins/discord/package.json — 2 component(s)
  external_plugins/fakechat/.mcp.json — 1 component(s)
  ...

# endpoint mode -v
loaded 6 OpenACA overlay(s)
loaded 1 OSV advisory record(s)
detected config_dir=/Users/.../.claude (mode=endpoint)
resolved 14 active plugin(s):
  claude-plugin/claude-plugins-official/supabase@0.1.6 (sha: <short>) [scope=user]
  claude-plugin/claude-plugins-official/superpowers@5.1.0 (sha: <short>) [scope=user]
  ...
```

Findings carry a `via <plugin>` annotation when discovered through an
active plugin (plans 008 and 009 populate this; plan 007 only emits
plugin-level components).

## How it works

```
   Your repo                    OSV.dev + OpenACA overlays
       |                             |
       v                             v
  Manifest parsers  --->  Three-tier matcher  --->  SARIF + GitHub annotations
  (package.json,          (high / low /
   mcp.json, ...)          unknown confidence)
```

1. **Parse** every supported manifest under `--target`. Each parser
   emits component identifiers — standard PURLs (`pkg:npm/...`,
   `pkg:pypi/...`) where possible, OpenACA-native identifiers
   (`mcp-server/<name>` for MCP servers without a package coordinate)
   where standard PURLs don't apply.
2. **Match** queryable PURLs against OSV.dev records, then merge
   OpenACA overlays from the bundled `overlays/` directory by alias-set
   intersection. Confidence tiers:
   - **high** — concrete pinned version inside an OSV ECOSYSTEM
     range (`introduced` / `fixed` / `last_affected` / `limit`).
   - **low** — version present but unparseable (e.g., `^1.0.0`).
   - **unknown** — unpinned manifest reference (e.g., `npx pkg`,
     `uvx pkg`) that names a package with a known advisory.
3. **Emit** SARIF v2.1.0 (severity mapped from confidence) and
   GitHub annotations for the PR.

## What gets scanned

OpenACA follows a tiered model loosely analogous to traditional SCA's
"lockfile > manifest > source code" hierarchy:

| Tier | What it reads | V0 status |
|---|---|---|
| **1. Declarative manifests** (host-specific) | `.claude/settings.json`, `.claude-plugin/plugin.json`, `mcp.json`, `.mcp.json`, `claude_desktop_config.json`, `installed_plugins.json` (endpoint mode), `SKILL.md`, `hooks/hooks.json`, `.claude/commands/*.md`, `.claude/agents/*.md` | ✅ V0 |
| **2. Dependency manifests** (universal) | `package.json`, `pyproject.toml`, lockfiles inside active plugins (plan 009) | ✅ V0 |
| **3. SDK-aware code extraction** (host-specific SAST-like) | parse `query({mcpServers: [...]})`, `Agent(tools=[...])`, etc. | ⏸ V1 |
| **4. Runtime attestation** | ask the deployed app what it loaded | ⏸ out of OpenACA scope; that's a deployment-side product layer |

OSV.dev is queried by default for versioned package refs. Network
failures are fail-soft: OpenACA still reports inventory and parse coverage,
but overlay-backed vulnerability matching needs upstream OSV records.

**Agent-composition scope.** Repo-mode dependency manifests
(`package.json`, `pyproject.toml`, `package-lock.json`, `uv.lock`) are
classified as **agent-dependency** only when co-located with a
`.claude-plugin/plugin.json` sibling — i.e., they declare the deps
*of a plugin's implementation*. Direct dep manifests in repos that
aren't plugins are classified as **software-dependency** and
suppressed from output (and from OSV.dev queries) — that's
general-purpose SCA territory, not ACA. Scan those with a
general-purpose SCA scanner instead. A non-empty repo with only
software-dependency refs produces an explicit footer rather than a
silent "no findings."

Per-parser detail. Rows such as `claude-plugin/...`, `skill/...`, and
`claude-hook/...` are graph occurrence identities. Match coordinates are
separate and exist only when the manifest or lock entry provides a package,
Git, or explicit external audit/registry coordinate.

| Manifest | Detects | Component/source identifier emitted |
|---|---|---|
| `package.json` | npm dependencies (deps + devDeps) | `pkg:npm/<name>@<version>` |
| `pyproject.toml` | PEP 621 deps, optional-deps, PEP 735 dependency-groups | `pkg:pypi/<name>@<version>` |
| `mcp.json` / `.mcp.json` / `claude_desktop_config.json` | MCP server launches via `npx`, `uvx`, `python -m`, etc. | PURL for package launches; graph identity plus install context for local/binary launches |
| `.claude-plugin/plugin.json` | Claude Code plugin identity | `claude-plugin/<name>` |
| `.claude/settings.json` | Enabled-plugin enumeration; direct `mcpServers`; direct `hooks` per scope | mixed (see surface-specific rows) |
| `installed_plugins.json` (endpoint mode) | Active plugins (resolved versions, gitCommitSha) | `claude-plugin/<marketplace>/<name>` when marketplace is known |
| `SKILL.md` (`.claude/skills/*/` or `<plugin>/skills/*/`) | Agent skills | `skill/<name>[@<metadata.version>]` |
| `hooks/hooks.json` (plugin) or `settings.json.hooks` (direct) | Hook entries by event + index | `claude-hook/<plugin>/<event>/<i>` (bundled) or `claude-hook/settings/<scope>/<event>/<i>` (direct) |
| `.claude/commands/*.md` and `<plugin>/commands/*.md` | Slash commands | `claude-command/<owner>/<name>` (owner = plugin or `repo`) |
| `.claude/agents/*.md` and `<plugin>/agents/*.md` | Subagents | `claude-agent/<owner>/<name>` |

## Limitations

What OpenACA V0 doesn't see:

- **Programmatic SDK configuration is invisible to repo mode.** Code
  that constructs agents with `query({ mcpServers: [...] })` (Claude
  Agent SDK) or `Agent(tools=[...], mcp_servers=[...])` (OpenAI Agents
  SDK) bypasses manifest scanning entirely. Manifest-backed paths like
  `query({ mcpConfig: ".mcp.json" })` *are* covered because `.mcp.json`
  is a parsed manifest; the inline / code-defined forms need Tier-3
  SDK-aware extraction (V1).
- **Repo mode is a survey of *declared* agent-component manifests,
  not a guarantee about what a deployed app loads.** A finding means
  "this manifest declares a vulnerable component"; whether the
  deployed application actually executes that component depends on
  runtime configuration we can't see from static files. Endpoint mode
  is closer to ground truth because it reads resolved install state.
- **`.claude/*` in repo mode describes project-host posture, not app
  runtime.** Files like `.claude/settings.json` and `.claude/commands`
  describe what Claude Code will load when a developer runs Claude Code
  *in this repo* — not what an agent application built from the repo
  uses at runtime. Useful for reviewing committed developer-agent
  posture; not a substitute for runtime composition analysis.
- **Repo mode is Claude-family-biased today.** Tier-1 declarative parsers
  cover Claude Code / Claude Agent SDK filesystem conventions. Cursor,
  Windsurf, Codex CLI, VS Code agent-mode, and OpenAI Agents SDK have
  their own conventions (or no conventions); those are V1 adapters.
- **Endpoint mode is Claude Code-specific.** It reads
  `~/.claude/installed_plugins.json` and friends. Codex CLI's
  `~/.codex/` and Cursor's local state will need their own resolvers.
- **Advisory matching needs matchable coordinates.** Vulnerability
  matching works for components with a package, Git, or known external
  match coordinate. Local-only skills and source-less components are
  inventory- and posture-only until a matchable coordinate exists.
- **No runtime observation or enforcement.** OpenACA inventories and
  assesses composition; it does not watch live tool invocations or
  block agent tool use at runtime. It's the map of what's installed,
  not a guardrail on what runs.
- **The Agent BOM format is pre-1.0.** Field names, identities, and CLI
  output may change before the first stable schema release.

## Overlay Schema

- **ID format**: upstream advisory ID, usually the OSV record ID
  (`GHSA-*`, `CVE-*`, `PYSEC-*`, etc.).
- **Aliases**: overlays list known equivalent IDs so they can merge
  with any OSV record whose alias set intersects.
- **Severity and fixes**: come from upstream OSV/GHSA/CVE records and
  are not duplicated in OpenACA overlays.
- **Taxonomies**: `database_specific.openaca.taxonomies` carries
  OpenACA-defined mappings such as OWASP Agentic Top 10 (`asi01`–`asi10`)
  and OWASP MCP Top 10 (`mcp01:2025`–`mcp10:2025`). CWE is not duplicated
  by default when upstream already provides it.
- **Agent context**: `database_specific.openaca` carries
  `component_type`, `surfaces`, `agent_impact`, and evidence metadata.

Sample overlay:
[`overlays/GHSA-3q26-f695-pp76.yaml`](https://github.com/open-agent-security/openaca/blob/main/overlays/GHSA-3q26-f695-pp76.yaml).
Schema source of truth:
[`schema/openaca.schema.json`](https://github.com/open-agent-security/openaca/blob/main/schema/openaca.schema.json).

## Status

V0, in development. See [`docs/specs/openaca-thesis.md`](https://github.com/open-agent-security/openaca/blob/main/docs/specs/openaca-thesis.md)
for what OpenACA is and the V0 → V1 roadmap,
[`docs/adrs/0009-overlay-only-v0.md`](https://github.com/open-agent-security/openaca/blob/main/docs/adrs/0009-overlay-only-v0.md)
for the overlay-only architecture, and [`docs/plans/`](https://github.com/open-agent-security/openaca/tree/main/docs/plans/) for
implementation plans.

## License

- **Code**: [Apache License 2.0](https://github.com/open-agent-security/openaca/blob/main/LICENSE).
- **Overlay data** (YAML under `overlays/` and the static exports derived
  from them): [Creative Commons Attribution 4.0
  International](https://creativecommons.org/licenses/by/4.0/) (CC-BY-4.0)
  — matches OSV.dev. Attribution: *OpenACA — Open Agent Composition
  Analysis, <https://openaca.dev>*.

## Contributing

See [`CONTRIBUTING.md`](https://github.com/open-agent-security/openaca/blob/main/CONTRIBUTING.md) for contribution guidance.

## Coordinated disclosure

OpenACA does not mint vulnerability IDs. Vulnerabilities in agent
components are filed upstream (CVE / GHSA / OSV / PYSEC / MAL); once
an upstream record is public, contribute an OpenACA overlay per
[`CONTRIBUTING.md`](https://github.com/open-agent-security/openaca/blob/main/CONTRIBUTING.md).

For security issues in **OpenACA's own code**, see
[`SECURITY.md`](https://github.com/open-agent-security/openaca/blob/main/SECURITY.md). Do not file public issues for
unembargoed vulnerabilities.
