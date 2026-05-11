# 009 — Plugin-internal implementation deps + OSV.dev federation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Tier-2 SCA coverage for both `repo` and `fs` modes (lockfile-rooted transitive deps with manifest fallback), plus opt-in OSV.dev live federation. Preserve ASVE's install-state filtering and attribution as the differentiator over `trivy`/`osv-scanner`.

**Architecture:** Two new lockfile parsers (`package_lock_json`, `uv_lock`) wired into both modes — into `REGISTRY` for repo mode, into a new `_walk_plugin_implementation_deps` for fs mode. A new `osv_federation` module batch-queries OSV.dev and merges results into the matching corpus. Two CLI flags: `--exclude-transitive` (default off; skips lockfile/manifest walks) and `--federate-osv` (default off; adds OSV.dev results to the corpus). SARIF gains `properties.{coverage, transitive, source}` to surface the new metadata.

**Tech Stack:** Python (`tomllib` for `uv.lock`, stdlib `json` + `urllib.request` for OSV.dev), pytest, existing parser/scan conventions. No new runtime deps.

**Depends on:** 007 (CLI shape, attribution data model), 008 (component inventory, claude_install walks, ADR-0007 ecosystem framing).

**Spec:** `docs/specs/009-plugin-internal-deps-design.md`.

---

## Context

Plan 008 dogfooding produced empirical evidence that motivates the design: `trivy filesystem ~/.claude/plugins/cache` and `osv-scanner --recursive ~/.claude/plugins/cache` both walked orphaned cache versions (`superpowers/5.0.7/` alongside the active `5.1.0/`), both reported plugin test fixtures as runtime paths, and both produced findings keyed only on file paths with no plugin attribution. ASVE's `fs` mode already filters orphaned versions (via `installed_plugins.json`) and skips `tests/` (no `rglob` in `_walk_plugin_install_root`) — but stops at Tier-1 inventory. This plan extends to Tier-2 implementation deps while preserving the filtering and attribution.

## File structure

| File | Status | Purpose |
|---|---|---|
| `tools/parsers/package_lock_json.py` | Create | npm v3 `package-lock.json` parser; skip `""` (host) and `dev: true`. |
| `tools/parsers/uv_lock.py` | Create | `uv.lock` TOML parser via `tomllib`. |
| `tools/parsers/__init__.py` | Modify | Register `package-lock.json` and `uv.lock` in `REGISTRY` for repo mode. |
| `tools/parsers/claude_install.py` | Modify | Add `_walk_plugin_implementation_deps`; thread `include_transitive=True` param through `parse_install` and `_walk_active_plugins`. |
| `tools/osv_federation.py` | Create | OSV.dev `/v1/querybatch` client + corpus merger. Fail-soft on network. |
| `tools/scan.py` | Modify | `--exclude-transitive` and `--federate-osv` flags; verbose output additions; unconditional stderr warning on OSV failure. |
| `tools/sarif.py` | Modify | Emit `properties.{coverage, transitive, source}` per finding (dereferenced from ref.extra and advisory record). |
| `tests/test_parsers/test_package_lock_json.py` | Create | npm lockfile parsing edge cases. |
| `tests/test_parsers/test_uv_lock.py` | Create | uv.lock parsing edge cases. |
| `tests/test_parsers/test_claude_install.py` | Modify | Lockfile-vs-manifest-fallback dispatch tests; `include_transitive` parameter tests. |
| `tests/test_osv_federation.py` | Create | OSV.dev client tests with `urllib` mocked at the network boundary. |
| `tests/test_scan.py` | Modify | `--exclude-transitive` and `--federate-osv` CLI integration. |
| `tests/test_sarif.py` | Modify | New properties on results. |
| `tests/test_e2e.py` | Modify | E2E with a fixture plugin whose lockfile contains a vulnerable dep matching a real corpus advisory. |
| `tests/fixtures/installs/with-transitive-vuln/` | Create | Fixture install layout exercising lockfile-detected transitive findings. |
| `docs/adrs/0008-lockfile-dispatch-and-osv-federation.md` | Create | ADR for parse-all-lockfiles, lockfile-vs-manifest semantics, federation opt-in. |
| `docs/adrs/INDEX.md` | Modify | Link ADR-0008. |
| `docs/sarif-conventions.md` | Create | Document ASVE-specific SARIF properties. |
| `README.md` | Modify | Tier-2 status to "✅ V0"; add federation note. |
| `docs/plans/README.md` | Modify | Mark 009 active. |

---

## Task 1: `package_lock_json` parser

**Files:**
- Create: `tools/parsers/package_lock_json.py`
- Test: `tests/test_parsers/test_package_lock_json.py`

The npm v3 lockfile uses a `packages` map keyed on filesystem path. The empty-string key is the host package (skip — `claude_install` emits the plugin self-identity from `installed_plugins.json`). `node_modules/<scope>/<name>` keys hold transitive dep entries.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parsers/test_package_lock_json.py
import json
from pathlib import Path

from tools.parsers.package_lock_json import parse


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "package-lock.json"
    p.write_text(json.dumps(data))
    return p


def test_emits_one_ref_per_transitive_package(tmp_path):
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host", "version": "1.0.0"},
                "node_modules/lodash": {"version": "4.17.20"},
                "node_modules/@scope/pkg": {"version": "2.0.0"},
            },
        },
    )
    refs = parse(path)
    by_name = {r.name: r for r in refs}
    assert set(by_name) == {"lodash", "@scope/pkg"}
    assert by_name["lodash"].ecosystem == "npm"
    assert by_name["lodash"].version == "4.17.20"
    assert by_name["lodash"].extra["transitive"] is True
    assert by_name["@scope/pkg"].version == "2.0.0"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/vinodkone/workspace/asve/.worktrees/feat-plugin-internal-deps
uv run pytest tests/test_parsers/test_package_lock_json.py -q
```

Expected: FAIL with `ImportError` (module doesn't exist yet).

- [ ] **Step 3: Implement parser**

```python
# tools/parsers/package_lock_json.py
"""Parse npm package-lock.json v3 lockfile.

Walks the `packages` map and emits one ComponentRef per resolved package.
The empty-string key is the host package — skipped, since the plugin
self-identity is emitted by claude_install from installed_plugins.json.
Entries with `dev: true` (devDependencies) are skipped: they don't ship
at plugin runtime, only at dev/CI time.

All emissions tag `extra["transitive"]=True` so the lockfile-vs-manifest
distinction propagates to SARIF properties.coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef


def parse(path: Path) -> list[ComponentRef]:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    packages = data.get("packages")
    if not isinstance(packages, dict):
        return []
    refs: list[ComponentRef] = []
    for key, entry in packages.items():
        if not key:
            continue  # host package
        if not isinstance(entry, dict):
            continue
        if entry.get("dev") is True:
            continue
        name = _name_from_key(key)
        version = entry.get("version")
        if not name or not isinstance(version, str) or not version:
            continue
        refs.append(
            ComponentRef(
                ecosystem="npm",
                name=name,
                version=version,
                source_manifest=str(path),
                source_locator=f"$.packages.{key!r}",
                extra={"transitive": True},
            )
        )
    return refs


def _name_from_key(key: str) -> str:
    """`node_modules/foo` → `foo`; `node_modules/@scope/name` → `@scope/name`.

    Handles nested `node_modules/foo/node_modules/bar` correctly by taking
    the segment AFTER the last `node_modules/`.
    """
    marker = "node_modules/"
    idx = key.rfind(marker)
    if idx == -1:
        return ""
    return key[idx + len(marker) :]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_parsers/test_package_lock_json.py -q
```

Expected: PASS.

- [ ] **Step 5: Add edge-case tests**

```python
def test_skips_dev_dependencies(tmp_path):
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host", "version": "1.0.0"},
                "node_modules/runtime-pkg": {"version": "1.0.0"},
                "node_modules/dev-pkg": {"version": "2.0.0", "dev": True},
            },
        },
    )
    refs = parse(path)
    assert {r.name for r in refs} == {"runtime-pkg"}


def test_skips_host_package(tmp_path):
    """The `""` key holds the host package — never emit a ref for it."""
    path = _write(
        tmp_path,
        {"lockfileVersion": 3, "packages": {"": {"name": "host", "version": "1.0.0"}}},
    )
    assert parse(path) == []


def test_handles_nested_node_modules(tmp_path):
    """Transitive deps of transitive deps: take the segment after the LAST
    `node_modules/` marker."""
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host", "version": "1.0.0"},
                "node_modules/parent/node_modules/child": {"version": "3.0.0"},
            },
        },
    )
    refs = parse(path)
    assert len(refs) == 1
    assert refs[0].name == "child"
    assert refs[0].version == "3.0.0"


def test_skips_entries_without_version(tmp_path):
    """A package entry without a string `version` is malformed; skip silently."""
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host"},
                "node_modules/no-version": {},
                "node_modules/null-version": {"version": None},
                "node_modules/numeric-version": {"version": 1},
            },
        },
    )
    assert parse(path) == []


def test_returns_empty_on_malformed_json(tmp_path):
    path = tmp_path / "package-lock.json"
    path.write_text("{not json")
    assert parse(path) == []


def test_returns_empty_on_non_object_top_level(tmp_path):
    path = tmp_path / "package-lock.json"
    path.write_text("[]")
    assert parse(path) == []


def test_returns_empty_when_packages_missing(tmp_path):
    """A lockfile without a `packages` map (e.g., v1 / v2 shape) returns []."""
    path = _write(tmp_path, {"lockfileVersion": 1, "dependencies": {}})
    assert parse(path) == []


def test_returns_empty_on_unreadable_file(tmp_path):
    """Directory at the lockfile path → IsADirectoryError; degrade silently."""
    p = tmp_path / "package-lock.json"
    p.mkdir()
    assert parse(p) == []


def test_skips_non_dict_entries(tmp_path):
    """A package entry that's a string/list/null should be skipped."""
    path = _write(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "host", "version": "1.0.0"},
                "node_modules/bad-string": "1.0.0",
                "node_modules/bad-list": ["1.0.0"],
                "node_modules/good-pkg": {"version": "1.0.0"},
            },
        },
    )
    refs = parse(path)
    assert {r.name for r in refs} == {"good-pkg"}
```

- [ ] **Step 6: Run all package_lock tests + full gate**

```bash
uv run pytest tests/test_parsers/test_package_lock_json.py -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
```

Expected: all green; ruff format applies any whitespace fixes idempotently.

- [ ] **Step 7: Commit**

```bash
git add tools/parsers/package_lock_json.py tests/test_parsers/test_package_lock_json.py
git commit -m "feat(parsers): npm package-lock.json v3 parser

Emits one ref per transitive package; skips host (\"\" key) and
dev:true entries. Extra carries transitive=True so SARIF can
surface properties.coverage. Plan 009, Task 1."
```

---

## Task 2: `uv_lock` parser

**Files:**
- Create: `tools/parsers/uv_lock.py`
- Test: `tests/test_parsers/test_uv_lock.py`

`uv.lock` is TOML with one `[[package]]` table per resolved package. Dev-vs-runtime annotations aren't reliably encoded; V0 emits all packages and accepts over-reporting dev deps as a known limitation (spec §Out of scope).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parsers/test_uv_lock.py
from pathlib import Path

from tools.parsers.uv_lock import parse


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "uv.lock"
    p.write_text(content)
    return p


def test_emits_one_ref_per_package(tmp_path):
    path = _write(
        tmp_path,
        """\
version = 1

[[package]]
name = "requests"
version = "2.31.0"

[[package]]
name = "urllib3"
version = "2.0.4"
""",
    )
    refs = parse(path)
    by_name = {r.name: r for r in refs}
    assert set(by_name) == {"requests", "urllib3"}
    assert by_name["requests"].ecosystem == "PyPI"
    assert by_name["requests"].version == "2.31.0"
    assert by_name["requests"].extra["transitive"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_parsers/test_uv_lock.py -q
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement parser**

```python
# tools/parsers/uv_lock.py
"""Parse uv.lock (TOML) — Python PyPI deps via uv's lockfile.

One ComponentRef per [[package]] entry. uv.lock doesn't reliably
encode dev-vs-runtime annotations (the schema is still evolving), so
V0 emits all packages and accepts over-reporting dev deps as a known
limitation. Refine in V1 if uv's schema stabilizes the distinction.

All emissions tag extra["transitive"]=True so SARIF can surface
properties.coverage.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from tools.component_ref import ComponentRef


def parse(path: Path) -> list[ComponentRef]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    packages = data.get("package")
    if not isinstance(packages, list):
        return []
    refs: list[ComponentRef] = []
    for i, entry in enumerate(packages):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        version = entry.get("version")
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(version, str) or not version:
            continue
        refs.append(
            ComponentRef(
                ecosystem="PyPI",
                name=name,
                version=version,
                source_manifest=str(path),
                source_locator=f"$.package[{i}]",
                extra={"transitive": True},
            )
        )
    return refs
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_parsers/test_uv_lock.py -q
```

Expected: PASS.

- [ ] **Step 5: Add edge-case tests**

```python
def test_returns_empty_on_malformed_toml(tmp_path):
    path = _write(tmp_path, "this is not = valid [[toml")
    assert parse(path) == []


def test_returns_empty_when_package_missing(tmp_path):
    path = _write(tmp_path, 'version = 1\n')
    assert parse(path) == []


def test_skips_entries_without_required_fields(tmp_path):
    path = _write(
        tmp_path,
        """\
version = 1

[[package]]
name = "valid-pkg"
version = "1.0.0"

[[package]]
name = "no-version"

[[package]]
version = "no-name"
""",
    )
    refs = parse(path)
    assert {r.name for r in refs} == {"valid-pkg"}


def test_returns_empty_on_unreadable_file(tmp_path):
    p = tmp_path / "uv.lock"
    p.mkdir()
    assert parse(p) == []


def test_source_locator_preserves_original_index(tmp_path):
    """When earlier [[package]] entries are malformed, the source_locator
    for valid entries should still reference their original position."""
    path = _write(
        tmp_path,
        """\
version = 1

[[package]]
name = "no-version-first"

[[package]]
name = "second-valid"
version = "1.0.0"
""",
    )
    refs = parse(path)
    assert len(refs) == 1
    assert refs[0].source_locator == "$.package[1]"
```

- [ ] **Step 6: Run uv_lock tests + full gate**

```bash
uv run pytest tests/test_parsers/test_uv_lock.py -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add tools/parsers/uv_lock.py tests/test_parsers/test_uv_lock.py
git commit -m "feat(parsers): uv.lock TOML parser for PyPI transitive deps

One ref per [[package]] entry. Dev-vs-runtime filtering is best-effort
(uv's schema doesn't reliably annotate); V0 over-reports dev deps as
known limitation. Extra carries transitive=True. Plan 009, Task 2."
```

---

## Task 3: Wire lockfile patterns into `REGISTRY` (repo mode)

**Files:**
- Modify: `tools/parsers/__init__.py`
- Create: `tests/test_parsers/test_repo_mode_lockfiles.py`
- Create: `tests/fixtures/repos/sample-lockfile-npm/package-lock.json`
- Create: `tests/fixtures/repos/sample-lockfile-uv/uv.lock`

Repo mode reads root-level lockfiles via the REGISTRY's `rglob` walk. Findings have `attributed_to=None` (host repo is direct, not "via a plugin").

- [ ] **Step 1: Add fixtures**

```bash
mkdir -p tests/fixtures/repos/sample-lockfile-npm
mkdir -p tests/fixtures/repos/sample-lockfile-uv
```

Write `tests/fixtures/repos/sample-lockfile-npm/package-lock.json`:

```json
{
  "name": "sample",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "packages": {
    "": {"name": "sample", "version": "1.0.0"},
    "node_modules/lodash": {"version": "4.17.20"}
  }
}
```

Write `tests/fixtures/repos/sample-lockfile-uv/uv.lock`:

```toml
version = 1

[[package]]
name = "requests"
version = "2.31.0"
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_parsers/test_repo_mode_lockfiles.py
"""Plan 009 Task 3: lockfile patterns fire in repo mode via parse_repo.

Refs emitted from host-repo lockfiles have attributed_to=None — the
host repo declares these deps directly, not via a plugin.
"""

from pathlib import Path

from tools.parsers import parse_repo

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_repo_mode_emits_npm_lockfile_refs():
    refs = parse_repo(REPOS / "sample-lockfile-npm")
    npm_refs = [
        r for r in refs if r.ecosystem == "npm" and r.extra.get("transitive") is True
    ]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "lodash"
    assert npm_refs[0].version == "4.17.20"
    assert npm_refs[0].attributed_to is None


def test_repo_mode_emits_uv_lock_refs():
    refs = parse_repo(REPOS / "sample-lockfile-uv")
    pypi_refs = [
        r for r in refs if r.ecosystem == "PyPI" and r.extra.get("transitive") is True
    ]
    assert len(pypi_refs) == 1
    assert pypi_refs[0].name == "requests"
    assert pypi_refs[0].attributed_to is None
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/test_parsers/test_repo_mode_lockfiles.py -q
```

Expected: FAIL (parsers not registered).

- [ ] **Step 4: Wire parsers into REGISTRY**

Edit `tools/parsers/__init__.py`:

```python
from tools.parsers import (
    claude_command_agent,
    claude_plugin,
    claude_settings,
    claude_skill,
    mcp_json,
    package_json,
    package_lock_json,
    pyproject_toml,
    uv_lock,
)
```

Append to the existing `REGISTRY` list, AFTER the Tier-1 patterns:

```python
REGISTRY: list[tuple[str, ParserFn]] = [
    # ... existing entries unchanged through line "(\".claude/agents/*.md\", _parse_repo_agent),"
    # Plan 009: lockfile parsers for repo-mode transitive coverage.
    # Refs from these patterns have attributed_to=None (host repo is direct);
    # extra["transitive"]=True so SARIF surfaces properties.coverage=transitive.
    ("package-lock.json", package_lock_json.parse),
    ("uv.lock", uv_lock.parse),
]
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_parsers/test_repo_mode_lockfiles.py -q
```

Expected: PASS.

- [ ] **Step 6: Run full suite + gate**

```bash
uv run pytest -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
```

Expected: all pre-existing tests still green; new tests pass.

- [ ] **Step 7: Commit**

```bash
git add tools/parsers/__init__.py tests/test_parsers/test_repo_mode_lockfiles.py tests/fixtures/repos/sample-lockfile-npm/ tests/fixtures/repos/sample-lockfile-uv/
git commit -m "feat(parsers): register lockfile patterns in REGISTRY for repo mode

A repo's host package-lock.json or uv.lock at root now produces
transitive npm/PyPI refs in repo-mode scans. attributed_to=None since
the host repo declares these directly. Plan 009, Task 3."
```

---

## Task 4: `_walk_plugin_implementation_deps` dispatch in `claude_install.py`

**Files:**
- Modify: `tools/parsers/claude_install.py`
- Modify: `tests/test_parsers/test_claude_install.py`

Per-active-plugin dispatch: parse every supported lockfile present at the installPath; for each ecosystem NOT covered by a lockfile, fall back to its manifest with `transitive=False`. The full helper threads `attributed_to` and (later) `include_transitive` through.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parsers/test_claude_install.py`:

```python
def test_install_emits_npm_lockfile_refs_for_active_plugin(tmp_path):
    """A plugin with package-lock.json at its installPath emits transitive
    npm refs, all attributed to the plugin."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "webp", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "lodash"
    assert npm_refs[0].version == "4.17.20"
    assert npm_refs[0].attributed_to == "claude-plugin/webp@1.0.0"
    assert npm_refs[0].extra["transitive"] is True


def test_install_falls_back_to_package_json_when_no_lockfile(tmp_path):
    """No package-lock.json but package.json exists → emit direct deps with
    transitive=False and a fallback_reason."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package.json").write_text(
        json.dumps({"name": "webp", "dependencies": {"lodash": "^4.17.0"}})
    )
    refs, warnings = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "lodash"
    assert npm_refs[0].extra.get("transitive") is False
    assert "no npm lockfile" in (npm_refs[0].extra.get("fallback_reason") or "")
    assert npm_refs[0].attributed_to == "claude-plugin/webp@1.0.0"


def test_install_parses_both_npm_and_pypi_lockfiles_per_plugin(tmp_path):
    """A plugin shipping JS + embedded Python: parse BOTH lockfiles, not
    first-match. Validates ADR-0008's parse-all-lockfiles decision."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="multi@m", plugin_name="multi", version="1.0.0"
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "multi", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    (install_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "requests"\nversion = "2.31.0"\n'
    )
    refs, _ = parse_install(install_root=tmp_path)
    ecosystems = {r.ecosystem for r in refs}
    assert "npm" in ecosystems
    assert "PyPI" in ecosystems


def test_install_lockfile_wins_when_both_lockfile_and_manifest_present(tmp_path):
    """Lockfile gets parsed; manifest fallback skipped for the covered ecosystem."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "webp", "version": "1.0.0"},
                    "node_modules/from-lock": {"version": "1.0.0"},
                },
            }
        )
    )
    (install_path / "package.json").write_text(
        json.dumps({"name": "webp", "dependencies": {"from-manifest": "^1.0.0"}})
    )
    refs, _ = parse_install(install_root=tmp_path)
    npm_names = {r.name for r in refs if r.ecosystem == "npm"}
    assert "from-lock" in npm_names
    assert "from-manifest" not in npm_names


def test_install_include_transitive_false_skips_lockfile_and_manifest(tmp_path):
    """When include_transitive=False, Tier-2 walk is skipped entirely;
    Tier-1 components still emit."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "webp", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    skill_dir = install_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: x\n---\nbody\n"
    )
    refs, _ = parse_install(install_root=tmp_path, include_transitive=False)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    skill_refs = [r for r in refs if r.ecosystem == "claude-skill"]
    assert npm_refs == []
    assert len(skill_refs) == 1  # Tier-1 still emitted


def test_install_pyproject_fallback_when_no_uv_lock(tmp_path):
    """No uv.lock but pyproject.toml exists → emit direct deps with transitive=False."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="pyp@m", plugin_name="pyp", version="1.0.0"
    )
    (install_path / "pyproject.toml").write_text(
        '[project]\nname = "pyp"\nversion = "1.0.0"\ndependencies = ["requests==2.31.0"]\n'
    )
    refs, _ = parse_install(install_root=tmp_path)
    pypi_refs = [r for r in refs if r.ecosystem == "PyPI"]
    assert any(r.name == "requests" for r in pypi_refs)
    requests_ref = next(r for r in pypi_refs if r.name == "requests")
    assert requests_ref.extra.get("transitive") is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_parsers/test_claude_install.py -q -k "lockfile or transitive or pyproject_fallback"
```

Expected: FAIL — parameter `include_transitive` doesn't exist on `parse_install` yet; lockfile walks don't happen.

- [ ] **Step 3: Add `include_transitive` parameter to `parse_install`**

Edit `tools/parsers/claude_install.py`:

```python
def parse_install(
    install_root: Path,
    project_root: Optional[Path] = None,
    mode: Mode = "fs",
    include_transitive: bool = True,
) -> tuple[list[ComponentRef], list[str]]:
```

- [ ] **Step 4: Thread the parameter through `_walk_active_plugins`**

Update the signature:

```python
def _walk_active_plugins(
    enabled_plugins: dict,
    plugins_map: dict,
    lockfile_path: Path,
    layers: SettingsLayers,
    mode: Mode,
    include_transitive: bool,
) -> tuple[list[ComponentRef], list[str]]:
```

Update the call site in `parse_install`:

```python
plugin_refs, plugin_walk_warnings = _walk_active_plugins(
    enabled_plugins=enabled_plugins,
    plugins_map=plugins_map,
    lockfile_path=lockfile_path,
    layers=layers,
    mode=mode,
    include_transitive=include_transitive,
)
```

- [ ] **Step 5: Wire the Tier-2 walk into the per-plugin loop**

After the existing Tier-1 bundled walk in `_walk_active_plugins`:

```python
        install_path = entry.get("installPath")
        if isinstance(install_path, str) and install_path:
            bundled_refs, bundled_warnings = _walk_plugin_install_root(
                Path(install_path), plugin_name=plugin_name, attributed_to=identity
            )
            refs.extend(bundled_refs)
            for w in bundled_warnings:
                warnings.append(f"{plugin_key}: {w}")

            if include_transitive:
                tier2_refs = _walk_plugin_implementation_deps(
                    Path(install_path), attributed_to=identity
                )
                refs.extend(tier2_refs)
```

- [ ] **Step 6: Implement `_walk_plugin_implementation_deps`**

Add to `tools/parsers/claude_install.py` (alongside `_walk_plugin_install_root`):

```python
from dataclasses import replace

from tools.parsers import package_json, package_lock_json, pyproject_toml, uv_lock

# (ecosystem, lockfile_filename, parser_callable) — parsed in order; multiple
# ecosystems can coexist (a single plugin can ship JS + embedded Python).
_LOCKFILE_DISPATCH: list[tuple[str, str, "ParserFn"]] = [  # noqa: F821
    ("npm", "package-lock.json", package_lock_json.parse),
    ("PyPI", "uv.lock", uv_lock.parse),
]

# Manifest fallback runs ONLY for ecosystems not already covered by a lockfile.
_MANIFEST_FALLBACK: list[tuple[str, str, "ParserFn"]] = [  # noqa: F821
    ("npm", "package.json", package_json.parse),
    ("PyPI", "pyproject.toml", pyproject_toml.parse),
]


def _walk_plugin_implementation_deps(
    install_path: Path, attributed_to: str
) -> list[ComponentRef]:
    """Tier-2 walk: parse every supported lockfile at the installPath, then
    manifest-fall-back for ecosystems not covered by a lockfile.

    ADR-0008: lockfile = full transitive; manifest fallback = direct deps
    only with extra["transitive"]=False. Parse ALL supported lockfiles, not
    first-match, so multi-language plugins emit refs for every ecosystem.
    All emissions tagged with the caller-supplied attributed_to.
    """
    if not install_path.is_dir():
        return []
    refs: list[ComponentRef] = []
    covered: set[str] = set()
    for ecosystem, filename, parser in _LOCKFILE_DISPATCH:
        lockfile = install_path / filename
        if not lockfile.is_file():
            continue
        try:
            lock_refs = parser(lockfile)
        except Exception:
            continue
        for r in lock_refs:
            refs.append(replace(r, attributed_to=attributed_to))
        covered.add(ecosystem)
    for ecosystem, filename, parser in _MANIFEST_FALLBACK:
        if ecosystem in covered:
            continue
        manifest = install_path / filename
        if not manifest.is_file():
            continue
        try:
            manifest_refs = parser(manifest)
        except Exception:
            continue
        for r in manifest_refs:
            extra = dict(r.extra)
            extra["transitive"] = False
            extra["fallback_reason"] = f"no {ecosystem} lockfile present"
            refs.append(replace(r, attributed_to=attributed_to, extra=extra))
    return refs
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/test_parsers/test_claude_install.py -q
```

Expected: PASS, including the six new lockfile/manifest tests.

- [ ] **Step 8: Full gate**

```bash
uv run pytest -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
```

Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add tools/parsers/claude_install.py tests/test_parsers/test_claude_install.py
git commit -m "feat(parsers): lockfile + manifest dispatch in claude_install

_walk_plugin_implementation_deps parses every supported lockfile at
each active plugin's installPath (npm package-lock.json, uv.lock) and
falls back to manifest scanning (package.json, pyproject.toml) per
ecosystem when no lockfile is present. All emissions attributed to
claude-plugin/<name>@<version>. Manifest fallback tags transitive=False
+ fallback_reason so SARIF can surface coverage=direct-only.

include_transitive parameter (default True) threads through
parse_install → _walk_active_plugins → here; --exclude-transitive will
wire it in Task 5. Plan 009, Task 4."
```

---

## Task 5: `--exclude-transitive` flag on `fs` subcommand

**Files:**
- Modify: `tools/scan.py`
- Modify: `tests/test_scan.py`

The flag lets users skip Tier-2 entirely and focus on agent-stack inventory.

- [ ] **Step 1: Write the failing CLI test**

Append to `tests/test_scan.py`:

```python
def test_fs_subcommand_exclude_transitive_skips_lockfile_walk(tmp_path):
    """--exclude-transitive: Tier-2 refs suppressed; Tier-1 still emitted."""
    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    skill_dir = cache_dir / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: x\n---\nbody\n"
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"demo@m": True}})
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}
                    ]
                },
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fs",
            "--target",
            str(tmp_path),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "--exclude-transitive",
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    # No lockfile refs reported. The plugin self-identity is the only
    # claude-plugin ref; lodash should NOT appear.
    assert "lodash" not in result.output
    # Tier-1 skill still emitted.
    assert "demo-skill" in result.output or "1 bundled skills" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_scan.py::test_fs_subcommand_exclude_transitive_skips_lockfile_walk -q
```

Expected: FAIL — `--exclude-transitive` is not a recognized option.

- [ ] **Step 3: Add the flag to the `fs` subcommand**

In `tools/scan.py`, add the option decorator and parameter to the `fs` subcommand:

```python
@main.command()
@click.pass_context
@_target_option_required
@_advisories_option_required
@_sarif_option
@_fail_on_option
@_verbose_option
@click.option(
    "--exclude-transitive",
    is_flag=True,
    default=False,
    help="Skip Tier-2 dependency scanning (lockfiles + manifest fallback). "
    "Tier-1 agent-stack inventory still emitted.",
)
def fs(
    ctx: click.Context,
    target: Path,
    advisories: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    exclude_transitive: bool,
) -> None:
    ...
    sarif, fail_on, verbose = _apply_group_opts(ctx, sarif, fail_on, verbose)
    install_root, project_root = _resolve_fs_roots(target)

    refs, warnings = parse_install(
        install_root=install_root,
        project_root=project_root,
        mode="fs",
        include_transitive=not exclude_transitive,
    )
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/test_scan.py::test_fs_subcommand_exclude_transitive_skips_lockfile_walk -q
```

Expected: PASS.

- [ ] **Step 5: Add the symmetric "default-on" test**

```python
def test_fs_subcommand_includes_transitive_by_default(tmp_path):
    """Without --exclude-transitive, lockfile refs are emitted."""
    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"demo@m": True}})
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}
                    ]
                },
            }
        )
    )
    # Use the resolver directly to inspect refs (CLI suppresses non-matching
    # refs in its summary — the dispatch-level test is cleaner).
    from tools.parsers.claude_install import parse_install

    refs, _ = parse_install(install_root=tmp_path)
    assert any(r.ecosystem == "npm" and r.name == "lodash" for r in refs)
```

- [ ] **Step 6: Run all scan tests + full gate**

```bash
uv run pytest tests/test_scan.py -q
uv run pytest -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add tools/scan.py tests/test_scan.py
git commit -m "feat(scan): --exclude-transitive flag on fs subcommand

Default OFF (Tier-2 included). When set, skips lockfile + manifest
dispatch entirely; Tier-1 agent-stack inventory still emitted.
Plumbed through parse_install via include_transitive=not exclude_transitive.
Plan 009, Task 5."
```

---

## Task 6: `osv_federation` module

**Files:**
- Create: `tools/osv_federation.py`
- Create: `tests/test_osv_federation.py`

Live query OSV.dev with the emitted PURLs in a single batched pass. Fail-soft on network errors. Returns the augmented advisory corpus.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_osv_federation.py
"""Tests for the OSV.dev federation client.

Network is mocked at the urllib.request boundary; tests never hit the
real OSV.dev endpoint.
"""

from unittest.mock import patch

from tools.component_ref import ComponentRef
from tools.osv_federation import augment_corpus


def _ref(eco: str, name: str, version: str) -> ComponentRef:
    return ComponentRef(ecosystem=eco, name=name, version=version)


def test_augment_returns_base_corpus_when_no_refs():
    base = [{"id": "ASVE-2026-0001"}]
    augmented, warnings = augment_corpus(refs=[], base_corpus=base)
    assert augmented == base
    assert warnings == []


def test_augment_returns_base_corpus_when_no_versioned_refs():
    """Refs without ecosystem+name+version (e.g., identity-only hooks)
    can't be queried via OSV.dev — they're skipped, base corpus returned."""
    refs = [
        ComponentRef(component_identity="claude-hook/p/PreToolUse/0"),
        ComponentRef(ecosystem="claude-skill", name="x"),  # no version
    ]
    base = [{"id": "ASVE-2026-0001"}]
    augmented, warnings = augment_corpus(refs=refs, base_corpus=base)
    assert augmented == base


def test_augment_batches_purls_and_merges_results():
    """Versioned refs get batched into /v1/querybatch; full advisory records
    fetched via /v1/vulns/<id>; deduped against the base corpus by id."""
    refs = [_ref("npm", "lodash", "4.17.20"), _ref("PyPI", "requests", "2.31.0")]
    base = [{"id": "ASVE-2026-0001"}]
    querybatch_response = {
        "results": [
            {"vulns": [{"id": "GHSA-1111"}]},
            {"vulns": [{"id": "GHSA-2222"}]},
        ]
    }
    vuln_records = {
        "GHSA-1111": {"id": "GHSA-1111", "affected": [{"package": {"ecosystem": "npm", "name": "lodash"}}]},
        "GHSA-2222": {"id": "GHSA-2222", "affected": [{"package": {"ecosystem": "PyPI", "name": "requests"}}]},
    }

    def fake_post(url, payload):
        assert "querybatch" in url
        purls = [p["package"]["purl"] for p in payload["queries"]]
        assert "pkg:npm/lodash@4.17.20" in purls
        assert any("requests" in p for p in purls)
        return querybatch_response

    def fake_get(url):
        vuln_id = url.rsplit("/", 1)[-1]
        return vuln_records[vuln_id]

    with patch("tools.osv_federation._post_json", fake_post), patch(
        "tools.osv_federation._get_json", fake_get
    ):
        augmented, warnings = augment_corpus(refs=refs, base_corpus=base)
    assert warnings == []
    ids = {a["id"] for a in augmented}
    assert ids == {"ASVE-2026-0001", "GHSA-1111", "GHSA-2222"}


def test_augment_fails_soft_on_network_error():
    """If the batch query raises, return base corpus + a warning string."""
    refs = [_ref("npm", "lodash", "4.17.20")]
    base = [{"id": "ASVE-2026-0001"}]

    def fake_post(url, payload):
        raise OSError("connection refused")

    with patch("tools.osv_federation._post_json", fake_post):
        augmented, warnings = augment_corpus(refs=refs, base_corpus=base)
    assert augmented == base
    assert any("osv.dev" in w.lower() for w in warnings)


def test_augment_dedupes_purls_within_a_scan():
    """The same PURL appearing on multiple refs should be queried once."""
    refs = [_ref("npm", "lodash", "4.17.20"), _ref("npm", "lodash", "4.17.20")]
    base = []
    calls = []

    def fake_post(url, payload):
        calls.append(payload)
        return {"results": [{"vulns": []}]}

    with patch("tools.osv_federation._post_json", fake_post):
        augment_corpus(refs=refs, base_corpus=base)
    assert len(calls) == 1
    assert len(calls[0]["queries"]) == 1  # deduped


def test_augment_chunks_large_batches():
    """OSV.dev /v1/querybatch caps at 1000 packages; chunk into multiple calls."""
    refs = [_ref("npm", f"pkg-{i}", "1.0.0") for i in range(1500)]
    base = []
    calls = []

    def fake_post(url, payload):
        calls.append(payload)
        return {"results": [{"vulns": []} for _ in payload["queries"]]}

    with patch("tools.osv_federation._post_json", fake_post):
        augment_corpus(refs=refs, base_corpus=base)
    assert len(calls) == 2
    assert len(calls[0]["queries"]) == 1000
    assert len(calls[1]["queries"]) == 500


def test_augment_skips_purls_without_purl_form():
    """Refs whose ecosystem isn't in the PURL map (e.g., claude-skill) aren't
    queryable via OSV.dev — skip them, query the rest."""
    refs = [
        _ref("npm", "lodash", "4.17.20"),
        _ref("claude-skill", "demo", "1.0.0"),
    ]
    base = []

    def fake_post(url, payload):
        # Only the npm ref should have made it into the query batch.
        purls = [p["package"]["purl"] for p in payload["queries"]]
        assert purls == ["pkg:npm/lodash@4.17.20"]
        return {"results": [{"vulns": []}]}

    with patch("tools.osv_federation._post_json", fake_post):
        augment_corpus(refs=refs, base_corpus=base)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_osv_federation.py -q
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the module**

```python
# tools/osv_federation.py
"""OSV.dev federation: batched live query against /v1/querybatch.

ASVE's default scan uses only the local advisories/ corpus. This module
provides opt-in federation via --federate-osv: given a list of emitted
ComponentRefs, fetch matching vulnerability records from OSV.dev and
merge them into the corpus for the matcher to consume.

Behavior:
- Only refs with a derivable PURL (ecosystem in PURL_ECOSYSTEM_MAP +
  name + version) are queried. Identity-only refs (claude-hook,
  claude-command, claude-agent) and ASVE-native ecosystems
  (claude-skill, claude-plugin) are skipped — OSV.dev wouldn't have
  records for them anyway.
- PURLs are deduplicated within a scan (same PURL queried once).
- /v1/querybatch caps at 1000 packages per request; chunked into
  multiple requests if needed.
- Network errors fail-soft: return the base corpus unchanged with a
  warning string. The scan continues with local-corpus-only matching.
- Returned vuln IDs are dereferenced to full records via /v1/vulns/<id>
  and merged into the corpus, deduped against base by `id` (base wins
  on conflict — local advisories override upstream).

Module API:
    augment_corpus(refs, base_corpus) -> (augmented_corpus, warnings)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from tools.component_ref import ComponentRef

_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"
_VULN_URL = "https://api.osv.dev/v1/vulns/{id}"
_BATCH_SIZE = 1000
_TIMEOUT_SECONDS = 30


def augment_corpus(
    refs: list[ComponentRef], base_corpus: list[dict]
) -> tuple[list[dict], list[str]]:
    """Return `(merged_corpus, warnings)`. Fail-soft on any network issue."""
    purls = _collect_purls(refs)
    if not purls:
        return list(base_corpus), []
    try:
        vuln_ids = _query_batch(purls)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return list(base_corpus), [f"osv.dev federation failed: {exc}"]
    if not vuln_ids:
        return list(base_corpus), []
    new_records: list[dict] = []
    fetch_warnings: list[str] = []
    for vid in vuln_ids:
        try:
            record = _get_vuln(vid)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            fetch_warnings.append(f"osv.dev fetch failed for {vid}: {exc}")
            continue
        if isinstance(record, dict) and record.get("id"):
            new_records.append(record)
    base_ids = {a.get("id") for a in base_corpus if isinstance(a, dict)}
    merged = list(base_corpus)
    for r in new_records:
        if r["id"] not in base_ids:
            merged.append(r)
            base_ids.add(r["id"])
    return merged, fetch_warnings


def _collect_purls(refs: list[ComponentRef]) -> list[str]:
    """Deduplicate PURLs from refs that have a derivable PURL."""
    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        purl = r.purl
        if purl is None:
            continue
        if purl in seen:
            continue
        seen.add(purl)
        out.append(purl)
    return out


def _query_batch(purls: list[str]) -> list[str]:
    """POST /v1/querybatch in chunks of <=1000; collect returned vuln IDs."""
    ids: list[str] = []
    seen: set[str] = set()
    for i in range(0, len(purls), _BATCH_SIZE):
        chunk = purls[i : i + _BATCH_SIZE]
        payload = {"queries": [{"package": {"purl": p}} for p in chunk]}
        response = _post_json(_QUERYBATCH_URL, payload)
        for entry in response.get("results", []) or []:
            for vuln in entry.get("vulns") or []:
                vid = vuln.get("id")
                if isinstance(vid, str) and vid not in seen:
                    seen.add(vid)
                    ids.append(vid)
    return ids


def _get_vuln(vuln_id: str) -> dict:
    """GET /v1/vulns/<id> → full advisory record."""
    return _get_json(_VULN_URL.format(id=vuln_id))


def _post_json(url: str, payload: dict) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_osv_federation.py -q
```

Expected: PASS.

- [ ] **Step 5: Full gate**

```bash
uv run pytest -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add tools/osv_federation.py tests/test_osv_federation.py
git commit -m "feat(federation): OSV.dev /v1/querybatch client + corpus merger

augment_corpus(refs, base_corpus) -> (merged, warnings).

Behavior:
- Only PURL-derivable refs (npm/PyPI/GitHub/Docker) queried; identity-only
  and ASVE-native ecosystems skipped.
- PURLs deduped within a scan.
- /v1/querybatch chunked at 1000 packages per request.
- Returned vuln IDs dereferenced via /v1/vulns/<id>.
- Network errors fail-soft: return base corpus + warning.
- Base corpus wins on id conflict (local advisories override upstream).

Mocked at the _post_json/_get_json boundary in tests; real OSV.dev
never hit. Plan 009, Task 6."
```

---

## Task 7: `--federate-osv` flag wiring in `tools/scan.py`

**Files:**
- Modify: `tools/scan.py`
- Modify: `tests/test_scan.py`

When set, after `parse_install` (or `parse_repo`), call `augment_corpus(refs, base_corpus)` and re-run the matcher against the augmented corpus. Network failures emit an unconditional stderr warning (not gated on `-v`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scan.py`:

```python
def test_fs_subcommand_federate_osv_augments_corpus(tmp_path):
    """--federate-osv: augment_corpus is invoked and findings include
    osv.dev-sourced advisories."""
    from unittest.mock import patch

    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"demo@m": True}})
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}
                    ]
                },
            }
        )
    )

    fake_advisory = {
        "schema_version": "1.7.1",
        "id": "GHSA-FAKE-LODASH",
        "modified": "2026-05-10T00:00:00Z",
        "type": "vulnerability",
        "published": "2026-05-10T00:00:00Z",
        "summary": "test",
        "details": "test",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "lodash"},
                "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "5.0.0"}]}],
            }
        ],
    }

    def fake_augment(refs, base_corpus):
        return list(base_corpus) + [fake_advisory], []

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "fs",
                "--target",
                str(tmp_path),
                "--advisories",
                str(REPO_ROOT / "advisories"),
                "--federate-osv",
                "-v",
            ],
        )
    assert result.exit_code == 1, result.output  # finding crossed default --fail-on=any
    assert "GHSA-FAKE-LODASH" in result.output


def test_fs_subcommand_federate_osv_failure_prints_warning(tmp_path, capfd):
    """OSV.dev network failure prints unconditional stderr warning even
    without -v. Exit code stays findings-driven (= 0 when no findings)."""
    from unittest.mock import patch

    (tmp_path / "settings.json").write_text(json.dumps({}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )

    def fake_augment(refs, base_corpus):
        return list(base_corpus), ["osv.dev federation failed: connection refused"]

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "fs",
                "--target",
                str(tmp_path),
                "--advisories",
                str(REPO_ROOT / "advisories"),
                "--federate-osv",
            ],
        )
    assert result.exit_code == 0
    assert "osv.dev federation failed" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_scan.py -q -k federate_osv
```

Expected: FAIL — `--federate-osv` not recognized.

- [ ] **Step 3: Import and wire**

In `tools/scan.py`:

```python
from tools.osv_federation import augment_corpus
```

Add the option to the `fs` subcommand:

```python
@click.option(
    "--federate-osv",
    is_flag=True,
    default=False,
    help="Query OSV.dev for additional vulnerability records covering "
    "emitted PURLs. Augments the local corpus with osv.dev-sourced "
    "findings. Network required; fails soft if OSV.dev is unreachable.",
)
def fs(
    ctx: click.Context,
    target: Path,
    advisories: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    exclude_transitive: bool,
    federate_osv: bool,
) -> None:
```

Wire after `parse_install` and before `match`:

```python
    refs, warnings = parse_install(
        install_root=install_root,
        project_root=project_root,
        mode="fs",
        include_transitive=not exclude_transitive,
    )
    corpus = load_corpus(advisories)
    if federate_osv:
        corpus, fed_warnings = augment_corpus(refs, corpus)
        # Federation warnings print to stderr unconditionally — the user
        # explicitly opted in; silent fallback would violate principle of
        # least surprise.
        for fw in fed_warnings:
            click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus)
```

- [ ] **Step 4: Apply the same pattern to `repo` subcommand**

```python
@click.option(
    "--federate-osv",
    is_flag=True,
    default=False,
    help="Query OSV.dev for additional vulnerability records.",
)
def repo(
    ctx: click.Context,
    target: Path,
    advisories: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    federate_osv: bool,
) -> None:
    ...
    grouped, n_found = parse_repo_grouped(target)
    refs = [r for _, rs in grouped for r in rs]
    corpus = load_corpus(advisories)
    if federate_osv:
        corpus, fed_warnings = augment_corpus(refs, corpus)
        for fw in fed_warnings:
            click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus)
```

- [ ] **Step 5: Tag advisories with source for SARIF**

After loading corpus and augmenting, walk both and stamp:

```python
    for a in corpus:
        if not isinstance(a, dict):
            continue
        if "database_specific" not in a:
            a["database_specific"] = {}
        ds = a["database_specific"]
        if not isinstance(ds, dict):
            continue
        ds_asve = ds.get("asve")
        if not isinstance(ds_asve, dict):
            ds_asve = {}
            ds["asve"] = ds_asve
        # Local advisories tagged asve.dev unless the record already specifies otherwise.
        # OSV.dev advisories (added by augment_corpus) get tagged osv.dev.
        if "source" not in ds_asve:
            ds_asve["source"] = "osv.dev" if a.get("id") and a["id"] not in {x.get("id") for x in load_corpus(advisories)} else "asve.dev"
```

Simplify — track sources explicitly during augmentation. **Revise the wiring:**

In `tools/scan.py`, instead of post-tagging, mark each advisory at load/augment time:

```python
    corpus = load_corpus(advisories)
    _stamp_source(corpus, "asve.dev")
    if federate_osv:
        before_ids = {a.get("id") for a in corpus if isinstance(a, dict)}
        corpus, fed_warnings = augment_corpus(refs, corpus)
        for a in corpus:
            if isinstance(a, dict) and a.get("id") not in before_ids:
                _stamp_source([a], "osv.dev")
        for fw in fed_warnings:
            click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus)
```

Helper at module level:

```python
def _stamp_source(corpus: list[dict], source: str) -> None:
    """Set `database_specific.asve.source = <source>` on every advisory."""
    for a in corpus:
        if not isinstance(a, dict):
            continue
        ds = a.setdefault("database_specific", {})
        if not isinstance(ds, dict):
            continue
        asve_block = ds.setdefault("asve", {})
        if isinstance(asve_block, dict) and "source" not in asve_block:
            asve_block["source"] = source
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_scan.py -q -k federate_osv
```

Expected: PASS.

- [ ] **Step 7: Full gate**

```bash
uv run pytest -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add tools/scan.py tests/test_scan.py
git commit -m "feat(scan): --federate-osv flag; corpus augmentation + source stamping

Both fs and repo subcommands gain --federate-osv (default OFF). When set,
augment_corpus queries OSV.dev for emitted PURLs and merges results into
the matching corpus. Per-advisory source tag stamped into
database_specific.asve.source so SARIF can surface (Task 8).

Federation network failures print unconditional stderr warnings (not
gated on -v) — the user explicitly opted in. Exit code stays findings-
driven. Plan 009, Task 7."
```

---

## Task 8: SARIF `properties.{coverage, transitive, source}`

**Files:**
- Modify: `tools/sarif.py`
- Modify: `tests/test_sarif.py`

Surface the new metadata on each SARIF result. `coverage` and `transitive` come from `finding.component.extra`; `source` from `advisory["database_specific"]["asve"]["source"]`. All three are absent when their underlying data is missing.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sarif.py`:

```python
def test_sarif_emits_coverage_and_transitive_for_lockfile_findings():
    """A finding from a lockfile-derived ref gets coverage=transitive +
    transitive=true in SARIF properties."""
    from tools.component_ref import ComponentRef
    from tools.matcher import Finding
    from tools.sarif import to_sarif

    ref = ComponentRef(
        ecosystem="npm",
        name="lodash",
        version="4.17.20",
        attributed_to="claude-plugin/demo@1.0.0",
        extra={"transitive": True},
    )
    finding = Finding(
        advisory_id="GHSA-1",
        component=ref,
        confidence="high",
        reason="match",
        attributed_to="claude-plugin/demo@1.0.0",
    )
    advisory = {
        "id": "GHSA-1",
        "summary": "test",
        "details": "test",
        "database_specific": {"asve": {"source": "osv.dev"}},
    }
    doc = to_sarif([finding], {"GHSA-1": advisory})
    result = doc["runs"][0]["results"][0]
    properties = result.get("properties", {})
    assert properties.get("coverage") == "transitive"
    assert properties.get("transitive") is True
    assert properties.get("source") == "osv.dev"
    assert properties.get("attributed_to") == "claude-plugin/demo@1.0.0"


def test_sarif_emits_direct_only_for_manifest_fallback_findings():
    from tools.component_ref import ComponentRef
    from tools.matcher import Finding
    from tools.sarif import to_sarif

    ref = ComponentRef(
        ecosystem="npm",
        name="lodash",
        version="4.17.20",
        attributed_to="claude-plugin/demo@1.0.0",
        extra={"transitive": False, "fallback_reason": "no npm lockfile present"},
    )
    finding = Finding(
        advisory_id="GHSA-1",
        component=ref,
        confidence="high",
        reason="match",
        attributed_to="claude-plugin/demo@1.0.0",
    )
    advisory = {
        "id": "GHSA-1",
        "summary": "test",
        "details": "test",
        "database_specific": {"asve": {"source": "asve.dev"}},
    }
    doc = to_sarif([finding], {"GHSA-1": advisory})
    properties = doc["runs"][0]["results"][0]["properties"]
    assert properties.get("coverage") == "direct-only"
    assert properties.get("transitive") is False


def test_sarif_omits_coverage_for_tier1_findings():
    """Tier-1 inventory findings (extra without `transitive`) have no
    coverage/transitive properties."""
    from tools.component_ref import ComponentRef
    from tools.matcher import Finding
    from tools.sarif import to_sarif

    ref = ComponentRef(
        ecosystem="claude-skill",
        name="vulnerable-skill",
        version="0.9.0",
    )
    finding = Finding(
        advisory_id="ASVE-2026-9001",
        component=ref,
        confidence="high",
        reason="match",
    )
    advisory = {"id": "ASVE-2026-9001", "summary": "test", "details": "test"}
    doc = to_sarif([finding], {"ASVE-2026-9001": advisory})
    properties = doc["runs"][0]["results"][0].get("properties", {})
    assert "coverage" not in properties
    assert "transitive" not in properties
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_sarif.py -q -k "coverage or direct_only or tier1"
```

Expected: FAIL — properties not yet populated.

- [ ] **Step 3: Update `tools/sarif.py`**

Locate the function that builds a SARIF result and extend its `properties` block. The relevant logic computes properties per finding:

```python
def _properties_for(finding: Finding, advisory: dict | None) -> dict:
    props: dict = {}
    if finding.attributed_to:
        props["attributed_to"] = finding.attributed_to
    extra = finding.component.extra or {}
    if "transitive" in extra:
        transitive = bool(extra["transitive"])
        props["transitive"] = transitive
        props["coverage"] = "transitive" if transitive else "direct-only"
    if isinstance(advisory, dict):
        ds = advisory.get("database_specific")
        if isinstance(ds, dict):
            asve_block = ds.get("asve")
            if isinstance(asve_block, dict):
                source = asve_block.get("source")
                if isinstance(source, str):
                    props["source"] = source
    return props
```

Replace the inline properties construction in the SARIF result builder with a call to `_properties_for(finding, advisory_index.get(finding.advisory_id))`, emitting `properties=props` only when `props` is non-empty (existing behavior preserved for findings with no metadata).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_sarif.py -q
```

Expected: PASS.

- [ ] **Step 5: Full gate**

```bash
uv run pytest -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add tools/sarif.py tests/test_sarif.py
git commit -m "feat(sarif): emit properties.{coverage, transitive, source}

Per-result properties now surface plan 009 metadata:
- coverage = 'transitive' (lockfile) | 'direct-only' (manifest fallback)
- transitive = bool mirror of coverage for easier downstream parsing
- source = 'asve.dev' | 'osv.dev' from advisory.database_specific.asve.source

Tier-1 findings (no extra['transitive']) omit coverage + transitive.
Documented in docs/sarif-conventions.md (Task 10). Plan 009, Task 8."
```

---

## Task 9: Verbose output — per-plugin coverage line + federation note

**Files:**
- Modify: `tools/scan.py`
- Modify: `tests/test_scan.py`

Extend the fs-mode `-v` block to show per-plugin Tier-2 coverage and (when federation is on) a federation summary line.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scan.py`:

```python
def test_fs_verbose_shows_per_plugin_tier2_coverage(tmp_path):
    """Verbose output includes a 'npm: package-lock.json (transitive, N packages)'
    line per plugin that has Tier-2 coverage."""
    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                    "node_modules/underscore": {"version": "1.13.0"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"demo@m": True}})
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}
                    ]
                },
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fs",
            "--target",
            str(tmp_path),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "npm:" in result.output
    assert "package-lock.json" in result.output
    assert "2 packages" in result.output or "transitive, 2" in result.output


def test_fs_verbose_shows_manifest_fallback_line(tmp_path):
    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"lodash": "^4.17.0"}})
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"demo@m": True}})
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}
                    ]
                },
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fs",
            "--target",
            str(tmp_path),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert "direct only" in result.output
    assert "package.json" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_scan.py::test_fs_verbose_shows_per_plugin_tier2_coverage -q
```

Expected: FAIL — verbose output doesn't include per-plugin coverage lines yet.

- [ ] **Step 3: Add Tier-2 coverage rendering**

In `tools/scan.py`, replace the per-plugin verbose echo with a helper-augmented form:

```python
        for r in refs:
            if r.ecosystem == "claude-plugin":
                sha = r.extra.get("gitCommitSha")
                sha_note = f" (sha: {sha[:8]})" if isinstance(sha, str) and sha else ""
                bundled = _bundled_breakdown(refs, r.component_identity)
                scope_str = r.extra.get("scope")
                click.echo(
                    f"  {r.component_identity}{sha_note} "
                    f"[scope={scope_str}] → {bundled}",
                    err=True,
                )
                for line in _tier2_coverage_lines(refs, r.component_identity):
                    click.echo(f"    {line}", err=True)
```

Add the helper near `_bundled_breakdown`:

```python
def _tier2_coverage_lines(
    refs: list[ComponentRef], plugin_identity: str | None
) -> list[str]:
    """Per-plugin Tier-2 coverage: one line per ecosystem covered.

    Format:
      npm: package-lock.json (transitive, 247 packages)
      PyPI: package.json (direct only, 8 packages)
    """
    if plugin_identity is None:
        return []
    by_eco: dict[str, list[ComponentRef]] = {}
    for r in refs:
        if r.attributed_to != plugin_identity:
            continue
        if r.ecosystem not in {"npm", "PyPI"}:
            continue
        if r.extra.get("transitive") is None:
            continue
        by_eco.setdefault(r.ecosystem or "", []).append(r)
    out: list[str] = []
    for eco, ecorefs in sorted(by_eco.items()):
        is_transitive = any(r.extra.get("transitive") is True for r in ecorefs)
        if is_transitive:
            source = "package-lock.json" if eco == "npm" else "uv.lock"
            out.append(f"{eco}: {source} (transitive, {len(ecorefs)} packages)")
        else:
            source = "package.json" if eco == "npm" else "pyproject.toml"
            out.append(f"{eco}: {source} (direct only, {len(ecorefs)} packages)")
    return out
```

- [ ] **Step 4: Add federation summary line**

After the per-plugin block in the verbose path:

```python
        if federate_osv:
            osv_count = sum(
                1
                for f in findings
                if (advisory_index.get(f.advisory_id) or {})
                .get("database_specific", {})
                .get("asve", {})
                .get("source")
                == "osv.dev"
            )
            click.echo(f"federation: osv.dev returned {osv_count} additional finding(s)", err=True)
```

(Place this guarded on `federate_osv` so it only appears when the flag is set. Hoist `advisory_index` if needed.)

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_scan.py::test_fs_verbose_shows_per_plugin_tier2_coverage -q
uv run pytest tests/test_scan.py::test_fs_verbose_shows_manifest_fallback_line -q
```

Expected: PASS.

- [ ] **Step 6: Full gate**

```bash
uv run pytest -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add tools/scan.py tests/test_scan.py
git commit -m "feat(scan): per-plugin Tier-2 coverage + federation note in -v

Verbose fs output now includes per-plugin ecosystem coverage lines:
  npm: package-lock.json (transitive, 247 packages)
  PyPI: pyproject.toml (direct only, 8 packages)

When --federate-osv is set, an additional summary line shows the
osv.dev-sourced finding count. Plan 009, Task 9."
```

---

## Task 10: ADR-0008 + `docs/sarif-conventions.md`

**Files:**
- Create: `docs/adrs/0008-lockfile-dispatch-and-osv-federation.md`
- Create: `docs/sarif-conventions.md`
- Modify: `docs/adrs/INDEX.md`

- [ ] **Step 1: Write ADR-0008**

```bash
cat > docs/adrs/0008-lockfile-dispatch-and-osv-federation.md <<'EOF'
---
id: 0008
title: Lockfile dispatch; manifest fallback; OSV.dev federation as opt-in
status: accepted
date: 2026-05-10
supersedes: null
superseded-by: null
---

## Context

Plan 007 wired identity + attribution. Plan 008 enumerated the Tier-1
declarative agent stack. Plan 009 adds Tier-2 implementation-deps SCA —
the dominant attack-surface for known-CVE matching. Empirical dogfood
(2026-05-10) against `~/.claude/plugins/cache` showed that
`trivy filesystem` and `osv-scanner --recursive` both walk orphaned
cache versions and plugin test fixtures, with no plugin attribution
on results. Plan 009 differentiates by being install-state-aware and
attribution-aware. The design choices below would be re-suggested
without an ADR.

## Decision

### 1. Parse ALL supported lockfiles per active plugin, not first-match

A single plugin can legitimately ship JS code (with `package-lock.json`)
alongside an embedded Python tool (with `uv.lock`). First-match priority
would silently miss one ecosystem. Cost: one extra existence check per
ecosystem per plugin — negligible.

### 2. Lockfile vs manifest fallback are NOT equivalent

Lockfile = full transitive tree for that ecosystem. Manifest fallback =
direct deps only. Manifest-fallback emissions tag `extra["transitive"]
=False` and `extra["fallback_reason"]=f"no {ecosystem} lockfile present"`.
SARIF surfaces this via `properties.coverage`. Downstream consumers
explicitly know which case they're in. Pretending the two are equivalent
would let manifest-fallback findings claim coverage they don't have.

### 3. `--exclude-transitive` is opt-OUT (default OFF)

Default-on mirrors Dependabot/Snyk/Trivy default-everything behavior.
Power users wanting agent-stack-only output disable via the flag.
Considered alternative: default-off (opt-in via `--include-transitive`)
— rejected because the dominant CVE-matching use case is Tier-2, and
making it opt-in would surprise users coming from traditional SCA.

### 4. `--federate-osv` is opt-IN (default OFF)

OSV.dev federation adds a network dependency to scans. Default-off
keeps the default scan offline and focused on the ASVE corpus. Users
who want full Tier-2 coverage (generic CVEs in plugin transitive deps)
explicitly opt in. Considered alternatives:

- **Default-on federation**: makes scans network-dependent by default;
  rejected as too aggressive for V0.
- **Offline OSV.dev mirror**: ~30k records, refresh discipline,
  significant storage. Deferred to V1.

### 5. ASVE's value-add is filtering + attribution, not corpus coverage

The empirical comparison against `trivy`/`osv-scanner` showed they
report against orphaned cache versions and test fixtures inside plugins
with no attribution. ASVE walks per `installed_plugins.json` (active
plugins only) and per `plugin.json` defaults (no `rglob` inside the
install path), tagging every Tier-2 ref with `attributed_to`. This is
the load-bearing differentiator — federation enhances it; it doesn't
replace it.

### 6. No `node_modules` walking, no package-manager invocation

Trust the lockfile or fall back to direct-only manifest scanning.
Re-implementing npm/pip resolution at scan time is V0-out-of-scope.

### 7. `uv.lock` dev-vs-runtime filtering is best-effort

`uv.lock` doesn't reliably annotate dev-only the way npm's `dev: true`
does. V0 emits all packages from `uv.lock`; over-reporting dev deps is
acceptable. Refine in V1 if uv's lockfile schema stabilizes the
distinction.

### 8. `source` ecosystem-style naming: `asve.dev` and `osv.dev`

Per-finding SARIF property `properties.source` takes values `"asve.dev"`
or `"osv.dev"` (matching the OSV.dev convention). Future-aligned with
the eventual asve.dev domain; consistent ecosystem-style provenance
for downstream consumers.

## Alternatives considered

- **First-match lockfile priority**: rejected (multi-language plugins
  miss ecosystems).
- **Treat manifest fallback as full coverage**: rejected (claims coverage
  it doesn't have).
- **Default-on federation**: rejected (network-dependent default scan).
- **Offline OSV.dev mirror**: deferred to V1 (storage + refresh
  discipline beyond V0 scope).
- **Reimplement package-manager resolution**: rejected (V0-out-of-scope).

## Consequences

**Enables:**
- ASVE becomes a better-UX Tier-2 scanner than `trivy`/`osv-scanner` for
  the agent-stack case (filtered + attributed).
- `--federate-osv` lets users compose ASVE's filtering with OSV.dev's
  full corpus.
- Lockfile-vs-manifest coverage is honestly surfaced in SARIF.

**Costs:**
- `--federate-osv` adds a network dependency when enabled. Fail-soft
  semantics mitigate (warning + continue with corpus-only).
- `uv.lock` over-reports dev deps in V0.
- Two new flags add CLI surface area.

**Watch:**
- OSV.dev rate-limiting if scans grow large; V1 may need backoff/retry.
- If yarn.lock or pnpm-lock.yaml become demand-driven (real plugins
  using them), add parsers in a follow-up.

## When to revisit

- We add an offline OSV.dev mirror (V1+).
- A real plugin ships in yarn.lock or pnpm-lock.yaml format.
- uv's lockfile schema stabilizes dev-vs-runtime annotations.
- We hit OSV.dev rate limits on real scans.
EOF
```

- [ ] **Step 2: Write SARIF conventions doc**

```bash
cat > docs/sarif-conventions.md <<'EOF'
# ASVE SARIF Conventions

ASVE emits SARIF v2.1.0 with ASVE-specific extensions under
`runs[].results[].properties`. This document is the contract for
downstream consumers.

## Property reference

| Key | Type | Values | Set when |
|---|---|---|---|
| `attributed_to` | string \| absent | `"claude-plugin/<name>@<version>"` | The component was discovered via an active plugin's installPath (ADR-0006). Absent when the component is direct (bare in settings, repo-declared, host repo lockfile). |
| `coverage` | string \| absent | `"transitive"` \| `"direct-only"` | Tier-2 implementation-dep findings. `"transitive"` when the ref came from a lockfile; `"direct-only"` when it came from a manifest fallback (no lockfile for that ecosystem). Absent on Tier-1 inventory findings (ADR-0007, ADR-0008). |
| `transitive` | bool \| absent | `true` \| `false` | Bool mirror of `coverage` for easier downstream parsing. Absent when `coverage` is absent. |
| `source` | string \| absent | `"asve.dev"` \| `"osv.dev"` | The advisory record's provenance. `"asve.dev"` is the local ASVE corpus; `"osv.dev"` is OSV.dev (when `--federate-osv` was set during the scan). Absent when no source is declared on the advisory. |

## Stability promise

Per ADR-0008 these properties are part of the V0 contract. Adding new
properties or new values to existing properties is non-breaking; removing
or changing semantics requires a superseding ADR.

## Example

```json
{
  "ruleId": "GHSA-FAKE-LODASH",
  "level": "error",
  "message": { "text": "lodash@4.17.20 matches GHSA-FAKE-LODASH" },
  "locations": [/* ... */],
  "properties": {
    "attributed_to": "claude-plugin/superpowers@5.1.0",
    "coverage": "transitive",
    "transitive": true,
    "source": "osv.dev"
  }
}
```

This result means: `lodash@4.17.20` was found in the transitive tree
(`coverage=transitive`) of the active plugin `superpowers@5.1.0`
(`attributed_to`); the advisory came from OSV.dev's federation pass
(`source=osv.dev`).

## Why these keys

`attributed_to` answers "which plugin should I remediate?" — directly
actionable. `coverage`/`transitive` distinguish "lockfile says this is in
the tree" from "manifest says this is a declared direct dep, transitive
unknown." `source` lets corpus-aware consumers (e.g., users running
ASVE-only governance) filter out federation-sourced findings.
EOF
```

- [ ] **Step 3: Update ADR INDEX**

Edit `docs/adrs/INDEX.md`, append to the "Active" list:

```markdown
- [ADR-0008 — Lockfile dispatch, manifest fallback, OSV.dev federation](0008-lockfile-dispatch-and-osv-federation.md): parse ALL supported lockfiles per active plugin (not first-match); manifest fallback ≠ lockfile coverage (extra.transitive distinguishes); `--exclude-transitive` is opt-OUT; `--federate-osv` is opt-IN; ASVE's value-add is install-state-aware filtering + attribution, not corpus coverage; SARIF `properties.source` uses ecosystem-style `asve.dev`/`osv.dev` naming.
```

- [ ] **Step 4: Commit**

```bash
git add docs/adrs/0008-lockfile-dispatch-and-osv-federation.md docs/adrs/INDEX.md docs/sarif-conventions.md
git commit -m "docs: ADR-0008 + SARIF conventions for plan 009

ADR-0008 captures the load-bearing decisions: parse-all-lockfiles,
lockfile-vs-manifest coverage distinction, --exclude-transitive opt-out,
--federate-osv opt-in, ASVE's filtering+attribution value-add over
trivy/osv-scanner, asve.dev/osv.dev source naming.

docs/sarif-conventions.md documents the ASVE-specific properties
(attributed_to, coverage, transitive, source) as the V0 contract for
downstream consumers. Plan 009, Task 10."
```

---

## Task 11: README + plans index updates

**Files:**
- Modify: `README.md`
- Modify: `docs/plans/README.md`

- [ ] **Step 1: Update README's tier table**

Edit `README.md`, find the Tier table and change Tier 2's status from "✅ V0 (lockfiles in plan 009)" to "✅ V0".

Add a brief federation note immediately after the table:

```markdown
**OSV.dev federation (opt-in).** Pass `--federate-osv` to either subcommand
to query OSV.dev for additional vulnerability records covering emitted
PURLs. Combines ASVE's install-state filtering and attribution with OSV.dev's
full corpus — a more accurate Tier-2 scanner for agent stacks than generic
recursive walkers. See `docs/adrs/0008-lockfile-dispatch-and-osv-federation.md`.
```

- [ ] **Step 2: Update plans README**

Edit `docs/plans/README.md`:

```markdown
| 009 | [Plugin-internal implementation deps (lockfile transitive scanning) + OSV.dev federation](009-plugin-internal-deps.md) | 🟡 Active | 008 |
```

Position it just after the 008 row.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/plans/README.md
git commit -m "docs: README tier-2 to V0; plans index marks 009 active

Plan 009, Task 11."
```

---

## Task 12: End-to-end test + dogfood + full gate

**Files:**
- Modify: `tests/test_e2e.py`
- Create: `tests/fixtures/installs/with-transitive-vuln/` and contents

- [ ] **Step 1: Add fixture install layout**

```bash
mkdir -p tests/fixtures/installs/with-transitive-vuln/plugins
mkdir -p tests/fixtures/installs/with-transitive-vuln/cache/vuln-plugin/1.0.0
```

`tests/fixtures/installs/with-transitive-vuln/settings.json`:

```json
{"enabledPlugins": {"vuln-plugin@m": true}}
```

`tests/fixtures/installs/with-transitive-vuln/plugins/installed_plugins.json` will be templated by the test (it needs an absolute installPath); see Step 2.

`tests/fixtures/installs/with-transitive-vuln/cache/vuln-plugin/1.0.0/package-lock.json`:

```json
{
  "lockfileVersion": 3,
  "packages": {
    "": {"name": "vuln-plugin", "version": "1.0.0"},
    "node_modules/@cyanheads/git-mcp-server": {"version": "1.1.0"}
  }
}
```

(Re-uses the same package as ASVE-2026-0001 in the existing corpus.)

- [ ] **Step 2: Write the E2E test**

Append to `tests/test_e2e.py`:

```python
def test_fs_lockfile_transitive_finding_with_attribution(tmp_path):
    """Plan 009 end-to-end: an active plugin's package-lock.json contains
    a package that matches a real corpus advisory; the finding fires with
    via-claude-plugin attribution and SARIF coverage=transitive."""
    from tools.scan import main as scan_main

    # Build install layout with a real cache dir (must be absolute).
    cache_dir = tmp_path / "cache" / "vuln-plugin" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "vuln-plugin", "version": "1.0.0"},
                    "node_modules/@cyanheads/git-mcp-server": {"version": "1.1.0"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"vuln-plugin@m": True}})
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "vuln-plugin@m": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}
                    ]
                },
            }
        )
    )

    sarif_path = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "fs",
            "--target",
            str(tmp_path),
            "--advisories",
            str(ADVISORIES_DIR),
            "--sarif",
            str(sarif_path),
            "-v",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "ASVE-2026-0001" in result.output
    assert "via claude-plugin/vuln-plugin@1.0.0" in result.output

    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    results = sarif["runs"][0]["results"]
    matching = [
        r for r in results if r.get("ruleId") == "ASVE-2026-0001"
    ]
    assert matching
    properties = matching[0].get("properties", {})
    assert properties.get("coverage") == "transitive"
    assert properties.get("transitive") is True
    assert properties.get("attributed_to") == "claude-plugin/vuln-plugin@1.0.0"
    assert properties.get("source") == "asve.dev"


def test_fs_exclude_transitive_suppresses_lockfile_finding(tmp_path):
    """The same fixture under --exclude-transitive should not fire the finding."""
    from tools.scan import main as scan_main

    cache_dir = tmp_path / "cache" / "vuln-plugin" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "vuln-plugin", "version": "1.0.0"},
                    "node_modules/@cyanheads/git-mcp-server": {"version": "1.1.0"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"vuln-plugin@m": True}})
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "vuln-plugin@m": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}
                    ]
                },
            }
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "fs",
            "--target",
            str(tmp_path),
            "--advisories",
            str(ADVISORIES_DIR),
            "--exclude-transitive",
        ],
    )
    assert result.exit_code == 0
    assert "ASVE-2026-0001" not in result.output


def test_repo_lockfile_finds_corpus_advisory(tmp_path):
    """Repo mode + package-lock.json at root: lockfile findings emit with
    attributed_to=None and coverage=transitive."""
    from tools.scan import main as scan_main

    target = tmp_path / "host-repo"
    target.mkdir()
    (target / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "host", "version": "1.0.0"},
                    "node_modules/@cyanheads/git-mcp-server": {"version": "1.1.0"},
                },
            }
        )
    )
    sarif_path = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            str(target),
            "--advisories",
            str(ADVISORIES_DIR),
            "--sarif",
            str(sarif_path),
        ],
    )
    assert result.exit_code == 1, result.output
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    matching = [r for r in sarif["runs"][0]["results"] if r.get("ruleId") == "ASVE-2026-0001"]
    assert matching
    properties = matching[0].get("properties", {})
    assert properties.get("coverage") == "transitive"
    assert properties.get("attributed_to") is None or "attributed_to" not in properties
```

- [ ] **Step 3: Run E2E tests**

```bash
uv run pytest tests/test_e2e.py -q
```

Expected: all pass, including the three new tests.

- [ ] **Step 4: Full gate**

```bash
uv run pytest -q
uv run ruff format tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
uv run asve-lint advisories/
```

Expected: all green.

- [ ] **Step 5: Dogfood on real `~/.claude` install**

```bash
uv run asve-scan fs --target ~/.claude --advisories advisories -v
```

Expected: per-plugin Tier-2 coverage lines appear; no findings fire unless real-corpus matches exist; output stays sensible (no crashes, no warnings unless valid).

- [ ] **Step 6: Dogfood with federation (online)**

```bash
uv run asve-scan fs --target ~/.claude --advisories advisories --federate-osv -v
```

Expected: federation summary line shows osv.dev results count; if OSV.dev is unreachable, unconditional stderr warning appears.

- [ ] **Step 7: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): plan 009 end-to-end with attribution + coverage SARIF

Three scenarios:
1. fs mode + plugin lockfile contains @cyanheads/git-mcp-server@1.1.0
   → ASVE-2026-0001 fires with via-attribution + coverage=transitive +
   transitive=true + source=asve.dev in SARIF properties.
2. Same fixture under --exclude-transitive → finding suppressed.
3. Repo mode + package-lock.json at repo root → finding fires with
   attributed_to absent and coverage=transitive.

Dogfooded against real ~/.claude (results vary by user's installed
plugins). Plan 009, Task 12."
```

- [ ] **Step 8: Push and open PR**

```bash
git push -u origin feat/plugin-internal-deps
gh pr create --base main --title "Plan 009: plugin-internal deps + OSV.dev federation" --body "$(cat <<'PR_EOF'
## Summary

Implements plan 009: Tier-2 SCA coverage with `--federate-osv` opt-in for OSV.dev.

- Two new lockfile parsers: `package-lock.json` (npm v3, skips dev:true + host) and `uv.lock` (TOML, best-effort dev filtering).
- Both modes wired: REGISTRY in repo mode; `_walk_plugin_implementation_deps` in fs mode (per-plugin).
- Parse-ALL-lockfiles (not first-match): multi-language plugins emit both ecosystems.
- Manifest fallback when no lockfile for an ecosystem; tagged `transitive=False` + `fallback_reason`.
- `--exclude-transitive` (default OFF): skips Tier-2 walks entirely.
- `--federate-osv` (default OFF): batched `/v1/querybatch` query, full advisory fetch via `/v1/vulns/<id>`, merged into local corpus. Fail-soft on network errors with unconditional stderr warning.
- SARIF `properties.coverage` (`transitive`|`direct-only`), `properties.transitive` (bool), `properties.source` (`asve.dev`|`osv.dev`) — Tier-1 omits these.
- Per-plugin Tier-2 coverage line in `-v` output: `npm: package-lock.json (transitive, N packages)`.
- ADR-0008 captures load-bearing decisions; `docs/sarif-conventions.md` formalizes the property contract.

Empirically motivated: dogfood (2026-05-10) showed `trivy filesystem ~/.claude/plugins/cache` and `osv-scanner --recursive ~/.claude/plugins/cache` walk orphaned cache versions + test fixtures with no attribution. ASVE walks per `installed_plugins.json` + plugin.json defaults; this PR adds the Tier-2 layer on top.

## Test plan

- [x] `uv run pytest -q` — full suite green
- [x] `uv run ruff format --check tools/ tests/`
- [x] `uv run ruff check tools/ tests/`
- [x] `uv run pyright tools/ tests/`
- [x] `uv run asve-lint advisories/`
- [x] Dogfooded `asve-scan fs --target ~/.claude -v` — per-plugin coverage lines render correctly
- [x] Dogfooded `asve-scan fs --target ~/.claude --federate-osv -v` — federation summary appears; fail-soft when offline
- [x] Dogfooded `asve-scan repo --target . -v` — repo-mode unchanged for the GitHub Action; new lockfile patterns fire on `package-lock.json`/`uv.lock` if present

🤖 Generated with [Claude Code](https://claude.com/claude-code)
PR_EOF
)"
```

---

## Self-review

### 1. Spec coverage

| Spec requirement | Task |
|---|---|
| `package_lock_json` parser (skip `""`, `dev:true`, `extra.transitive=True`) | 1 |
| `uv_lock` parser (TOML, best-effort dev, `extra.transitive=True`) | 2 |
| Repo-mode REGISTRY wiring (`attributed_to=None`) | 3 |
| fs-mode `_walk_plugin_implementation_deps` (parse-all-lockfiles, manifest fallback per uncovered ecosystem) | 4 |
| `--exclude-transitive` flag (default OFF, opt-out) | 5 |
| `osv_federation` module (batched `/v1/querybatch`, `/v1/vulns/<id>`, dedup, chunk-1000, fail-soft) | 6 |
| `--federate-osv` flag wiring + source stamping + unconditional stderr warning | 7 |
| SARIF `properties.{coverage, transitive, source}` | 8 |
| Verbose per-plugin Tier-2 coverage line + federation summary | 9 |
| ADR-0008 + SARIF conventions doc | 10 |
| README + plans index updates | 11 |
| E2E + dogfood | 12 |
| Spec §"Resolved details" #1 (Finding stays minimal) | Implemented via Task 8 (SARIF dereferences) |
| Spec §"Resolved details" #2 (single-pass batched query, no cache) | Implemented in Task 6 (`_query_batch` is single-pass) |
| Spec §"Resolved details" #3 (unconditional stderr warning) | Implemented in Task 7 |
| Spec §"Active-state filtering as ASVE's edge" | Already in plan 008; plan 009's `_walk_plugin_implementation_deps` only walks plugin install roots discovered via `installed_plugins.json` — preserves the property |

No gaps.

### 2. Placeholder scan

Searched for `TBD`, `TODO`, `implement later`, `fill in details`, `Add appropriate error handling`, `Similar to Task N`. None present in the plan body. All steps that change code include the actual code.

### 3. Type consistency

- `parse(path: Path) -> list[ComponentRef]` used in both lockfile parsers — matches existing parser convention.
- `parse_install(..., include_transitive: bool = True)` consistent through Tasks 4 and 5.
- `_walk_plugin_implementation_deps(install_path: Path, attributed_to: str) -> list[ComponentRef]` consistent in Task 4 definition and Task 4 call site.
- `augment_corpus(refs: list[ComponentRef], base_corpus: list[dict]) -> tuple[list[dict], list[str]]` consistent across Task 6 implementation, Task 7 import, and Task 6 tests.
- `extra["transitive"]` (bool), `extra["fallback_reason"]` (str), `properties.coverage` (`"transitive"`/`"direct-only"`) consistent across Tasks 1, 2, 4, 6, 8, 9.
- `database_specific.asve.source` consistent across Tasks 7, 8, 10.

No inconsistencies.
