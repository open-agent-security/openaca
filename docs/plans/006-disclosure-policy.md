# 006 — Disclosure Policy and Contributor Docs

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the documentation V0 needs to be a credible OSS project: a coordinated-disclosure policy (adopted from OpenSSF baseline, with ASVE-specific defaults), a `SECURITY.md` pointing to it, and a `CONTRIBUTING.md` covering advisory authoring, linter discipline, ID reservation, and the PR workflow.

**Architecture:** Documentation-only plan. No code, no tests beyond markdown linting. The disclosure policy *documents* the V0 process; per the V0 spec, ASVE V0 does **not** operate the disclosure pipeline at scale — that's gated to V1.

**Tech Stack:** Markdown. No build step.

**Depends on:** none (can run in parallel with 001–005).

---

## File structure

| File | Purpose |
|---|---|
| `docs/disclosure-policy.md` | Full coordinated-disclosure policy |
| `SECURITY.md` | Repo-root pointer to the policy + how to report |
| `CONTRIBUTING.md` | Advisory authoring guide + PR workflow |

---

## Task 1: Coordinated disclosure policy

**Files:**
- Create: `docs/disclosure-policy.md`

- [ ] **Step 1: Write the policy**

```markdown
# ASVE Coordinated Disclosure Policy

ASVE follows the [OpenSSF coordinated disclosure
guidance](https://openssf.org/) with the project-specific defaults captured
below. The policy defines what we commit to, on what timeline, and how
disputes are handled.

## V0 status

ASVE V0 documents this policy. **V0 does not operate an active disclosure
program.** Submissions described here will not be processed at scale until
V1, which is gated on the readiness criteria in
[`docs/specs/asve-v0-design.md`](specs/asve-v0-design.md) §10.

When V0 receives a report that meets the bar for inclusion, the maintainers
will run a single end-to-end coordinated-disclosure case as part of the
V1 readiness gate. That case proves the framework before active disclosure
scales.

## Scope

ASVE accepts reports for vulnerabilities affecting **agent-stack
components** that are publicly distributed and identifiable by version or
stable hash:

- MCP servers (npm, PyPI, GitHub-hosted, container).
- Claude Code plugins distributed via marketplaces.
- Skill bundles with a stable identifier.
- Agent frameworks and model proxies that integrate into agent runtimes.

Out of scope:

- Vulnerabilities in agent applications themselves (file upstream with the
  application's maintainer).
- Configuration patterns that don't tie to a specific component instance
  (V1 — `type: config`).
- AI model behavioral failures unrelated to a specific component
  (out of scope entirely; not what ASVE catalogs).

## How to report

Email `security@asve.dev` with:

- Affected component (name + version or commit SHA).
- Reproduction steps or proof-of-concept.
- Impact analysis: which `agent_impact` dimensions are reachable
  (`repo_read`, `repo_write`, `credential_exfiltration`, `tool_hijack`,
  `memory_poisoning`, `pr_manipulation`, `code_execution`).
- OWASP Agentic Top 10 categories you believe apply (`asi01`–`asi10`).
- Whether you have already contacted the affected maintainer.

Encrypted submissions: include a PGP key in your initial message; we will
respond with our key and switch to encrypted exchange.

## Process and timeline

| Stage | Default timeline |
|---|---|
| Acknowledgement | within 5 business days of receipt |
| Maintainer-response checkpoint | 21 days from initial notice to upstream maintainer |
| Embargo | 90 days from acknowledgement (default) |
| Nonresponsive review | 35 days; if maintainer is unresponsive at 35 days, ASVE re-evaluates publication path |
| Publication | within 7 days of fix availability or embargo expiry |

**Active exploitation** accelerates the timeline. If credible evidence
indicates active exploitation, ASVE may publish ahead of the default
embargo on a case-by-case basis.

## Dispute lifecycle

Each ASVE record has a status:

```
published → disputed → modified | upheld | withdrawn
```

- **published**: the record is live in the corpus.
- **disputed**: an affected maintainer or downstream contests the record.
  ASVE marks the record `disputed` and pauses propagation.
- **modified**: ASVE accepts the dispute and revises the record.
- **upheld**: ASVE rejects the dispute. The record stays published with
  the dispute history attached.
- **withdrawn**: ASVE retracts the record (false-positive, duplicate, or
  out-of-scope).

A disputed record always carries a public dispute history so consumers
can see what changed and why.

## Attribution and credit

- Reporter credit: ASVE includes reporter attribution in the published
  record unless the reporter requests anonymity.
- Tooling attribution: where a finding originated from a third-party
  open-source scanner, ASVE attributes the tool by name and version
  (e.g., "detected during ASVE triage using <tool> v0.X"). Attribution
  is descriptive — it does not imply endorsement, partnership, or
  third-party confirmation.

## Aliases and upstream submission

- Records aliasing existing CVE/GHSA/OSV require no upstream filing —
  the upstream record already exists.
- ASVE-original component vulnerabilities: ASVE will attempt upstream
  disclosure to CVE/GHSA where the affected ecosystem is accepted by
  upstream pipelines. Where upstream pipelines don't accept the
  ecosystem cleanly, ASVE may carry the authoritative record.

## Out of scope (escalation, indemnity, payment)

ASVE V0 is an OSS advisory database. We do not:

- Pay bug bounties (consider huntr or other dedicated bounty platforms).
- Provide legal indemnity.
- Act as an intermediary for legal threats; if a maintainer asserts a
  legal claim against a reporter, ASVE will not relay or escalate it.

## Contact

`security@asve.dev` for vulnerability reports. For non-security questions,
file a GitHub issue against the repo.
```

- [ ] **Step 2: Commit**

```bash
git add docs/disclosure-policy.md
git commit -m "docs: coordinated disclosure policy"
```

---

## Task 2: `SECURITY.md` at repo root

**Files:**
- Create: `SECURITY.md`

- [ ] **Step 1: Write the file**

```markdown
# Security Policy

ASVE follows a coordinated-disclosure process. Read the full policy at
[`docs/disclosure-policy.md`](docs/disclosure-policy.md).

## Reporting a vulnerability

**Do not** open public GitHub issues for unembargoed vulnerabilities. Email
`security@asve.dev` instead. We acknowledge reports within 5 business
days.

## V0 status

ASVE V0 documents the disclosure process. The active disclosure pipeline
starts at V1, gated on readiness criteria in
[`docs/specs/asve-v0-design.md`](docs/specs/asve-v0-design.md) §10.
Reports submitted during V0 will be acknowledged and triaged but may not
be processed end-to-end until V1.

## Scope, timelines, and dispute lifecycle

See [`docs/disclosure-policy.md`](docs/disclosure-policy.md).
```

- [ ] **Step 2: Commit**

```bash
git add SECURITY.md
git commit -m "docs: SECURITY.md pointer to disclosure policy"
```

---

## Task 3: `CONTRIBUTING.md` at repo root

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Write the file**

```markdown
# Contributing to ASVE

ASVE is an open-source advisory database. Contributions are welcome:
new advisories, parser improvements, schema clarifications, documentation,
and bug fixes.

This guide covers the most common contribution flow: filing or updating
an advisory.

## Before you start

- Read [`docs/specs/asve-v0-design.md`](docs/specs/asve-v0-design.md) for
  the V0 scope and architecture.
- Read [`CLAUDE.md`](CLAUDE.md) for project-wide conventions, including
  the OSS-only scope rules.
- For security reports of active vulnerabilities, follow
  [`SECURITY.md`](SECURITY.md). Do not file new advisories as public PRs
  before coordinated disclosure has run.

## Project setup

The project uses [`uv`](https://docs.astral.sh/uv/) for environment and
dependency management. Install uv first if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # or: brew install uv
```

Then:

```bash
git clone git@github.com:open-agent-security/asve.git
cd asve
uv sync
```

`uv sync` reads `.python-version` and `uv.lock`, creates a `.venv`, and
installs all runtime + dev dependencies.

You should now have these CLIs (invoked via `uv run`):

- `uv run asve-lint <path>` — validate advisories.
- `uv run asve-reserve-id <advisories-dir> --year YYYY` — print the next
  free ID.
- `uv run asve-import-osv --osv-file FILE --asve-id ID --out PATH` —
  generate an advisory skeleton from an OSV record.
- `uv run asve-export` — build the static export under `dist/`.
- `uv run asve-scan --target REPO --advisories DIR --sarif OUT` — run the
  reference scanner.

Run the test suite:

```bash
uv run pytest
```

## Filing an advisory

1. **Reserve an ID** for the current year:
   ```bash
   uv run asve-reserve-id advisories/ --year 2026
   # ASVE-2026-NNNN
   ```

2. **Generate a skeleton** if the vulnerability already has an OSV/GHSA
   record:
   ```bash
   uv run asve-import-osv --osv-id GHSA-XXXX-YYYY-ZZZZ \
                          --asve-id ASVE-2026-NNNN \
                          --out advisories/2026/ASVE-2026-NNNN.yaml
   ```
   Or hand-write the YAML using
   [`tests/fixtures/valid/asve-2026-0001.yaml`](tests/fixtures/valid/asve-2026-0001.yaml)
   as a model.

3. **Fill in the `database_specific.asve` block** — this is what
   distinguishes an ASVE record from a passthrough alias:
   - `component_type` (e.g., `mcp_server`, `claude_plugin`, `model_proxy`,
     `agent_framework`, `skill_bundle`).
   - `surfaces`: which agent surfaces the component touches.
   - `agent_impact`: boolean table of attainable impacts.
   - `owasp_agentic_top10`: array of `asi01`–`asi10` categories.
   - `evidence_level`: `confirmed` | `likely` | `research` | `disputed`
     | `withdrawn`.

4. **Add a CVSS v4 vector** under `severity[]` if known:
   ```yaml
   severity:
     - type: CVSS_V4
       score: "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
   ```

5. **Lint locally**:
   ```bash
   uv run asve-lint advisories/2026/ASVE-2026-NNNN.yaml
   ```
   Fix any failures before opening a PR.

6. **Open a PR** with the advisory file and a one-paragraph summary of
   what the advisory covers.

## V0 record-type policy

V0 accepts only `type: vulnerability` records. PRs proposing
`type: exposure` or `type: config` records are closed with a pointer to
the V1 methodology track. The schema reserves these values; runtime
acceptance is gated by an explicit V1 process.

## Linter discipline

The CI linter has two tiers:

**Hard fail** (your PR will not merge):
- Schema validation.
- ID format and uniqueness within `ASVE-YYYY-NNNN`.
- Required fields per `type`.
- CVSS v4 vector parses.
- OWASP ASI categories are valid (`asi01`–`asi10`).
- File path matches ID year and number.
- Internal cross-references resolve.

**Warning only** (separate scheduled job):
- Link liveness.
- OSV/GHSA enrichment via remote APIs.
- Remote alias resolution.

Don't try to "fix" warnings on every PR. They run nightly against the
full corpus, so transient remote-API failures don't block authors.

## Aliasing policy

- If your advisory aliases an existing CVE/GHSA/OSV, list the upstream IDs
  in `aliases[]`. ASVE creates the alias and overlays agent-context
  metadata. **No new upstream filing required.**
- If your advisory is ASVE-original, attempt upstream disclosure to
  CVE/GHSA where the affected ecosystem is accepted upstream. ASVE will
  carry the authoritative record only when upstream pipelines don't fit.

## Code contributions (parsers, linter, scanner)

- Follow TDD for non-trivial logic. Write the failing test first.
- Match existing code style; no unrelated refactors.
- Default to writing no comments. Add one only when *why* is non-obvious.
- One logical change per commit. Commit messages focus on *why*, not
  *what*.
- Run `uv run pytest` and `uv run ruff check tools/ tests/` before opening a PR.

## What does not belong in this repo

Per [`CLAUDE.md`](CLAUDE.md), all artifacts here are OSS-focused. Do not
include in PRs:

- Commercial product plans or monetization framings.
- Comparisons against vendor products positioning ASVE as a competitor.
- Market analysis, sales narratives, go-to-market content.
- Vendor names framed as competitors. (Naming a tool we *use* with
  attribution is fine; naming a product as a competitor is not.)

If a draft contains content in those categories, rewrite to remove. When
unsure, ask in the PR.

## Code of conduct

Treat reporters, maintainers, and affected projects with respect. Disagree
on technical merits, not on people. Coordinated disclosure is collaboration
with affected maintainers — approach it as such.
```

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: contributor guide for advisory authoring and code"
```

---

## Verification

```bash
ls SECURITY.md CONTRIBUTING.md docs/disclosure-policy.md
```

Sanity-check links resolve from a freshly-cloned repo:

```bash
grep -E "\(\.?\.?/?(docs|tests|schema|advisories)/" SECURITY.md CONTRIBUTING.md docs/disclosure-policy.md
```

All referenced paths should exist (or be promised by sibling plans 001–005).

---

## Self-review checklist

- [ ] **Disclosure policy** captures: scope, timelines (5/21/35/90/7), dispute lifecycle, attribution, upstream policy, out-of-scope items.
- [ ] **V0 status** is explicit: policy is documented, not operated at scale; first real case is part of V1 readiness gate.
- [ ] **`SECURITY.md`** is a thin pointer; the substance lives in `docs/disclosure-policy.md`.
- [ ] **`CONTRIBUTING.md`** documents: setup, advisory authoring flow, linter discipline tiers, aliasing policy, code style, OSS-only scope rule.
- [ ] **OSS-only scope** is reinforced explicitly, mirroring `CLAUDE.md`.
- [ ] **No commercial / competitor framing** in any of the three documents.
