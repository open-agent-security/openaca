---
id: 0021
title: Use skills CLI lockfiles as source provenance for direct skills
status: accepted
date: 2026-05-20
supersedes: null
superseded-by: null
---

## Context

Closed-beta feedback showed that direct skills can lose useful source
attribution. A tester had skills activated from `~/.claude/skills`, but those
entries were symlinks into the canonical skills CLI store under
`~/.agents/skills`. OpenACA reported them as direct skills with no indication
of where they originally came from. A second tester had project-local skills
that were copied manually from GitHub; those should remain source-unknown
because no machine-readable provenance remains.

The skills CLI writes lockfiles that preserve source information:
`~/.agents/.skill-lock.json` for global installs, and `skills-lock.json` for
project installs. These lockfiles are not registries and do not make
`skills.sh` the source ecosystem. They are observation evidence that can connect
an activated skill directory back to an underlying source such as GitHub,
node_modules, or a local path.

## Decision

OpenACA reads skills CLI lockfiles to enrich direct skill inventory with source
provenance. The activated skill remains a direct skill: `source_manifest` stays
at the `SKILL.md` path Claude sees, and `attributed_to` remains reserved for
components discovered through an active plugin. When a direct skill resolves to
a canonical skills CLI path and a lock entry exists, OpenACA records the lock
entry under `ComponentRef.extra.source_provenance`. When the skill is symlinked
but no lock entry is found, OpenACA records the resolved target only. When the
skill is a manual copy with no symlink and no lockfile evidence, OpenACA records
no source provenance.

## Alternatives considered

- **Treat symlinked skills as plugin-bundled components**: rejected because the
  Claude activation context is still direct. `attributed_to` answers "which
  active plugin caused this component to be present?"; symlink source recovery
  answers a different question.
- **Make `skills.sh` a source ecosystem**: rejected because the lockfile points
  at the underlying source. The same skill can be discoverable through multiple
  catalogs, but the installed artifact is sourced from GitHub, node_modules, or
  a local path.
- **Use recovered source provenance for advisory matching immediately**:
  rejected for V0 because this changes matching semantics. First make inventory
  truthful and visible; matching against recovered source identity can be a
  later decision once source identity shape is stable.

## Consequences

Verbose endpoint output becomes more useful for skills installed through the
skills CLI: users can see both that Claude activates the skill directly and
where the skill came from. Manual copies remain correctly labeled by omission:
OpenACA does not guess GitHub provenance from directory names or comments.

The scanner gains a dependency on a third-party lockfile shape, so the parser
must be tolerant: malformed files and malformed entries are ignored rather than
failing scans. The metadata lives in `extra` because it is scanner observation
data, not overlay schema.

## When to revisit

Revisit if OpenACA starts matching advisories against recovered skill source
identity, if the skills CLI changes its lockfile shape incompatibly, or if an
Agent BOM format standardizes a source-provenance field that should replace the
current `extra.source_provenance` shape.
