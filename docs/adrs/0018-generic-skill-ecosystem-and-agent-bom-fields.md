---
id: 0018
title: Use generic skill ecosystem and Agent BOM-compatible scanner metadata
status: accepted
date: 2026-05-19
supersedes: 0007
superseded-by: null
---

## Context

ADR-0007 introduced `claude-skill` as the first skill ecosystem because the
initial parser targeted Claude Code `SKILL.md` files. Round-1 beta feedback and
subsequent ecosystem review changed that assumption. Agent skills are becoming a
cross-runtime component type: Claude Code, Codex, opencode, Vercel `skills`, and
other hosts can activate the same underlying skill artifact. Treating the skill
itself as Claude-specific bakes the first host adapter into the component
identity.

OpenACA also expects to generate an Agent BOM in a future release. Public
Agent BOM / AI BOM work points toward standard BOM concepts: components,
source identities, evidence, activation/runtime context, and relationships.
AOS AgBOM extends CycloneDX/SPDX/SWID for agent components; Snyk's AI-BOM uses
CycloneDX and models agents, tools, MCP clients, MCP servers, tools/resources,
and dependency graph relationships. OpenACA V0 scanner metadata should align
with that direction even before a standalone Agent BOM export exists.

## Decision

OpenACA uses `skill` as the scanner ecosystem and component identity prefix for
agent skills:

- `ecosystem: skill`
- `component_identity: skill/<name>`
- `component_identity: skill/<name>@<metadata.version>` when a skill declares a
  string `metadata.version`

`claude-skill` remains a pre-release compatibility alias in the matcher only.
A `skill` ref may match an advisory that still says `affected[*].package.ecosystem:
claude-skill`, and a legacy `claude-skill` ref may match a `skill` advisory.
New overlays and tests should use `skill`.

Host specificity moves out of the skill ecosystem and into scan context:

- runtime host: `runtime_hosts: ["claude-code"]` in scanner metadata
- activation scope: user, project, local, or managed where known
- activation mode: direct, plugin-bundled, settings-declared, or equivalent
- declaration/containment: `declared_by`, `attributed_to`, and `component_path`

OpenACA scanner metadata follows an Agent BOM-compatible model:

1. **Component identity**: what the component is (`skill/foo`,
   `claude-plugin/bar@1.0.0`, `mcp-server/...`).
2. **Source identity**: canonical upstream artifact identity when known, such as
   an npm/PyPI/Docker PURL or GitHub repo + revision + subpath.
3. **Observation evidence**: scanner-local evidence, such as file path,
   manifest path, locator, symlink target, or lockfile entry.
4. **Activation context**: how the component enters the agent runtime, such as
   direct project skill or plugin-bundled skill active in Claude Code.
5. **Relationships**: containment/declaration edges, such as plugin contains
   skill or config declares MCP server.

V0 does not need a new Agent BOM export format for this decision. It only needs
scanner metadata and output fields to preserve those distinctions so later
CycloneDX/SPDX exports can map them cleanly.

## Alternatives considered

- **Keep `claude-skill` as the canonical ecosystem**: rejected because skills
  are no longer clearly Claude-only. Runtime host is scan context, not the
  component type.
- **Use `skills.sh` as the ecosystem**: rejected by ADR-0016. `skills.sh` is a
  discovery/registry/install channel for many GitHub-backed skills; it is not
  the canonical source artifact ecosystem.
- **Use `pkg:openaca/...` for skills**: rejected by ADR-0016. OpenACA is not the
  artifact ecosystem; PURL-like identities should name the actual source
  ecosystem when one exists.
- **Rename every host-specific ecosystem now**: rejected. Hooks, commands,
  agents, and plugins are still host-specific enough in V0 that
  `claude-hook`, `claude-command`, `claude-agent`, and `claude-plugin` remain
  accurate scanner ecosystems. Skills are the cross-runtime exception.
- **Drop `claude-skill` compatibility immediately**: rejected because this is a
  pre-release rename, but old local overlays/tests may exist during beta. The
  compatibility alias is cheap and does not change new canonical output.

## Consequences

- Public scan output and new overlays use the generic `skill` ecosystem.
- Existing pre-release `claude-skill` overlays continue to match through the
  compatibility alias, but new corpus work should not add more `claude-skill`
  records.
- Agent BOM export can later map `skill` refs to a generic agent component type
  while preserving `runtime_hosts` and activation metadata separately.
- ADR-0007 is superseded for skill ecosystem naming. Its tiered scanning model,
  endpoint/application framing, identity-scope disambiguation, and host-adapter
  deferrals roll forward unless contradicted here.

## When to revisit

- If package-url standardizes an agent-skill PURL type, consider whether it
  should replace `skill` for source identity. Component type may still remain
  generic `skill`.
- If hooks, commands, agents, or plugins become cross-runtime standards with
  shared semantics, consider the same generic-ecosystem migration for those
  component types.
- If compatibility aliases complicate matching or output after V0, remove the
  `claude-skill` alias in a documented breaking release.
