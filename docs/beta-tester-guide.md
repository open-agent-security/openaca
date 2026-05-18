# OpenACA — Beta Tester Guide

Thanks for trying OpenACA. This is a closed beta on the `0.1.0b1`
pre-release. Goal: surface the highest-friction gaps before a wider
release.

## Install

```bash
pip install openaca==0.1.0b1
openaca --version
# expected: openaca, version 0.1.0b1
```

Requires Python 3.11+.

## First scan

Point it at one repo or endpoint you already maintain — your own
agent project, a Claude Code install, an MCP server you author. The
scanner doesn't phone home; everything runs locally.

**Repo mode** — scans declared manifests (`.mcp.json`,
`.claude-plugin/plugin.json`, `.claude/settings.json`, etc.):

```bash
openaca scan repo --target /path/to/your/repo
```

**Endpoint mode** — scans an installed Claude Code endpoint
(`~/.claude` or `$CLAUDE_CONFIG_DIR`):

```bash
openaca scan endpoint
```

Both modes accept `--sarif results.sarif`, `--format json`, and
`--include-posture` (configuration-hygiene rules — off by default,
worth turning on if you want first-scan signal even when the corpus
finds no vulnerabilities).

Want to verify the install before pointing it at a real target? The
README's "Try it in 30 seconds" section has a copy-paste `mcp.json`
snippet, and the
[openaca-demo](https://github.com/open-agent-security/openaca-demo)
repo has three sandbox fixtures (vulnerability, clean, posture) you
can clone and run end-to-end.

## What feedback I want

Three buckets — please tag your issue with the closest fit:

1. **Scanner ergonomics** — does install → first-scan → output read
   right? Anywhere you bounced, anything you had to guess at, anything
   the CLI made you do twice.
2. **Coverage gaps** — V0 reads declared manifests only (no SDK-inline
   parsing). What did your environment contain that the scanner
   should have inventoried and didn't? What did it inventory that
   surprised you?
3. **Workflow fit** — where would this slot into your existing
   security tooling? CI gate? Pre-deploy check? Endpoint sweep on a
   schedule? Something else? And what's missing for that fit?

One filed observation is plenty. The friction signal is more valuable
than the polish.

## Privacy & redaction

OpenACA runs locally and doesn't send scan data anywhere. But the
issue template asks for output, and your output may contain internal
package names, paths, or component IDs you don't want public:

- **Redact freely.** Replace internal names with `<redacted>` or
  generic placeholders. The shape of the output is more useful than
  the literal contents.
- **SARIF is sometimes easier to redact than text** — it's structured
  JSON, you can drop or rename specific entries cleanly. Run with
  `--sarif results.sarif --format json` and edit before pasting.
- **If you'd rather email** sensitive output instead of filing
  publicly: vinodkone@gmail.com is fine. Just include the same
  fields the issue template asks for.

## Known limitations in V0

- **Declared manifests only.** V0 reads `mcp.json`,
  `.claude-plugin/plugin.json`, `.claude/settings.json`, marketplace
  registries, and similar. It does **not** extract MCP servers
  defined SDK-inline (`query({ mcpServers: { ... } })`), tools
  registered programmatically, or anything from source-code parsing.
  Those are V1 scope.
- **Corpus focused on agent-stack threats.** The overlay corpus
  prioritizes malicious-package records for MCP servers, agent
  framework packages, and AI infra. It's not a substitute for a
  general-purpose SCA tool on your whole dependency tree — use both.
- **Posture findings off by default.** The configuration-hygiene
  rules (mutable install references, insecure transport, missing
  remote auth) are opt-in via `--include-posture`. They're separate
  from vulnerability findings and never fail CI by default.
- **In-flight work.** `docs/plans/` shows what's actively being
  built. If you find yourself wishing for something there, that's
  useful feedback — say so.

## How to report

Use the
[beta-feedback issue template](https://github.com/open-agent-security/openaca/issues/new?template=beta-feedback.md).
The template covers command run, version, expected/actual,
output (redacted), and any inventory mismatch.

Filing one observation is the bar. The friction signal compounds
across the cohort.

## Pinning a version

Since you're testing `0.1.0b1`, your bug report links to a specific
build. If I push fixes and you want to re-test the same scenario:

```bash
pip install --force-reinstall openaca==0.1.0b1
```

For the next pre-release (e.g., `0.1.0b2`), I'll send a note.

— Vinod
