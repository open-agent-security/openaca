---
status: accepted
date: 2026-06-19
---

# ADR-0036: Defer skill-bundled dependency vulnerability coverage

## Context

OpenACA classifies a supported dependency manifest or lockfile as
`agent-dependency` (in scope) or `software-dependency` (filtered before OSV
federation) based on whether an agent-component marker — a
`.claude-plugin/plugin.json` — sits in its *immediate* parent directory. This
keeps OpenACA from behaving like a general SCA tool: deps belonging to ordinary
software that merely lives in the repo are out of scope.

A skill that bundles its own implementation dependencies — a `package.json` or
`pyproject.toml` beside its `SKILL.md` under `.claude/skills/<name>/` — has those
deps classified as `software-dependency` and filtered out, so OpenACA does not
OSV-query them, even though the skill is a first-class agent component. Two ways
to close that gap were prototyped and backed out:

1. Extend `_classify_dep_manifest` with a `SKILL.md` marker (and walk skill
   subdirectories in every layout: `.claude/skills/`, plugin-bundled `skills/`,
   nested project skills) so skill-bundled deps reach the OSV path.
2. Ingest an external scanner's own dependency-vulnerability findings (e.g. the
   SkillSpector SC4 "known-vulnerable dependency" rule) as OpenACA findings.

Both turned out to be the wrong shape for V0. Approach 1 accreted path-shape
heuristics — one per skill layout — that each review round found a new gap in;
the underlying problem is that OpenACA has no composition model from which to
*derive* that a dep belongs to a skill. Approach 2 would route advisory matches
(vulnerability claims) through the scanner-observation adapter, blurring the
claim-type model (ADR-0035) and creating a second advisory source with no
deduplication path against OpenACA's own OSV federation.

## Decision

Skill-bundled dependency vulnerability coverage is **deferred**. In V0:

- Dependency classification recognizes only the `.claude-plugin/plugin.json`
  marker. A dep manifest is `agent-dependency` iff a plugin manifest is in its
  immediate parent directory; skill-adjacent deps remain `software-dependency`
  and are filtered.
- OpenACA's OSV federation over natively parsed, in-scope dependency manifests
  and lockfiles is the **sole** source of dependency vulnerability findings.
  OpenACA does not ingest external-scanner vulnerability findings.
- The SkillSpector observation adapter **skips SC4** (known-vulnerable
  dependency), because it is an advisory-lookup result with no OpenACA
  ingestion path — not because OpenACA already covers it.

The composition graph (see `docs/specs/composition-graph.md`) is the correct
mechanism: it models skills, plugins, and packages as nodes with composition
edges and **derives** dependency scope from a node's lineage, so a skill's
bundled deps fall out as agent-dependencies without any path-shape marker. That
work — and a separate external-scanner vulnerability ingestion + deduplication
path, if pursued — lands skill-bundled-dep coverage properly, after this PR.

## Consequences

- A known-vulnerable dependency bundled inside a skill (and not also reachable
  via a plugin marker) is not surfaced by V0 OpenACA. This is an accepted,
  documented gap, not a silent one.
- The SkillSpector adapter still contributes its posture and observation
  findings; only its SC4 advisory-lookup rule is skipped.
- Dependency classification stays the narrow plugin-marker rule it was before
  this PR — no new skill-layout heuristics to maintain or regress.

## Rejected

- **Extend `_classify_dep_manifest` with skill markers now.** This is the
  approach reviewers keep re-suggesting because the gap is real. It is rejected
  for V0 specifically because per-layout path heuristics are the symptom of a
  missing composition graph; the graph derives scope from lineage and makes the
  coverage correct-by-construction. Adding the heuristics now means building,
  reviewing, and then deleting them when the graph lands.
- **Ingest external scanner advisory matches (SC4) as findings/observations.**
  Advisory matches are vulnerability claims (ADR-0035). Routing them through the
  observation adapter blurs the claim-type model, and ingesting them as
  vulnerability findings needs a deduplication path against OpenACA's own OSV
  federation that does not exist yet.
