---
id: 0075
title: The Devin agent kind is `devin`, named Devin
status: proposed
date: 2026-10-10
supersedes: null
superseded-by: null
---

## Context

0.9.0 registered the Devin kind as `devin-cli`, named "Devin CLI", after the
program its composition was read from. Measured on a machine running both
Devin CLI 3000.11.3 and Devin Desktop, that name describes one way of starting
the agent, not the agent:

- Devin Desktop starts its local agent by spawning its own bundled copy of the
  same program (`Devin.app/…/extensions/windsurf/devin/bin/devin acp`, version
  3000.10.48).
- Both read the same configuration (`~/.config/devin`, a project's `.devin/`)
  and write the same data root (`~/.local/share/devin/cli`), so they compose
  from the same surfaces this kind already inventories.

OpenAIDR, which reads that agent's sessions, renames its kind to `devin` in
the same window (its ADR-0024). A consumer joins a session to its composition
by the kind, so the two packages must use one name.

## Decision

The kind id is `devin`, its display name is "Devin", and its BOM root label
and configuration-layer labels drop the `-cli` with it (`devin`,
`devin-legacy`, `devin-data`). `--kind devin` selects it. `devin-cli` is not
an alias and is refused as an unknown kind. ADR-0070 to ADR-0074 keep the old
id: they record what shipped in 0.9.0.

## Alternatives considered

- **Keep `devin-cli`**: no churn — rejected because a Devin Desktop user would
  see their agent inventoried as a CLI they never ran, and the name only gets
  more expensive to change after 0.9.0.
- **Accept `devin-cli` as an alias**: rejected because a kind with two names
  splits every join on `openaca:agent_kind`, and OpenAIDR emits only the new one.
- **Rename the id and keep "Devin CLI" as the display name**: rejected because
  the display name is the one place a reader sees the kind, and it would still
  name the wrong product.

## Consequences

- The BOM's `openaca:agent_kind`, its root name and every Devin component's
  `bom-ref` prefix change, so a consumer that stored Devin components by
  `bom-ref` sees them as new.
- The file `bom endpoint` writes for the kind is `devin.cdx.json`.
