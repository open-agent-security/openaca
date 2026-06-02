# bun.lock Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse Bun's text lockfile (`bun.lock`) so the pinned transitive npm
dependencies of bun-based MCP plugins become visible to advisory matching —
closing the one high-value lockfile coverage gap. (Four official Anthropic
plugins ship local MCP servers via Bun; their `bun.lock` trees contain real OSV
advisories that OpenACA can't currently see.)

**Architecture:** A new parser module mirrors the existing
`tools/parsers/package_lock_json.py` — emit one `npm` `ComponentRef` per
resolved package with `extra={"transitive": True}`. The only wrinkle: `bun.lock`
is JSON **plus trailing commas** (verified across all observed files — no
comments, single quotes, or unquoted keys). So we do **not** add a JSON5
dependency; a small string-aware preprocessor strips trailing commas, then the
stdlib `json.loads` parses it, failing closed (`[]`) on anything it can't
handle. The parser is then registered in the same two dispatch tables that
already route `package-lock.json` and `uv.lock`.

**Tech Stack:** Python stdlib `json` (no new dependency), `ComponentRef`.

**No ADR.** This is a new instance of the established lockfile-transitive-scanning
pattern (plan 009; ADR-0008 → superseded by 0009), not new architecture. The
trailing-comma-stripping choice is an implementation detail recorded in the
parser docstring, not an architectural decision.

---

## File Structure

```
tools/parsers/bun_lock.py              # NEW — parse() + _strip_trailing_commas()
tools/parsers/__init__.py              # MODIFY — import + _DEP_MANIFEST_PATTERNS + REGISTRY
tools/parsers/claude_install.py        # MODIFY — import + _LOCKFILE_DISPATCH
tests/test_parsers/test_bun_lock.py    # NEW — unit tests (stripper + parse)
tests/fixtures/repos/bun-plugin/       # NEW — e2e fixture plugin (.claude-plugin + bun.lock)
tests/test_e2e.py                      # MODIFY — one e2e test
```

`bun_lock.py` owns one responsibility: turn a `bun.lock` file into
`ComponentRef`s. The stripper is a private helper in the same module (it exists
only for this format).

---

## Task 1: bun.lock parser (TDD)

**Files:**
- Create: `tools/parsers/bun_lock.py`
- Test: `tests/test_parsers/test_bun_lock.py`

Read `tools/parsers/package_lock_json.py` first — this mirrors its contract
(npm refs, `transitive: True`, skip the root entry, fail closed).

- [ ] **Step 1: Write failing tests**

Create `tests/test_parsers/test_bun_lock.py`:

```python
from __future__ import annotations

from pathlib import Path

from tools.parsers.bun_lock import _strip_trailing_commas, parse


def test_strip_trailing_comma_in_object():
    assert _strip_trailing_commas('{"a": 1,}') == '{"a": 1}'


def test_strip_trailing_comma_in_array():
    assert _strip_trailing_commas("[1, 2, ]") == "[1, 2 ]"


def test_comma_before_brace_inside_string_is_preserved():
    # A literal "...,}" inside a string value must NOT be touched.
    src = '{"a": "x,}"}'
    assert _strip_trailing_commas(src) == src


def test_escaped_quote_does_not_break_string_state():
    # The escaped quote keeps us inside the string, so the ",]" stays literal.
    src = '{"a": "he\\",]"}'
    assert _strip_trailing_commas(src) == src


def test_parse_extracts_pinned_versions_and_skips_root(tmp_path: Path):
    lock = tmp_path / "bun.lock"
    lock.write_text(
        """{
  "lockfileVersion": 1,
  "workspaces": {
    "": { "name": "host-pkg", "dependencies": { "hono": "^4" }, },
  },
  "packages": {
    "hono": ["hono@4.12.5", "", {}, "sha512-abc=="],
    "@discordjs/builders": ["@discordjs/builders@1.13.1", "", {}, "sha512-def=="],
  },
}
""",
        encoding="utf-8",
    )
    refs = parse(lock)
    by_name = {r.name: r for r in refs}
    assert set(by_name) == {"hono", "@discordjs/builders"}
    assert by_name["hono"].version == "4.12.5"
    assert by_name["hono"].ecosystem == "npm"
    assert by_name["hono"].extra["transitive"] is True
    assert by_name["@discordjs/builders"].version == "1.13.1"  # scoped name preserved


def test_parse_malformed_returns_empty(tmp_path: Path):
    bad = tmp_path / "bun.lock"
    bad.write_text("this is not a lockfile {{{", encoding="utf-8")
    assert parse(bad) == []


def test_parse_missing_packages_returns_empty(tmp_path: Path):
    lock = tmp_path / "bun.lock"
    lock.write_text('{"lockfileVersion": 1}', encoding="utf-8")
    assert parse(lock) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsers/test_bun_lock.py -q`
Expected: FAIL with `ModuleNotFoundError: tools.parsers.bun_lock`.

- [ ] **Step 3: Implement the parser**

Create `tools/parsers/bun_lock.py`:

```python
"""Parse Bun's text lockfile (`bun.lock`).

Walks the top-level `packages` map and emits one ComponentRef per resolved
package. Each value is an array whose element [0] is the resolved
`name@version` (e.g. "@discordjs/builders@1.13.1"); the version is taken from
there so it is always the exact pinned version. The empty-string / workspace
key is the host package — skipped, since plugin self-identity is emitted by
claude_install from plugin.json. All emissions tag `extra["transitive"]=True`
so the lockfile-vs-manifest distinction propagates to SARIF
properties.coverage.

Bun lockfiles observed in the wild are strict JSON with trailing commas — no
comments, single quotes, or unquoted keys. We therefore strip trailing commas
with a small string-aware preprocessor and hand the result to the stdlib JSON
parser, rather than taking a JSON5 dependency. If Bun ever emits broader
JSONC/JSON5 syntax, `json.loads` raises and this parser fails closed (returns
[]), matching the other lockfile parsers.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef


def parse(path: Path) -> list[ComponentRef]:
    try:
        raw = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        data = json.loads(_strip_trailing_commas(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    packages = data.get("packages")
    if not isinstance(packages, dict):
        return []
    refs: list[ComponentRef] = []
    for key, entry in packages.items():
        if not key:
            continue  # workspace / host-root entry
        if not isinstance(entry, list) or not entry:
            continue
        spec = entry[0]
        if not isinstance(spec, str):
            continue
        # "@scope/name@1.2.3" -> ("@scope/name", "@", "1.2.3");
        # "name@1.2.3" -> ("name", "@", "1.2.3").
        name, _, version = spec.rpartition("@")
        if not name or not version:
            continue
        refs.append(
            ComponentRef(
                ecosystem="npm",
                name=name,
                version=version,
                source_manifest=str(path),
                source_locator=f"$.packages[{key!r}]",
                extra={"transitive": True},
            )
        )
    return refs


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas (a comma whose next non-whitespace char is `}` or
    `]`) that appear OUTSIDE string literals. Tracks string state and escapes so
    a literal comma inside a string value is never touched."""
    out: list[str] = []
    n = len(text)
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                continue  # drop the trailing comma
        out.append(ch)
    return "".join(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsers/test_bun_lock.py -q`
Expected: PASS — 7/7.

- [ ] **Step 5: Commit**

```bash
git add tools/parsers/bun_lock.py tests/test_parsers/test_bun_lock.py
git commit -m "feat(parsers): bun.lock parser (npm transitive deps)"
```

---

## Task 2: Register bun.lock in the repo-scan dispatch

**Files:**
- Modify: `tools/parsers/__init__.py` (three spots: import, `_DEP_MANIFEST_PATTERNS`, `REGISTRY`)

- [ ] **Step 1: Add the import**

In the `from tools.parsers import (...)` block (alongside `package_lock_json`,
`uv_lock`), add `bun_lock`:

```python
    bun_lock,
    claude_skill,
    mcp_json,
    package_json,
    package_lock_json,
    pyproject_toml,
    uv_lock,
)
```

- [ ] **Step 2: Add `bun.lock` to `_DEP_MANIFEST_PATTERNS`**

```python
_DEP_MANIFEST_PATTERNS: frozenset[str] = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "package-lock.json",
        "uv.lock",
        "bun.lock",
    }
)
```

- [ ] **Step 3: Add `bun.lock` to `REGISTRY`**

After the `("uv.lock", uv_lock.parse),` entry:

```python
    ("package-lock.json", package_lock_json.parse),
    ("uv.lock", uv_lock.parse),
    ("bun.lock", bun_lock.parse),
]
```

- [ ] **Step 4: Verify nothing regressed**

Run: `uv run pytest tests/test_parsers/ -q`
Expected: PASS (existing parser tests + the new bun.lock tests).

- [ ] **Step 5: Commit**

```bash
git add tools/parsers/__init__.py
git commit -m "feat(parsers): route bun.lock in repo-scan dispatch"
```

---

## Task 3: Register bun.lock in the endpoint-scan dispatch

**Files:**
- Modify: `tools/parsers/claude_install.py` (import + `_LOCKFILE_DISPATCH`)

- [ ] **Step 1: Add the import**

Alongside the existing `package_lock_json` / `uv_lock` imports in
`claude_install.py`, add `bun_lock`.

- [ ] **Step 2: Add the dispatch entry**

```python
_LOCKFILE_DISPATCH: list[tuple[str, str, object]] = [
    ("npm", "package-lock.json", package_lock_json.parse),
    ("PyPI", "uv.lock", uv_lock.parse),
    ("npm", "bun.lock", bun_lock.parse),
]
```

- [ ] **Step 3: Verify**

Run: `uv run pytest tests/test_parsers/ tests/test_scan.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tools/parsers/claude_install.py
git commit -m "feat(parsers): route bun.lock in endpoint-scan dispatch"
```

---

## Task 4: e2e — bun-plugin scan surfaces a bundled finding with Risk Attribution

**Files:**
- Create: `tests/fixtures/repos/bun-plugin/.claude-plugin/plugin.json`
- Create: `tests/fixtures/repos/bun-plugin/bun.lock`
- Test: `tests/test_e2e.py`

This is the hermetic version of the discord-plugin demo: a checked-in plugin
whose `bun.lock` pins `@cyanheads/git-mcp-server@1.1.0` — already in conftest's
offline-OSV map (`_osv_fixture_for_ref` → `ghsa-3q26-f695-pp76.json`), so no
live OSV and no new fixture. It reuses the exact assertion pattern from
`test_openaca_scan_attributes_bundled_finding_to_plugin` (#95).

- [ ] **Step 1: Create the fixture plugin**

`tests/fixtures/repos/bun-plugin/.claude-plugin/plugin.json`:

```json
{ "name": "bun-sample", "version": "0.0.1" }
```

`tests/fixtures/repos/bun-plugin/bun.lock` (note the trailing commas — this is
also the real-format check):

```jsonc
{
  "lockfileVersion": 1,
  "workspaces": {
    "": { "name": "bun-sample", "dependencies": { "@cyanheads/git-mcp-server": "1.1.0" }, },
  },
  "packages": {
    "@cyanheads/git-mcp-server": ["@cyanheads/git-mcp-server@1.1.0", "", {}, "sha512-fixturehash=="],
  },
}
```

- [ ] **Step 2: Write the failing e2e test**

Add to `tests/test_e2e.py` (near `test_openaca_scan_attributes_bundled_finding_to_plugin`):

```python
def test_openaca_scan_bun_lock_surfaces_bundled_finding():
    """Risk Attribution over a bun.lock (plan 024): a bun-based plugin whose
    bun.lock pins a vulnerable transitive dep gets the [! bundles: …] marker on
    the plugin header, the direct marker on the dep leaf, across parser →
    matcher → composition graph → renderer. Uses the offline-OSV fixture."""
    from tools.scan import main as scan_main

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            str(REPO_ROOT / "tests" / "fixtures" / "repos" / "bun-plugin"),
            "--no-color",
        ],
    )
    assert result.exit_code == 1, result.output
    out = result.output
    plugin_line = next(ln for ln in out.splitlines() if "bun-sample" in ln)
    assert "[! bundles: GHSA-3q26-f695-pp76]" in plugin_line
    leaf_line = next(ln for ln in out.splitlines() if "@cyanheads/git-mcp-server" in ln)
    assert "[! GHSA-3q26-f695-pp76]" in leaf_line
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_e2e.py::test_openaca_scan_bun_lock_surfaces_bundled_finding -q`
Expected: PASS. (If the plugin header label differs from `bun-sample`, adjust
the `plugin_line` match to the actual plugin display id printed by the tree —
confirm against the real output, don't guess.)

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/repos/bun-plugin/ tests/test_e2e.py
git commit -m "test(e2e): bun.lock plugin surfaces bundled finding with Risk Attribution"
```

---

## Task 5: Document the unpinned-`@latest` known limitation

**Files:**
- Modify: `README.md` (known-limitations / scope note) — one sentence.

bun.lock pins exact versions, so it matches well. But MCP servers launched via
`npx pkg@latest` (no version) can't be OSV-range-matched — that's inherent, not
a parser bug.

- [ ] **Step 1: Add a one-line known-limitation note**

In the README's scope/limitations area, add:

```markdown
- Unpinned components (e.g. an MCP launched via `npx pkg@latest` with no
  version) are inventoried but cannot be advisory-matched — OSV needs an exact
  version. Lockfile-pinned transitive deps (`package-lock.json`, `uv.lock`,
  `bun.lock`) are matched.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: note unpinned-component matching limitation"
```

---

## Task 6: Full gate

- [ ] **Run the full gate:** `uv run ruff format`, `uv run ruff check`,
  `uv run pyright`, `uv run pytest`, `uv run openaca lint`. All green before
  done.

---

## Verification

- `uv run pytest tests/test_parsers/test_bun_lock.py` — stripper handles
  object/array trailing commas, preserves commas inside strings, survives
  escaped quotes; parser extracts pinned versions (incl. scoped names), skips
  the root, fails closed on malformed input.
- The e2e test shows a bun-based plugin's pinned transitive dep producing a
  finding with the `[! bundles: …]` containment marker — proving bun.lock flows
  through parser → matcher → composition graph → renderer.
- **Manual demo (not CI):** `uv run openaca scan repo --target
  ~/workspace/claude-plugins-official/external_plugins/discord --no-color`
  surfaces real OSV findings (hono@4.12.5 → 12 GHSAs, undici, etc.) with
  containment attribution on the discord plugin header — the design-partner
  demo, on a real official plugin, live against OSV.
- Full gate green.

## Deferred

- **pnpm-lock.yaml / yarn.lock** — other npm-ecosystem lockfile formats; same
  pattern, but not present in the target corpus. Add when a real need appears.
- **`bun.lockb`** (Bun's older *binary* lockfile) — not parsed; the text
  `bun.lock` is the committed default. Out of scope.
- **Resolving `@latest` to a concrete version** at scan time — would let
  unpinned MCPs match, but trades reproducibility for coverage; a separate
  design decision, not this plan.
