# OpenACA Docs

This directory contains both user-facing documentation and project-internal
engineering records. Start with the user docs unless you are looking for a
specific architecture decision or implementation plan.

## User docs

- [Getting Started](getting-started.md) - install OpenACA and run your first
  endpoint or repository scan.
- [Scan Modes](concepts/scan-modes.md) - understand repository scans, endpoint
  scans, and project context.
- [Identity Model](concepts/identities.md) - graph identities, match
  coordinates, and why OpenACA keeps them separate.
- [CLI Reference](reference/cli.md) - install options, scan commands, output
  formats, SARIF, JSON, and exit codes.
- [Coverage](reference/coverage.md) - supported manifests, parser behavior, and
  current limitations.
- [Overlay Reference](reference/overlays.md) - how OpenACA overlays enrich
  upstream OSV / GHSA / CVE / MAL records.
- [Agent BOM Schema](openaca-bom-schema.md) - Agent BOM structure and fields.
- [Posture Findings](posture/README.md) - configuration-hygiene checks and
  remediation guidance.

## Project internals

- [ADRs](adrs/INDEX.md) - accepted architecture decisions and rejected
  alternatives.
- [Plans](plans/README.md) - implementation plans and current project state.
- [Specs](specs/openaca-thesis.md) - thesis, roadmap, and design notes.
- [Framework mappings](frameworks/README.md) - taxonomy source material.
- [Release notes](releases/) - release-specific notes.

## Maintainer docs

- [PyPI Publishing](pypi-publishing.md)
- [Deploy Notes](deploy.md)
- [SARIF Conventions](sarif-conventions.md)
- [Seed Review Rules](seed-review-rules.md)
- [Remote Deployment](remote-deployment.md)
