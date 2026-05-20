# Source Ecosystem and Component Type Cleanup Plan

**Goal:** Make scanner internals and output reflect ADR-0019: `ecosystem` is the
source naming/versioning space, while `component_type` is the agent-stack role.

**Scope:** V0 parser/matcher/output cleanup only. Do not expand the canonical
overlay schema in this plan.

## Tasks

- [x] Add ADR-0019 and supersede ADR-0018 frontmatter/index entry.
- [x] Register plan 018 in the plan index.
- [x] Add failing parser tests: skills, plugins, hooks, commands, and agents do
  not emit component-type names as `ecosystem`.
- [x] Add failing output test: source-less agent components render
  `source.status: unknown`.
- [x] Add failing compatibility matcher tests for legacy `skill`,
  `claude-skill`, and `claude-plugin` affected ecosystems.
- [x] Update parsers to set `extra.component_type` and leave `ecosystem` unset
  for source-less agent components.
- [x] Update render/finding output to categorize by `component_type` and avoid
  source identity for source-less refs.
- [x] Update matcher to use source ecosystems first and legacy component-type
  matching only as a transition path.
- [x] Update tests and docs from component-type ecosystems to component types.
- [x] Run focused parser/matcher/render tests.
- [x] Run full verification: `ruff format --check`, `ruff check`, `pyright`,
  `pytest -q`, and `openaca lint overlays/`.
