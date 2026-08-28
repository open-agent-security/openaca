# CLI Reference

`openaca scan` scans observed agent composition and reports inventory,
vulnerabilities, and optional posture findings. `openaca triage` turns
structured scan output into component-centric exposure reports.

## Install options

Default install:

```bash
curl -fsSL https://raw.githubusercontent.com/open-agent-security/openaca/main/scripts/install.sh | sh
```

Pinned install:

```bash
curl -fsSL https://raw.githubusercontent.com/open-agent-security/openaca/main/scripts/install.sh | OPENACA_VERSION=<version> sh
```

Manual install:

```bash
uv tool install openaca
pip install openaca
```

Install from source:

```bash
git clone https://github.com/open-agent-security/openaca.git
cd openaca
uv sync
```

## Scan commands

```bash
openaca scan repo \
    --target /path/to/repo \
    --sarif results.sarif \
    --fail-on any
```

```bash
openaca scan endpoint \
    --fail-on any
```

```bash
openaca scan endpoint \
    --kind claude-code \
    --config-dir ~/.claude \
    --project /path/to/repo
```

`scan endpoint`, `bom endpoint`, and `remote sync endpoint` all take `--kind`
to limit discovery to one registered agent kind (`claude-code`, `cursor`, or
`codex`).
Omit `--kind` and discovery finds every installed kind whose own default root
exists — a bare `openaca scan endpoint` with Claude Code, Cursor, and Codex
installed renders one card per kind. `--config-dir` names one kind's root, so
it **requires** `--kind`: with more than one installed kind, `--config-dir`
alone can't say which kind's root it names, and the CLI errors rather than
guessing. Each kind resolves its own default root when `--config-dir` is
omitted — Claude Code from `$CLAUDE_CONFIG_DIR`, else `~/.claude`; Cursor from
`~/.cursor` (no environment variable); Codex from `$CODEX_HOME`, else
`~/.codex`. An unrecognized `--kind` is a hard error listing the known kinds.

Not every kind accepts `--config-dir`. A kind declares whether naming a root
fully specifies its target, and Cursor's does not: an installed Cursor is
gathered from three separately-relocated places, so an override would move
only one and produce a composition stitched from two homes
([ADR-0054](../adrs/0054-per-kind-root-override.md)). Claude Code and Codex
both accept it — Codex because `$CODEX_HOME` moves its whole tree and it reads
no other runtime's config ([ADR-0056](../adrs/0056-codex-root-override.md)).
`--kind cursor --config-dir …` is refused with the reason.

A subcommand is required. Shared options such as `-v`, `--fail-on`, `--sarif`,
`--format`, and `--no-color` can sit before or after the subcommand name:

```bash
openaca scan -v repo --target .
openaca scan repo --target . -v
```

## Output formats

`openaca scan` emits three stdout formats by default:

- **`text`** *(default)* - grouped human-readable output. One block per
  affected package, severity per finding, ANSI-colored when stdout is a TTY.
  Add `-v` for per-finding component/source/container context.
- **`github`** - GitHub workflow annotation lines (`::error file=...::`).
  Auto-selected when `GITHUB_ACTIONS=true`; can also be selected explicitly.
- **`json`** - structured per-finding records plus a `stats` block for
  programmatic consumption.

`markdown` is available only with `--report exposure`; it renders a
forwardable exposure report instead of the raw scan view.

`--sarif <path>` writes a SARIF 2.1.0 artifact in addition to the chosen stdout
format. `--no-color` disables ANSI output. Color is also disabled automatically
when stdout is not a TTY.

## Exposure reports

The scan/triage split keeps evidence collection separate from decision output.
Use scan JSON when you want a reusable artifact:

```bash
openaca scan endpoint --format json > openaca-scan.json
openaca triage openaca-scan.json --report exposure --format markdown --output openaca-exposure-report.md
```

For the common local path, `scan --report exposure` runs a normal scan and
renders the same triage report in one command:

```bash
openaca scan endpoint --report exposure --format markdown --output openaca-exposure-report.md
```

Exposure reports are static composition reports. They rank components using the
scan evidence available in the artifact; they do not monitor runtime behavior
or prove exploitability.

Plugin integrations should invoke one of these CLI paths rather than
implementing report logic themselves. For latency-sensitive plugin UX, omit
optional external scanners unless the user explicitly opts in.

## JSON fields

JSON output uses one top-level `findings[]` array. Vulnerability entries carry
`finding_type: "vulnerability"` and posture entries carry
`finding_type: "posture"`.

Each finding includes:

- `component` - the vulnerable or risky agent component being reported.
- `component.source` - the package, Git, source, or external coordinate used
  for matching or explanation.
- `active_in` - runtime host IDs observed by the scanner, when known.
- `declared_by` - manifest, plugin, or lock entry that introduced the
  component.
- `component_path` - containment path such as `plugin -> mcp_server`.
- `matched_advisory` - advisory identity for vulnerability findings.
- `taxonomies` - OpenACA overlay taxonomy mappings (e.g. `owasp_agentic_top10`)
  for the matched advisory. Absent entirely when the advisory carries no
  overlay.

Posture findings always carry a `standards` key, even when it is `{}`, while
vulnerability findings omit `taxonomies` entirely when empty; a consumer
iterating one `findings[]` array should expect both conventions depending on
`finding_type`.

Overlay records remain advisory data. They do not store local scan context such
as `active_in`, `declared_by`, or `component_path`.

## Posture findings

Pass `--include-posture` to include scanner-side configuration hygiene checks:

```bash
openaca scan endpoint --include-posture
```

Posture findings render in their own text section, share the JSON `findings[]`
array, and emit as separate SARIF rules. They do not affect `--fail-on` exit
codes.

See [Posture Findings](../posture/README.md) for the V0 rule list and
remediation guidance.

## Verbose output

Pass `-v` or `--verbose` for parser and attribution detail:

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

## Scan output and agents

Text output prints **one card per agent**, each with that agent's Target block
(including its `coverage` row), inventory tree, and next actions. The inventory
tree is rooted at the agent that loads the composition — in both `repo` and
`endpoint` mode — so every block says who owns it and the scan path stays in the
Target block; with more than one agent a rule separates the cards, since the
section headings repeat. Next actions are deduplicated across agents — two agents of one
kind emit the same generic actions, while an action naming its own root (a
`bom repo --target <root>`) survives per agent. `stats` and the Summary stay
scan-wide.

Machine formats stay one payload per scan (ADR-0047). `--format json` emits a
single document whose `findings[]` stays a flat list — each finding carrying the
agent it belongs to under `agent` — plus an `agents[]` array with one entry per
discovered agent, so an installed agent with nothing configured still appears.
`stats` remains scan-wide. `--format github` is an annotation stream and gains no
`agents[]` block; the agent travels on each annotated finding.

## Exit codes

- `0` - scan completed and no findings met the `--fail-on` threshold.
- `1` - scan completed and findings met or exceeded the `--fail-on` threshold.

Use `openaca scan --help` for the complete generated option list.

## Agent BOM commands

A BOM describes one **agent**, and each command emits one document per agent it
discovers — one or more, since Claude Code and Cursor are both registered
kinds; a repo or endpoint declaring evidence for both emits two documents.

Generate an Agent BOM for each agent a repository declares:

```bash
openaca bom repo --target . --output-dir boms/
```

Generate an Agent BOM for each agent installed on this endpoint:

```bash
openaca bom endpoint --output-dir boms/
```

**Output shapes.** A consumer never needs to know the agent count in advance:

| Sink | Behaviour |
|---|---|
| stdout (default) | **NDJSON** — one CycloneDX document per line. One agent is a single line, so `jq` and `json.load` keep working; many agents are line-wise and self-describing. |
| `--output-dir <dir>` | One file per agent, named `<kind>[--<agent-id>].cdx.json`. Uniform for one agent or many. |
| `--output <file>` | **Deprecated.** Still writes a single document, and errors with a pointer to `--output-dir` only when more than one agent resolves. |

`--output-dir` tracks the files it wrote in `.openaca-bom-manifest.json` inside
that directory: a rerun that resolves fewer agents removes only the previously
written files that no longer resolve, so a stale file is never left behind to
be misread as current. A `.cdx.json` file the tool didn't write — hand-authored,
from another tool, or from a scan that predates this manifest — is left alone,
including when its name collides with a basename the current run would
generate; the command refuses to overwrite it and exits non-zero instead. A
manifest entry that isn't itself a plain `<kind>[--<agent-id>].cdx.json` basename
(a path with separators, `.`/`..`, or a name this emitter could never have
produced) is never treated as owned, whether it got there from hand-editing or
a planted file. If publishing or cleaning up a run's files fails partway
through, the command tries to rewrite the manifest to describe exactly what
ended up on disk before exiting non-zero; if that rewrite itself fails, the
command still exits non-zero and reports it, since the manifest may now be
stale.

A tree that declares no agent emits **no document** and exits `0` with a note on
stderr — a repo of ordinary package manifests is not an agent. Likewise
`bom endpoint` reports `no installed agent found` and exits `0` when the config
root does not exist.

`openaca scan bom --input <file>` and `openaca bom diff` both read either shape:
a single JSON object, or NDJSON with one document per line.

`bom diff` with many documents per side pairs them on `(agent_kind, agent_id)`
and diffs each pair, reporting an unpaired document as an added or removed
agent; the diff primitive itself stays singular. One document each side keeps
the single-diff output unchanged. The asset — the third part of an agent's
instance key (ADR-0045) — is deliberately absent from a document, so diffing
two files asserts they came from the same asset.

`--output-dir` ignores its ownership manifest when the directory is sticky and
writable by other users (`/tmp` and friends): there a planted manifest could
get this tool to delete a file its planter could not, so nothing is treated as
stale. Everywhere else, writing that manifest requires the same directory
permission needed to destroy the files it names, so it grants no new capability.

Compare two Agent BOMs without running advisory lookups:

```bash
openaca bom diff \
    --before openaca-agent-bom.previous.json \
    --after openaca-agent-bom.json
```

`openaca bom diff` compares component occurrences by `bom-ref` and reports
added, removed, and changed components plus added and removed composition
edges. Use JSON output for automation:

```bash
openaca bom diff \
    --before openaca-agent-bom.previous.json \
    --after openaca-agent-bom.json \
    --format json
```
