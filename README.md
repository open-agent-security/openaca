# ASVE

**Agent Stack Vulnerabilities and Exposures** — an open, OSV-compatible
advisory database for AI agent infrastructure: plugins, MCP servers,
skills, agent frameworks, model proxies, and runtime components.

> Open advisories for agent stack security.

## Why ASVE

ASVE is **Agent Composition Analysis (ACA)**, not general Software
Composition Analysis (SCA). It is targeted at the agent stack: which
plugins, MCP servers, skills, hooks, and commands compose an agent —
and which of those have known security advisories. For general
software dependency scans (your app's transitive npm/PyPI tree, your
container image, etc.), use [osv-scanner](https://github.com/google/osv-scanner)
or [Trivy](https://github.com/aquasecurity/trivy) alongside ASVE; the
layers stack.

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

## Two scan modes

ASVE scans two distinct surfaces, named via explicit subcommands.
The same advisory matches in both, but the surface tells you *what
question you're asking*:

| Mode | Question | Audience | Where it runs |
|---|---|---|---|
| `asve-scan repo` | *"What agent components will this app ship with when deployed?"* | AppSec / platform security | CI gate, PR check |
| `asve-scan endpoint` | *"What agent tools are installed on this machine right now?"* | Endpoint security / IT | Developer laptop, CI runner, MDM-managed device |

The same identifier (e.g., `@modelcontextprotocol/server-filesystem@1.0.0`)
means different things in each context — future deployed-agent exposure
vs. current developer-machine exposure. The scanner output makes the
distinction explicit.

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
checkout without GitHub Actions. Two modes via subcommands:

```bash
git clone https://github.com/open-agent-security/asve.git
cd asve
uv sync

# Repo mode: walks declared manifests in a code repo (today's behavior;
# what the GitHub Action uses).
uv run asve-scan repo \
    --target /path/to/your/repo \
    --advisories advisories/ \
    --sarif results.sarif \
    --fail-on any

# Endpoint mode: install-state-aware scan of an installed Claude Code
# agent stack. Defaults to $CLAUDE_CONFIG_DIR, else ~/.claude.
uv run asve-scan endpoint \
    --advisories advisories/

# Or scan a specific endpoint config directory and layer in project/local
# settings from a repo.
uv run asve-scan endpoint \
    --config-dir ~/.claude \
    --project /path/to/your/repo \
    --advisories advisories/
```

A subcommand is required. Shared options (`-v`, `--fail-on`, `--sarif`,
`--format`, `--no-color`) can sit before or after the subcommand name —
`asve-scan -v repo --target X ...` is equivalent to
`asve-scan repo --target X ... -v`.

### Output formats

`asve-scan` emits three formats; pick with `--format`:

- **`text`** *(default)* — grouped human-readable output. One block per
  affected package, severity per finding, ANSI-colored when stdout is a
  TTY. Add `-v` for per-finding `surfaces` / `agent_impact` metadata.
- **`github`** — GitHub workflow annotation lines (`::error file=...::`).
  Auto-selected when `GITHUB_ACTIONS=true` so the included Action keeps
  working without configuration. Use explicitly to emit annotations
  outside CI.
- **`json`** — structured per-finding records plus a `stats` block. For
  programmatic consumption.

`--sarif <path>` is orthogonal and writes a SARIF 2.1.0 artifact in
addition to the chosen stdout format. `--no-color` disables ANSI in text
output (color is also off automatically when stdout isn't a TTY).

Or via `uvx`, which clones, builds, and runs in one shot (no manual
checkout):

```bash
uvx --from git+https://github.com/open-agent-security/asve asve-scan repo \
    --target /path/to/your/repo \
    --advisories advisories/ \
    --sarif results.sarif
```

`asve-scan --help` lists all options. Exit codes: `0` clean (or
findings below `--fail-on` threshold), `1` findings at or above the
threshold.

Pass `-v` / `--verbose` for the per-manifest breakdown (repo mode) or
the resolved active-plugin tree (endpoint mode):

```text
# repo mode -v
loaded 5 advisory(ies) from advisories
scanned 87 manifest(s), 70 component(s):
  external_plugins/discord/package.json — 2 component(s)
  external_plugins/fakechat/.mcp.json — 1 component(s)
  ...

# endpoint mode -v
loaded 5 advisory(ies) from advisories
detected config_dir=/Users/.../.claude (mode=endpoint)
resolved 14 active plugin(s):
  claude-plugin/supabase@0.1.6 (sha: <short>) [scope=user]
  claude-plugin/superpowers@5.1.0 (sha: <short>) [scope=user]
  ...
```

Findings carry a `via <plugin>` annotation when discovered through an
active plugin (plans 008 and 009 populate this; plan 007 only emits
plugin-level components).

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

ASVE follows a tiered model loosely analogous to traditional SCA's
"lockfile > manifest > source code" hierarchy:

| Tier | What it reads | V0 status |
|---|---|---|
| **1. Declarative manifests** (host-specific) | `.claude/settings.json`, `.claude-plugin/plugin.json`, `mcp.json`, `.mcp.json`, `claude_desktop_config.json`, `installed_plugins.json` (endpoint mode), `SKILL.md`, `hooks/hooks.json`, `.claude/commands/*.md`, `.claude/agents/*.md` | ✅ V0 |
| **2. Dependency manifests** (universal) | `package.json`, `pyproject.toml`, lockfiles inside active plugins (plan 009) | ✅ V0 |
| **3. SDK-aware code extraction** (host-specific SAST-like) | parse `query({mcpServers: [...]})`, `Agent(tools=[...])`, etc. | ⏸ V1 |
| **4. Runtime attestation** | ask the deployed app what it loaded | ⏸ out of ASVE scope; that's a deployment-side product layer |

**Advisory database selection.** `--db` controls which backends are
consulted:

- `--db asve` (default) — local ASVE corpus only.
- `--db asve,osv` — also query OSV.dev for additional vulnerability
  records covering emitted PURLs. Network required; fails soft if
  OSV.dev is unreachable.

See `docs/adrs/0008-lockfile-dispatch-and-osv-federation.md` for the
federation design.

**Agent-composition scope.** Repo-mode dependency manifests
(`package.json`, `pyproject.toml`, `package-lock.json`, `uv.lock`) are
classified as **agent-dependency** only when co-located with a
`.claude-plugin/plugin.json` sibling — i.e., they declare the deps
*of a plugin's implementation*. Bare dep manifests in repos that
aren't plugins are classified as **software-dependency** and
suppressed from output (and from OSV.dev queries) — that's
general-purpose SCA territory, not ACA. Scan those with osv-scanner
or Trivy instead. A non-empty repo with only software-dependency
refs produces an explicit footer pointing to those tools rather than
a silent "no findings."

Per-parser detail:

| Manifest | Detects | Identifier emitted |
|---|---|---|
| `package.json` | npm dependencies (deps + devDeps) | `pkg:npm/<name>@<version>` |
| `pyproject.toml` | PEP 621 deps, optional-deps, PEP 735 dependency-groups | `pkg:pypi/<name>@<version>` |
| `mcp.json` / `.mcp.json` / `claude_desktop_config.json` | MCP server launches via `npx`, `uvx`, `python -m`, etc. | PURL when pinned; `mcp-stdio/...` otherwise |
| `.claude-plugin/plugin.json` | Claude Code plugin identity | `claude-plugin/<name>@<version>` |
| `.claude/settings.json` | Enabled-plugin enumeration; bare `mcpServers`; bare `hooks` per scope | mixed (see surface-specific rows) |
| `installed_plugins.json` (endpoint mode) | Active plugins (resolved versions, gitCommitSha) | `claude-plugin/<name>@<version>` |
| `SKILL.md` (`.claude/skills/*/` or `<plugin>/skills/*/`) | Agent skills | `claude-skill/<name>[@<metadata.version>]` |
| `hooks/hooks.json` (plugin) or `settings.json.hooks` (bare) | Hook entries by event + index | `claude-hook/<plugin>/<event>/<i>` (bundled) or `claude-hook/settings/<scope>/<event>/<i>` (bare) |
| `.claude/commands/*.md` and `<plugin>/commands/*.md` | Slash commands | `claude-command/<owner>/<name>` (owner = plugin or `repo`) |
| `.claude/agents/*.md` and `<plugin>/agents/*.md` | Subagents | `claude-agent/<owner>/<name>` |

## Limitations

Be honest about what ASVE V0 doesn't see:

- **Programmatic SDK configuration is invisible to repo mode.** Code
  that constructs agents with `query({ mcpServers: [...] })` (Claude
  Agent SDK) or `Agent(tools=[...], mcp_servers=[...])` (OpenAI Agents
  SDK) bypasses manifest scanning entirely. Tier-3 SDK-aware extraction
  is V1.
- **Repo mode is Claude-family-biased today.** Tier-1 declarative parsers
  cover Claude Code / Claude Agent SDK filesystem conventions. Cursor,
  Windsurf, Codex CLI, VS Code agent-mode, and OpenAI Agents SDK have
  their own conventions (or no conventions); those are V1 adapters.
- **Endpoint mode is Claude Code-specific.** It reads
  `~/.claude/installed_plugins.json` and friends. Codex CLI's
  `~/.codex/` and Cursor's local state will need their own resolvers.
- **Repo mode is a manifest survey, not a runtime guarantee.** A
  finding means "this manifest declares a vulnerable component";
  whether it actually executes depends on runtime config we can't
  see from static files alone. Endpoint mode is closer to ground truth
  because it reads the resolved lockfile.

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
