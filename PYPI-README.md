# OpenACA

**Agent Composition Analysis (ACA)** — OSV-compatible agent-context
overlays and a reference scanner for AI agent infrastructure: plugins,
MCP servers, skills, agent frameworks, model proxies, and runtime
components.

OpenACA is the AI-agent analogue of Software Composition Analysis
(SCA): it identifies the versioned plugins, MCP servers, skills, and
framework components that make up an AI agent, and matches them
against known security records (CVE/GHSA/OSV + agent-context overlays
maintained in this corpus).

## Beta status

This is the `0.1.0b5` closed-beta pre-release. The scanner and
overlay corpus are usable; expect rough edges. If you're a beta
tester, start with the
[**beta-tester guide**](https://github.com/open-agent-security/openaca-demo/blob/main/BETA-TESTER-GUIDE.md) —
it covers install, first scan, what feedback I'm looking for, and
how to report.

## Install

Requires Python 3.11+.

**Recommended — uv tool** ([install uv](https://docs.astral.sh/uv/getting-started/installation/)
if you don't have it; uv also provisions Python for you so a 3.11+
runtime isn't a prerequisite you need to satisfy separately):

```bash
uv tool install openaca
openaca --version
```

**Alternative — pip** (if you already have a Python 3.11+ workflow):

```bash
pip install openaca
```

Both commands auto-pick the latest pre-release while OpenACA has no
stable version yet. Current latest is `0.1.0b5`; check with `openaca
--version`.

Pin to a specific build if you need to reproduce a bug report:
`uv tool install openaca==0.1.0b5` or `pip install openaca==0.1.0b5`.

## Try it in 30 seconds

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
openaca scan repo --target .
```

Expected output:

```
Found 1 vulnerability in 1 package.

@cyanheads/git-mcp-server 1.1.0
  location: mcp.json
  fix:      upgrade to >=2.1.5

  HIGH  GHSA-3q26-f695-pp76  fixed in 2.1.5  @cyanheads/git-mcp-server vulnerable to command injection in several tools  [osv.dev]

Scanned 1 manifest, 1 component. Sources: osv.dev.
```

For more scenarios (clean scan, configuration-hygiene checks via
`--include-posture`), clone
[openaca-demo](https://github.com/open-agent-security/openaca-demo)
and try each of its fixtures.

## Two scan modes

| Mode | Question | Where it runs |
|---|---|---|
| `openaca scan repo --target <path>` | What agent components are declared in this repository? | CI gate, PR check |
| `openaca scan endpoint` | What agent components are installed on this machine right now? | Developer laptop, CI runner |

Both modes emit text (default), JSON (`--format json`), or SARIF 2.1
(`--sarif <path>`). Use `-v` for per-finding context.

Configuration-hygiene posture rules (mutable install references,
insecure transport) are opt-in via `--include-posture`. They run
separately from vulnerability findings and never fail CI by default.

## V0 scope

- **Endpoint mode** scans Claude Code only (`~/.claude` or
  `$CLAUDE_CONFIG_DIR`). Other agent hosts aren't endpoint-supported
  yet.
- **Repo mode** parses Claude Code's declared manifests
  (`.claude-plugin/plugin.json`, `.claude/settings.json`) plus the
  host-agnostic `mcp.json` that most MCP-aware hosts use.
- **Declared manifests only.** SDK-inline definitions
  (`query({ mcpServers: { ... } })`), tools registered
  programmatically, and source-code parsing are V1 scope.
- **Overlay corpus** focuses on malicious-package records for MCP
  servers, agent-framework packages, and AI infrastructure. Pair
  with a general-purpose SCA scanner for your full dependency tree.

## Links

- **Beta-tester guide**: https://github.com/open-agent-security/openaca-demo/blob/main/BETA-TESTER-GUIDE.md
- **Sandbox fixtures**: https://github.com/open-agent-security/openaca-demo
- **Feedback**: DM the maintainer (vinodkone@gmail.com). The
  openaca source repo is private during the closed beta; GitHub
  issues open up when the repo flips public.

Apache-2.0.
