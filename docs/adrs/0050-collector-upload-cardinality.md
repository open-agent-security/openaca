---
id: 0050
title: Register one asset per machine and upload one BOM per agent
status: accepted
date: 2026-08-24
supersedes: null
superseded-by: null
---

## Context

ADR-0044 made a BOM describe one agent, and the local scan path migrated with it. The
remote collector is being migrated now (`docs/specs/collector-agent-rooted-uploads.md`),
which forces a question the single-document collector never had to answer: a machine can
resolve several agents, and the upload envelope carries exactly one BOM.

Today the collector registers one asset on hostname, caches its `asset_id` in
`~/.config/openaca/remote.toml`, and sends one upload per sync. One cached id cannot
address several agents, so something has to give — either the asset splits, or the
upload count grows, or the envelope grows.

The hosted side is gaining per-agent awareness in parallel, and resolves an agent from
`metadata.component` in the document rather than from registration. So the wire needs no
new field to say which agent an upload describes; the document already says it.

## Decision

A sync registers **one asset — the machine — and sends one upload per discovered
agent**. The registration payload, its hostname key, and the cached `asset_id` are
unchanged. Nothing is added to the upload envelope: the agent is named inside the
document, in `metadata.component`. An agent whose upload fails on the network is written
to the pending cache and retried on a later sync while the other agents' uploads stand.

## Alternatives considered

- **One asset per agent** — rejected because it changes what an asset means. The asset
  carries the owner, the team, and the remediation route, and all three are properties
  of the machine, not of a runtime installed on it. Splitting would double the asset
  count for a single laptop, make "how many machines are affected" unanswerable without
  a de-duplication rule, and break the single-`asset_id` registration cache the
  collector is built around. An agent is a child of an asset, not a replacement for one.
- **One request carrying an array of agent BOMs** — rejected because it churns a shipped
  request contract to solve a problem that does not exist yet: one kind ships, so one
  agent resolves, so one upload is sent. It also converts a per-agent network failure
  into an all-or-nothing one, and the offline-cache replay path is built around single
  payloads.
- **A new envelope field naming the agent** (`agent_kind` / `agent_id` beside
  `asset_id`) — rejected as a second source of truth for a fact the document already
  carries. Two copies of the agent identity can disagree, and the one inside the BOM is
  the one the schema validates.
- **Refusing to upload when more than one agent resolves**, pending a hosted capability
  advertisement — rejected because the hosted side is gaining per-agent awareness in
  parallel. The guard would protect a window that never opens, and would then block
  correct multi-agent uploads until someone remembered to delete it.

## Consequences

The asset count keeps meaning the machine count, and no hosted or console concept has to
be renamed. Multi-agent syncs work by sending more of the same request rather than a new
one, so the envelope, the offline cache, and the replay path are unchanged.

The cost is N requests per sync where there was one. That is one request today and is
only worth revisiting for a kind with same-kind multiplicity — a managed runtime holding
dozens of agents in one account would make per-request overhead real. Retry semantics
also become per-agent rather than per-sync: a sync can now be partially applied, which
is correct only because the hosted side tracks each agent's state separately.

## When to revisit

- If an asset ever stops meaning one machine or repository — the whole shape derives
  from that meaning.
- If a kind with same-kind multiplicity ships and a sync starts sending tens of
  requests, revisit batching. Not before.
- If the hosted side ever needs to know the agent before parsing the document (routing,
  quota, authorization), the envelope-field alternative comes back with a real reason.
