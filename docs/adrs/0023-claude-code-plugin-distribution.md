---
id: 0023
title: Ship Claude Code integration as a thin plugin wrapper
status: accepted
date: 2026-05-22
supersedes: null
superseded-by: null
---

## Context

OpenACA's endpoint scan already inventories Claude Code configuration:
plugins, skills, MCP servers, hooks, commands, and related posture
findings. Users can run this through the CLI, but Claude Code's plugin
system gives a more natural distribution and invocation surface for
Claude Code users.

The plugin boundary is easy to blur. A Claude Code plugin could embed
scanner logic, install hooks, expose an MCP server, or become the
primary implementation of Claude-specific scanning. That would make the
core scanner less runtime-neutral and make plugin review harder because
the plugin would contain more logic and more ambient behavior.

## Decision

OpenACA will ship Claude Code integration as a separate, thin plugin
repository. The plugin may provide Claude Code skills and slash-command
surfaces that invoke the published `openaca` CLI, explain scan output,
and guide Agent BOM generation. Scanner logic, advisory matching,
Agent BOM schema, posture rules, and host parsers remain in the main
OpenACA repository. The initial plugin version is explicit-invocation
only: no hooks, no background monitors, no MCP server, and no automatic
policy blocking.

## Alternatives considered

- **Put the plugin inside the main repository**: rejected because plugin
  packaging, marketplace metadata, screenshots, and Claude-specific
  workflow docs are a different release surface from the runtime-neutral
  scanner and corpus.
- **Embed scanner logic in the plugin**: rejected because it would
  create two implementations of scanning and make the plugin harder to
  audit. The `openaca` CLI remains the execution boundary.
- **Ship hooks in the first plugin version**: rejected because ambient
  security behavior is more intrusive and harder to review. Hook-based
  prompts or blocking can be added later as opt-in behavior after the
  explicit command workflow proves useful.
- **Expose the corpus through a plugin-bundled MCP server immediately**:
  rejected because it expands the trust and maintenance surface before
  the simpler command workflow is validated. A future MCP server can
  still use the same CLI and data boundaries.

## Consequences

Claude Code users get a focused plugin that is easy to inspect: it
teaches Claude Code when and how to run `openaca`, but it does not
duplicate scanner internals. The main OpenACA repository remains the
source of truth for schemas, parsers, posture rules, matching, and
Agent BOM generation.

The separate repository adds one more release artifact to maintain.
The plugin must version its assumptions about the `openaca` CLI and
should prefer `uvx openaca` examples so users do not need a preinstalled
binary. Plugin updates may need to track CLI flag changes in the main
repository.

## When to revisit

Revisit this decision if the plugin needs runtime capabilities that
cannot be expressed as CLI-backed skills, such as long-lived local state,
interactive marketplace-managed configuration, or a stable MCP server
surface consumed by multiple agent runtimes. Revisit hook deferral once
explicit plugin usage shows which checks are valuable enough to run
automatically.
