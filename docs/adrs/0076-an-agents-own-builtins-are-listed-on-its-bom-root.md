---
id: 0076
title: An agent's own sub-agents and skills are listed on its BOM root
status: proposed
date: 2026-10-11
supersedes: null
superseded-by: null
---

## Context

Every agent kind OpenACA reads provides some sub-agents and skills itself.
Claude Code ships its `Explore` and `general-purpose` sub-agent types and a few
dozen skills inside its executable. Devin defines its `subagent_explore` and
`subagent_general` profiles in its binary. Codex writes its own skills to
`skills/.system/` under a marker file, and Cursor syncs its own into
`skills-cursor/`.

None of these is in a BOM. The executable's contents have no manifest, so no
filesystem scan can find them. The vendor roots are excluded on purpose:
ADR-0052 keeps `skills-cursor/` out and ADR-0055 keeps Codex's marked root out,
because reporting vendor content as the user's composition is wrong on every
endpoint.

That exclusion is still right. But a consumer that joins an agent's recorded
activity to its BOM can't distinguish a call to `Explore` from a call to a
sub-agent that was never installed. Both are absent from the components. Each
consumer then keeps its own list of every kind's built-ins, and that per-kind
knowledge is exactly what OpenACA exists to hold.

## Decision

1. **A kind lists what an installed agent of it provides itself.**
   `AgentKind.builtins` returns the sub-agent and skill names. Each kind reads
   them the most stable way available:
   - **Codex and Cursor** read their vendor roots: the marker-bearing root
     (ADR-0055) and `skills-cursor/` (ADR-0052). They never use a name list.
   - **Claude Code and Devin** use a list, because they keep these inside the
     executable and nothing on disk names them. Each list records the release
     it was read from.
2. **They are listed on the BOM root, never as components.** The root carries
   `openaca:builtin_agents` and `openaca:builtin_skills`, each a JSON array of
   unique names. Each is absent when the kind lists none. Nothing installed
   them and nothing can remove them, so they are not part of the composition.
   No posture rule, policy admission, vulnerability coverage or component count
   ever sees them. ADR-0052 and ADR-0055 stand as written.
3. **Installed agents only.** A repository declares what it adds to an agent,
   and the agent's own built-ins are not part of that. A declared document
   never carries these properties.
4. **Installed wins.** A consumer resolving a call by name asks the components
   first. A user's skill named `verify` is the one that ran, whatever the agent
   ships under that name.

## Alternatives considered

- **Built-ins as components marked `openaca:builtin`.** Rejected. Every
  consumer of components would need to skip them: posture rules, skill
  scanning, policy admission (a default-blocked skills policy would block every
  built-in), vulnerability coverage (every built-in listed as unassessed),
  counts and the inventory render. Each is a site that forgets. On the root,
  nothing that walks components can reach them.
- **Nested components under `metadata.component`.** Rejected. Nested
  components are CycloneDX's assembly shape, and readers that walk them would
  meet the same problem one level down.
- **Keep the lists in each consumer.** Rejected. That is the per-kind table
  this package exists to hold, and every consumer's copy drifts on its own.
- **Read the executable at scan time.** Rejected. Extracting definitions from a
  minified bundle or a compiled binary depends on how each release happens to
  be built, and breaks without notice. A list read from a named release and
  re-read on the next audit fails visibly.
- **One property per name.** Rejected. `bom lint` refuses a repeated `openaca:*`
  property on one component.

## Consequences

- The Claude Code and Devin lists drift between audits. Leaving a name out
  costs a consumer one call it reads as absent, which is a visible gap.
  Including a name that was never built in would attribute a call to the agent
  silently. So the lists hold only names read as definitions from the named
  release. Claude Code's plugin-authoring docs show example skills, such as
  `meeting-notes`, as frontmatter, and those are not built-ins.
- A built-in that a release removes stays listed only when sessions recorded
  before still name it (`output-style-setup`).
- The vendor roots are now read twice: once to keep them out of the
  composition and once to list their names. For Codex, both reads find the
  root with the same marker check, so they cannot disagree about what is vendor
  content. Cursor's composition never lists `skills-cursor/` as a skills root,
  and only the built-in listing reads it.
- Cursor also reads `~/.codex/skills`, and so inventories Codex's `.system`
  skills as Cursor components. That is unchanged here. Those skills are Codex's
  built-ins, not Cursor's, and the fix belongs in a Cursor change.

## When to revisit

- When an agent exposes its own built-ins through a command or a file, read
  them there instead of from a list.
- When a consumer needs to know which release a list was read from, put the
  release on the root too.
