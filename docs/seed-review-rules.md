# OpenACA candidate annotation rules

Canonical rules for annotating reviewable seed candidates under
`candidates/` and the canonical overlays under `overlays/`. Both the
deterministic seeder, the LLM annotator (when used), and the
candidate-review skill (when used) MUST conform.

This document is the single source of truth for review behavior. It is
read by `tools/seed/validator.py` and `tools/lint.py` (which enforce
the structural subset in CI), by reviewers, and by the Claude Code
skill at `examples/skills/claude/openaca-candidate-review/SKILL.md`.

## Editable fields

When annotating or re-reviewing a candidate, ONLY edit:

- `database_specific.openaca.taxonomies.*` (framework mapping arrays)
- `database_specific.openaca.evidence_level` (one of:
  `confirmed`, `likely`, `research`, `disputed`, `withdrawn`)

## Forbidden fields (do not set, edit, or remove via review)

- `database_specific.openaca.threat_kind` — seeder-owned (see below)
- `_candidate.*`, `_evidence.*` — review metadata, set by the seeder
- `id`, `aliases`, `modified`, `summary`, `details`, `references`,
  `affected`, `severity` — upstream-owned, sourced from the OSV record

## Deterministic fields owned by the seeder

`threat_kind` is allowed only when the record's `id` starts with `MAL-`
OR `aliases` contains a `MAL-*` entry. In that case `threat_kind`
MUST be `"malicious_package"`. For all other records (ordinary CVEs,
GHSAs, authz bugs, code execution flaws in agent-component packages),
`threat_kind` MUST be omitted entirely.

Rationale: per ADR-0012, `threat_kind` flags genuinely malicious or
backdoored packages. Ordinary vulnerabilities in agent-component
packages are vulnerabilities, not malware; setting `threat_kind` for
them misroutes downstream users.

## Structural rules (CI-enforced)

The validator (`tools/seed/validator.py`) and overlay linter
(`tools/lint.py`) reject:

1. `threat_kind` set when neither `id` nor any `aliases[]` entry
   begins with `MAL-`.
2. Empty taxonomy arrays (e.g., `owasp_mcp_top10: []`). Omit the key
   instead.
3. Empty `supplemental_taxonomies: {}`. Omit the key instead.
4. Unknown keys under `database_specific.openaca` — schema enforces
   `additionalProperties: false` and rejects via JSON-schema
   validation.

## Semantic rules (review-enforced)

These cannot be checked structurally and depend on reading the OSV
record. The skill and human reviewers MUST apply them:

1. **No supply-chain mapping without supply-chain evidence.** Do not
   set `owasp_agentic_top10: [asi04]`, `owasp_llm_top10: [llm03:2025]`,
   or `mitre_atlas: [AML.T0010.*]` unless the OSV record describes a
   malicious package, compromised dependency, typosquat, or
   dependency-confusion attack. A code bug in an agent-component
   package is not a supply-chain incident.
2. **Prefer omission over speculation.** If a framework family does
   not apply, omit the key entirely. Do not emit empty arrays or
   guess-quality mappings.
3. **Quote your evidence.** Each taxonomy assertion should be
   defensible against a quoted line from the OSV `summary`/`details`.
4. **Evidence level discipline.** Use `confirmed` only when the OSV
   record itself states the agent context. Use `likely` when the
   package or component is clearly an agent component and the OSV record
   supports the classification. Use `research` when the mapping is
   plausible but needs reviewer confirmation.

## Review modes

The Claude Code skill supports two modes:

- **Annotate**: candidate has no `database_specific.openaca` block.
  Skill adds it from scratch, applying both structural and semantic
  rules.
- **Re-review**: candidate has an existing `database_specific.openaca`
  block (typically from an earlier API-mode annotation). Skill
  audits it against the rules above, edits to comply, and re-runs
  validation.

## Worked example: Flowise GHSA-mq53-pc65-wjc4

The OSV record describes an Object.assign mass-assignment / IDOR bug
in Flowise's evaluation controller. An authenticated workspace member
can overwrite `workspaceId` on a row they own, exposing captured
prompts and model outputs to other workspaces.

**Correct annotation:**

```yaml
database_specific:
  openaca:
    taxonomies:
      owasp_agentic_top10: [asi03]    # identity / privilege abuse
      owasp_llm_top10: [llm02:2025]   # sensitive info disclosure
    evidence_level: likely
```

**Wrong annotation (nano-style):**

```yaml
database_specific:
  openaca:
    threat_kind: malicious_package    # Flowise is not malicious
    taxonomies:
      owasp_agentic_top10: [asi04]    # not a supply-chain incident
      owasp_llm_top10: [llm03:2025]   # not supply chain
      mitre_atlas: [AML.T0010.001]    # not ATLAS supply chain
      owasp_agentic_skills_top10: []  # empty bucket, omit instead
      owasp_mcp_top10: []             # empty bucket, omit instead
      supplemental_taxonomies: {}     # empty, omit instead
    evidence_level: likely
```
