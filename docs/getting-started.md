# Getting Started

OpenACA is a local scanner. Component manifests are read locally, then
versioned package and Git coordinates are matched against live advisories from
OSV.dev (`api.osv.dev`) unless network access is unavailable.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/open-agent-security/openaca/main/scripts/install.sh | sh
```

The install script bootstraps
[`uv`](https://docs.astral.sh/uv/getting-started/installation/) if needed, then
installs OpenACA as an isolated CLI tool.

You can also install directly:

```bash
uv tool install openaca
```

Or with pip in an existing Python 3.11+ workflow:

```bash
pip install openaca
```

For reproducible deployments, pin an exact version:

```bash
curl -fsSL https://raw.githubusercontent.com/open-agent-security/openaca/main/scripts/install.sh | OPENACA_VERSION=<version> sh
```

Avoid `uv ... --prerelease allow` for normal installs; it applies to the whole
dependency resolution, not just OpenACA. Pin `openaca==<version>` when you need
to reproduce a specific build.

## Scan an endpoint

```bash
openaca scan endpoint
```

This scans your user-level Claude Code config (`~/.claude`). Project-local
agent config is opt-in:

```bash
openaca scan endpoint --project /path/to/repo
```

## Scan a repository

```bash
openaca scan repo --target /path/to/repo
```

Repository mode is the right default for CI and PR checks.

## Generate an Agent BOM

```bash
openaca bom repo --target /path/to/repo --output openaca-bom.json
```

The Agent BOM is a portable snapshot of the agent composition OpenACA observed.

## Include posture findings

By default, `openaca scan` reports advisory-backed vulnerability findings. Add
`--include-posture` to include scanner-side configuration hygiene findings:

```bash
openaca scan endpoint --include-posture
```

Posture findings do not affect `--fail-on` exit codes.

## Use the GitHub Action

Add `.github/workflows/openaca.yml`:

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
          fail-on: high
```

See the [CLI Reference](reference/cli.md) for output formats, SARIF, JSON, and
exit codes.
