---
id: 0077
title: Select Claude MCP policy evidence before evaluation
status: accepted
date: 2026-10-17
supersedes: null
superseded-by: null
---

## Context

Native Claude MCP inventory adds user and local entries to project `.mcp.json` entries.
Separate collectors retain shadowed entries and can produce posture findings for servers that lose scope selection.
The generic parser also accepts URL entries without a type.
Claude Code skips those entries instead of assuming HTTP.

ADR-0020 chooses permissive MCP declaration parsing for inventory completeness.
That parser behavior does not establish which declarations a particular host selects.
[Claude's MCP documentation](https://code.claude.com/docs/en/mcp) defines transport requirements and scope precedence.
Claude Code 2.1.302 probes confirm native source locations and selection of valid entries.

## Decision

Use one Claude resolver for native local, project `.mcp.json`, and native user entries.
Select valid entries by server name in that precedence order.
Use the whole winning entry, without merging fields.
Apply the selected project's disable list to the local and user scopes.
A project `.mcp.json` entry uses a different approval list. The disable list does not control it.
Skip invalid entries without suppressing their valid siblings or lower-scope alternatives.
Malformed containers still report incomplete evidence.

Require a command for stdio declarations and an explicit supported type for URL declarations.
Derive native and project posture inputs from the selected refs.
Reread credentials only for the selected source, server and native scope.
Keep credential values outside refs and exported BOMs, as ADR-0065 requires.

Keep the generic declaration parser permissive.
Its standalone inventory output does not certify host selection.
Legacy settings declarations, plugin selection and other agent kinds retain their existing behavior.

## Alternatives considered

- **Use every discovered declaration for policy:** rejected because invalid or shadowed entries can produce incorrect restrictions.
- **Change the shared parser for every host:** rejected because host validation does not belong in a neutral declaration parser.
- **Resolve precedence separately in posture:** rejected because separate selection rules drift and can attribute findings to different components.

## Consequences

Policy evidence and posture use the same native and project components.
A URL declaration without a type no longer becomes a Claude policy subject.
Configured source precedence does not certify connection success or project approval.
Workspace trust, session-specific overrides and dynamic connectors remain outside this resolver's contract.

Tests cover transport requirements, all three scopes, disable lists, provenance, and posture agreement.
These supported selection rules define the correctness bar.
Complete emulation of every Claude configuration field and runtime state remains outside this change.

## When to revisit

Revisit when Claude changes transport requirements or configured scope precedence.
Revisit when collection receives authoritative session approval, trust or override state.
