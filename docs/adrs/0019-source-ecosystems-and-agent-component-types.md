---
id: 0019
title: Separate source ecosystems from agent component types
status: accepted
date: 2026-05-20
supersedes: 0018
superseded-by: null
---

## Context

ADR-0018 corrected one part of the model: agent skills are generic components,
not Claude-specific components. It still placed `skill` in
`affected[*].package.ecosystem` and `ComponentRef.ecosystem`.

That overloaded the word ecosystem. In OSV and PURL-adjacent tooling,
ecosystem is best understood as the naming, versioning, and distribution space
for the source artifact: `npm`, `PyPI`, `github`, `docker`, or a real
marketplace with stable package coordinates. Agent-stack roles such as skill,
plugin, hook, command, agent, and MCP server describe what a component is or how
it is activated. They are not source ecosystems.

This matters for repositories that contain multiple agent components. For
example, `pkg:github/anthropics/skills@<sha>` identifies a repository snapshot,
not a single skill. A specific skill inside that repository needs component
metadata such as `component_type: skill` and `component_subpath:
skills/frontend-design`.

## Decision

OpenACA uses `ecosystem` only for source identity spaces:

- package registries: `npm`, `PyPI`
- source repositories/forges where known: `github`, `gitlab`, `git`
- artifact registries where known: `docker`
- future marketplace ecosystems only if they provide stable naming and version
  semantics

Agent-stack roles live in scanner metadata and output:

- `component_type: mcp_server`
- `component_type: plugin`
- `component_type: skill`
- `component_type: hook`
- `component_type: command`
- `component_type: agent`

`ComponentRef.ecosystem` therefore means source ecosystem. Parsers must not use
agent component types such as `skill`, `claude-plugin`, `claude-hook`,
`claude-command`, or `claude-agent` as ecosystems. When source identity is
unknown, the parser should leave `ecosystem` unset, keep a logical
`component_identity`, and set `extra.component_type`.

Matching order:

1. If a ref has source identity (`ecosystem`, `name`, and optionally `version`),
   match OSV-style `affected[*].package` records using that source identity.
2. If a ref is an unpinned package launch, keep the existing package-name
   extraction for npm/PyPI advisories.
3. If a ref has no source identity, match only records that explicitly target
   `database_specific.openaca.component_identity`.
4. During the pre-release transition, keep compatibility matching for legacy
   `affected[*].package.ecosystem` values `skill`, `claude-skill`, and
   `claude-plugin`, but treat them as component-type aliases, not canonical
   ecosystems.

For future source-backed agent components, the source coordinate stays separate
from the agent role:

```yaml
affected:
  - package:
      ecosystem: github
      name: anthropics/skills
      purl: pkg:github/anthropics/skills@<sha>#skills/frontend-design
database_specific:
  openaca:
    component_type: skill
    component_subpath: skills/frontend-design
```

OpenACA V0's canonical overlay schema remains minimal per ADR-0012. The
`component_type` and `component_subpath` fields above describe the intended
matching model, not a schema expansion in this ADR.

## Alternatives Considered

- **Keep `skill` as an ecosystem**: rejected because it names the agent role,
  not the source naming/versioning space. It also cannot distinguish multiple
  skills shipped inside one source repository without adding a second path key.
- **Use GitHub repo paths as `package.name` for every source-backed component**:
  accepted only for source identity. The component inside the repo remains
  separate metadata; `name: owner/repo/path/to/skill` would make the OSV package
  name no longer correspond to the GitHub source artifact.
- **Invent OpenACA ecosystems for every component type**: rejected because it
  makes OpenACA the package ecosystem and weakens interoperability with OSV,
  PURL, SBOM, and external scanners.
- **Remove all legacy component-type ecosystem matching immediately**: rejected
  because the project is still pre-release and beta branches may contain local
  test overlays using yesterday's names.

## Consequences

- Public scan output should report source status clearly. Source-less direct
  skills, hooks, commands, agents, and plugins have `source.status: unknown`.
- Verbose inventory still groups components by component type, not source
  ecosystem.
- OSV federation remains limited to refs with source ecosystems that can produce
  PURLs.
- Future Agent BOM export can map source identity, component type, observation
  evidence, activation context, and relationships without translating overloaded
  ecosystem values.

## When to Revisit

- When canonical overlays need to describe component subpaths inside GitHub or
  marketplace artifacts, define the schema addition explicitly.
- When a real agent-component package registry emerges, decide whether it is a
  source ecosystem based on its naming and versioning guarantees, not on the
  component type it hosts.
