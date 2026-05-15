# Security Policy

This policy covers **bugs in OpenACA's own code** — the scanner, linter,
parsers, schema, and reference Action. For vulnerabilities in agent-stack
components (MCP servers, plugins, skills, etc.), see "Reporting an
agent-stack vulnerability" below.

## Reporting a bug in OpenACA itself

If you've found a security issue in OpenACA's code — for example a parser
that mishandles a malicious manifest, a linter that crashes on crafted
input, or a scanner path that exfiltrates secrets — please report it
privately rather than opening a public issue.

**Email:** `security@openaca.dev`. We acknowledge reports within 5
business days.

> **Note:** the `security@openaca.dev` mailbox goes live alongside the
> public V0 launch. Until then, file reports as private security
> advisories through the GitHub Security tab on
> `open-agent-security/openaca`.

## Reporting an agent-stack vulnerability

OpenACA does not mint vulnerability IDs. If you've found a vulnerability
in an agent-stack component (an MCP server, a published plugin, a skill
bundle, an agent framework), the workflow is:

1. Disclose to the component's maintainer using their security policy.
2. Get an upstream ID issued (CVE / GHSA / OSV / PYSEC / MAL).
3. Once the upstream record is public, contribute an OpenACA overlay
   per the flow in [`CONTRIBUTING.md`](CONTRIBUTING.md).

See [`docs/disclosure-policy.md`](docs/disclosure-policy.md) for the
disclosure framework OpenACA uses when contributors coordinate
upstream disclosures.
