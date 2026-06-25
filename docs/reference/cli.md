# CLI Reference

`openaca scan` scans observed agent composition and reports inventory,
vulnerabilities, and optional posture findings.

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
    --config-dir ~/.claude \
    --project /path/to/repo
```

A subcommand is required. Shared options such as `-v`, `--fail-on`, `--sarif`,
`--format`, and `--no-color` can sit before or after the subcommand name:

```bash
openaca scan -v repo --target .
openaca scan repo --target . -v
```

## Output formats

`openaca scan` emits three stdout formats:

- **`text`** *(default)* - grouped human-readable output. One block per
  affected package, severity per finding, ANSI-colored when stdout is a TTY.
  Add `-v` for per-finding component/source/container context.
- **`github`** - GitHub workflow annotation lines (`::error file=...::`).
  Auto-selected when `GITHUB_ACTIONS=true`; can also be selected explicitly.
- **`json`** - structured per-finding records plus a `stats` block for
  programmatic consumption.

`--sarif <path>` writes a SARIF 2.1.0 artifact in addition to the chosen stdout
format. `--no-color` disables ANSI output. Color is also disabled automatically
when stdout is not a TTY.

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

## Exit codes

- `0` - scan completed and no findings met the `--fail-on` threshold.
- `1` - scan completed and findings met or exceeded the `--fail-on` threshold.

Use `openaca scan --help` for the complete generated option list.

## Agent BOM commands

Generate an Agent BOM for a repository:

```bash
openaca bom repo --target . --output openaca-agent-bom.json
```

Generate an Agent BOM from the local endpoint configuration:

```bash
openaca bom endpoint --output openaca-agent-bom.json
```

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
