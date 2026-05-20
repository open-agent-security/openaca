# Skill Lock Source Provenance Plan

**Goal:** Parse skills CLI lockfiles and surface source provenance for direct
skills, especially symlinked `~/.claude/skills/*` entries that resolve into
`~/.agents/skills/*`.

**Architecture:** Add a focused lockfile parser for global `.skill-lock.json`
and project `skills-lock.json` formats. Endpoint direct-skill walking keeps the
activation path as `source_manifest`, resolves symlink targets for provenance
lookup, and attaches any recovered source metadata under
`extra.source_provenance`. Verbose inventory renders a compact provenance note
for direct skills only.

## Tasks

- [x] Add ADR-0021 documenting lockfile provenance vs activation context.
- [x] Register plan 020 in the plan index.
- [x] Add parser tests for global v3 `.skill-lock.json` and project v1
  `skills-lock.json`, including malformed-entry tolerance.
- [x] Implement `tools/parsers/skill_lock.py` with normalized lock entries.
- [x] Add endpoint parser tests for symlinked direct skills with lockfile
  provenance, symlinked skills without lock entries, and manual copied skills.
- [x] Wire direct skill walking to attach `extra.source_provenance` while
  preserving `source_manifest` and `attributed_to=None`.
- [x] Add render tests for compact direct-skill source labels.
- [x] Render known source provenance and symlink-target fallback in verbose
  direct component output.
- [x] Run focused tests for skill lock parsing, Claude install walking, and
  render output.
- [x] Run the full verification gate: ruff format/check, pyright, pytest, and
  `openaca lint overlays/`.
