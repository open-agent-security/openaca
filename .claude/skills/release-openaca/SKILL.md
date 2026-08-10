---
name: release-openaca
description: Use when cutting a new openaca PyPI release. Walks through version bump, LLM-judged release-notes drafting, pre-flight checks, tag, and push — enforces the publish-pypi.yml notes-file gate so the GitHub Releases page can't silently drift from PyPI.
---

# release-openaca

End-to-end release procedure for the `openaca` Python package. The
workflow at `.github/workflows/publish-pypi.yml` enforces a notes-file
gate (`docs/releases/v<version>.md`) before PyPI publish — this skill is
the human-side counterpart that drafts the file with LLM judgment,
runs the pre-flight checks, and pushes the tag.

**Announce at start:** "I'm using the release-openaca skill to cut a new
openaca release."

## When to invoke

User says any of: "cut a release", "ship 0.1.0bN", "release openaca",
"tag a new version", or starts editing `pyproject.toml`'s version field
in the openaca repo.

## Inputs

The skill needs to know:

1. **Target version** (e.g. `0.1.0b7`). Inferred from `pyproject.toml`
   if the user has already bumped it; otherwise ask.
2. **Theme** (e.g. "openaca.core consumption facade + OSV-native source
   queries"). The H1 line of the notes file. Ask the user — this is the
   one thing a commit log can't infer for you.

## Procedure

### Step 1. Pre-flight checks

Run from the openaca repo root. Stop and ask if any fails.

```bash
# On main, clean tree
git rev-parse --abbrev-ref HEAD                    # must be main
git status --porcelain                              # must be empty
git fetch --tags origin
git rev-parse --verify "v<version>" 2>/dev/null    # MUST FAIL — tag not yet exists
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"    # in sync with remote
```

Surface any failures verbatim to the user; do not try to "fix" a dirty
tree or out-of-sync main automatically — those are signals of in-flight
work that the user must decide on.

### Step 2. Find the previous tag, gather the commit log

```bash
prev_tag=$(git describe --tags --abbrev=0)         # e.g. v0.1.0b6
git log --oneline "${prev_tag}..HEAD" -- .
```

This is the raw material. Do **not** dump the list into the notes file
verbatim.

### Step 2b. Doc currency sweep

If the release renames a CLI surface, changes identity/output shapes,
or alters install behavior, the README is probably stale somewhere the
commit log won't tell you. Before drafting notes:

```bash
# stale CLI names / hardcoded version pins
grep -nE "openaca <old-subcommand>|0\.1\.0b[0-9]" README.md docs/*.md

# identity/output-shape examples — compare claimed identifiers against
# what the release actually emits (run the scanner, don't trust prose)
grep -nE "mcp-stdio/|mcp-server/|<other identity prefixes>" README.md
```

Fold any surgical doc fixes into the release PR (Step 5) — the release
that *documents* a change shouldn't ship alongside a README that
contradicts it. (b7 example: README still showed the pre-ADR-0029
`mcp-stdio/uvx-unpinned:<package>` identifier example.)

### Step 2c. Schema-version drift check (don't skip)

Two formats carry their own version, **independent of the package
version**, and consumers rely on them to detect changes:

- **BOM output:** `OPENACA_BOM_SCHEMA_VERSION` in `tools/bom.py` (emitted
  as the `openaca:schema_version` BOM property) + `schema/openaca-bom.schema.json`.
  The Fleet backend that ingests uploaded BOMs and the site corpus
  pipeline read this to tell formats apart.
- **Overlay record:** `schema_version` rules in `schema/openaca.schema.json`.

A format change that does **not** bump its version field is silent and
breaks downstream change-detection. This has already bitten us: 0.2.0
shipped a graph-encoded Agent BOM (explicit dependencies, `attributed_to`
removed, `library` component type) but left `openaca:schema_version` at
`0.1`. Do not repeat it.

Diff the format-defining files since the previous release:

```bash
# BOM output: emitter + schema + the graph code that shapes it
git diff "${prev_tag}..HEAD" -- tools/bom.py tools/graph_build.py schema/openaca-bom.schema.json
# overlay record format
git diff "${prev_tag}..HEAD" -- schema/openaca.schema.json
# did the BOM version constant move with the format?
git diff "${prev_tag}..HEAD" -- tools/bom.py | grep -E "OPENACA_BOM_SCHEMA_VERSION ="
```

**Decision gate — stop and ask the user** if either holds:

- The emitted BOM structure changed (added/removed components, dependency
  edges, properties, or component types) but `OPENACA_BOM_SCHEMA_VERSION`
  / `schema/openaca-bom.schema.json` did **not** change → bump
  `OPENACA_BOM_SCHEMA_VERSION` and document it under `## Compatibility`.
- The overlay record shape changed but `schema/openaca.schema.json` (and
  the records' `schema_version`) did **not** → bump.

Bump on *consumer-visible format change*, not on every release. If the
format genuinely didn't change, say so in one line in the release PR so
the next release knows the check ran and wasn't an oversight.

### Step 3. Draft `docs/releases/v<version>.md` with LLM judgment

Read the commit list and synthesize themed bullets. Follow the shape
established by existing files in `docs/releases/`:

```markdown
# <version> — <theme>

## Highlights

- **<Theme A>.** 1-3 sentence explanation pitched at a maintainer or
  downstream consumer. Reference ADRs / plans by number when the change
  has a design doc behind it.
- **<Theme B>.** ...
- **<Theme C>.** ...
- **Bug fixes.** Combine small fixes into a single bullet listing them
  as a comma-separated tail rather than one bullet each.

## Install

`uv tool install openaca==<version>` or `pip install openaca==<version>`.

## Compatibility

Pre-V0 — no back-compat hedging. <Call out any behavior changes that
existing downstream code will notice.>
```

**Judgment guidelines (apply ruthlessly):**

- **Group by theme, not by file or commit.** Five commits that
  collectively add Windows path handling are one bullet, not five.
- **Lead with the user-visible impact**, not the implementation. "Stdio
  MCP labels strip CLI flags" — not "refactored `_strip_cli_flags`."
- **Cite ADRs/plans** for design-doc-backed changes. Future readers
  searching for "why did we add X" deserve the pointer.
- **Cut docs-only / format / chore / "wrap long line" commits.** They
  aren't release-visible.
- **Cut release-machinery commits** (`release: bump uv.lock`, `release:
  0.1.0bN`). They're tautological.
- **Group bug fixes** into a single trailing bullet. If a fix is
  load-bearing, promote it to its own themed bullet.
- **Acknowledge breaking changes explicitly** in `## Compatibility`. If
  a label format / file path / public type changed, that goes here.

Show the drafted file to the user and ask: "Drafted notes — does the
theme + bullets capture what shipped?" Iterate until they agree.

### Step 4. Bump `pyproject.toml` version

If the user hasn't already done so:

```bash
sed -i.bak 's/^version = ".*"/version = "<version>"/' pyproject.toml && rm pyproject.toml.bak
uv lock                                            # refresh uv.lock
```

### Step 5. Release-prep commit + PR

If the user's original request did not explicitly ask to cut, ship, or
publish a release, stop before the push commands and ask for confirmation.
Editing `pyproject.toml`'s version field is enough to invoke this skill, but
it is not by itself permission to publish a branch.

```bash
git checkout -b release/<version>
git add pyproject.toml uv.lock README.md docs schema overlays tools/bom.py
git diff --name-only        # MUST be empty; inspect/stage intended release-prep files or stop
git commit -m "release: <version> — <theme>"
git push -u origin release/<version>
```

Opening the PR needs its own explicit ask — "cut a release" or "publish
`<version>`" authorizes the branch push above but not opening a PR. Stop
here and confirm before running `gh pr create`, unless the user's original
request explicitly asked for a PR too.

```bash
gh pr create --title "release: <version>" --body "$(cat <<EOF
Release prep for <version>.

- Bumps \`pyproject.toml\` to <version>
- Adds release notes at \`docs/releases/v<version>.md\`
- After merge: tag \`v<version>\` on main and push to trigger PyPI publish + GitHub Release

🤖 Generated with the release-openaca skill
EOF
)"
```

Surface the PR URL. **Stop here.** The user reviews and merges the PR.

### Step 6. After merge: tag + push (post-confirmation)

After the user confirms the PR has merged, on a clean main:

```bash
git checkout main
git pull --ff-only
git tag v<version>
git push origin v<version>
```

Then watch the workflow:

```bash
gh run watch --repo open-agent-security/openaca \
  $(gh run list --repo open-agent-security/openaca --workflow publish-pypi.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```

### Step 7. Verify

```bash
# PyPI shows the new version
curl -s https://pypi.org/pypi/openaca/json | jq -r '.releases | keys | .[]' | sort -V | tail -3

# GitHub Release exists with the notes file as body
gh release view v<version> --repo open-agent-security/openaca --json name,body
```

Both must succeed. If either is missing, surface the workflow run URL
so the user can inspect what failed.

### Step 8. Downstream follow-ups

The release isn't done when PyPI shows the version. Walk this checklist
and surface each item to the user (do them on request, not silently):

1. **openaca-demo**: its `README.md` and
   `BETA-TESTER-GUIDE.md` embed *expected scan output* — identity
   strings, posture lines, tree shapes. If the release changed any of
   those, re-run the demo fixtures with the **published** version
   (`uvx --prerelease allow --from openaca openaca scan repo --target
   <fixture>`) and diff against the docs' sample blocks. Refresh after
   publish, not before — the guide should match what a tester actually
   installs. Check for in-flight docs branches on the demo repo first;
   coordinate rather than parallel-edit.
2. **Downstream consumers pinning `openaca==<old-version>`**: re-pin
   them to the new version (separate PR in each consuming repo) so
   their matching semantics track what scanners now upload. Urgent
   when the release changed BOM identity shapes or upload redaction;
   routine otherwise.
3. **Served install scripts**: if the release changed CLI surface that
   `deploy/remote/*.sh` or the site's `collect.sh` invoke, confirm the
   site has rebuilt/redeployed so served scripts and the published
   package agree (the b6→b7 `fleet`→`remote` rename had a window where
   scripts on main called a CLI that PyPI didn't have yet).

## Failure modes the skill prevents

1. **Tag exists on PyPI but not on GitHub Releases.** Caught by the
   workflow's notes-file gate at Step 5 of the publish-pypi.yml `build`
   job: no notes file at the tag → no PyPI publish.
2. **Notes file is a raw commit dump.** Step 3's judgment guidelines.
   Pre-merge code review catches the rest.
3. **Tagging the wrong branch.** Caught by `publish-pypi.yml`'s
   "Verify tag is on main" step.
4. **Tag name doesn't match `pyproject.toml`.** Caught by the existing
   "Verify tag matches package version" step.
5. **Forgetting to refresh `uv.lock`.** Caught downstream by `uv sync
   --frozen` failing in the build job, but the skill bumps the lock at
   Step 4 to prevent the round-trip.
6. **Docs contradicting the release they ship with.** Step 2b sweeps
   README/docs for stale CLI names, version pins, and identity-shape
   examples so the fixes ride in the release PR.
7. **Downstream drift after publish.** Step 8's checklist: demo-repo
   expected outputs, downstream `openaca==` pins, and served
   install scripts that must agree with the published CLI surface.

## What this skill does NOT do

- **Doesn't publish to PyPI directly.** That's the workflow's job
  (Trusted Publishing via OIDC, no API key here).
- **Doesn't create the GitHub Release directly.** Same reason — the
  workflow's `release-github` job does it from the notes file.
- **Doesn't write CHANGELOG.md.** OpenACA's convention is per-release
  notes files under `docs/releases/`, not a single CHANGELOG.
- **Doesn't yank.** PyPI versions are immutable; if a release ships
  broken, the skill should be invoked again to ship the next version.
