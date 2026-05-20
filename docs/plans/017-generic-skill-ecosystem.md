# Generic Skill Ecosystem Implementation Plan

**Goal:** Rename the public scanner ecosystem for agent skills from `claude-skill` to generic `skill`, while preserving `claude-skill` as a pre-release matching alias.

**Architecture:** Keep Agent BOM component type and runtime host separate. The SKILL.md parser emits `ecosystem="skill"` and `component_identity="skill/<name>[@version]"`; renderers group `skill` refs under the existing `skills/` tree; the matcher treats `skill` and legacy `claude-skill` as equivalent for affected-package matching only.

**Tech Stack:** Python Click CLI, dataclass `ComponentRef`, pytest, pyright, ruff.

---

## Files

- Modify `docs/adrs/0007-component-inventory-and-host-adapters.md` frontmatter only to mark it superseded by ADR-0018.
- Create `docs/adrs/0018-generic-skill-ecosystem-and-agent-bom-fields.md`.
- Modify `docs/adrs/INDEX.md` to move ADR-0007 to superseded and add ADR-0018 to active.
- Modify `docs/plans/README.md` to register plan 017.
- Modify `tools/parsers/claude_skill.py` to emit `skill` refs.
- Modify `tools/matcher.py` to alias `skill` and `claude-skill` for versioned advisory matching.
- Modify `tools/render.py` to group and display both `skill` and legacy `claude-skill` refs under `skills/`.
- Modify `tools/osv_federation.py` comments/tests so generic `skill` remains non-queryable unless a real source PURL is present.
- Update tests under `tests/` from `claude-skill` to `skill`, with explicit compatibility tests for old `claude-skill` advisories.

## Tasks

- [x] Add ADR-0018 and supersede ADR-0007 frontmatter/index entry.
- [x] Register plan 017 in the plan index.
- [x] Write failing parser tests expecting `ecosystem="skill"` and `component_identity="skill/..."`.
- [x] Update `tools/parsers/claude_skill.py` to emit generic skill refs.
- [x] Write failing matcher test proving a `skill` ref matches a legacy `claude-skill` advisory.
- [x] Add matcher ecosystem aliasing for `skill` / `claude-skill`.
- [x] Update render/tree tests and renderer grouping to use `skill` while still accepting legacy `claude-skill` refs.
- [x] Update OSV federation tests/comments for the generic `skill` ecosystem.
- [x] Update e2e and parser tests to canonical `skill` advisories.
- [x] Run focused tests for parser, matcher, render, federation, and e2e.
- [x] Run full verification: `ruff format --check`, `ruff check`, `pyright`, `pytest -q`, and `openaca lint overlays/`.
