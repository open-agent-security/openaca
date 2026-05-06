# ASVE

**Agent Stack Vulnerabilities and Exposures** — an open, OSV-compatible advisory
database for AI agent infrastructure: plugins, MCP servers, skills, agent
frameworks, model proxies, and runtime components.

> Open advisories for agent stack security.

## What ASVE provides

ASVE extends the Software Composition Analysis (SCA) model to manifests that
traditional SCA tools don't yet parse:

- **Versioned components** in `package.json`, `mcp.json`,
  `.claude-plugin/plugin.json`, `.claude/settings.json`, and similar
  agent-installation manifests.
- **Agent-context metadata** layered on existing CVE/GHSA records:
  `component_type`, `surfaces`, `agent_impact`, OWASP Agentic Top 10 (ASI)
  category mapping.
- **A reference scanner** (a thin GitHub Action) that consumes the static
  advisory export and reports findings against a repository's installed
  agent-stack components.

## Status

V0, in development. See [`docs/specs/asve-v0-design.md`](docs/specs/asve-v0-design.md)
for the canonical V0 design and [`docs/plans/`](docs/plans/) for implementation
plans.

## Schema and IDs

- **ID format**: `ASVE-YYYY-NNNN` (single namespace).
- **Type-tagged records**: `type: vulnerability` is the only public V0 record
  type. `type: exposure` and `type: config` are reserved in the schema for V1.
- **Severity**: CVSS v4 base + environmental.
- **Category**: OWASP Agentic Top 10 (`asi01`–`asi10`).
- **Aliasing**: every record aliases existing CVE/GHSA/OSV identifiers where
  available. ASVE adds the agent-context overlay; it does not duplicate
  upstream authority.

## Reference scanner

Use the GitHub Action in your workflow:

```yaml
- uses: open-agent-security/asve@v1
```

The Action consumes the latest static export, parses your repository's
agent-installation manifests, and reports advisories against installed
components.

## License

- **Code**: [Apache License 2.0](LICENSE).
- **Advisory data**: [CC-BY-4.0](LICENSE-DATA) (matches OSV.dev).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the advisory authoring guide,
linter discipline, ID reservation flow, and PR workflow.

## Coordinated disclosure

ASVE follows the [OpenSSF coordinated disclosure
guidance](https://openssf.org/) with project-specific defaults documented in
[`docs/disclosure-policy.md`](docs/disclosure-policy.md). Report security
issues per that policy; do not file public issues for unembargoed
vulnerabilities.
