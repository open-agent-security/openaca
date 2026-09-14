---
id: 0065
title: Detect inline MCP credentials by authentication field
status: accepted
date: 2026-09-14
supersedes: null
superseded-by: null
---

## Context

An MCP configuration containing a literal bearer token returned zero posture
findings. Transport and advisory checks cannot identify the separate risk of
credentials stored in a shareable configuration file.

## Decision

Report literal values in known MCP authentication headers as medium-severity
posture, independent of token issuer, entropy, or validity. Dummy values count.
Recognized indirect references do not; literal fallback values do. The finding
names the component, manifest, and fixed-vocabulary fields, never header values.

For graph-derived posture collectors, reread only the source and server already
selected by composition. Header values stay in the transient local posture pass;
they are not added to component refs, graph properties, or BOM exports.

## Alternatives considered

- **Match only recognized token formats:** misses arbitrary bearer tokens even
  though the authentication field establishes their intended role. This rule
  claims risky storage, not credential validity or observed exfiltration.
- **Store raw headers in the composition graph:** makes secrets available to
  inventory serializers and caches that do not need them.
- **Walk every config again for posture:** can report shadowed or excluded
  components that the agent's composition did not select.

## Consequences

The rule catches test credentials as well as real ones. It does not validate
credentials or infer compromise. Coverage is intentionally limited to known
authentication headers; generic environment values and command arguments need
separate semantics. Re-reading a selected source adds local I/O and cannot
guarantee a snapshot if configuration changes during a scan.

## When to revisit

Revisit when composition supports private, non-exportable snapshot data or
when additional authentication fields have an established host contract.
