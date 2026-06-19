---
status: accepted
date: 2026-06-19
---

# ADR-0036: Skill-bundled dependencies are agent-dependencies (in scope)

## Context

OpenACA classifies a dependency manifest (`package.json`, `pyproject.toml`, …) as
`agent-dependency` (in scope) or `software-dependency` (filtered before OSV
federation) based on whether an agent-component marker sits in its *immediate*
parent directory. This keeps OpenACA from behaving like a general SCA tool: deps
belonging to ordinary software that merely lives in the repo are out of scope.

Until now the only marker recognized was `.claude-plugin/plugin.json`. A skill
that bundles its own implementation dependencies — a `package.json` or
`pyproject.toml` beside its `SKILL.md` under `.claude/skills/<name>/` — had those
deps classified as `software-dependency` and filtered out, so OpenACA never
OSV-queried them, even though the skill is a first-class agent component and the
deps are its own implementation.

This created a coverage gap: a known-vulnerable dependency bundled in a skill was
invisible to OpenACA and surfaced, if at all, only via an external scanner.

## Decision

A dependency manifest whose immediate parent directory also contains a `SKILL.md`
is an `agent-dependency`, exactly as one beside a `.claude-plugin/plugin.json` is.
Skills are agent components; their bundled deps are part of the agent composition
and are scanned and OSV-matched like any other agent dependency.

The check stays narrow (immediate parent dir only), mirroring the plugin rule, so
general repo software without an adjacent skill/plugin marker remains out of scope.

## Consequences

- OpenACA surfaces known-vulnerable dependencies bundled inside skills via its own
  advisory path, closing the gap that previously hid them.
- External scanner findings about the same skill deps (e.g. a SkillSpector
  known-vulnerable-dependency rule) are redundant with OpenACA's own coverage and
  can be excluded to avoid double-reporting.
- Scans of repos/endpoints containing skills-with-deps report more findings; this
  is correct — those deps were always part of the agent's composition.

## Rejected

- **Treat skill-bundled deps as general software (status quo).** A skill's
  implementation deps are not incidental repo software; excluding them hid real
  vulnerabilities in agent components. The "not a general SCA tool" framing is
  preserved by the narrow adjacent-marker rule, which still filters unmarked
  software.
