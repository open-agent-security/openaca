# Plugin-First CLI Primitives Spec

## Goal

Support the Claude plugin-first workflow with fast, local CLI primitives that
answer "what changed?" without forcing a full advisory scan every time.

## V1 Primitive

This iteration adds `openaca bom diff`.

`bom diff` compares two CycloneDX Agent BOM files and reports:

- added components,
- removed components,
- changed components,
- added composition edges,
- removed composition edges.

The diff is local and does not query advisory sources. It is intended for
interactive plugin workflows where a user or hook wants to understand recent
agent-stack changes before deciding whether to run a deeper scan.

## Identity

Component diff identity uses `bom-ref`, which is the graph occurrence key in
OpenACA graph-backed BOMs. This intentionally answers "what changed in this
installed composition?" rather than "is this the same package coordinate?"

The human summary displays `openaca:identity` when present because it is the
canonical component identity users recognize across occurrences.

## Output

The default output is a concise text summary for interactive use. A `--format
json` option emits a stable machine-readable object for plugin workflows.

## Deferred

The following primitives are intentionally deferred:

- path-scoped `scan skill/plugin/mcp`,
- endpoint graph selectors such as `openaca scan endpoint --select ...`,
- "scan only changed BOM components."

Those need a graph-selector design so CLI targets, BOM occurrence keys, and
component identities do not diverge.

