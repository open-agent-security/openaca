---
status: accepted
date: 2026-06-19
---

# ADR-0036: Skill-bundled supported dependency manifests are in scope

## Context

OpenACA classifies a supported dependency manifest or lockfile as
`agent-dependency` (in scope) or `software-dependency` (filtered before OSV
federation) based on whether an agent-component marker sits in its *immediate*
parent directory. This keeps OpenACA from behaving like a general SCA tool:
deps belonging to ordinary software that merely lives in the repo are out of
scope.

Until now the only marker recognized was `.claude-plugin/plugin.json`. A skill
that bundles its own implementation dependencies — a `package.json` or
`pyproject.toml` beside its `SKILL.md` under `.claude/skills/<name>/` — had those
deps classified as `software-dependency` and filtered out, so OpenACA never
OSV-queried them, even though the skill is a first-class agent component and
the deps are its own implementation.

This created a coverage gap: a known-vulnerable dependency bundled in a skill was
invisible to OpenACA and surfaced, if at all, only via an external scanner.

External scanners can also perform their own vulnerability lookups against
dependency files they discover while auditing a skill directory. Those advisory
matches are vulnerability claims, not posture or observation claims. OpenACA does
not yet have an external vulnerability-source ingestion and deduplication path;
dependency vulnerability findings currently come from OpenACA's own OSV
federation over natively parsed dependency manifests and lockfiles.

## Decision

A supported dependency manifest or lockfile whose immediate parent directory
also contains a `SKILL.md` is an `agent-dependency`, exactly as one beside a
`.claude-plugin/plugin.json` is. Skills are agent components; their bundled
deps are part of the agent composition and are scanned and OSV-matched like any
other agent dependency.

The check stays narrow (immediate parent dir only), mirroring the plugin rule, so
general repo software without an adjacent skill/plugin marker remains out of scope.

External scanner vulnerability findings are intentionally skipped for now when
they belong to the scanner's advisory-lookup family. Adding native parser
coverage for additional dependency formats, or adding an external
vulnerability-source ingestion and deduplication path, is separate follow-up
work.

## Consequences

- OpenACA surfaces known-vulnerable dependencies bundled inside skills when they
  are declared in dependency formats OpenACA already parses.
- Unsupported skill-bundled dependency files may not produce vulnerability
  findings while external scanner advisory-lookup results are skipped. This is
  an accepted gap until native parser coverage or external vulnerability
  ingestion is added.
- Scans of repos/endpoints containing skills-with-deps report more findings; this
  is correct — those deps were always part of the agent's composition.

## Rejected

- **Treat skill-bundled deps as general software (status quo).** A skill's
  implementation deps are not incidental repo software; excluding them hid real
  vulnerabilities in agent components. The "not a general SCA tool" framing is
  preserved by the narrow adjacent-marker rule, which still filters unmarked
  software.
- **Add new dependency parsers as part of this scanner-adapter PR.** New parser
  coverage should be reviewed on its own merits with format-specific tests. This
  decision only scopes already-supported dependency formats and records how
  external advisory-lookup output is handled for now.
- **Ingest external scanner advisory matches as observations.** Advisory matches
  are vulnerability claims. Routing them through observations would blur the
  claim-type model and make later advisory deduplication harder.
