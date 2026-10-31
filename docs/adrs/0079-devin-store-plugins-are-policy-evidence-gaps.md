---
id: 0079
title: Report a Devin store plugin as a policy evidence gap
status: accepted
date: 2026-10-31
supersedes: null
superseded-by: null
amended-by: 0080
---

## Context

`collect_policy_evidence` now accepts `agent_kind="devin"`.
Devin plugins can provide MCP servers, skills, subagents and hooks.
Devin installs plugins at user level, in a store under `$XDG_DATA_HOME/devin/cli/plugins`.
The Devin CLI kind reads only plugin names from the store's `lock.json`.
The bundle layout in the store is not verified, because `devin plugins install` needs a signed-in account.
Devin's documentation does not give the layout, and does not say if a plugin server's tool name has a prefix.

A policy consumer resolves a hook's MCP server name against the evidence.
A plugin's server is not in the evidence.
Without a gap, the evidence looks complete for MCP selectors when it is not.

## Decision

Each plugin in the Devin store adds one entry to `inventory_gaps` of the Devin policy evidence.
Strict collection fails when the store holds a plugin.
A caller that requests partial inventory gets the readable components and the gaps.
The scan and BOM output do not change.
The details are in [the policy compiler spec](../specs/policy-compiler.md#plugins).

## Alternatives considered

- **Treat a presence-only plugin as complete evidence:** rejected because the caller cannot tell that the plugin's servers are missing. A hook call to one of them then looks like a call to a server that is not installed.
- **Compose the store bundles from an assumed layout:** rejected because the layout is not verified. A wrong layout gives wrong subjects or wrong names, and those look correct to the caller.
- **Record the gap in the Devin graph:** rejected because it changes the scan and BOM output for every Devin user. The kind spec already defers the store layout and keeps the `partial` coverage baseline.

## Consequences

Strict Devin collection fails on an endpoint with any store plugin.
A runtime caller must request partial inventory and report the gap count.
A hook call to a plugin's MCP server gets the caller's uncertainty behavior, not an exact allow or block.

## When to revisit

Revisit when an observation of a signed-in install verifies the store bundle layout and the plugin server's tool name.
Then compose the plugin contents and remove these gaps.
