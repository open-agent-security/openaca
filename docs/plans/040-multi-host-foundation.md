# Cursor Multi-Host Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tasks 1-9 are already implemented and committed (checked below); Tasks 10-18 are not yet started.

**Goal:** Make Cursor a fully-supported host across every OpenACA scan surface — MCP servers, Skills, Plugins (both the native Cursor Plugins format and the Agent Plugins open standard), Subagents, and Commands — in both `openaca scan repo` and `openaca scan endpoint`.

**Architecture:** A `HostAdapter` registry (`tools/hosts.py`) records, per host, what varies for discovery: which manifest patterns belong to it, what `runtime_hosts` tag its parsers stamp, and (for endpoint mode) how to compose its own children onto the shared target node. Surfaces that place uniformly (direct child of `target`, regardless of host — MCP servers, Skills, Commands, and a plugin's own top-level manifest) reuse one registry-driven dispatch pattern (`HostAdapter.manifest_registry` + `registry_pattern_matches`, read identically by manifest accounting and graph placement), instead of hardcoded per-host branches. Subagents doesn't fit that pattern: Cursor's compatibility read of `.claude/agents/` requires inspecting a *sibling* path before deciding occurrence count, something the registry's per-pattern matcher can't express, so it gets a dedicated precedence-aware resolver instead. A plugin's *bundled* components (skills/agents/commands/hooks/MCP nested inside it) are walked programmatically by a parameterized root-walker, not through the registry. Endpoint mode closes the second half of the host-agnostic architecture (`EndpointSeedFn`, the per-host composition loop): `_seed_endpoint()`'s body moves to `tools/endpoint_seeds/claude_code.py` (bound to the adapter through a lazy wrapper in `tools/hosts.py`, keeping the module graph acyclic), `build_graph()`'s endpoint branch becomes a per-host loop over an explicit `{host_id: config_root}` map plus one cross-host Subagent pass (shared-file occurrences can span hosts, so neither host's seed can own them), the graph's root-sensitive stages (normalization, manifest-name index, launch-dependency attachment) become multi-root with per-host key labels, and Cursor gets `tools/endpoint_seeds/cursor.py` composing MCP/Skills/Commands/dev-linked Plugins (both formats, presence-only, per ADR-0045 Decision #7). Endpoint posture collection, `bom endpoint`, and `remote sync endpoint` resolve hosts and roots through the same shared request contract (`resolve_endpoint_request`). See `docs/specs/multi-host-support.md`, `docs/adrs/0044-multi-host-support.md` (the host-agnostic mechanism), and `docs/adrs/0045-cursor-host.md` (Cursor-specific decisions) for full design rationale.

**Tech Stack:** Python 3.11+, click (CLI), pytest, existing `tools/graph_build.py` / `tools/parsers/` / `tools/posture/` / `tools/hosts.py` modules — no new dependencies.

## Global Constraints

- POSIX-only manifest parsing (ADR-0005) — no Windows path handling.
- `openaca:identity` never includes host; host is provenance only, carried in `ComponentRef.extra["runtime_hosts"]` / emitted as `openaca:runtime_hosts` (ADR-0029, ADR-0042, ADR-0044 Decision #2 — one property, no derived singular companion). **Plugins specifically:** use the unqualified `plugin/{name}` identity string for both Cursor plugin formats — never `plugin/cursor/{name}` or any string with 2+ slashes unless `extra["marketplace"]` is genuinely set from verified install-state. A 2-or-more-slash `component_identity` with no `marketplace` extra is silently accepted as real cross-BOM identity by `tools/identity.py`'s `canonical_component_identity` fallback branch — ADR-0044 Decision #2 documents this trap generically; ADR-0045 Decision #2 is the concrete Cursor-plugin mistake it was found from and corrects; do not reintroduce it.
- No OpenACA-minted vulnerability IDs, no new severity taxonomy, no commercial/competitive framing in any file (CLAUDE.md).
- Default to no comments in new code; add one only when the *why* is non-obvious (a hidden constraint, a subtle invariant) — matches existing style in every file this plan touches.
- Every new/changed public function keeps its existing call sites working unless a step explicitly updates them. Two distinct parameters, two distinct defaults: `hosts` (which hosts to look for — `build_graph`, `parse_repo_grouped`) defaults to *every registered host*, consistently across both functions; `runtime_hosts` (which host a parser stamps onto a ref it's emitting — `mcp_json.parse`, `claude_skill.parse`, `claude_command_agent.parse_file`/`enumerate_dir`, `claude_plugin.parse`) defaults to `["claude-code"]`, preserving each function's exact pre-existing output when called with no new argument.
- Cross-layer tests (parser + graph + posture + CLI acting together) go in `tests/test_e2e.py`, not module-local test files (CLAUDE.md).
- Subagent "same subagent" matching is by **relative file path** under the agents directory, never by frontmatter `name:` — see ADR-0045 Decision #4 for why. Do not build a name-indexing mechanism.
- Agent Plugins format (`$schema` pointing at `agent-plugins.org`) walks only `skills/` and `mcp.json` — never `agents/`, `commands/`, `hooks/`, `rules/`, even if present, since those aren't part of the portable v1 contract (ADR-0045 Decision #3).
- Cursor endpoint-mode Plugin support never asserts an `enabled`/`active` property — the property must be absent from `extra`, not `False`. See ADR-0045 Decision #7.
- Every posture rule change (or non-change) must be verified against the rule's actual code, not assumed — `tools/posture/rules/mutable_install.py` and `skill_capability.py` were checked during design and need no change; do not re-litigate without re-reading the code.
- Hooks (standalone, `.cursor/hooks.json`), Rules, `AGENTS.md`, and Extensions remain out of scope for this plan — see `docs/specs/multi-host-support.md` for why they're staged separately. Plugin-*bundled* hooks are in scope (Task 13).

---

## Task 1: Companion ADR

**Files:**
- Create: `docs/adrs/0044-multi-host-support.md`
- Modify: `docs/adrs/INDEX.md`

**Interfaces:** None (documentation only).

- [x] **Step 1: Write the ADR**

Use `docs/adrs/TEMPLATE.md`'s structure. Content sourced from `docs/specs/multi-host-support.md`'s design decisions and "Architecture" section (the repo/endpoint split, the registry-driven repo-mode dispatch). The finished ADR (`docs/adrs/0044-multi-host-support.md`) is the canonical record of these decisions — write it directly from the spec rather than a copy embedded here, and keep it in sync with the spec's Architecture section.

- [x] **Step 2: Add the INDEX.md entry**

Insert alphabetically after the ADR-0043 entry in the `## Active` section of `docs/adrs/INDEX.md`:

```markdown
- [ADR-0044 — Host abstraction for multi-host support; Cursor as the first new host](0044-multi-host-support.md): `HostAdapter` frozen dataclass + `tools/hosts.py` registry; host is provenance never identity, carried in exactly one place (`runtime_hosts`, emitted as the `openaca:runtime_hosts` array); repo mode's `--host` defaults to every registered host (machine-state-independent) while endpoint mode's default will depend on `detect()`; two pre-existing posture-rule bugs (`insecure_transport.py`/`mcp_auto_approve.py` guessing host from manifest shape) fixed as part of this work, independent of Cursor.
```

- [x] **Step 3: Commit**

```bash
git add docs/adrs/0044-multi-host-support.md docs/adrs/INDEX.md
git commit -m "docs(adrs): add ADR-0044, host abstraction for multi-host support"
```

---

## Task 2: `HostAdapter` dataclass + Claude Code registration

**Files:**
- Create: `tools/hosts.py`
- Test: `tests/test_hosts.py`

**Interfaces:**
- Produces: `tools.hosts.HostAdapter` (dataclass), `tools.hosts.HOSTS: dict[str, HostAdapter]`, `tools.hosts.detected_hosts() -> list[str]`, `tools.hosts.all_host_ids() -> list[str]`.
- Consumes: `tools.parsers.ParserFn` (existing type alias), `tools.parsers.REGISTRY` (existing, unchanged in this task).

- [x] **Step 1: Write the failing test**

```python
# tests/test_hosts.py
from __future__ import annotations

import os
from pathlib import Path

from tools.hosts import HOSTS, all_host_ids, detected_hosts

def test_claude_code_registered():
    assert "claude-code" in HOSTS
    adapter = HOSTS["claude-code"]
    assert adapter.host_id == "claude-code"
    assert adapter.manifest_registry  # non-empty, reuses existing REGISTRY

def test_all_host_ids_stable_order():
    assert all_host_ids() == ["claude-code"]

def test_detect_claude_code_config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert HOSTS["claude-code"].detect() is False
    (tmp_path / ".claude").mkdir()
    assert HOSTS["claude-code"].detect() is True

def test_detect_claude_code_respects_env_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-claude-dir"
    override.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(override))
    assert HOSTS["claude-code"].detect() is True

def test_detected_hosts_reflects_env(tmp_path, monkeypatch):
    override = tmp_path / "custom-claude-dir"
    override.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(override))
    assert detected_hosts() == ["claude-code"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hosts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.hosts'`

- [x] **Step 3: Write `tools/hosts.py`**

```python
"""Host adapter registry (ADR-0044).

A `HostAdapter` records what varies by host for repo-mode discovery:
which manifest patterns belong to it, which posture rules apply to its
manifests, and (for hosts that support it) how to detect the host's
config root on the local machine. Endpoint-mode composition
(`seed_endpoint`) is a placeholder field here — no adapter populates it
yet; that's a later phase (see docs/specs/multi-host-support.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from tools.parsers import REGISTRY, ParserFn

EndpointSeedFn = Callable[..., None]

@dataclass(frozen=True)
class HostAdapter:
    host_id: str
    detect: Callable[[], bool]
    config_root: Callable[[Optional[Path]], Optional[Path]]
    manifest_registry: list[tuple[str, ParserFn]]
    posture_rule_ids: frozenset[str]
    seed_endpoint: Optional[EndpointSeedFn] = None

def _claude_code_config_root(override: Optional[Path]) -> Optional[Path]:
    if override is not None:
        return override.expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"

def _claude_code_detect() -> bool:
    root = _claude_code_config_root(None)
    return root is not None and root.is_dir()

_CLAUDE_CODE_POSTURE_RULE_IDS = frozenset(
    {
        "openaca-posture-insecure-transport",
        "openaca-posture-mcp-auto-approve",
        "openaca-posture-api-endpoint-override",
        "openaca-posture-mutable-install",
        "openaca-posture-skill-executable-tools",
    }
)

_CLAUDE_CODE = HostAdapter(
    host_id="claude-code",
    detect=_claude_code_detect,
    config_root=_claude_code_config_root,
    manifest_registry=REGISTRY,
    posture_rule_ids=_CLAUDE_CODE_POSTURE_RULE_IDS,
)

HOSTS: dict[str, HostAdapter] = {
    "claude-code": _CLAUDE_CODE,
}

def all_host_ids() -> list[str]:
    """Every registered host, in registration order."""
    return list(HOSTS.keys())

def detected_hosts() -> list[str]:
    """Registered hosts whose `detect()` is true on this machine."""
    return [host_id for host_id, adapter in HOSTS.items() if adapter.detect()]
```

`posture_rule_ids` values are the `RULE_ID` constants from
`tools/posture/rules/*.py` — confirm each string against those files
(`insecure_transport.RULE_ID`, `mcp_auto_approve.RULE_ID`,
`api_endpoint_override.RULE_ID`, `mutable_install.RULE_ID`,
`skill_capability.RULE_ID`) rather than retyping by hand, to avoid a
silent typo mismatch; this task doesn't wire `posture_rule_ids` into the
posture dispatcher yet (that's Task 8), so a mismatch here wouldn't be
caught by this task's own tests.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hosts.py -v`
Expected: PASS (5 tests)

- [x] **Step 5: Commit**

```bash
git add tools/hosts.py tests/test_hosts.py
git commit -m "feat(hosts): add HostAdapter dataclass and Claude Code registration"
```

---

## Task 3: Host-parameterize `mcp_json.py`'s `parse()` entrypoint

**Files:**
- Modify: `tools/parsers/mcp_json.py:782-817`
- Test: `tests/test_parsers/test_mcp_json.py` — already exists in the repo (confirmed), add to it directly.

**Interfaces:**
- Produces: `parse(path: Path, runtime_hosts: list[str] | None = None) -> list[ComponentRef]` — `runtime_hosts` defaults to `["claude-code"]`, preserving every existing call site's behavior unchanged.

- [x] **Step 1: Write the failing test**

```python
# tests/test_parsers/test_mcp_json.py — add to the existing file
def test_parse_default_runtime_hosts_is_claude_code(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text('{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}')
    refs = parse(path)
    assert refs[0].extra["runtime_hosts"] == ["claude-code"]

def test_parse_accepts_explicit_runtime_hosts(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text('{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}')
    refs = parse(path, runtime_hosts=["cursor"])
    assert refs[0].extra["runtime_hosts"] == ["cursor"]
```

(Use whichever name the existing file already imports `mcp_json.parse`
under — `parse`, or an aliased name — match it rather than introducing a
second alias in the same file.)

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parsers/test_mcp_json.py -k runtime_hosts -v`
Expected: FAIL with `TypeError: parse() got an unexpected keyword argument 'runtime_hosts'`

- [x] **Step 3: Modify `parse()`**

In `tools/parsers/mcp_json.py`, change:

```python
def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("mcpServers"), dict):
        return parse_mcp_servers(
            data["mcpServers"],
            source_manifest=str(path),
            locator_prefix="$.mcpServers",
            runtime_hosts=["claude-code"],
        )
```

to:

```python
def parse(path: Path, runtime_hosts: list[str] | None = None) -> list[ComponentRef]:
    if runtime_hosts is None:
        runtime_hosts = ["claude-code"]
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("mcpServers"), dict):
        return parse_mcp_servers(
            data["mcpServers"],
            source_manifest=str(path),
            locator_prefix="$.mcpServers",
            runtime_hosts=runtime_hosts,
        )
```

The two remaining `parse_mcp_servers(...)` calls in the same function (for the `servers` VS Code shape and the flat-shape fallback) currently pass `runtime_hosts=[]` unconditionally — leave those two untouched. They're deliberately host-unknown shapes (VS Code's `servers` key, an unwrapped flat map), not the Claude/Cursor `mcpServers` shape this task host-tags; changing them is out of this task's scope.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_parsers/test_mcp_json.py -k runtime_hosts -v`
Expected: PASS (2 tests)

- [x] **Step 5: Run the full existing parser/e2e suite to confirm no regression**

Run: `python -m pytest tests/test_parsers/test_mcp_json.py tests/test_e2e.py tests/test_graph_build.py -v`
Expected: PASS, unchanged count from before this task (the default-`None`-means-`["claude-code"]` behavior is byte-identical to before)

- [x] **Step 6: Commit**

```bash
git add tools/parsers/mcp_json.py tests/test_parsers/test_mcp_json.py
git commit -m "feat(parsers): host-parameterize mcp_json.parse()"
```

---

## Task 4: Host-parameterize `claude_skill.py`'s `parse()`

**Files:**
- Modify: `tools/parsers/claude_skill.py:31-66`
- Test: `tests/test_parsers/test_claude_skill.py` — already exists in the repo (confirmed), add to it directly.

**Interfaces:**
- Consumes: none new.
- Produces: `parse(skill_md_path: Path, runtime_hosts: list[str] | None = None) -> list[ComponentRef]` — `runtime_hosts` defaults to `["claude-code"]`. **Behavior change, intentional and tested**: today `parse()` sets no `runtime_hosts` key in `extra` at all; after this task it always does.

- [x] **Step 1: Write the failing test**

```python
# tests/test_parsers/test_claude_skill.py — add to the existing file

def _write_skill(tmp_path, name="deploy"):
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"---\nname: {name}\ndescription: d\n---\nbody\n")
    return skill_md

def test_parse_default_runtime_hosts_is_claude_code(tmp_path):
    refs = parse(_write_skill(tmp_path))
    assert refs[0].extra["runtime_hosts"] == ["claude-code"]

def test_parse_accepts_explicit_runtime_hosts(tmp_path):
    refs = parse(_write_skill(tmp_path), runtime_hosts=["cursor"])
    assert refs[0].extra["runtime_hosts"] == ["cursor"]

def test_parse_accepts_multiple_runtime_hosts(tmp_path):
    refs = parse(_write_skill(tmp_path), runtime_hosts=["cursor", "codex"])
    assert refs[0].extra["runtime_hosts"] == ["cursor", "codex"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parsers/test_claude_skill.py -v`
Expected: FAIL — `refs[0].extra["runtime_hosts"]` raises `KeyError` (key doesn't exist today), and the `runtime_hosts=` call fails with `TypeError`. (If `_write_skill` collides with a helper already defined in the file, rename the new one — check the existing file's content first.)

- [x] **Step 3: Modify `parse()`**

In `tools/parsers/claude_skill.py`, change the signature and the `extra` construction:

```python
def parse(skill_md_path: Path, runtime_hosts: list[str] | None = None) -> list[ComponentRef]:
    if runtime_hosts is None:
        runtime_hosts = ["claude-code"]
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    frontmatter = _extract_frontmatter(text)
    if frontmatter is None:
        return []
    raw_name = frontmatter.get("name")
    if isinstance(raw_name, str) and raw_name:
        name = raw_name
    else:
        name = skill_md_path.parent.name
    if not name:
        return []
    version = _extract_version(frontmatter)
    identity = f"skill/{name}"
    if version:
        identity = f"{identity}@{version}"
    extra: dict[str, object] = {
        "component_type": "skill",
        "runtime_hosts": list(runtime_hosts),
    }
    coordinate = _skill_tree_coordinate(skill_md_path.parent)
    if coordinate is not None:
        extra["artifact_coordinates"] = [coordinate]
    return [
        ComponentRef(
            name=name,
            version=version,
            component_identity=identity,
            source_manifest=str(skill_md_path),
            source_locator="$.frontmatter",
            extra=extra,
        )
    ]
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_parsers/test_claude_skill.py -v`
Expected: PASS (3 tests)

- [x] **Step 5: Run the full existing graph-build suite to check for downstream fallout**

Run: `python -m pytest tests/test_graph_build.py tests/test_e2e.py -v`
Expected: PASS. This is the step that actually validates the "intentional behavior change" claim — Claude Code skill refs built via `build_graph` now carry `runtime_hosts=["claude-code"]` in `extra` where they previously carried nothing. Check specifically whether any existing assertion inspects a skill ref's `extra` dict and would break on the new key (e.g. an exact-dict-equality assertion rather than a subset check). If one does, that test's assertion is the thing to update — not this task's new behavior — since the new key is additive and correct per ADR-0044.

- [x] **Step 6: Commit**

```bash
git add tools/parsers/claude_skill.py tests/test_parsers/test_claude_skill.py
git commit -m "feat(parsers): host-parameterize claude_skill.parse(), stamp runtime_hosts"
```

---

## Task 5: Cursor `HostAdapter` registration

**Files:**
- Modify: `tools/hosts.py`
- Modify: `tests/test_hosts.py`

**Interfaces:**
- Consumes: `HostAdapter` from Task 2.
- Produces: `HOSTS["cursor"]`, with `manifest_registry` still empty (populated in Task 6) and `posture_rule_ids` set now (consumed in Task 8).

- [x] **Step 1: Write the failing test**

```python
def test_cursor_registered():
    assert "cursor" in HOSTS
    adapter = HOSTS["cursor"]
    assert adapter.host_id == "cursor"
    # api_endpoint_override is Claude-schema-specific; Cursor must not run it.
    assert "openaca-posture-api-endpoint-override" not in adapter.posture_rule_ids
    assert "openaca-posture-insecure-transport" in adapter.posture_rule_ids
    # mcp_auto_approve keys on a manifest-level autoApprove field that's
    # specific to Claude Code's mcp.json — verified against Cursor's own
    # MCP docs (cursor.com/docs/context/mcp): approval there is Run-Modes/
    # UI state with no documented per-server manifest equivalent. Cursor
    # must not run it either (see Task 8's owning_host-gated fix in
    # mcp_auto_approve.py itself, which is the actual enforcement point —
    # this membership is kept accurate for readers of the adapter, not
    # load-bearing on its own).
    assert "openaca-posture-mcp-auto-approve" not in adapter.posture_rule_ids

def test_all_host_ids_includes_cursor_after_claude_code():
    assert all_host_ids() == ["claude-code", "cursor"]

def test_detect_cursor_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert HOSTS["cursor"].detect() is False
    (tmp_path / ".cursor").mkdir()
    assert HOSTS["cursor"].detect() is True
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hosts.py -v`
Expected: FAIL — `"cursor" not in HOSTS`

- [x] **Step 3: Add the Cursor adapter to `tools/hosts.py`**

```python
def _cursor_config_root(override: Optional[Path]) -> Optional[Path]:
    # Cursor documents no whole-root relocation variable: CURSOR_CONFIG_DIR
    # scopes only the CLI's cli-config.json (cursor.com/docs/cli/reference/
    # configuration), so honoring it here would misread its meaning — only
    # the explicit override param and the default location are supported.
    if override is not None:
        return override.expanduser()
    return Path.home() / ".cursor"

def _cursor_detect() -> bool:
    root = _cursor_config_root(None)
    return root is not None and root.is_dir()

_CURSOR_POSTURE_RULE_IDS = frozenset(
    {
        "openaca-posture-insecure-transport",
        # No "openaca-posture-mcp-auto-approve": verified against Cursor's
        # own MCP docs — approval/auto-run is Run-Modes/UI state there,
        # with no documented per-server manifest field. Asserting an
        # "auto-approval enabled" finding against a Cursor manifest would
        # claim an active posture Cursor's own config surface doesn't
        # support.
        "openaca-posture-mutable-install",
        "openaca-posture-skill-executable-tools",
    }
)

_CURSOR = HostAdapter(
    host_id="cursor",
    detect=_cursor_detect,
    config_root=_cursor_config_root,
    manifest_registry=[],  # populated in Task 6
    posture_rule_ids=_CURSOR_POSTURE_RULE_IDS,
)

HOSTS: dict[str, HostAdapter] = {
    "claude-code": _CLAUDE_CODE,
    "cursor": _CURSOR,
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hosts.py -v`
Expected: PASS (8 tests)

- [x] **Step 5: Commit**

```bash
git add tools/hosts.py tests/test_hosts.py
git commit -m "feat(hosts): register Cursor host adapter (posture rules only; manifest registry pending)"
```

---

## Task 6: Cursor MCP support — host-tagged registry and graph dispatch, landed atomically

**This task absorbs the registry split and the graph dispatch in one
commit — the registry split alone is not a safe standalone commit.**
`tools/scan.py`'s `repo`
command already calls `parse_repo_grouped()` for its manifest count
*and* `build_graph()` for its actual components/findings in the same
invocation (`tools/scan.py:749` and the graph-build call a few lines
earlier), and combines both numbers in one summary line:
`f"scanned {n_found} manifest(s), {len(refs)} component(s)..."`
(`tools/scan.py:817`, `913`). If the registry split landed alone,
`parse_repo_grouped`'s new default (this task's own "every registered
host" policy) would take effect on the *existing*, already-wired call
site immediately — before the graph dispatch below exists to back it
up. Scanning a real repo with a `.cursor/mcp.json` file
at that commit would print something like "scanned 2 manifest(s), 1
component(s):" with no parse-failure note to explain the gap, since
the file did parse successfully — just not into the graph yet. That's
not "Cursor support doesn't exist yet" (the scaffold-then-capability
pattern accepted elsewhere in this plan); it's the tool's own summary
line visibly contradicting itself. The registry split and the graph
dispatch land in one commit instead.

**Files:**
- Create: `tools/host_paths.py`
- Modify: `tools/parsers/__init__.py`
- Modify: `tools/hosts.py` (fill in Cursor's `manifest_registry`)
- Test: `tests/test_parsers/test_registry.py` (existing file — add to it, don't create a new one; confirmed present in the repo)

**Interfaces:**
- Consumes: `mcp_json.parse` (Task 3), `claude_skill.parse` (Task 4).
- Produces: `tools.host_paths.owning_host(path) -> str`. `parse_repo_grouped(root, include_gitignored=False, hosts=None)`, `parse_repo(root, include_gitignored=False, hosts=None)` — `hosts: list[str] | None`, `None` means "every registered host" (this is the layer where the "repo mode defaults to every host" policy actually lives). `HOST_AGNOSTIC_REGISTRY: list[tuple[str, ParserFn]]` (renamed from the old flat `REGISTRY` — software-dependency/lockfile entries, no host concept). `CLAUDE_CODE_MANIFEST_REGISTRY` and `CURSOR_MANIFEST_REGISTRY` — the host-tagged entries, importable by `tools/hosts.py`. `registry_pattern_matches(path, root, pattern) -> bool` (public, cross-module — consumed by `tools/graph_build.py`). `resolve_host_selection(hosts) -> list[str]` (public — returns `hosts` resolved to a concrete, duplicate-free, order-preserving list of known host IDs; raises `ValueError` if `hosts` names an unregistered host ID, or if two distinct selected hosts claim the identical registry pattern; consumed by both `_active_registry` here and `build_graph`'s repo-mode entry in `tools/graph_build.py`).

- [x] **Step 1: Write the failing test**

```python
# tests/test_parsers/test_registry.py — add to the existing file
from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers import parse_repo_grouped

def test_cursor_mcp_json_repo_scan(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["cursor"])
    assert n_found == 1
    refs = grouped[0][1]
    assert refs[0].extra["runtime_hosts"] == ["cursor"]

def test_cursor_mcp_json_excluded_when_host_not_selected(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code"])
    assert n_found == 0
    assert grouped == []

def test_default_hosts_is_every_registered_host(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path)  # hosts omitted
    assert n_found == 1  # Cursor's manifest is found without being asked for explicitly

def test_lockfile_manifests_are_host_agnostic(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x", "dependencies": {"lodash": "4.17.20"}}')
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["cursor"])
    # package.json has no host concept — found regardless of which hosts are selected.
    assert n_found == 1

def test_cursor_mcp_json_not_double_counted_when_both_hosts_selected(tmp_path):
    # Regression guard: .cursor/mcp.json shares a basename with Claude's
    # bare mcp.json pattern. Without the owning_host exclusion (Step 3),
    # this file matches both registry entries and n_found becomes 2 for
    # one file, with two conflicting refs (one mistagged claude-code).
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert len(grouped) == 1
    refs = grouped[0][1]
    assert refs[0].extra["runtime_hosts"] == ["cursor"]

def test_claude_bare_mcp_json_still_matches_when_cursor_also_selected(tmp_path):
    # The exclusion must be narrow: a plain (non-.cursor) mcp.json must
    # still match Claude's pattern even when Cursor is also selected.
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert grouped[0][1][0].extra["runtime_hosts"] == ["claude-code"]

def test_synthetic_host_registered_in_hosts_is_discoverable_without_touching_registry(
    tmp_path, monkeypatch
):
    # Proves that registering a HostAdapter is *sufficient* for repo-mode
    # accounting to pick it up, with no edit to this module. This test
    # can pass now, for exactly this half — registering the adapter
    # alone is enough here, because _active_registry reads
    # HOSTS[host_id].manifest_registry directly. It does NOT prove the
    # same for the graph (tools/graph_build.py's descend()), which still
    # needs a hand-written branch per host regardless of what's
    # registered in HOSTS — that half stays the open question.
    from tools.hosts import HOSTS, HostAdapter

    def _synthetic_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="synthetic-widget",
                extra={"component_type": "mcp_server", "runtime_hosts": ["synthetic-host"]},
            )
        ]

    synthetic_adapter = HostAdapter(
        host_id="synthetic-host",
        detect=lambda: False,
        config_root=lambda override: None,
        manifest_registry=[("synthetic.json", _synthetic_parse)],
        posture_rule_ids=frozenset(),
    )
    monkeypatch.setitem(HOSTS, "synthetic-host", synthetic_adapter)

    (tmp_path / "synthetic.json").write_text("{}")
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["synthetic-host"])
    assert n_found == 1
    assert grouped[0][1][0].name == "synthetic-widget"

def test_cursor_cache_mcp_json_is_claude_owned_not_invisible(tmp_path):
    # Boundary case: .cursor/cache/mcp.json is nested
    # UNDER .cursor/ but is not the exact .cursor/mcp.json shape Cursor's
    # pattern matches. It must fall back to Claude's catch-all (found,
    # tagged claude-code) — not silently invisible to both patterns,
    # which is what a loose "under .cursor/" classifier would produce.
    nested = tmp_path / ".cursor" / "cache"
    nested.mkdir(parents=True)
    (nested / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert grouped[0][1][0].extra["runtime_hosts"] == ["claude-code"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parsers/test_registry.py -v`
Expected: FAIL — `parse_repo_grouped() got an unexpected keyword argument 'hosts'`

- [x] **Step 3: Add the shared host-path classifier**

Cursor's `.cursor/mcp.json` shares a basename (`mcp.json`) with Claude
Code's own bare-filename pattern. Without an explicit exclusion, both
patterns match the same file: the loop in `parse_repo_grouped` doesn't
stop at the first match, so a Cursor MCP file would be parsed twice —
once mistagged `runtime_hosts=["claude-code"]`, once correctly tagged
`["cursor"]` — inflating `n_found` and emitting conflicting refs for one
file. `tools/graph_build.py`'s graph dispatch (later in this same task)
and `tools/scan.py`'s posture manifest collection (Task 8) hit the
identical collision independently,
so this classifier is written once and imported by all three, rather
than reimplemented three times with a risk of drifting out of sync.

It cannot live in `tools/hosts.py`: that module imports from
`tools/parsers/__init__.py` (for `CLAUDE_CODE_MANIFEST_REGISTRY`/
`CURSOR_MANIFEST_REGISTRY`, wired in Step 4 below), so
`tools/parsers/__init__.py` importing back from `tools/hosts.py` would
be circular. It's a new, dependency-free leaf module instead:

**This classifier has to match the exact shape the graph and registry
already recognize, not a loose "is `.cursor` an ancestor" heuristic.**
`collect_mcp_manifests` (Task 8) finds any of `mcp.json`, `.mcp.json`,
or `claude_desktop_config.json` anywhere in the tree via `rglob`, at
any depth. `_is_cursor_mcp_json` and the registry's
`.cursor/mcp.json` pattern (both this task) only ever recognize the exact
shape `.cursor/mcp.json` — parent directory literally named `.cursor`,
filename literally `mcp.json`. A loose classifier that returns
`"cursor"` for anything merely nested under a `.cursor/` directory —
`.cursor/.mcp.json`, `.cursor/cache/mcp.json` — would make Task 8 keep
and posture-scan files as "Cursor components" that the graph never
recognized as components at all, since neither the specific Cursor
branch nor the generic Claude branch (guarded by `owning_host(path) ==
"claude-code"`) would match them. That's a graph/posture divergence in
the same shape as the bug this classifier exists to prevent, just
introduced one level up. The classifier must check the exact same two
path components `_is_cursor_mcp_json` checks, not merely "somewhere
under `.cursor`":

```python
# tools/host_paths.py
"""Disambiguates manifest filenames that collide across hosts (ADR-0044).

Cursor's `mcp.json` and Claude Code's `mcp.json` share a basename; only
directory context tells them apart. This module is the one place that
decides ownership for such filenames, imported by the parser registry
(tools/parsers/__init__.py), graph construction
(tools/graph_build.py), and posture manifest collection (tools/scan.py)
alike — those three call sites independently discover the same files
and must agree on which host owns each one.
"""

from __future__ import annotations

from pathlib import Path

def owning_host(path: Path) -> str:
    """Which registered host's directory convention `path` belongs to.

    Path-based, not content-based: manifest content shape can't
    disambiguate Claude Code's `mcp.json` from Cursor's — both use the
    same `mcpServers` JSON shape. Matches the *exact* shape
    `_is_cursor_mcp_json` recognizes — parent directory named `.cursor`,
    filename exactly `mcp.json` — not merely "somewhere under a
    `.cursor/` directory": `.cursor/.mcp.json` and
    `.cursor/cache/mcp.json` are neither the real Cursor convention nor
    invisible to Claude's catch-all, so both fall back to Claude Code's
    original unqualified convention (bare `mcp.json`/`.mcp.json`
    anywhere in the tree, predating any other host) — the same
    treatment they'd get if Cursor didn't exist at all.
    """
    if len(path.parts) >= 2 and path.parts[-2:] == (".cursor", "mcp.json"):
        return "cursor"
    return "claude-code"
```

- [x] **Step 4: Write the failing test for the classifier**

```python
# tests/test_parsers/test_registry.py — add near other registry tests
from tools.host_paths import owning_host

def test_owning_host_cursor_root_mcp_json():
    assert owning_host(Path("repo/.cursor/mcp.json")) == "cursor"

def test_owning_host_cursor_nested_mcp_json():
    # Nested project (packages/frontend/.cursor/mcp.json) — same
    # depth-independent shape _is_cursor_mcp_json already recognizes.
    assert owning_host(Path("repo/packages/frontend/.cursor/mcp.json")) == "cursor"

def test_owning_host_claude_bare_mcp_json():
    assert owning_host(Path("repo/.mcp.json")) == "claude-code"
    assert owning_host(Path("repo/plugins/foo/mcp.json")) == "claude-code"

def test_owning_host_cursor_dotfile_variant_not_cursor():
    # Boundary case: .cursor/.mcp.json is NOT the
    # real Cursor convention (filename has the leading dot; Cursor's
    # own docs and this plan's registry/graph dispatch only recognize
    # bare "mcp.json"). A loose ".cursor is an ancestor" check would
    # wrongly call this "cursor" even though neither the graph's
    # Cursor branch nor the registry's Cursor pattern would ever
    # match this exact filename — falls back to claude-code, the same
    # answer it would get if Cursor didn't exist at all.
    assert owning_host(Path("repo/.cursor/.mcp.json")) == "claude-code"

def test_owning_host_cursor_subdirectory_not_cursor():
    # .cursor/cache/mcp.json: nested under .cursor/ but not directly
    # in it — not the real convention either. Same reasoning as above.
    assert owning_host(Path("repo/.cursor/cache/mcp.json")) == "claude-code"
```

Run: `python -m pytest tests/test_parsers/test_registry.py -k owning_host -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.host_paths'`

Create the file per Step 3, then re-run — Expected: PASS (6 tests).

- [x] **Step 5: Split `REGISTRY` and add the `hosts` parameter**

In `tools/parsers/__init__.py`, replace the flat `REGISTRY` with a host-agnostic registry plus a per-host registry dict, and thread `hosts` through the walk:

```python
import functools

from tools.parsers import (
    bun_lock,
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

# Software-dependency / lockfile manifests: no host concept, always active
# regardless of which hosts are selected.
HOST_AGNOSTIC_REGISTRY: list[tuple[str, ParserFn]] = [
    ("package.json", package_json.parse),
    ("pyproject.toml", pyproject_toml.parse),
    ("package-lock.json", package_lock_json.parse),
    ("uv.lock", uv_lock.parse),
    ("bun.lock", bun_lock.parse),
]

# Claude Code's agent-component surfaces (ADR-0044). Unchanged content from
# the pre-split REGISTRY; only the name and grouping changed.
CLAUDE_CODE_MANIFEST_REGISTRY: list[tuple[str, ParserFn]] = [
    ("mcp.json", mcp_json.parse),
    (".mcp.json", mcp_json.parse),
    ("claude_desktop_config.json", mcp_json.parse),
    (".claude-plugin/plugin.json", claude_plugin.parse),
    (".claude/settings.json", claude_settings.parse),
    ("**/.claude/skills/*/SKILL.md", claude_skill.parse),
    ("**/.claude/commands/**/*.md", _parse_repo_command),
    ("**/.claude/agents/**/*.md", _parse_repo_agent),
]

# Cursor's repo-mode MCP surface (ADR-0044). Parsers are pre-bound via
# functools.partial so each still matches the single-Path ParserFn
# signature; the registry dispatch loop never needs to know
# host-tagging happened. Skills entries are added in Task 7, landed
# together with the graph-side dispatch that makes them real: a registry
# entry with no matching graph dispatch would make parse_repo_grouped's
# manifest count include Cursor skill files before anything could turn
# them into real components, reproducing the exact "N manifests, 0
# components" self-contradiction this task's own opening paragraph says
# is unacceptable for MCP — just for Skills instead. Same fix applies:
# land the registry entry and the graph dispatch that makes it real in
# the same task, not two.
CURSOR_MANIFEST_REGISTRY: list[tuple[str, ParserFn]] = [
    (".cursor/mcp.json", functools.partial(mcp_json.parse, runtime_hosts=["cursor"])),
]
```

**Derive the active registry from `HOSTS` itself, not a second
host-keyed dict.** `CLAUDE_CODE_MANIFEST_REGISTRY`/`CURSOR_MANIFEST_REGISTRY`
hold the real, per-host values; the dispatch function must read
`HOSTS[host_id].manifest_registry` directly rather than building a
*second* mapping (e.g. a module-level `_MANIFEST_REGISTRIES` dict)
shaped like it from the same values — that would mean the registry
dispatch never actually consulted the adapter that's supposed to be the
seam, just a parallel dict shaped like it. `tools/hosts.py` imports from this module
at its own module level (`CLAUDE_CODE_MANIFEST_REGISTRY`/
`CURSOR_MANIFEST_REGISTRY`, Step 6 below), so a module-level `from
tools.hosts import HOSTS` here would be circular — but `_active_registry`
is a function body, not module-level code, and by the time anything
*calls* it, both modules have already finished importing. A deferred
import inside the function avoids the cycle with no behavior change and
closes the gap for real:

```python
def resolve_host_selection(hosts: list[str] | None) -> list[str]:
    """Resolve `hosts` to a concrete, order-preserving, duplicate-free
    list of *known* host IDs, and raise if two of them claim the
    identical registry pattern string.

    Combines three concerns handled inconsistently before this fix:
    unknown-ID rejection, deduplication,
    and collision rejection. **Unknown-ID rejection:**
    `tools/scan.py`'s CLI already rejects an unrecognized `--host` value
    with a clear `click.BadParameter` before calling either public
    function below, but a direct caller bypassing the CLI — a test, or a
    future non-CLI consumer — passing `hosts=["typo"]` previously got no
    error at all: `_active_registry`/graph dispatch simply found no
    adapter for `"typo"` and silently contributed nothing for it, while
    `HOST_AGNOSTIC_REGISTRY`'s dependency-manifest parsers still ran
    normally — producing a scan that *looks* like a legitimate, complete
    result (real BOM entries from `package.json` etc.) rather than an
    obviously-wrong one, with no signal that the requested host was
    never recognized. Rejected explicitly now, with the same
    unknown-host message shape the CLI already uses, so a direct caller
    gets the same safety property `_resolve_hosts` (`tools/scan.py`)
    already gives CLI users. **Deduplication:** `tools/scan.py`'s
    CLI already dedupes repeated/comma-separated `--host` values before
    calling either public function below, but `_active_registry` and
    `build_graph` are themselves public (see this task's Interfaces
    block) and a direct caller bypassing the CLI passing
    `hosts=["cursor", "cursor"]` would previously make `_active_registry`
    extend its registry with Cursor's `manifest_registry` twice,
    double-counting `n_found` and producing duplicate refs for the same
    file, while graph dispatch (`_mcp_parser_for_path`/
    `_skill_parser_for_path`, Tasks 6/7) stayed correct — its `for
    host_id in hosts: ... return parser` loop is idempotent for a
    repeated ID, so it silently produced one node either way. That's the
    same "accounting over-counts, graph doesn't" divergence class the
    pattern-collision check below exists to prevent, from a different
    cause — closed by deduplicating once, at the shared boundary, rather
    than trusting every caller to do it themselves. **Collision
    rejection:** a bare (non-host-scoped) pattern like
    "mcp.json" carries no path information distinguishing which host
    owns a matching file. Reusing one verbatim across two *distinct*,
    simultaneously selected hosts is genuinely ambiguous, not just
    under-specified — the same double-count-vs-first-match divergence
    described above, but between two different hosts' entries rather
    than one host's entry counted twice. Cursor's own pattern
    (`.cursor/mcp.json`) never collides with Claude's bare filenames
    precisely because it's host-scoped in the path itself — a future
    host wanting to reuse a bare, already-allowlisted pattern
    *alongside* its existing owner must do the same (adopt a
    host-scoped pattern shape) rather than share the identical string.
    Called from both `_active_registry` and `build_graph`'s repo-mode
    entry point (same reasoning as sharing `registry_pattern_matches`:
    one implementation, so the two mechanisms can't silently disagree
    about any of the three concerns, only fail or normalize identically).
    """
    from tools.hosts import HOSTS

    selected = list(dict.fromkeys(hosts if hosts is not None else HOSTS.keys()))
    if hosts is not None:
        unknown = [host_id for host_id in selected if host_id not in HOSTS]
        if unknown:
            known = ", ".join(sorted(HOSTS))
            raise ValueError(
                f"unknown host(s) {unknown!r}; known hosts: {known}"
            )
    owners: dict[str, str] = {}
    for host_id in selected:
        adapter = HOSTS.get(host_id)
        if adapter is None:
            continue
        for pattern, _parser in adapter.manifest_registry:
            if pattern in owners and owners[pattern] != host_id:
                raise ValueError(
                    f"registry pattern {pattern!r} is claimed by both "
                    f"{owners[pattern]!r} and {host_id!r} — reusing a "
                    "pattern verbatim across two simultaneously selected "
                    "hosts is ambiguous. Give the new host a distinct, "
                    "host-scoped pattern (e.g. the '.newhost/mcp.json' "
                    "shape Cursor already uses), or move the parser to "
                    "HOST_AGNOSTIC_REGISTRY if it's genuinely meant to be "
                    "shared across hosts."
                )
            owners[pattern] = host_id
    return selected

def _active_registry(hosts: list[str] | None) -> list[tuple[str, ParserFn]]:
    from tools.hosts import HOSTS  # deferred: tools.hosts imports from this module

    selected = resolve_host_selection(hosts)
    registry = list(HOST_AGNOSTIC_REGISTRY)
    for host_id in selected:
        adapter = HOSTS.get(host_id)
        if adapter is not None:
            registry.extend(adapter.manifest_registry)
    return registry
```

This closes half of the "`HostAdapter` doesn't drive discovery" gap —
the registry/accounting side now genuinely reads
`HostAdapter.manifest_registry`, not a lookalike.

**Close the other half too, in this same task, scoped to this plan's two
surfaces.** A full generalization of registry-driven graph placement to
every surface would be bigger and riskier than this gap demands — but
the still-open half (graph placement, via `tools/scan.py`'s own "single
source of truth" comment) is specifically the one that produces actual
scan output, not a secondary concern, so leaving it unfixed while
shipping the registry/accounting half alone wouldn't be a responsible
smaller fix — it would ship exactly the self-contradictory-output bug
this task's opening paragraph describes. `_add_repo_standalone_components` (below) and
`_add_project_skills` (Task 7) are rewritten to walk
`HostAdapter.manifest_registry` directly, the same way
`_active_registry` now does — reusing `registry_pattern_matches`
(renamed from `_registry_pattern_matches`, below — now a cross-module
function, not a `tools/parsers/__init__.py`-private one) so graph
placement and manifest accounting are provably driven by the same
matching logic, not two implementations that happen to agree today.

This is deliberately **not** the fully general version that would be
needed for placement-varying surfaces (plugins, nested contexts):
`manifest_registry`'s
`list[tuple[str, ParserFn]]` shape doesn't carry placement information,
only "which parser to call." MCP and Skills both happen to place the
same way regardless of host (direct child of `target`, with Skills
additionally descending into its own directory for dependency
manifests) — that uniformity is what makes a scoped version of this fix
safe now. A small, explicit allowlist
(`_MCP_REGISTRY_PATTERNS`/`_SKILL_REGISTRY_PATTERNS`, below) marks which
registry *patterns* place this way; a future host reusing an
already-allowlisted pattern shape needs zero `graph_build.py` changes,
only its `HostAdapter` registration. A host inventing a genuinely new
pattern shape still needs that one line added to the relevant allowlist
— smaller and more centralized than the old "write a whole new
hardcoded dispatch branch" requirement, but not fully pattern-agnostic.
Full pattern-agnosticism, and placement-varying surfaces like plugins,
remain the real future work — now with a much narrower, precisely named
gap instead of "the graph doesn't consult the adapter at all."

Update the pattern-matching function to recognize the two new nested
patterns (`.cursor/mcp.json`, and the `.cursor/skills` / `.agents/skills`
variants of the existing skill-matching branch), and **drop its leading
underscore** — the graph-placement fix above makes `tools/graph_build.py`
a second, real caller of this exact function, so it's no longer
`tools/parsers/__init__.py`-private. Extend the existing hardcoded
special-case set and generalize the skill-match helper to take the
config-dir name as a parameter:

`.cursor/mcp.json` and Claude's bare `mcp.json`/`.mcp.json` patterns
would otherwise both match the same file (Step 3's collision). Fix: the
bare-basename branch defers to `owning_host` for the two filenames that
collide across hosts, so a `.cursor`-nested `mcp.json` stops matching
Claude's pattern at all — the two registry entries become mutually
exclusive by construction. Loop-ordering alone doesn't work here, since
the dispatch loop checks every pattern, not just the first match:

```python
from tools.host_paths import owning_host

_HOST_AMBIGUOUS_BASENAMES = frozenset({"mcp.json", ".mcp.json"})

def registry_pattern_matches(path: Path, root: Path, pattern: str) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path

    if "/" not in pattern and "*" not in pattern:
        if pattern in _HOST_AMBIGUOUS_BASENAMES and owning_host(rel) != "claude-code":
            return False
        return rel.name == pattern

    rel_parts = rel.parts
    rel_posix = rel.as_posix()
    if pattern in {
        ".claude-plugin/plugin.json",
        ".claude/settings.json",
        ".cursor/mcp.json",
    }:
        return rel_posix == pattern or rel_posix.endswith(f"/{pattern}")

    skill_dir_name = _skill_pattern_config_dir(pattern)
    if skill_dir_name is not None:
        return _skill_path_matches(rel_parts, skill_dir_name)

    if pattern in {"**/.claude/commands/**/*.md", "**/.claude/agents/**/*.md"}:
        kind = "commands" if "commands" in pattern else "agents"
        return rel.suffix == ".md" and any(
            rel_parts[i] == ".claude" and i + 2 < len(rel_parts) and rel_parts[i + 1] == kind
            for i in range(len(rel_parts) - 2)
        )

    return rel.match(pattern)

_SKILL_PATTERN_CONFIG_DIRS = {
    "**/.claude/skills/*/SKILL.md": ".claude",
    "**/.cursor/skills/*/SKILL.md": ".cursor",
    "**/.agents/skills/*/SKILL.md": ".agents",
}

def _skill_pattern_config_dir(pattern: str) -> str | None:
    return _SKILL_PATTERN_CONFIG_DIRS.get(pattern)

def _skill_path_matches(rel_parts: tuple[str, ...], config_dir: str) -> bool:
    if len(rel_parts) < 4 or rel_parts[-1] != "SKILL.md":
        return False
    return any(
        rel_parts[i] == config_dir
        and i + 3 < len(rel_parts)
        and rel_parts[i + 1] == "skills"
        and i + 3 == len(rel_parts) - 1
        for i in range(len(rel_parts) - 3)
    )
```

Finally, thread `hosts` through the two public functions:

```python
def parse_repo_grouped(
    root: Path,
    include_gitignored: bool = False,
    hosts: list[str] | None = None,
) -> tuple[list[tuple[Path, list[ComponentRef]]], int]:
    spec = None if include_gitignored else load_gitignore_spec(root)
    grouped: list[tuple[Path, list[ComponentRef]]] = []
    n_found = 0
    registry = _active_registry(hosts)
    for path in iter_unignored_files(root, spec):
        for pattern, parser in registry:
            if not registry_pattern_matches(path, root, pattern):
                continue
            n_found += 1
            try:
                refs = parser(path)
                refs = _filter_secondary_refs(refs, path, root, spec)
                grouped.append((path, refs))
            except Exception:
                continue
    return grouped, n_found

def parse_repo(
    root: Path, include_gitignored: bool = False, hosts: list[str] | None = None
) -> list[ComponentRef]:
    grouped, _ = parse_repo_grouped(root, include_gitignored=include_gitignored, hosts=hosts)
    return flatten_grouped(grouped)
```

Remove the old flat `REGISTRY` name entirely — grep for other importers
before deleting:

```bash
grep -rn "from tools.parsers import.*REGISTRY\|parsers\.REGISTRY" tools/ tests/
```

Every hit needs updating to import `CLAUDE_CODE_MANIFEST_REGISTRY` (or
`HOST_AGNOSTIC_REGISTRY`, depending on what it actually needs) instead.

- [x] **Step 6: Fill in Cursor's `manifest_registry` in `tools/hosts.py`**

```python
from tools.parsers import CLAUDE_CODE_MANIFEST_REGISTRY, CURSOR_MANIFEST_REGISTRY

_CLAUDE_CODE = HostAdapter(
    ...,
    manifest_registry=CLAUDE_CODE_MANIFEST_REGISTRY,
    ...,
)

_CURSOR = HostAdapter(
    ...,
    manifest_registry=CURSOR_MANIFEST_REGISTRY,
    ...,
)
```

(Also delete the now-stale `from tools.parsers import REGISTRY` import at
the top of `tools/hosts.py` from Task 2, replacing it with the above.)

- [x] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_parsers/test_registry.py tests/test_hosts.py -v`
Expected: PASS

- [x] **Step 8: Run the full existing suite to confirm the split preserved Claude-only behavior so far**

Run: `python -m pytest tests/ -v`
Expected: PASS, no regressions. Pay particular attention to any test that imported the old flat `REGISTRY` name directly — those need updating per Step 5's grep, and this is where a missed one surfaces as an `ImportError`. **Do not commit yet** — the registry split by itself is not a safe standalone commit (see this task's opening note); continue directly into graph dispatch below in the same working tree.

**Files (continuing this task, not a new one):**
- Modify: `tools/graph_build.py` (`_add_repo_standalone_components`, `descend`, `build_graph`)
- Test: `tests/test_graph_build.py`

**Interfaces (graph-dispatch half):**
- Consumes: `mcp_json.parse` (Task 3), `tools.hosts.all_host_ids` (Task 2/5).
- Produces: `build_graph(target, mode, project_root=None, *, include_gitignored=False, warnings=None, hosts=None)` — new `hosts: list[str] | None` kwarg, `None` means "every registered host" — **matching `parse_repo_grouped`'s default (Step 5 above), not diverging from it.** Defaulting `build_graph` to Claude-only instead, on the theory that the ~30 existing tests in `tests/test_graph_build.py` (which call `build_graph` without `hosts`) need protecting, doesn't hold up: none of those fixtures create a `.cursor/` directory, so broadening the default to "every host" changes nothing for any of them — Cursor's new dispatch branches simply find nothing to do. Keeping the two functions' defaults inconsistent instead would mean any caller that invokes both without threading the same explicit `hosts` list gets a graph and a manifest count describing different inventories — the exact self-contradictory-output bug this task's merge exists to prevent.

- [x] **Step 9: Write the failing test — Cursor MCP in the graph**

```python
def test_repo_cursor_mcp_json_becomes_direct_child(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["cursor"])
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1
    assert mcp_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]

def test_repo_cursor_mcp_json_absent_when_host_not_selected(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code"])
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert mcp_nodes == []

def test_repo_default_hosts_omitted_is_every_registered_host(tmp_path):
    # build_graph's default matches parse_repo_grouped's (Task 6) — both
    # "every registered host" — so a caller that uses one without the
    # other still sees the same inventory. Existing tests in this file
    # are unaffected: none of their fixtures create a .cursor/ directory,
    # so this broadening finds nothing extra for any of them.
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo")  # hosts omitted
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1
    assert mcp_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]

def test_repo_cursor_cache_mcp_json_is_claude_owned(tmp_path):
    # Same boundary case as Task 6's registry-level test, at the graph
    # layer: .cursor/cache/mcp.json is nested under .cursor/ but isn't
    # the exact .cursor/mcp.json shape — must be found and tagged
    # claude-code, not silently dropped by both branches.
    nested = tmp_path / ".cursor" / "cache"
    nested.mkdir(parents=True)
    (nested / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1
    assert mcp_nodes[0].ref.extra["runtime_hosts"] == ["claude-code"]
```

- [x] **Step 10: Run test to verify it fails**

Run: `python -m pytest tests/test_graph_build.py -k cursor_mcp -v`
Expected: FAIL — `build_graph() got an unexpected keyword argument 'hosts'`

- [x] **Step 11: Thread `hosts` through `build_graph`, `descend`, and `_add_repo_standalone_components`**

In `tools/graph_build.py`:

```python
from tools.hosts import all_host_ids
from tools.parsers import resolve_host_selection

def build_graph(
    target: Path,
    mode: str,
    project_root: Path | None = None,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
    hosts: list[str] | None = None,
) -> Graph:
    if mode not in ("repo", "endpoint"):
        raise ValueError(f"unknown mode: {mode!r}")
    hosts = hosts if hosts is not None else all_host_ids()
    if mode == "repo":
        # Same resolution `_active_registry` runs for parse_repo_grouped
        # — called here once, before descend()'s per-directory walk, so
        # duplicate host IDs are deduped and a colliding host selection
        # fails loudly at the single graph entry point, not per-
        # directory or not at all. The deduped list is what actually
        # flows into descend() below, not the raw `hosts` argument.
        hosts = resolve_host_selection(hosts)

    root = Node(key=_TARGET_KEY, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    normalize = _make_normalizer(mode, Path(target), Path(target), project_root)
    attach_root_dir: Path | None = None
    attach_root_spec: GitIgnoreSpec | None = None
    attach_include_gitignored = include_gitignored
    if mode == "endpoint":
        _seed_endpoint(graph, root, Path(target), project_root, normalize, warnings=warnings)
        attach_include_gitignored = True
    else:
        root_dir = Path(target)
        root_spec = None if include_gitignored else load_gitignore_spec(root_dir)
        descend(
            graph,
            root,
            root_dir,
            normalize,
            include_gitignored=include_gitignored,
            root_dir=root_dir,
            root_spec=root_spec,
            hosts=hosts,
        )
        attach_root_dir = root_dir
        attach_root_spec = root_spec
    ...  # rest unchanged
```

```python
def descend(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize: SourceNormalizer,
    *,
    emit_own_root_deps: bool = True,
    include_gitignored: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
    hosts: list[str] | None = None,
) -> None:
    hosts = hosts if hosts is not None else all_host_ids()
    if parent.kind == "target":
        plugin_roots = _find_plugin_roots(directory, include_gitignored=include_gitignored)
        realized_roots: list[Path] = []
        for plugin_root in plugin_roots:
            plugin_node = _descend_into_plugin(
                graph, parent, plugin_root,
                plugin_root / ".claude-plugin" / "plugin.json",
                normalize, root_dir=root_dir, root_spec=root_spec,
            )
            if plugin_node is not None:
                realized_roots.append(plugin_root)
        _add_project_skills(
            graph, parent, directory,
            normalize=normalize, exclude_under=realized_roots,
            include_gitignored=include_gitignored, root_dir=root_dir, root_spec=root_spec,
            hosts=hosts,  # Task 7 adds this parameter
        )
        if not any(_same_path(directory, root) for root in realized_roots):
            _add_dep_manifest_packages(
                graph, parent, directory, normalize,
                include_gitignored=include_gitignored, root_dir=root_dir, root_spec=root_spec,
            )
        _add_repo_standalone_components(
            graph, parent, directory, normalize,
            exclude_under=realized_roots,
            include_gitignored=include_gitignored, root_dir=root_dir, root_spec=root_spec,
            hosts=hosts,
        )
    elif parent.kind == "plugin":
        ...  # unchanged — plugins are Claude-only in this phase
    elif parent.kind == "skill":
        ...  # unchanged
```

`descend()`'s recursive calls from the `plugin`/`skill` branches, and the
nested call inside `_add_skill_node` (`descend(graph, skill_node, ...)`
with no `hosts` kwarg), fall back to the new parameter's default. That
default no longer matters for those branches either way — the `plugin`/
`skill` bodies (unchanged in this phase) never read `hosts` at all, so
whatever it resolves to is inert there regardless of value. It only ever
does something in the `target` branch, which is exactly where Cursor's
new dispatch lives.

Now `_add_repo_standalone_components`, **rewritten to walk
`HostAdapter.manifest_registry` for its MCP surface instead of hardcoded
per-host branches** — this is the graph-side half of the fix above.
A hardcoded, per-host approach — a Cursor-specific `if` branch checked
before a Claude-specific one, with `owning_host` as a guard preventing
the two from double-matching `.cursor/mcp.json` — would work, but
every future host would need its own new branch, hardcoded here,
separately from registering its `HostAdapter`. The registry-driven
version asks each selected host's `manifest_registry` directly, using
`registry_pattern_matches` — the exact same function
`parse_repo_grouped` uses — so this function and the manifest-accounting
one can never independently decide a path belongs to different hosts;
they're now provably driven by one matcher, not two implementations
that happen to agree:

```python
_MCP_REGISTRY_PATTERNS = frozenset({*_STANDALONE_MCP_FILENAMES, ".cursor/mcp.json"})

def _mcp_parser_for_path(path: Path, root: Path, hosts: list[str]) -> ParserFn | None:
    """First selected host's manifest_registry entry whose pattern is
    MCP-shaped and matches `path`, or None.

    The `_MCP_REGISTRY_PATTERNS` allowlist is this function's one
    remaining piece of host-specific knowledge: manifest_registry's
    `(pattern, ParserFn)` shape doesn't itself say what kind of
    component a pattern produces, only which parser to call for it.
    Registering a new host with an *already-allowlisted* pattern shape
    (any of these filenames, at any of these locations) needs zero
    changes here — only its HostAdapter registration. A host inventing
    a genuinely new pattern shape still needs one line added to this
    set; that's a smaller, more centralized ask than the old "write a
    new hardcoded dispatch branch" one.
    """
    for host_id in hosts:
        adapter = HOSTS.get(host_id)
        if adapter is None:
            continue
        for pattern, parser in adapter.manifest_registry:
            if pattern in _MCP_REGISTRY_PATTERNS and registry_pattern_matches(path, root, pattern):
                return parser
    return None

def _add_repo_standalone_components(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize: SourceNormalizer,
    *,
    exclude_under: list[Path] | None = None,
    include_gitignored: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
    hosts: list[str] | None = None,
) -> None:
    hosts = hosts if hosts is not None else all_host_ids()
    eval_root, spec = _ignore_context(directory, include_gitignored, root_dir, root_spec)
    walk_spec = spec if eval_root == directory else None
    exclude_resolved = [p.resolve() for p in exclude_under] if exclude_under else []
    for path in iter_unignored_files(directory, walk_spec):
        if _is_ignored_under(path, eval_root, spec):
            continue
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in exclude_resolved):
            continue

        mcp_parser = _mcp_parser_for_path(path, directory, hosts)
        if mcp_parser is not None:
            for ref in _safe_parse(mcp_parser, path):
                if _component_type(ref) != "mcp_server":
                    continue
                node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
                _add_child(graph, parent, node)
            continue

        if path.name == "settings.json" and _is_claude_settings_json(path, directory):
            for ref in _safe_parse(claude_settings.parse, path):
                if _component_type(ref) != "plugin":
                    continue
                node = Node(key=occurrence_key(ref, normalize), kind="plugin", ref=ref)
                _add_child(graph, parent, node)
            continue
        if path.suffix == ".md":
            ...  # unchanged (command/agent branch, Claude-only)
```

`_is_cursor_mcp_json` and the old `"cursor" in hosts` branch are
removed entirely — their job (deciding `.cursor/mcp.json` belongs to
Cursor, never Claude) is now `registry_pattern_matches`'s job alone,
reused rather than re-implemented. `_STANDALONE_MCP_FILENAMES` stays —
it's the pre-existing Claude-only filename set this codebase already
had; `_MCP_REGISTRY_PATTERNS` extends it with Cursor's pattern for this
function's own allowlist use, without changing what
`_STANDALONE_MCP_FILENAMES` itself means elsewhere.

**Why this is safe to land without changing any existing test's
expected result:** every registry entry's parser is the exact same
object as before (`mcp_json.parse` for Claude's three bare filenames,
relying on its own `runtime_hosts=None → ["claude-code"]` default from
Task 3; the pre-bound `functools.partial(mcp_json.parse,
runtime_hosts=["cursor"])` for Cursor's, built once in
`CURSOR_MANIFEST_REGISTRY`, Step 5 above) — only *how this function
finds which parser to call* changed, not what any parser does once
found. And `registry_pattern_matches` is the same, already-tested
function `parse_repo_grouped` has used since Step 5; every boundary case
this plan already added a test for (`.cursor/mcp.json` vs. plain
`mcp.json`, `.cursor/cache/mcp.json` falling back to Claude,
`.cursor/.mcp.json` likewise) resolves identically here, because it's
the identical matcher deciding it, not a parallel copy of the same
logic.

Add `import functools` to the top of `tools/graph_build.py` if not
already present (still needed elsewhere in this file), and add
`from tools.hosts import HOSTS` and `from tools.parsers import
registry_pattern_matches` alongside the existing `from tools.hosts
import all_host_ids` and `from tools.parsers import
resolve_host_selection` imports shown above.

- [x] **Step 12: Run test to verify it passes**

Run: `python -m pytest tests/test_graph_build.py -k cursor -v`
Expected: PASS (4 tests) — plain `-k cursor_mcp` would only catch 2 of
the 4 tests added in Step 9 as a literal substring
(`test_repo_default_hosts_omitted_is_every_registered_host` and
`test_repo_cursor_cache_mcp_json_is_claude_owned` don't contain that
exact substring); `-k cursor` catches all four, and at this point in
the plan's sequence nothing else in `test_graph_build.py` has "cursor"
in its name yet (Task 7 adds those next).

- [x] **Step 13: Write the regression test — two selected hosts claiming the identical registry pattern raise a clear error**

Nothing yet proves that colliding hosts actually fail loudly instead of
silently diverging — `parse_repo_grouped` double-counting a path while
graph dispatch picks whichever host happens to be first in the `hosts`
list. This test proves the guard added above actually closes that gap,
at both call sites, from one shared function:

```python
# tests/test_parsers/test_registry.py

def test_parse_repo_grouped_rejects_colliding_host_patterns(tmp_path, monkeypatch):
    from tools.hosts import HOSTS, HostAdapter

    def _collider_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="collider",
                extra={"component_type": "mcp_server", "runtime_hosts": ["collider-host"]},
            )
        ]

    collider_adapter = HostAdapter(
        host_id="collider-host",
        detect=lambda: False,
        config_root=lambda override: None,
        # Same bare pattern Claude's adapter already owns — this is
        # exactly the ambiguous reuse resolve_host_selection
        # exists to reject, not a new pattern shape.
        manifest_registry=[("mcp.json", _collider_parse)],
        posture_rule_ids=frozenset(),
    )
    monkeypatch.setitem(HOSTS, "collider-host", collider_adapter)

    (tmp_path / "mcp.json").write_text("{}")
    with pytest.raises(ValueError, match="mcp.json.*claude-code.*collider-host|mcp.json.*collider-host.*claude-code"):
        parse_repo_grouped(tmp_path, hosts=["claude-code", "collider-host"])
```

```python
# tests/test_graph_build.py

def test_build_graph_rejects_colliding_host_patterns(tmp_path, monkeypatch):
    from tools.hosts import HOSTS, HostAdapter

    def _collider_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="collider",
                extra={"component_type": "mcp_server", "runtime_hosts": ["collider-host"]},
            )
        ]

    collider_adapter = HostAdapter(
        host_id="collider-host",
        detect=lambda: False,
        config_root=lambda override: None,
        manifest_registry=[("mcp.json", _collider_parse)],
        posture_rule_ids=frozenset(),
    )
    monkeypatch.setitem(HOSTS, "collider-host", collider_adapter)

    (tmp_path / "mcp.json").write_text("{}")
    with pytest.raises(ValueError, match="mcp.json"):
        build_graph(tmp_path, mode="repo", hosts=["claude-code", "collider-host"])
```

Both tests select `["claude-code", "collider-host"]` together, not
`collider-host` alone — a colliding host combined with its existing
pattern owner, the "coexistence is the feature's normal state" case,
not the degenerate single-host case. The
correct behavior here is a loud, immediate failure, not silent
coexistence — a bare, host-ambiguous pattern genuinely can't be shared
safely, so proving the guard rejects it *is* the safety property, not
a lesser substitute for proving it works.

- [x] **Step 14: Run both tests to verify they pass**

Run: `python -m pytest tests/test_parsers/test_registry.py -k colliding -v tests/test_graph_build.py -k colliding -v`
Expected: PASS (2 tests). If either fails with the collision going
undetected instead of raising, double check the new host's adapter was
registered via `monkeypatch.setitem(HOSTS, ...)` (so it's visible to
`resolve_host_selection`'s deferred `from tools.hosts import
HOSTS`) and that `hosts=` explicitly includes both `"claude-code"` and
`"collider-host"` — omitting `hosts` would default to every
*permanently registered* host, which doesn't include this test's
monkeypatched one unless it's in the explicit list.

- [x] **Step 15: Write the regression test — a direct caller passing a duplicate host ID gets deduped, not double-counted**

The collision guard above doesn't cover a second cause of
the same "accounting over-counts, graph doesn't" divergence: `tools/scan.py`'s
CLI already dedupes repeated/comma-separated `--host` values before
calling `parse_repo_grouped`/`build_graph`, but those two functions are
themselves public (see this task's Interfaces block) and nothing
stopped a *direct* caller — a test, or a future non-CLI consumer —
from passing `hosts=["cursor", "cursor"]` and getting the registry
extended with Cursor's `manifest_registry` twice while the graph
stayed correct by coincidence (its first-match loop is idempotent for
a repeated ID). `resolve_host_selection`'s dedup (Step 5 above) closes
this; this test proves it from a caller that skips the CLI entirely:

```python
# tests/test_parsers/test_registry.py

def test_parse_repo_grouped_dedupes_duplicate_host_ids(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["cursor", "cursor"])
    assert n_found == 1
    assert len(grouped) == 1
```

- [x] **Step 16: Run test to verify it passes**

Run: `python -m pytest tests/test_parsers/test_registry.py -k dedupes_duplicate_host_ids -v`
Expected: PASS. If it fails with `n_found == 2`, `resolve_host_selection`
isn't deduplicating `selected` before `_active_registry` builds the
combined registry from it — check `list(dict.fromkeys(...))` (Step 5
above) is actually being used, not a plain pass-through of `hosts`.

- [x] **Step 17: Write the regression test — an unknown host ID raises at the public boundary, not just at the CLI**

The public-function contract so far covers `None`,
duplicates, and collisions, but not an unrecognized host ID: a direct
caller passing `hosts=["typo"]` got no error — `_active_registry` and
graph dispatch just found no adapter for it and silently contributed
nothing, while `HOST_AGNOSTIC_REGISTRY`'s dependency-manifest parsers
kept running normally, producing a scan that looks complete rather than
obviously wrong. `tools/scan.py`'s CLI already rejects this
(`test_scan_repo_unknown_host_errors`), but that's a CLI-only
safeguard, not a property of the public functions themselves:

```python
# tests/test_parsers/test_registry.py

def test_resolve_host_selection_rejects_unknown_host():
    with pytest.raises(ValueError, match="typo"):
        resolve_host_selection(["claude-code", "typo"])

def test_parse_repo_grouped_rejects_unknown_host(tmp_path):
    with pytest.raises(ValueError, match="typo"):
        parse_repo_grouped(tmp_path, hosts=["typo"])
```

```python
# tests/test_graph_build.py

def test_build_graph_rejects_unknown_host(tmp_path):
    with pytest.raises(ValueError, match="typo"):
        build_graph(tmp_path, mode="repo", hosts=["typo"])
```

- [x] **Step 18: Run all three tests to verify they pass**

Run: `python -m pytest tests/test_parsers/test_registry.py -k unknown_host -v tests/test_graph_build.py -k unknown_host -v`
Expected: PASS (3 tests). If any fails with no error raised, check that
`resolve_host_selection`'s unknown-ID check (Step 5 above) only runs
when `hosts is not None` — the `None`-default case resolves to
`HOSTS.keys()` directly and can never contain an unknown ID by
construction, so guarding the check behind `hosts is not None` avoids a
contradiction, not a workaround.

- [x] **Step 19: Run the full graph-build AND registry suites together**

Run: `python -m pytest tests/test_graph_build.py tests/test_parsers/test_registry.py tests/test_hosts.py tests/ -v`
Expected: PASS. All ~30 pre-existing `test_graph_build.py` tests
unchanged — they all call `build_graph` without `hosts`, which now
defaults to every registered host, but since none of their fixtures
contain a `.cursor/` directory, Cursor's new branches find nothing and
output is byte-identical to before this task. This is also the check
that closes the reason this task exists as one unit: run
`openaca scan repo` by hand (or add a quick manual check) against a
fixture repo containing `.cursor/mcp.json` and confirm the summary
line's manifest count and component count now agree — they would not
have, at the registry-only halfway point this task deliberately never
commits.

- [x] **Step 20: Commit**

One commit for both halves — this is the whole point of merging the
two original tasks: the registry split and the graph dispatch it
depends on for output consistency land together, never separately.
The pattern-collision guard, duplicate-host-ID dedup, and unknown-host
rejection (Steps 5, 11, 13-18 above) land in this same commit — all
three are properties of the shared registry/dispatch this task
introduces, not separable follow-ups.

```bash
git add tools/parsers/__init__.py tools/host_paths.py tools/hosts.py tools/graph_build.py tests/
git commit -m "feat: recognize Cursor MCP servers in repo-mode manifest accounting and graph construction together"
```

---

## Task 7: Cursor Skills in `tools/graph_build.py`'s `descend()` path

**This task also adds Cursor's Skills entries to `CURSOR_MANIFEST_REGISTRY`,**
not Task 6: registering these entries in Task 6 instead would make
`parse_repo_grouped`'s manifest count include Cursor skill files
before this task's graph-side wiring exists to turn them into real
components — the same "N manifests, 0 components" self-contradiction
Task 6's own opening paragraph says is unacceptable, reproduced for
Skills instead of MCP. Same fix: land the registry entry and the graph
dispatch that makes it real together, in this task, not split across
Task 6 and Task 7.)

**Files:**
- Modify: `tools/parsers/__init__.py` (add Cursor's two Skills patterns to `CURSOR_MANIFEST_REGISTRY`)
- Modify: `tools/graph_build.py` (`_add_skill_node`, `_add_project_skills`; removes `_is_project_skill_md`, `_project_skill_config_dir`, `_SKILL_DIR_RUNTIME_HOSTS`)
- Test: `tests/test_graph_build.py`

**Interfaces:**
- Consumes: `claude_skill.parse` (Task 4), `tools.hosts.HOSTS` and `tools.parsers.registry_pattern_matches` (both Task 6).
- Produces: `CURSOR_MANIFEST_REGISTRY` gains its two Skills entries (previously MCP-only after the Task 6 fix above). `_add_project_skills(..., hosts: list[str] | None = None)` (internal, but Task 6's `descend()` call already passes `hosts` positionally-by-keyword — this task makes that parameter real). `_add_skill_node(..., skill_parser: ParserFn | None = None)` — new registry-driven path, additive to the existing `runtime_hosts` parameter.

- [x] **Step 1: Write the failing test**

```python
def test_repo_cursor_skills_dir_found(tmp_path):
    skill_dir = tmp_path / ".cursor" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nbody\n")
    g = build_graph(tmp_path, mode="repo", hosts=["cursor"])
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1
    assert skill_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]

def test_repo_agents_skills_dir_tagged_cursor_only(tmp_path):
    # Not ["cursor", "codex"]: Codex isn't a registered host in this plan
    # (no HOSTS["codex"] entry), so the scan never actually verified it.
    # See _SKILL_DIR_RUNTIME_HOSTS above for the full rationale.
    skill_dir = tmp_path / ".agents" / "skills" / "shared"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: shared\ndescription: d\n---\nbody\n")
    g = build_graph(tmp_path, mode="repo", hosts=["cursor"])
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1
    assert skill_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]

def test_repo_cursor_skills_absent_when_host_not_selected(tmp_path):
    skill_dir = tmp_path / ".cursor" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nbody\n")
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code"])
    assert [n for n in g.nodes.values() if n.kind == "skill"] == []

def test_repo_claude_skills_now_tagged_claude_code(tmp_path):
    # Regression/behavior-change guard from Task 4: existing Claude skill
    # refs now carry runtime_hosts, threaded correctly through build_graph.
    skill_dir = tmp_path / ".claude" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nbody\n")
    g = build_graph(tmp_path, mode="repo")  # hosts omitted -> every registered host,
    # but this fixture has no .cursor/ content, so only the Claude skill is found
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert skill_nodes[0].ref.extra["runtime_hosts"] == ["claude-code"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_graph_build.py -k cursor_skills -v`
Expected: FAIL — `.cursor/skills`/`.agents/skills` directories produce no
skill node (only `.claude/skills` is recognized today).

- [x] **Step 3: Add Cursor's Skills entries to `CURSOR_MANIFEST_REGISTRY`**

In `tools/parsers/__init__.py`, extend the `CURSOR_MANIFEST_REGISTRY`
list Task 6 defined (currently MCP-only after that task's fix) with
the two Skills patterns:

```python
CURSOR_MANIFEST_REGISTRY: list[tuple[str, ParserFn]] = [
    (".cursor/mcp.json", functools.partial(mcp_json.parse, runtime_hosts=["cursor"])),
    (
        "**/.cursor/skills/*/SKILL.md",
        functools.partial(claude_skill.parse, runtime_hosts=["cursor"]),
    ),
    (
        "**/.agents/skills/*/SKILL.md",
        # Not ["cursor", "codex"]: Codex isn't a registered host in this
        # plan (no HOSTS["codex"] entry exists), so a scan can never
        # actually select it — tagging refs with a host the scan didn't
        # verify contradicts this project's evidence-over-inference
        # discipline, even though .agents/skills genuinely is Codex-
        # readable per the spec's own research. It also collides with
        # the spec's Identity section, which names subagents as the
        # *only* confirmed case where one occurrence needs multiple
        # runtime_hosts — .agents/skills getting the same treatment here
        # wasn't reconciled against that. Revisit together with subagents
        # once Codex is a registered host (ADR-0045's "When to revisit"
        # names the Codex trigger; not resolved by this design).
        functools.partial(claude_skill.parse, runtime_hosts=["cursor"]),
    ),
]
```

This alone makes `parse_repo_grouped`'s manifest accounting recognize
Cursor skill files — Step 4's graph dispatch below is what makes them
real components, landing in the same commit (Step 8) so neither half
ships without the other.

`claude_skill` and `functools` are already imported in this module
(Task 6's Step 5) — no new imports needed for this step.

- [x] **Step 4: Rewrite `_add_skill_node`/`_add_project_skills` to walk `HostAdapter.manifest_registry`**

**Same decision as Task 6's MCP dispatch, applied here** (see Task 6's
opening note for the full reasoning): rather
than hardcoding a `config_dirs`/`_SKILL_DIR_RUNTIME_HOSTS` table here
that duplicates what each host's `manifest_registry` already declares,
`_add_project_skills` asks the registry directly, through the same
`registry_pattern_matches` function Task 6 just made cross-module.

**Verified against `tools/graph_build.py:1175-1205` directly** — the
real current signature has no `exclude_under` parameter. The rewrite
below matches the actual signature, extended with an optional
`skill_parser` — the registry-provided parser, when the caller has one
— alongside the pre-existing `runtime_hosts` parameter, which the two
*other* callers of this function (endpoint mode, plugin-bundled skills
— both still Claude-only hardcoded paths, unaffected by this task)
keep using exactly as before:

```python
def _add_skill_node(
    graph: Graph,
    parent: Node,
    skill_subdir: Path,
    *,
    normalize: SourceNormalizer,
    project_root: Path | None = None,
    stamp_provenance: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
    runtime_hosts: list[str] | None = None,
    skill_parser: ParserFn | None = None,
) -> None:
    if skill_parser is None:
        runtime_hosts = runtime_hosts if runtime_hosts is not None else ["claude-code"]
        skill_parser = functools.partial(claude_skill.parse, runtime_hosts=runtime_hosts)
    skill_md = skill_subdir / "SKILL.md"
    for ref in _safe_parse(skill_parser, skill_md):
        if stamp_provenance and ref.name:
            provenance = skill_lock.provenance_for_skill(
                skill_md, ref.name, project_root=project_root
            )
            if provenance is not None:
                ref = replace(ref, extra={**ref.extra, "source_provenance": provenance})
        skill_node = Node(key=occurrence_key(ref, normalize), kind="skill", ref=ref)
        _add_child(graph, parent, skill_node)
        descend(graph, skill_node, skill_subdir, normalize, root_dir=root_dir, root_spec=root_spec)
```

Both `runtime_hosts` and `skill_parser` are new, keyword-only, both
defaulted — every existing call site keeps compiling unchanged whether
it passes neither, `runtime_hosts`, or (only from `_add_project_skills`
below) `skill_parser`.

```python
_SKILL_REGISTRY_PATTERNS = frozenset({
    "**/.claude/skills/*/SKILL.md",
    "**/.cursor/skills/*/SKILL.md",
    "**/.agents/skills/*/SKILL.md",
})

def _skill_parser_for_path(path: Path, root: Path, hosts: list[str]) -> ParserFn | None:
    """Same pattern as Task 6's `_mcp_parser_for_path`, for skill-shaped
    registry entries. Same allowlist trade-off applies: a new host
    reusing one of these three directory-name shapes needs zero changes
    here, a genuinely new skill-directory convention needs one line
    added to this set."""
    for host_id in hosts:
        adapter = HOSTS.get(host_id)
        if adapter is None:
            continue
        for pattern, parser in adapter.manifest_registry:
            if pattern in _SKILL_REGISTRY_PATTERNS and registry_pattern_matches(path, root, pattern):
                return parser
    return None

def _add_project_skills(
    graph: Graph,
    parent: Node,
    directory: Path,
    exclude_under: list[Path] | None = None,
    *,
    normalize: SourceNormalizer,
    project_root: Path | None = None,
    stamp_provenance: bool = False,
    include_gitignored: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
    hosts: list[str] | None = None,
) -> None:
    hosts = hosts if hosts is not None else all_host_ids()
    eval_root, spec = _ignore_context(directory, include_gitignored, root_dir, root_spec)
    walk_spec = spec if eval_root == directory else None
    exclude_resolved = [p.resolve() for p in exclude_under] if exclude_under else []
    for path in iter_unignored_files(directory, walk_spec):
        if path.name != "SKILL.md":
            continue
        skill_parser = _skill_parser_for_path(path, directory, hosts)
        if skill_parser is None:
            continue
        if _is_ignored_under(path, eval_root, spec):
            continue
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in exclude_resolved):
            continue
        _add_skill_node(
            graph,
            parent,
            path.parent,
            normalize=normalize,
            project_root=project_root,
            stamp_provenance=stamp_provenance,
            root_dir=root_dir,
            root_spec=root_spec,
            skill_parser=skill_parser,
        )
```

`_project_skill_config_dir` and `_SKILL_DIR_RUNTIME_HOSTS` are removed
entirely — replaced by the registry lookup above. Remove the
already-unused `_is_project_skill_md` helper too (dead since an earlier
round; grep `grep -rn "_is_project_skill_md" tools/` to confirm no
remaining callers before deleting).

**Why this preserves every existing test's expected result:**
`registry_pattern_matches`'s skill-matching branch (`_skill_path_matches`,
Task 6 Step 5) accepts arbitrary depth before the config-dir component,
exactly like the old `_project_skill_config_dir` did (both anchor the
match to the *end* of the path — `.claude/skills/<name>/SKILL.md` — not
the start), so nested cases like
`packages/frontend/.claude/skills/ui/SKILL.md` resolve identically.
Each registry entry's parser is the same pre-bound object as before
(bare `claude_skill.parse` for Claude, relying on its
`runtime_hosts=None → ["claude-code"]` default from Task 4;
`functools.partial(claude_skill.parse, runtime_hosts=["cursor"])` for
`.cursor`/`.agents`, built once in `CURSOR_MANIFEST_REGISTRY`) — only
*how this function finds which parser to call* changed.

Also update the two other existing call sites of `_add_skill_node`
(the endpoint-mode path in `_seed_endpoint`/`_add_skills_from_dir`,
and the plugin/bundled-skill branch inside `descend()`'s `plugin`
case) — check `tools/graph_build.py` for every call site via
`grep -n "_add_skill_node(" tools/graph_build.py` and confirm each one
either passes an explicit `runtime_hosts` or is fine relying on the new
default (`["claude-code"]`, correct for both — endpoint mode and plugin
bundled skills are Claude-only in this phase). Neither needs
`skill_parser` — that parameter only makes sense for a registry-driven
caller.

- [x] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_graph_build.py -k "cursor_skills or claude_skills_now_tagged" -v`
Expected: PASS (4 tests)

- [x] **Step 6: Run the full graph-build suite**

Run: `python -m pytest tests/test_graph_build.py -v`
Expected: PASS. This is the step most likely to surface a signature
mismatch from the `_add_skill_node` parameter changes — if any existing
call site breaks, fix the call site to match the real (not speculative)
original signature discovered in Step 4.

- [x] **Step 7: Write the failing test — a synthetic host is graph-discoverable, not just accounted for**

This test proves the graph-side half directly: not "a
synthetic host's files are counted" (Task 6's registry test already
proves that, and manifest accounting alone isn't enough), but "a
synthetic host's components appear in the graph that `tools/scan.py`
treats as its single source of truth for BOM, matching, and findings."
Register one synthetic host with both an MCP entry and a skill entry —
proving both surfaces this task and Task 6 cover are genuinely
registry-driven in the graph, not just in isolation:

```python
def test_synthetic_host_registered_in_hosts_is_graph_discoverable(tmp_path, monkeypatch):
    # Proves registering a HostAdapter is sufficient for build_graph() —
    # the path tools/scan.py's own comment calls "the single source of
    # truth" for scope, attribution, BOM, and findings — to pick up a
    # new host's components. No edit to graph_build.py beyond what this
    # task already lands; only a HOSTS registration plus (per
    # _mcp_parser_for_path's/_skill_parser_for_path's documented
    # trade-off) reusing already-allowlisted pattern shapes.
    #
    # Registers both an MCP entry and a skill entry — this step's title
    # promises both surfaces are graph-discoverable, so both are
    # registered and asserted below, not just one.
    from tools.hosts import HOSTS, HostAdapter

    def _synthetic_mcp_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="synthetic-widget",
                extra={"component_type": "mcp_server", "runtime_hosts": ["synthetic-host"]},
            )
        ]

    def _synthetic_skill_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="synthetic-skill",
                extra={"component_type": "skill", "runtime_hosts": ["synthetic-host"]},
            )
        ]

    synthetic_adapter = HostAdapter(
        host_id="synthetic-host",
        detect=lambda: False,
        config_root=lambda override: None,
        # Reuses two already-allowlisted pattern shapes — bare
        # "mcp.json" (MCP) and Cursor's own "**/.agents/skills/*/SKILL.md"
        # shape (Skills) — rather than inventing new ones. This test is
        # deliberately run with hosts=["synthetic-host"] alone (see
        # Step 8's note), so reusing Claude's bare "mcp.json" pattern
        # here doesn't collide with Claude's own registration; Task 6's
        # Steps 13-14 cover what happens when a colliding pattern *is*
        # selected alongside its existing owner.
        manifest_registry=[
            ("mcp.json", _synthetic_mcp_parse),
            ("**/.agents/skills/*/SKILL.md", _synthetic_skill_parse),
        ],
        posture_rule_ids=frozenset(),
    )
    monkeypatch.setitem(HOSTS, "synthetic-host", synthetic_adapter)

    (tmp_path / "mcp.json").write_text("{}")
    skill_dir = tmp_path / ".agents" / "skills" / "synthetic-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: synthetic-skill\n---\nbody\n")

    g = build_graph(tmp_path, mode="repo", hosts=["synthetic-host"])
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(mcp_nodes) == 1
    assert mcp_nodes[0].ref.name == "synthetic-widget"
    assert len(skill_nodes) == 1
    assert skill_nodes[0].ref.name == "synthetic-skill"
```

- [x] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/test_graph_build.py -k synthetic_host_registered_in_hosts_is_graph_discoverable -v`
Expected: PASS (both the MCP and Skill assertions). If it fails with
the synthetic host's file not found, double check `"synthetic-host"`
isn't accidentally excluded anywhere `hosts` gets filtered — this test
intentionally does not register `"synthetic-host"` with
`all_host_ids()` (it's a `monkeypatch.setitem` against `HOSTS`
directly, not a real permanent registration), so it must be passed
explicitly via `hosts=["synthetic-host"]`, matching what the test does.
If only the skill assertion fails, check `_add_skill_node`'s
`skill_parser` wiring from Steps 1, 2, and 4 above — the MCP path
(Task 6) and the skill path (this task) are two independent call sites
reusing the same registry-driven pattern, not one shared code path, so
a bug in one doesn't necessarily show up in the other.

- [x] **Step 9: Commit**

```bash
git add tools/parsers/__init__.py tools/graph_build.py tests/test_graph_build.py
git commit -m "feat(graph): make Cursor Skills real (registry + graph together)"
```

---

## Task 8: `--host` CLI, host-filtered posture collection, and host-attribution labeling

**CLI exposure, posture-collection host filtering, and posture-finding
host *labeling* all land in one task, not split across commits:** a
committed interval where `--host` exists and Cursor MCP is discoverable,
but posture findings about it are still mislabeled `claude-code`, is a
real defect, not just an aesthetic sequencing preference the way the
scaffold-then-capability pattern elsewhere in this plan is. Unlike "this
capability doesn't exist yet," "this capability exists and gives a
wrong, security-relevant answer" is worth collapsing into one commit —
`--host` is never live while any
part of the posture path can still produce a wrong or unfiltered
Cursor-attributed finding.

**Files:**
- Modify: `tools/scan.py` (`repo` command, around line 683-830 per the current file)
- Modify: `tools/posture/rules/insecure_transport.py`
- Modify: `tools/posture/rules/mcp_auto_approve.py`
- Test: `tests/test_scan.py` (CLI-option tests — `--config-dir`, `--project`, etc. already live there, confirmed by reading the file; not `tests/test_e2e.py`, which per `CLAUDE.md` is reserved for cross-*module* tests, not one command's option parsing)
- Test: `tests/test_posture_insecure_transport.py`, `tests/test_posture_mcp_auto_approve.py` — both already exist in the repo (confirmed), add to them directly.

**Interfaces:**
- Consumes: `tools.hosts.all_host_ids`, `tools.hosts.HOSTS` (for `posture_rule_ids`), `tools.host_paths.owning_host`, `build_graph(..., hosts=...)`, `parse_repo_grouped(..., hosts=...)` (all Task 6).
- Produces: `openaca scan repo --host cursor`, `--host claude-code --host cursor`, `--host claude-code,cursor` all resolve to the same host list; omitted `--host` resolves to every registered host; a value that resolves to zero valid host names (`--host ','`, `--host ''`) is a hard error, not a silent empty scan. Posture manifest collection is host-filtered (a manifest belonging to an unselected host is neither scanned nor labeled). `_infer_hosts(path: Path, manifest: dict) -> list[str]` in both `insecure_transport.py` and `mcp_auto_approve.py` — **signature change** from `_infer_hosts(manifest: dict)`, now path-aware. `mcp_auto_approve.check_mcp_auto_approve` additionally skips any manifest not owned by `claude-code` entirely (see Step 10).

- [x] **Step 1: Write the failing test — `--host` CLI behavior**

The scan CLI's `--format json` document has no top-level `"components"`
key — `tools/render.py::render_json` emits `{"findings": [...], "stats":
{...}, "target": {...}}`, where a component only appears at all if it
produced a vulnerability/posture/observation finding (see
`finding_to_output`/`posture_to_output` in `tools/finding_output.py`).
For a plain "was this component discovered" check with no guaranteed
finding, the default **text** output is the reliable signal instead: the
`repo` command always builds its inventory-tree card from `grouped` refs
(`tools/scan.py`'s `card_tree = render_repo_inventory_tree(...)` inside
`if is_text and grouped:`), which is unconditional, not verbose-gated.

```python
# tests/test_scan.py — add near existing repo/endpoint CLI-option tests
def test_scan_repo_cursor_mcp_via_host_flag(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["scan", "repo", "--target", str(tmp_path), "--host", "cursor"])
    assert "weather-mcp" in result.output

def test_scan_repo_default_host_includes_cursor(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["scan", "repo", "--target", str(tmp_path)])  # --host omitted
    assert "weather-mcp" in result.output

def test_scan_repo_host_claude_code_only_excludes_cursor(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["scan", "repo", "--target", str(tmp_path), "--host", "claude-code"]
    )
    assert "weather-mcp" not in result.output

def test_scan_repo_unknown_host_errors():
    runner = CliRunner()
    result = runner.invoke(
        main, ["scan", "repo", "--target", ".", "--host", "not-a-real-host"]
    )
    assert result.exit_code != 0

def test_scan_repo_duplicate_host_dedupes():
    # Repeated + comma-separated forms both resolve the same duplicate
    # away rather than erroring or double-scanning.
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["scan", "repo", "--target", ".", "--host", "claude-code", "--host", "claude-code"],
    )
    assert result.exit_code in (0, 1)

def test_scan_repo_host_comma_and_whitespace_forms_equivalent(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    runner = CliRunner()
    comma = runner.invoke(
        main, ["scan", "repo", "--target", str(tmp_path), "--host", "claude-code, cursor"]
    )
    repeated = runner.invoke(
        main,
        [
            "scan", "repo", "--target", str(tmp_path),
            "--host", "claude-code", "--host", "cursor",
        ],
    )
    assert ("weather-mcp" in comma.output) == ("weather-mcp" in repeated.output) == True

def test_scan_repo_host_empty_value_errors():
    # A comma-only or empty --host value resolves to zero valid host
    # names after stripping — that must be a hard error ("you gave me
    # nothing usable"), not a silent scan of zero hosts (which would
    # look identical to "target has no manifests" from the outside).
    runner = CliRunner()
    result = runner.invoke(main, ["scan", "repo", "--target", ".", "--host", ","])
    assert result.exit_code != 0
```

`tests/test_scan.py` already imports `main` and `CliRunner` for its
existing `repo`/`endpoint` CLI tests — no new imports needed.

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scan.py -k scan_repo_cursor -v`
Expected: FAIL — `no such option: --host`

- [x] **Step 3: Add the `--host` option and host resolution**

In `tools/scan.py`, add a shared option decorator and a resolution
helper near the other `_*_option` decorators (around line 285):

```python
from tools.hosts import HOSTS, all_host_ids
from tools.host_paths import owning_host

_host_option = click.option(
    "--host",
    "host_values",
    multiple=True,
    default=(),
    help=(
        "Host(s) to scan for (repeatable or comma-separated). Known hosts: "
        f"{', '.join(all_host_ids())}. Omitted: every known host."
    ),
)

def _resolve_hosts(host_values: tuple[str, ...]) -> list[str]:
    """Flatten repeatable/comma-separated --host values; omitted = every known host."""
    if not host_values:
        return all_host_ids()
    known = set(all_host_ids())
    resolved: list[str] = []
    for raw in host_values:
        for piece in raw.split(","):
            host_id = piece.strip()
            if not host_id:
                continue
            if host_id not in known:
                raise click.BadParameter(
                    f"unknown host {host_id!r}; known hosts: {', '.join(sorted(known))}"
                )
            if host_id not in resolved:
                resolved.append(host_id)
    if not resolved:
        # --host was given but every piece was empty after stripping
        # (e.g. "--host ','" or "--host ''") — an explicit, unusable
        # value is an error, not indistinguishable from "scanned zero
        # hosts on purpose."
        raise click.BadParameter(
            "--host given but contains no usable host name "
            f"(known hosts: {', '.join(sorted(known))})"
        )
    return resolved
```

Add `@_host_option` to the `repo` command's decorator stack (next to
`@_target_option_required` etc.) and add `host_values: tuple[str, ...]`
to its signature. Inside the function body, resolve hosts once near the
top (after `_validate_report_options`) and thread the result into both
graph construction and the parse-count walk:

```python
    hosts = _resolve_hosts(host_values)
    graph = build_graph(
        target, mode="repo", include_gitignored=include_gitignored, hosts=hosts
    )
    ...
    parse_groups, n_found = parse_repo_grouped(
        target, include_gitignored=include_gitignored, hosts=hosts
    )
```

**`collect_mcp_manifests`/`collect_settings_manifests` are not actually
host-aware, and leaving them as-is is a real bug, not a no-op.** They
walk by bare filename across `roots` with zero host filtering —
`collect_mcp_manifests` finds every file named `mcp.json`/`.mcp.json`/
`claude_desktop_config.json` anywhere under `target`, including
`.cursor/mcp.json`, regardless of which hosts were selected. Left
unfixed: `--host claude-code` (Cursor explicitly excluded) would still
run posture rules against `.cursor/mcp.json` and surface a posture
finding for a component the scan's own inventory says isn't there.
`--host` needs to mean "excluded hosts are excluded," not "excluded
from the component graph but not from posture." Filter the collected
manifests by `owning_host` (Task 6, Step 3) right after collection:

```python
manifests = collect_mcp_manifests([target], include_gitignored=include_gitignored)
manifests = [(p, d) for p, d in manifests if owning_host(p) in hosts]
```

`collect_settings_manifests` only feeds `api_endpoint_override`, which
is genuinely Claude-schema-specific (matches literal
`anthropic_base_url`/`anthropic_auth_token` env keys) — this is exactly
what `HostAdapter.posture_rule_ids` exists to gate, and Task 5 already
populates that field on both adapters without anything ever reading it.
Wire it here, the first real consumer (`HOSTS` already imported above):

```python
active_rule_ids = frozenset().union(*(HOSTS[h].posture_rule_ids for h in hosts))
settings_manifests = (
    collect_settings_manifests([target], include_gitignored=include_gitignored)
    if "openaca-posture-api-endpoint-override" in active_rule_ids
    else []
)
```

(`api_endpoint_override.RULE_ID` — check the exact constant name in
`tools/posture/rules/api_endpoint_override.py` rather than retyping the
string by hand; use it instead of a bare string literal if importing it
doesn't create a circular import with `tools/scan.py`, which already
imports from `tools.posture.rules` elsewhere in the file.)

- [x] **Step 4: Write the failing test for posture collection host-filtering**

```python
# tests/test_scan.py
def test_scan_repo_host_claude_code_excludes_cursor_posture_finding(tmp_path):
    # Confirms exclusion happens at collection, not just at labeling
    # (Steps 8-9 below fix labeling; this fixes collection).
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan", "repo", "--target", str(tmp_path),
            "--host", "claude-code", "--include-posture", "--format", "json",
        ],
    )
    doc = json.loads(result.output)
    posture = [
        f for f in doc["findings"]
        if f.get("finding_type") == "posture" and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture == []

def test_scan_repo_cursor_cache_mcp_json_posture_uses_claude_code(tmp_path):
    # Same boundary case as Task 6: a manifest
    # merely nested under .cursor/ but not the exact .cursor/mcp.json
    # shape must be posture-scanned as claude-code, not silently
    # dropped by a --host cursor selection (or, before the owning_host
    # precision fix, wrongly kept as "cursor" despite the graph never
    # recognizing it as a Cursor component at all).
    nested = tmp_path / ".cursor" / "cache"
    nested.mkdir(parents=True)
    (nested / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    cursor_only = runner.invoke(
        main,
        [
            "scan", "repo", "--target", str(tmp_path),
            "--host", "cursor", "--include-posture", "--format", "json",
        ],
    )
    doc = json.loads(cursor_only.output)
    posture = [
        f for f in doc["findings"]
        if f.get("finding_type") == "posture" and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture == []  # claude-code-owned manifest, not visible under --host cursor
```

- [x] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_scan.py -k "scan_repo_cursor or scan_repo_default_host or scan_repo_host_claude or scan_repo_unknown_host or scan_repo_duplicate_host or scan_repo_host_comma or scan_repo_host_empty" -v`
Expected: PASS (9 tests)

- [x] **Step 6: Write the failing test — host attribution labeling**

`tests/test_posture_insecure_transport.py` already has three tests
pinning down `_infer_hosts`'s *content*-based behavior —
`test_mcpservers_key_sets_claude_code_active_in`,
`test_servers_key_leaves_active_in_empty`,
`test_flat_root_leaves_active_in_empty` (confirmed by reading the file).
That content-shape distinction is worth keeping: a VS Code
`.vscode/mcp.json` uses the `servers` key, not `mcpServers` — it isn't
a Claude Code or Cursor manifest at all, and asserting `["claude-code"]`
for it purely because its path isn't under `.cursor/` would be a new,
wrong guess, not a fix. The correct design combines both signals:
content shape decides *whether* a host can be inferred at all (only
`mcpServers`-keyed manifests, the shape both Claude Code and Cursor
actually use, qualify); path decides *which* host, since content can't —
both hosts use identical JSON. Add these tests to the existing files,
alongside the ones above (don't remove or change the existing three —
they stay correct under the new implementation):

```python
# tests/test_posture_insecure_transport.py — add to the existing file
def test_cursor_mcp_json_sets_cursor_active_in(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    manifest = {"mcpServers": {"x": {"url": "http://x.example/mcp"}}}
    findings = check_insecure_transport([(cursor_dir / "mcp.json", manifest)])
    assert findings[0].active_in == ["cursor"]

def test_claude_mcp_json_still_sets_claude_code_active_in(tmp_path):
    # Regression guard alongside the existing test_mcpservers_key_sets_
    # claude_code_active_in — same assertion, different path, to pin
    # that a *non*-.cursor path still resolves to claude-code.
    manifest = {"mcpServers": {"x": {"url": "http://x.example/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "some/nested/mcp.json", manifest)])
    assert findings[0].active_in == ["claude-code"]

def test_cursor_cache_mcp_json_active_in_is_claude_code(tmp_path):
    # Boundary case: nested-under-.cursor/ but not the
    # exact .cursor/mcp.json shape — must resolve to claude-code, the
    # same as owning_host resolves it everywhere else.
    manifest = {"mcpServers": {"x": {"url": "http://x.example/mcp"}}}
    findings = check_insecure_transport([(tmp_path / ".cursor/cache/mcp.json", manifest)])
    assert findings[0].active_in == ["claude-code"]
```

(`tests/test_posture_mcp_auto_approve.py`'s host-attribution tests are
folded into Step 8 below, since that file's fix is different in kind —
it skips non-Claude manifests entirely rather than just relabeling
them.)

- [x] **Step 7: Fix `_infer_hosts` to combine content shape and path**

In both `tools/posture/rules/insecure_transport.py` and
`tools/posture/rules/mcp_auto_approve.py` (the two files have identical
copies of this helper today):

```python
from tools.host_paths import owning_host

def _infer_hosts(path: Path, manifest: dict) -> list[str]:
    """`mcpServers` is the shape both Claude Code and Cursor use — content
    alone can't tell them apart, but `owning_host` (path-based) always
    can. Other shapes (`servers` for VS Code, flat-root) carry no host
    signal at all; leave active_in empty rather than guess."""
    if not isinstance(manifest.get("mcpServers"), dict):
        return []
    return [owning_host(path)]
```

Update each call site — both currently read `active_in=_infer_hosts(manifest)`
inside `for path, manifest in manifests:` (`path` is already in scope,
just not threaded through):

```python
active_in=_infer_hosts(path, manifest),
```

Run: `python -m pytest tests/test_posture_insecure_transport.py -v`
Expected: PASS, full file green — including the three pre-existing
content-shape tests, unchanged.

- [x] **Step 8: Write the failing test — `mcp_auto_approve` must not fire on non-Claude manifests at all**

Checked directly against Cursor's own MCP
documentation (`cursor.com/docs/context/mcp`): Cursor's approval model
is Run-Modes/UI state — there is no documented per-server `mcp.json`
field for auto-approval, unlike Claude Code's `autoApprove`. Keeping
`mcp_auto_approve` applicable to Cursor as
"defensible caution" without checking wouldn't survive
contact with Cursor's actual docs. If a Cursor-owned manifest happens to
carry a Claude-shaped `autoApprove: true` key (e.g. copy-pasted from a
Claude config during a migration), Cursor's own config surface doesn't
recognize it — flagging it as "MCP server has auto-approval enabled"
asserts an active posture that isn't real. This is a different kind of
fix than Step 7's relabeling: the rule must not fire on a Cursor
manifest **at all**, not just fire with a corrected label.

```python
# tests/test_posture_mcp_auto_approve.py — add to the existing file
def test_cursor_mcp_json_not_flagged(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    manifest = {"mcpServers": {"x": {"autoApprove": True}}}
    findings = check_mcp_auto_approve([(cursor_dir / "mcp.json", manifest)])
    assert findings == []

def test_claude_mcp_json_still_flagged(tmp_path):
    # Regression guard: the Claude-only case must keep working.
    manifest = {"mcpServers": {"x": {"autoApprove": True}}}
    findings = check_mcp_auto_approve([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert findings[0].active_in == ["claude-code"]
```

Check `tests/test_posture_mcp_auto_approve.py`'s exact existing
content/import style before adding — it wasn't read line-for-line
during plan authoring, unlike `test_posture_insecure_transport.py`
above; match its established fixture pattern, which is presumably
parallel to `insecure_transport`'s given the parallel rule
implementations.

Run: `python -m pytest tests/test_posture_mcp_auto_approve.py -k "cursor_mcp_json_not_flagged or claude_mcp_json_still_flagged" -v`
Expected: FAIL — the Cursor manifest is still flagged (rule doesn't yet
skip non-Claude manifests).

- [x] **Step 9: Fix `mcp_auto_approve` to skip non-Claude-owned manifests**

In `tools/posture/rules/mcp_auto_approve.py`, add the skip at the top of
the per-manifest loop in `check_mcp_auto_approve` (after Step 7's
`_infer_hosts` change is already in this file):

```python
def check_mcp_auto_approve(
    manifests: list[tuple[Path, dict]],
) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for path, manifest in manifests:
        if owning_host(path) != "claude-code":
            # autoApprove is a Claude Code mcp.json convention with no
            # documented Cursor equivalent (verified against Cursor's
            # own MCP docs — approval there is Run-Modes/UI state).
            # A manifest belonging to another host carrying this key
            # isn't evidence of an active posture on that host.
            continue
        servers = _get_server_map(manifest)
        ...  # rest of the loop body unchanged
```

Run: `python -m pytest tests/test_posture_mcp_auto_approve.py -v`
Expected: PASS, full file green.

- [x] **Step 10: Run the full scan + posture + e2e suite**

Run: `python -m pytest tests/test_scan.py tests/test_posture_insecure_transport.py tests/test_posture_mcp_auto_approve.py tests/test_e2e.py tests/ -v`
Expected: PASS — every existing `openaca scan repo` invocation in the
test suite that doesn't pass `--host` now defaults to every registered
host (per Task 6/7's `hosts=None → all hosts` policy at the CLI
layer), but since no existing test fixture has a `.cursor/` directory,
output is unaffected for all of them.

- [x] **Step 11: Commit**

```bash
git add tools/scan.py tools/posture/rules/insecure_transport.py tools/posture/rules/mcp_auto_approve.py tests/test_scan.py tests/test_posture_insecure_transport.py tests/test_posture_mcp_auto_approve.py
git commit -m "feat(cli): add --host, filter posture collection by host, fix host attribution and Cursor auto-approve false positive"
```

---

## Task 9: Cross-layer e2e test — Cursor repo scan end to end

**Files:**
- Modify: `tests/test_e2e.py`

**Interfaces:** None new — this task only composes interfaces already produced by Tasks 2-8.

- [x] **Step 1: Write the test**

This is the one-screen test proving the whole layer wiring: parser →
graph → BOM → posture, together, per this repo's `tests/test_e2e.py`
convention. It combines two verification paths already established
elsewhere in this file: the BOM round-trip pattern (`build_agent_bom(...)
.to_cyclonedx()` → `component_refs_from_cyclonedx(bom)`, used at
`tests/test_e2e.py:993-998` for `runtime_hosts` provenance)
and the CLI JSON `findings` array (confirmed shape from Task 8) for the
posture-labeling half.

```python
def test_cursor_repo_scan_end_to_end(tmp_path):
    """A repo with Cursor MCP + Skills scans correctly by default: both
    surfaces are found, correctly host-tagged in the BOM, and posture
    rules label them as Cursor, not Claude Code."""
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]},
                    "insecure-api": {"url": "http://example.com/mcp"},
                    "git": {"command": "npx", "args": ["@cyanheads/git-mcp-server@1.1.0"]},
                }
            }
        )
    )
    skill_dir = cursor_dir / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: deploy things\n---\nrun the deploy\n"
    )

    # BOM layer: component discovered, correctly host-tagged.
    # build_agent_bom ignores its positional `refs` argument entirely
    # whenever `graph=` is also passed (it early-returns into
    # _build_agent_bom_from_graph(graph, ...) — confirmed by reading
    # tools/bom.py:138-166) — no need for tools.scan's private
    # _refs_from_graph helper here at all; pass [] for the ignored arg.
    graph = build_graph(tmp_path, mode="repo")  # --host omitted -> every host
    bom = build_agent_bom(
        [], target_type="repo", target=str(tmp_path), graph=graph
    ).to_cyclonedx()
    round_tripped = component_refs_from_cyclonedx(bom)
    weather = next(r for r in round_tripped if r.name == "weather-mcp")
    assert weather.extra["runtime_hosts"] == ["cursor"]
    skill = next(r for r in round_tripped if r.component_identity == "skill/deploy")
    assert skill.extra["runtime_hosts"] == ["cursor"]

    # CLI/posture layer: the insecure-transport rule labels the finding
    # as Cursor, not Claude Code (Task 8's fix).
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["scan", "repo", "--target", str(tmp_path), "--include-posture", "--format", "json"],
    )
    doc = json.loads(result.output)
    posture = [
        f for f in doc["findings"]
        if f.get("finding_type") == "posture" and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture
    assert posture[0]["active_in"] == ["cursor"]

    # OSV-matching layer: a Cursor-discovered component
    # must flow through vulnerability matching the same as a Claude one,
    # not just BOM/posture. Uses conftest's offline-OSV fixture map
    # (@cyanheads/git-mcp-server@1.1.0 -> GHSA-3q26-f695-pp76, already
    # relied on elsewhere in this file) rather than a live OSV.dev call.
    #
    # Filter by advisory ID, not component.name: finding_to_output()'s
    # component name comes from
    # component_name_for(), which prefers the last component_path entry
    # — the mcpServers dict *key* ("git", this fixture's server alias) —
    # over the package name ("git-mcp-server"), per
    # tools/finding_output.py:25-35. Filtering on "git-mcp-server" would
    # silently match nothing. The advisory ID is unambiguous and doesn't
    # depend on getting the display-name precedence rule right in the
    # test too.
    vuln_result = runner.invoke(
        main,
        ["scan", "repo", "--target", str(tmp_path), "--format", "json"],
    )
    vuln_doc = json.loads(vuln_result.output)
    vuln_findings = [
        f for f in vuln_doc["findings"]
        if f.get("finding_type") == "vulnerability" and f.get("id") == "GHSA-3q26-f695-pp76"
    ]
    assert vuln_findings
    assert vuln_findings[0]["active_in"] == ["cursor"]
```

Add a fourth `mcpServers` entry to the fixture written in this test —
`"git": {"command": "npx", "args": ["@cyanheads/git-mcp-server@1.1.0"]}`
— alongside `weather` and `insecure-api`, so the vulnerability-matching
assertion above has a real, offline-matchable package to find.

`build_agent_bom` and `component_refs_from_cyclonedx` are already
imported at the top of `tests/test_e2e.py` (confirmed in the file's
existing import block); add `from tools.graph_build import build_graph`
and `from tools.scan import main` if either isn't already imported
there. No import of `tools.scan._refs_from_graph` needed.

- [x] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_e2e.py::test_cursor_repo_scan_end_to_end -v`
Expected: PASS

- [x] **Step 3: Run the entire test suite one final time**

Run: `python -m pytest tests/ -v`
Expected: PASS, full suite green — this is the plan's final regression
gate.

- [x] **Step 4: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): Cursor repo scan end to end — MCP + Skills, correct host labeling"
```

---

## Task 10: Host-parameterize `claude_command_agent.py`

**Files:**
- Modify: `tools/parsers/claude_command_agent.py`
- Modify: `tools/parsers/__init__.py` (`_parse_repo_command`/`_parse_repo_agent` wrapper call sites — no behavior change, just confirm they still pass no `runtime_hosts` so the default holds)
- Modify: `tools/graph_build.py` (`descend()`'s `.md` dispatch call to `claude_command_agent.parse_file` — no behavior change yet, this task only adds the parameter)
- Test: `tests/test_parsers/test_claude_command_agent.py` (create if it doesn't exist — check first)

**Interfaces:**
- Consumes: nothing new.
- Produces: `parse_file(md_path: Path, kind: Kind, scope_owner: Optional[str] = None, runtime_hosts: list[str] | None = None) -> list[ComponentRef]` and `enumerate_dir(dir_path: Path, kind: Kind, scope_owner: Optional[str], runtime_hosts: list[str] | None = None) -> list[ComponentRef]` — `runtime_hosts=None` means the `extra` dict carries **no** `runtime_hosts` key at all, preserving every existing call site's exact output byte-for-byte (today's refs carry no such key); the key is set only when a caller explicitly passes a list. There is no internal resolution of `None` to `["claude-code"]` anywhere. Tasks 11 and 12 are the first callers to actually pass `runtime_hosts=["cursor"]`.

- [ ] **Step 1: Check for an existing test file, read the current parser in full**

```bash
ls tests/test_parsers/test_claude_command_agent.py 2>/dev/null || echo "does not exist"
```

Read `tools/parsers/claude_command_agent.py` in full before editing — the goal is to add one parameter cleanly, not restructure. Confirm `parse_file`'s current signature and the `ComponentRef(...)` construction site at the point `extra={"scope_owner": scope_owner, "component_type": kind}` is built.

- [ ] **Step 2: Write the failing test — `runtime_hosts` is threaded into `extra`, defaults preserve current output**

```python
# tests/test_parsers/test_claude_command_agent.py
from pathlib import Path

from tools.parsers.claude_command_agent import parse_file

def test_parse_file_default_omits_runtime_hosts_key(tmp_path):
    md = tmp_path / "deploy.md"
    md.write_text("run\n")
    refs = parse_file(md, kind="command")
    assert "runtime_hosts" not in refs[0].extra

def test_parse_file_explicit_runtime_hosts_cursor(tmp_path):
    md = tmp_path / "deploy.md"
    md.write_text("run\n")
    refs = parse_file(md, kind="command", runtime_hosts=["cursor"])
    assert refs[0].extra["runtime_hosts"] == ["cursor"]

def test_parse_file_explicit_runtime_hosts_claude_code_matches_default(tmp_path):
    md = tmp_path / "deploy.md"
    md.write_text("run\n")
    default_refs = parse_file(md, kind="command")
    explicit_refs = parse_file(md, kind="command", runtime_hosts=["claude-code"])
    assert explicit_refs[0].extra["runtime_hosts"] == ["claude-code"]
    # The two calls differ ONLY in whether the key is present — same host,
    # explicit vs. default, must not otherwise diverge.
    default_extra = dict(default_refs[0].extra)
    explicit_extra = dict(explicit_refs[0].extra)
    explicit_extra.pop("runtime_hosts")
    assert default_extra == explicit_extra
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsers/test_claude_command_agent.py -v`
Expected: FAIL — `parse_file() got an unexpected keyword argument 'runtime_hosts'`

- [ ] **Step 4: Add the parameter**

In `tools/parsers/claude_command_agent.py`, add `runtime_hosts: Optional[list[str]] = None` to both `parse_file` and `enumerate_dir`. In `parse_file`, only set the `extra` key when a caller actually passed one:

```python
def parse_file(
    md_path: Path,
    kind: Kind,
    scope_owner: Optional[str] = None,
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    if not md_path.is_file() or md_path.suffix != ".md":
        return []
    frontmatter = _read_frontmatter(md_path)
    name = _resolve_name(md_path, frontmatter)
    ecosystem = f"claude-{kind}"
    identity = (
        f"{ecosystem}/{scope_owner}/{name}" if scope_owner is not None else f"{ecosystem}/{name}"
    )
    extra: dict = {"scope_owner": scope_owner, "component_type": kind}
    if runtime_hosts is not None:
        extra["runtime_hosts"] = runtime_hosts
    parent = ComponentRef(
        name=name,
        component_identity=identity,
        source_manifest=str(md_path),
        source_locator="$",
        extra=extra,
    )
    refs = [parent]
    if kind == "agent" and scope_owner is None:
        refs.extend(_agent_frontmatter_child_refs(md_path, frontmatter))
    return refs
```

`enumerate_dir` just forwards the parameter to each `parse_file` call:

```python
def enumerate_dir(
    dir_path: Path,
    kind: Kind,
    scope_owner: Optional[str],
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    if not dir_path.is_dir():
        return []
    refs: list[ComponentRef] = []
    for child in sorted(dir_path.rglob("*.md")):
        if not child.is_file() or child.suffix != ".md":
            continue
        refs.extend(
            parse_file(child, kind=kind, scope_owner=scope_owner, runtime_hosts=runtime_hosts)
        )
    return refs
```

**Do not default `runtime_hosts` to `["claude-code"]` in the signature itself** (i.e., not `runtime_hosts: list[str] = ["claude-code"]` and not resolving `None` to `["claude-code"]` inside the function) — Step 2's first test requires the key to be *absent* on a no-argument call, matching today's exact output byte-for-byte. Tasks 11/12 are responsible for passing `runtime_hosts=["claude-code"]` explicitly at their own new call sites where the key needs to be present alongside Cursor's.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsers/test_claude_command_agent.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full parser + graph_build suites to confirm no regression**

Run: `uv run pytest tests/test_parsers/ tests/test_graph_build.py -v`
Expected: PASS, no changes in count — every existing call site (`_parse_repo_command`, `_parse_repo_agent` in `tools/parsers/__init__.py`; `descend()`'s `.md` dispatch and `_add_endpoint_command_agents` in `tools/graph_build.py`; `_parse_bundled_command_agents` in `tools/parsers/claude_plugin_root.py`) calls with no `runtime_hosts` argument, so every existing ref's `extra` dict is unchanged.

- [ ] **Step 7: Commit**

```bash
git add tools/parsers/claude_command_agent.py tests/test_parsers/test_claude_command_agent.py
git commit -m "feat(parsers): thread runtime_hosts through claude_command_agent's parse_file/enumerate_dir"
```

---

## Task 11: Cursor Commands — registry entry and unified graph dispatch

**Files:**
- Modify: `tools/parsers/__init__.py` (`CURSOR_MANIFEST_REGISTRY`)
- Modify: `tools/graph_build.py` (replace `_command_agent_kind`'s hardcoded `.claude` check, for **commands only**, with registry-driven dispatch)
- Test: `tests/test_parsers/test_registry.py`, `tests/test_graph_build.py`

**Interfaces:**
- Consumes: `claude_command_agent.parse_file`/`enumerate_dir` with `runtime_hosts` (Task 10); `registry_pattern_matches` (already public, from the MCP/Skills unification in Task 6/7 above).
- Produces: `.cursor/commands/*.md` real Cursor `command` refs, both in `parse_repo_grouped`'s accounting and `descend()`'s graph.

- [ ] **Step 1: Read `_command_agent_kind` and its call site in full**

```bash
grep -n "_command_agent_kind\|_COMMAND_AGENT_SURFACES" tools/graph_build.py
```

Confirm the exact current mechanism: `_command_agent_kind(path, root)` walks `path.relative_to(root).parts` looking for `.claude/commands` or `.claude/agents` at any depth, hardcoded to the literal string `".claude"`. This is a **second, independent** dispatch mechanism from the registry (`CLAUDE_CODE_MANIFEST_REGISTRY` already has `("**/.claude/commands/**/*.md", _parse_repo_command)` for accounting) — the same registry/graph duplication MCP and Skills had before their unification in Task 6/7 above, just never closed for Commands/Agents. This task closes it for **Commands only** — Agents/Subagents gets its own resolver in Task 12 and is explicitly **not** touched by this task's registry-unification change (Agents keeps using `_command_agent_kind`'s `.claude`-only path until Task 12 replaces that half separately, so this task's diff must not alter agent dispatch at all).

- [ ] **Step 2: Write the failing test — Cursor commands are accounted for**

```python
# tests/test_parsers/test_registry.py

def test_cursor_commands_registered_and_discovered(tmp_path):
    cursor_commands = tmp_path / ".cursor" / "commands"
    cursor_commands.mkdir(parents=True)
    (cursor_commands / "deploy.md").write_text("run\n")
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert grouped[0][1][0].extra["runtime_hosts"] == ["cursor"]
    assert grouped[0][1][0].extra["component_type"] == "command"
```

```python
# tests/test_graph_build.py

def test_repo_cursor_commands_are_graph_discoverable(tmp_path):
    cursor_commands = tmp_path / ".cursor" / "commands"
    cursor_commands.mkdir(parents=True)
    (cursor_commands / "deploy.md").write_text("run\n")
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    command_nodes = [n for n in g.nodes.values() if n.kind == "command"]
    assert len(command_nodes) == 1
    assert command_nodes[0].ref is not None
    assert command_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]

def test_repo_claude_commands_unaffected_by_cursor_dispatch_change(tmp_path):
    claude_commands = tmp_path / ".claude" / "commands"
    claude_commands.mkdir(parents=True)
    (claude_commands / "deploy.md").write_text("run\n")
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    command_nodes = [n for n in g.nodes.values() if n.kind == "command"]
    assert len(command_nodes) == 1
    assert command_nodes[0].ref is not None
    assert "runtime_hosts" not in command_nodes[0].ref.extra
```

The third test is the regression guard: Claude's existing commands must still dispatch correctly and — matching Task 10's Step 4 constraint — carry **no** `runtime_hosts` key at all (not `["claude-code"]`), since the registry-driven dispatch for Commands must pass `runtime_hosts=["claude-code"]` explicitly only when going through the *Cursor* registry entry's pre-bound parser; Claude's own registry entry (`_parse_repo_command`, unchanged) still calls with no `runtime_hosts` argument.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsers/test_registry.py -k cursor_commands -v tests/test_graph_build.py -k cursor_commands -v`
Expected: FAIL — no Cursor commands entry exists yet, `n_found == 0` / no command nodes.

- [ ] **Step 4: Add the `CURSOR_MANIFEST_REGISTRY` entry**

In `tools/parsers/__init__.py`, extend `CURSOR_MANIFEST_REGISTRY` (verify the exact current entries — `.cursor/mcp.json`, the two skills patterns — before editing, per the plan's own history of implementers finding the plan's transcribed contents slightly stale):

```python
CURSOR_MANIFEST_REGISTRY: list[tuple[str, ParserFn]] = [
    (".cursor/mcp.json", functools.partial(mcp_json.parse, runtime_hosts=["cursor"])),
    (
        "**/.cursor/skills/*/SKILL.md",
        functools.partial(claude_skill.parse, runtime_hosts=["cursor"]),
    ),
    (
        "**/.agents/skills/*/SKILL.md",
        functools.partial(claude_skill.parse, runtime_hosts=["cursor"]),
    ),
    (
        "**/.cursor/commands/**/*.md",
        functools.partial(
            claude_command_agent.parse_file, kind="command", runtime_hosts=["cursor"]
        ),
    ),
]
```

`claude_command_agent.parse_file`'s signature is `(md_path, kind, scope_owner=None, runtime_hosts=None)` — `functools.partial` binds `kind` and `runtime_hosts` by keyword, leaving `md_path` as the only positional argument the registry dispatch loop passes, matching the single-`Path`-argument `ParserFn` shape every other registry entry uses. Add `claude_command_agent` to this module's imports if not already present (it's already imported for `_parse_repo_command`/`_parse_repo_agent`).

- [ ] **Step 5: Extend the pattern-matching allowlist and dispatch for Commands, without touching Agents**

Read `_MCP_REGISTRY_PATTERNS`/`_SKILL_REGISTRY_PATTERNS` and `_mcp_parser_for_path`/`_skill_parser_for_path` in `tools/graph_build.py` first — this task follows the exact same shape for Commands. Add:

```python
_COMMAND_REGISTRY_PATTERNS = frozenset({"**/.claude/commands/**/*.md", "**/.cursor/commands/**/*.md"})

def _command_parser_for_path(path: Path, root: Path, hosts: list[str]) -> ParserFn | None:
    """First selected host's manifest_registry entry whose pattern is
    command-shaped and matches `path`, or None."""
    from tools.hosts import HOSTS

    for host_id in hosts:
        adapter = HOSTS.get(host_id)
        if adapter is None:
            continue
        for pattern, parser in adapter.manifest_registry:
            if pattern not in _COMMAND_REGISTRY_PATTERNS:
                continue
            if registry_pattern_matches(path, root, pattern):
                return parser
    return None
```

In `descend()`'s `.md`-suffix branch (the code around today's `_command_agent_kind(path, directory)` call, `graph_build.py` ~line 1483), split the dispatch: try `_command_parser_for_path` first for the command case; fall back to the existing `_command_agent_kind`-based path **only for `kind == "agent"`**, since Task 12 replaces the agent half separately. Concretely — read the exact surrounding code in `descend()` first (it currently does `kind = _command_agent_kind(path, directory)` then `refs = claude_command_agent.parse_file(path, kind=kind)`), and restructure so:

1. If `_command_parser_for_path(path, directory, hosts)` returns a parser (registry-driven, catches both `.claude/commands` and `.cursor/commands`), call it and use its output.
2. Otherwise, fall back to `_command_agent_kind(path, directory)` — which after this task should have its `"commands"` entry in `_COMMAND_AGENT_SURFACES` **removed** (Commands is now registry-driven only) so this fallback path becomes agent-only. Update `_COMMAND_AGENT_SURFACES` to `(("agents", "agent"),)`.

This keeps the two dispatch paths cleanly separated: Commands is fully registry-driven (like MCP/Skills), Agents still uses the legacy path-walking mechanism until Task 12 replaces it with the precedence-aware resolver.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsers/test_registry.py -k cursor_commands -v tests/test_graph_build.py -k command -v`
Expected: PASS (all 3 new tests, plus every existing command-related test unaffected).

- [ ] **Step 7: Run the full graph_build and registry suites**

Run: `uv run pytest tests/test_graph_build.py tests/test_parsers/ -v`
Expected: PASS, no regressions — this is the step most likely to surface an agent-dispatch break if Step 5's split accidentally touched the agent fallback path.

- [ ] **Step 8: Commit**

```bash
git add tools/parsers/__init__.py tools/graph_build.py tests/test_parsers/test_registry.py tests/test_graph_build.py
git commit -m "feat: recognize Cursor commands in repo-mode manifest accounting and graph construction"
```

---

## Task 12: Subagents — precedence-aware occurrence resolver

**Files:**
- Create: `tools/subagent_precedence.py`
- Modify: `tools/parsers/__init__.py` (`parse_repo_grouped` — add the resolver to the accounting pass)
- Modify: `tools/graph_build.py` (`descend()` — remove the `"agents"` entry from `_COMMAND_AGENT_SURFACES`'s old fallback, replace with a call to the new resolver)
- Test: `tests/test_subagent_precedence.py`, `tests/test_parsers/test_registry.py`, `tests/test_graph_build.py`

**Interfaces:**
- Consumes: `claude_command_agent.parse_file` with `runtime_hosts` (Task 10).
- Produces: `resolve_subagent_occurrences(root: Path, hosts: list[str]) -> list[ComponentRef]` — discovers every `.claude/agents/**/*.md` and `.cursor/agents/**/*.md` at **any depth** under `root` (matching the `**/`-prefixed registry patterns every other surface uses), groups them into scopes (a scope is the directory containing the `.claude`/`.cursor` dir — `root` itself, or `packages/frontend`, etc.), applies the precedence rule per scope, and returns the correctly-tagged refs. Discovery and precedence are two separate functions: `_discover_subagent_scopes(root)` (pure filesystem walk, host-selection-independent) and `_occurrences_for_scope(claude_files, cursor_files, hosts)` (pure precedence logic, no filesystem). Both `parse_repo_grouped` and `descend()` call `resolve_subagent_occurrences` instead of the old `_command_agent_kind`/`claude_command_agent.enumerate_dir` path for agents.
- Also produces: `resolve_subagent_occurrences_for_dirs(claude_agents_dir: Path | None, cursor_agents_dir: Path | None, hosts: list[str]) -> list[ComponentRef]` — the explicit-dirs entry point for one scope whose agents directories the caller already knows. Endpoint mode needs it (Task 15): endpoint config roots are arbitrary paths (`--config-dir /fixture/cursor`), so their `agents/` dirs can't be rediscovered by the dot-directory walk. It collects each named dir's `*.md` files and delegates to the same `_occurrences_for_scope` — one precedence implementation, two discovery front-ends.

**Host-selection semantics — the file walk is unconditional, only the emitted occurrences depend on selection.** Cursor's compatibility read of `.claude/agents/` is unconditional (spec, Subagents section), so a Cursor-only scan (`hosts=["cursor"]`) must still read `.claude/agents/` files — they are part of Cursor's selected surface. Selection gates which *occurrences* come out, never which directories get walked:

| File | Override exists | Selection | Occurrences emitted |
|---|---|---|---|
| `.claude/agents/<rel>.md` | no | claude + cursor | one, `runtime_hosts=["claude-code", "cursor"]` |
| `.claude/agents/<rel>.md` | no | claude only | one, **no `runtime_hosts` key** (byte-identical to today's registry-path output) |
| `.claude/agents/<rel>.md` | no | cursor only | one, `runtime_hosts=["cursor"]` — Cursor genuinely reads this file |
| `.claude/agents/<rel>.md` | yes | claude + cursor | one, `runtime_hosts=["claude-code"]` |
| `.claude/agents/<rel>.md` | yes | claude only | one, **no `runtime_hosts` key** — see below |
| `.claude/agents/<rel>.md` | yes | claude not selected | none |
| `.cursor/agents/<rel>.md` | — | cursor selected | one, `runtime_hosts=["cursor"]` |
| `.cursor/agents/<rel>.md` | — | cursor not selected | none |

Both claude-only rows deliberately omit the key rather than tagging `["claude-code"]` — same convention as Task 10/11: the no-Cursor-involved path must preserve today's exact `extra` dict. Override splitting is Cursor-involved logic and applies **only when Cursor is selected**: with `hosts=["claude-code"]`, a sibling `.cursor/agents/<rel>.md` merely existing on disk must not change the Claude occurrence's output in any way — the unselected host's file is invisible, and the Claude file is emitted through the legacy no-`runtime_hosts` call regardless of siblings.

- [ ] **Step 1: Write the failing test for the resolver's core logic — no override**

```python
# tests/test_subagent_precedence.py
from pathlib import Path

from tools.subagent_precedence import resolve_subagent_occurrences

def test_claude_only_agent_both_hosts_selected_single_occurrence_dual_host(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nhelp\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    assert len(refs) == 1
    assert refs[0].extra["runtime_hosts"] == ["claude-code", "cursor"]

def test_claude_only_agent_cursor_not_selected_output_unchanged_from_today(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nhelp\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code"])
    assert len(refs) == 1
    assert "runtime_hosts" not in refs[0].extra

def test_claude_only_selection_with_cursor_sibling_output_unchanged(tmp_path):
    # A .cursor/agents override merely existing on disk must not change
    # Claude-only output: override splitting is Cursor-involved logic,
    # applied only when Cursor is selected. Byte-compat with today's
    # key-less extra dict.
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nc\n")
    cursor_agents = tmp_path / ".cursor" / "agents"
    cursor_agents.mkdir(parents=True)
    (cursor_agents / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code"])
    assert len(refs) == 1
    assert refs[0].source_manifest.endswith(".claude/agents/helper.md")
    assert "runtime_hosts" not in refs[0].extra

def test_cursor_only_scan_still_reads_claude_agents_compatibility(tmp_path):
    # Cursor's compatibility read is unconditional: a Cursor-only scan must
    # surface a .claude/agents file as a cursor-readable occurrence.
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nhelp\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["cursor"])
    assert len(refs) == 1
    assert refs[0].extra["runtime_hosts"] == ["cursor"]

def test_cursor_only_scan_override_suppresses_claude_copy(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nc\n")
    cursor_agents = tmp_path / ".cursor" / "agents"
    cursor_agents.mkdir(parents=True)
    (cursor_agents / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["cursor"])
    assert len(refs) == 1
    assert refs[0].source_manifest.endswith(".cursor/agents/helper.md")
    assert refs[0].extra["runtime_hosts"] == ["cursor"]

def test_override_present_two_single_host_occurrences(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nclaude version\n")
    cursor_agents = tmp_path / ".cursor" / "agents"
    cursor_agents.mkdir(parents=True)
    (cursor_agents / "helper.md").write_text("---\nname: helper\n---\ncursor version\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    assert len(refs) == 2
    by_host = {tuple(r.extra["runtime_hosts"]): r for r in refs}
    assert by_host[("claude-code",)].source_manifest.endswith(".claude/agents/helper.md")
    assert by_host[("cursor",)].source_manifest.endswith(".cursor/agents/helper.md")

def test_cursor_only_agent_no_claude_counterpart(tmp_path):
    cursor_agents = tmp_path / ".cursor" / "agents"
    cursor_agents.mkdir(parents=True)
    (cursor_agents / "solo.md").write_text("---\nname: solo\n---\ncursor only\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    assert len(refs) == 1
    assert refs[0].extra["runtime_hosts"] == ["cursor"]

def test_nested_relative_path_override_matched_correctly(tmp_path):
    # Same relative path under a nested project root, not just top-level.
    nested = tmp_path / "packages" / "frontend"
    (nested / ".claude" / "agents").mkdir(parents=True)
    (nested / ".claude" / "agents" / "helper.md").write_text("---\nname: helper\n---\nc\n")
    (nested / ".cursor" / "agents").mkdir(parents=True)
    (nested / ".cursor" / "agents" / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    assert len(refs) == 2

def test_override_scopes_are_independent(tmp_path):
    # An override in one scope must not affect pairing in another: top-level
    # .claude/agents/helper.md has NO top-level .cursor override, so it stays
    # dual-host even though a nested scope has its own override pair.
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "helper.md").write_text("---\nname: helper\n---\nc\n")
    nested = tmp_path / "packages" / "frontend"
    (nested / ".claude" / "agents").mkdir(parents=True)
    (nested / ".claude" / "agents" / "helper.md").write_text("---\nname: helper\n---\nc\n")
    (nested / ".cursor" / "agents").mkdir(parents=True)
    (nested / ".cursor" / "agents" / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    hosts_by_manifest = {r.source_manifest: r.extra.get("runtime_hosts") for r in refs}
    top = str(tmp_path / ".claude" / "agents" / "helper.md")
    assert hosts_by_manifest[top] == ["claude-code", "cursor"]
    assert len(refs) == 3

def test_explicit_dirs_entry_point_ignores_directory_basenames(tmp_path):
    # Endpoint config roots are arbitrary paths — no `.claude`/`.cursor`
    # naming to discover from. The explicit-dirs entry point must pair by
    # relative path across whatever dirs the caller names.
    from tools.subagent_precedence import resolve_subagent_occurrences_for_dirs

    claude_dir = tmp_path / "claude-install" / "agents"
    claude_dir.mkdir(parents=True)
    (claude_dir / "helper.md").write_text("---\nname: helper\n---\nc\n")
    cursor_dir = tmp_path / "cursor-install" / "agents"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences_for_dirs(
        claude_dir, cursor_dir, hosts=["claude-code", "cursor"]
    )
    assert len(refs) == 2
    assert {tuple(r.extra["runtime_hosts"]) for r in refs} == {("claude-code",), ("cursor",)}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_subagent_precedence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.subagent_precedence'`

- [ ] **Step 3: Implement the resolver**

```python
# tools/subagent_precedence.py
"""Precedence-aware occurrence resolution for Claude/Cursor subagents.

Cursor's subagent compatibility read is unconditional: a `.claude/agents/*.md`
file is genuinely readable by Cursor with no `.cursor/agents/` copy, UNLESS
Cursor has its own same-relative-path override, in which case Cursor never
reads Claude's file at all (confirmed against Cursor's own subagent docs;
see docs/specs/multi-host-support.md's Subagents section and ADR-0045
Decision #4). This can't be expressed through the registry/pattern-matcher
mechanism the rest of this design uses, since it requires inspecting a
sibling path before deciding one file's occurrence count. "Same subagent" is
matched by relative file path under the agents directory, not frontmatter
`name:` (ADR-0045 Decision #4) — the more literal, verifiable reading of
Cursor's own "same name" wording.
"""

from __future__ import annotations

from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers import claude_command_agent

def _discover_subagent_scopes(root: Path) -> dict[Path, dict[str, dict[Path, Path]]]:
    """Map each scope dir (the directory containing `.claude`/`.cursor`) to
    {host_dir_name: {relative_path: absolute_file}}. Pure discovery — walks
    every depth, independent of host selection (Cursor's compatibility read
    means `.claude/agents/` files are part of Cursor's surface too)."""
    scopes: dict[Path, dict[str, dict[Path, Path]]] = {}
    for host_dir in (".claude", ".cursor"):
        for agents_dir in sorted(root.glob(f"**/{host_dir}/agents")):
            if not agents_dir.is_dir():
                continue
            scope = agents_dir.parent.parent
            files = {
                md.relative_to(agents_dir): md
                for md in sorted(agents_dir.rglob("*.md"))
                if md.is_file()
            }
            if files:
                scopes.setdefault(scope, {})[host_dir] = files
    return scopes

def _occurrences_for_scope(
    claude_files: dict[Path, Path],
    cursor_files: dict[Path, Path],
    hosts: list[str],
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    claude_selected = "claude-code" in hosts
    cursor_selected = "cursor" in hosts

    for rel, claude_path in claude_files.items():
        # Override splitting only applies when Cursor is selected: with
        # Cursor unselected, a sibling .cursor/agents file must not perturb
        # the legacy Claude-only output produced below.
        override_exists = rel in cursor_files and cursor_selected
        if override_exists:
            if claude_selected:
                refs.extend(
                    claude_command_agent.parse_file(
                        claude_path, kind="agent", runtime_hosts=["claude-code"]
                    )
                )
        elif claude_selected and cursor_selected:
            refs.extend(
                claude_command_agent.parse_file(
                    claude_path, kind="agent", runtime_hosts=["claude-code", "cursor"]
                )
            )
        elif cursor_selected:
            refs.extend(
                claude_command_agent.parse_file(
                    claude_path, kind="agent", runtime_hosts=["cursor"]
                )
            )
        elif claude_selected:
            refs.extend(claude_command_agent.parse_file(claude_path, kind="agent"))

    if cursor_selected:
        for cursor_path in cursor_files.values():
            refs.extend(
                claude_command_agent.parse_file(
                    cursor_path, kind="agent", runtime_hosts=["cursor"]
                )
            )

    return refs

def resolve_subagent_occurrences(root: Path, hosts: list[str]) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    for scope in sorted(_discover_subagent_scopes(root)):
        host_files = _discover_subagent_scopes(root)[scope]
        refs.extend(
            _occurrences_for_scope(
                host_files.get(".claude", {}), host_files.get(".cursor", {}), hosts
            )
        )
    return refs

def _agent_files(agents_dir: Path | None) -> dict[Path, Path]:
    if agents_dir is None or not agents_dir.is_dir():
        return {}
    return {
        md.relative_to(agents_dir): md
        for md in sorted(agents_dir.rglob("*.md"))
        if md.is_file()
    }

def resolve_subagent_occurrences_for_dirs(
    claude_agents_dir: Path | None,
    cursor_agents_dir: Path | None,
    hosts: list[str],
) -> list[ComponentRef]:
    """One scope, explicitly-named agents dirs — for callers (endpoint mode)
    whose config roots are arbitrary paths the dot-directory walk can't find."""
    return _occurrences_for_scope(
        _agent_files(claude_agents_dir), _agent_files(cursor_agents_dir), hosts
    )
```

(Call `_discover_subagent_scopes` once and iterate its items rather than the double call shown compressed above — the sketch shows the data flow, the implementation should bind it to one variable.) The claude-file branches encode the selection table from the Interfaces section above: the file walk never depends on selection; the *cursor-only* selection still emits `.claude/agents/` files (as `["cursor"]` occurrences, or not at all when a `.cursor` override shadows them), and the *claude-only* selection emits them with no `runtime_hosts` key, byte-identical to today's registry-path output. Repo-mode gitignore filtering: `descend()`/`parse_repo_grouped` already skip ignored files before dispatch; when wiring in Steps 5-6, apply the same root-spec `is_ignored` filter to the resolver's discovered files (pass the root spec in, or filter the returned refs by manifest path) so an ignored agents dir doesn't newly appear — verify against how the current `.claude/agents` registry entry is filtered today and match it exactly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_subagent_precedence.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Wire the resolver into `parse_repo_grouped` (manifest accounting)**

Read `parse_repo_grouped` in `tools/parsers/__init__.py` in full first. Find where it currently discovers `.claude/agents/**/*.md` via `CLAUDE_CODE_MANIFEST_REGISTRY`'s `"**/.claude/agents/**/*.md"` entry — this task removes that registry entry (agents are no longer registry-driven) and instead calls `resolve_subagent_occurrences(target, hosts)` once, adding its results directly to the grouped output. Remove `("**/.claude/agents/**/*.md", _parse_repo_agent)` from `CLAUDE_CODE_MANIFEST_REGISTRY` and call the resolver explicitly inside `parse_repo_grouped`, folding its refs into the same grouped-by-manifest structure the registry loop produces (group by each ref's `source_manifest`, matching the existing grouping convention).

- [ ] **Step 6: Wire the resolver into `descend()` (graph placement)**

In `tools/graph_build.py`'s `descend()`, replace the agent half of the old `_command_agent_kind` fallback (left in place for commands' removal in Task 11, now also removed for agents) with a direct call: at the point `descend()` begins processing `directory`, if `directory` is the scan root (or, more precisely, wherever the existing `.claude/agents` walk was triggered from — verify against the current code before restructuring), call `resolve_subagent_occurrences(root_dir, hosts)` once and add each ref as a child of the appropriate parent node the same way other direct components attach. After this step, `_command_agent_kind` and `_COMMAND_AGENT_SURFACES` should have no remaining callers or entries — delete both if so (confirm via grep before deleting).

- [ ] **Step 7: Write the registry/graph consistency tests**

```python
# tests/test_parsers/test_registry.py

def test_parse_repo_grouped_subagent_precedence_no_override(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nh\n")
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert grouped[0][1][0].extra["runtime_hosts"] == ["claude-code", "cursor"]
```

```python
# tests/test_graph_build.py

def test_repo_subagent_precedence_matches_registry_accounting(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nh\n")
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    agent_nodes = [n for n in g.nodes.values() if n.kind == "agent"]
    assert len(agent_nodes) == 1
    assert agent_nodes[0].ref is not None
    assert agent_nodes[0].ref.extra["runtime_hosts"] == ["claude-code", "cursor"]
```

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest tests/ -v -k "agent or subagent"`
Expected: PASS, including every pre-existing agent test (endpoint mode's agent handling is untouched by this task — Task 15's cross-host subagent pass wires endpoint mode's use of this same resolver).

- [ ] **Step 9: Commit**

```bash
git add tools/subagent_precedence.py tools/parsers/__init__.py tools/graph_build.py tests/
git commit -m "feat: precedence-aware Subagent occurrence resolution for Cursor's .claude/agents compatibility read"
```

---

## Task 13: Cursor Plugins (native format)

**Files:**
- Modify: `tools/parsers/claude_plugin.py` (host-parameterize `parse()`)
- Modify: `tools/parsers/claude_plugin_root.py` (thread manifest context — `runtime_hosts`, the real `plugin_json_path`, and the format-specific default MCP filename — through `walk_plugin_root` and every helper it calls)
- Modify: `tools/parsers/hooks_json.py` (format-aware plugin-hook parsing: `runtime_hosts` plus a per-format identity scheme — the module contract is Claude-only today)
- Modify: `tools/parsers/__init__.py` (`CURSOR_MANIFEST_REGISTRY` — add `.cursor-plugin/plugin.json`)
- Test: `tests/test_parsers/test_claude_plugin.py`, `tests/test_parsers/test_hooks_json.py`, `tests/test_parsers/test_registry.py`, `tests/test_graph_build.py`

**Interfaces:**
- Consumes: `mcp_json.parse`/`parse_mcp_servers`, `claude_skill.parse`, `claude_command_agent.parse_file` — all `runtime_hosts`-aware from prior tasks — and `hooks_json.parse_plugin_hooks`/`parse_plugin_hooks_inline`, which this task makes format-aware (they are Claude-only today: no `runtime_hosts`, Claude-scoped module docstring, hardcoded `claude-hook/` identity scheme).
- Produces: `walk_plugin_root(plugin_root, *, plugin_name, plugin_data, plugin_json_path=None, runtime_hosts=None) -> list[ComponentRef]` — every ref it produces (self-identity, dependencies, bundled skills/commands/agents/hooks/MCP) carries the given `runtime_hosts`, defaulting to `["claude-code"]` when omitted.

- [ ] **Step 1: Read `claude_plugin.py` and `claude_plugin_root.py` in full**

Confirm the exact current signatures of `walk_plugin_root` and its private helpers (`_parse_manifest_refs`, `_parse_default_mcp`, `_parse_bundled_skills`, `_parse_bundled_hooks`, `_parse_bundled_command_agents`, `_enumerate_bundled_command_agent_dir`) before editing — this task threads manifest context (`runtime_hosts`, plus the format-sensitive items Step 5 names) through them without restructuring the walk itself. Note two Claude-specific assumptions baked into the current walker that the threading must remove, because both break for a `.cursor-plugin` manifest: `_parse_default_mcp` resolves only `.mcp.json` (Claude's default bundled MCP filename — Cursor's verified default is root `mcp.json`, per the spec's Plugins section), and `_parse_bundled_hooks` internally constructs `plugin_root / ".claude-plugin" / "plugin.json"` as the inline-hooks source manifest (a path that does not exist for a Cursor plugin). A third Claude-specific assumption lives one module over: `tools/parsers/hooks_json.py` is a Claude-contract parser end to end — module docstring, PascalCase event examples, and a hardcoded `claude-hook/<kind>:<digest>` identity scheme — and Step 5 makes it format-aware rather than silently feeding Cursor input through a Claude contract.

- [ ] **Step 2: Verify the native plugin-bundled hooks contract against Cursor's documentation**

The shape of a native Cursor Plugin's bundled hooks — the standalone `hooks/hooks.json` wrapper, the inline `plugin.json.hooks` block, and the per-entry field set — is an external contract this task's fixtures and parser depend on. Already verified (spec, Hooks section): the event vocabulary is camelCase and disjoint from Claude's (`preToolUse`, `postToolUse`, `postToolUseFailure`, `beforeSubmitPrompt`, `stop`, plus agent-lifecycle events), the `{event: [entry, ...]}` array shape is shared with Claude, and relative command paths resolve from the declaring file's own location. **Not yet verified:** the exact per-entry fields (whether Claude's `type`/`matcher` exist for Cursor, and what Cursor-only fields do) and whether the bundled standalone file uses Claude's `{"hooks": {...}}` envelope or a bare `{event: [entries]}` root. Before writing the hook fixtures, check Cursor's plugin reference documentation for both, and record what was confirmed in this task's commit message body. If either diverges from Claude's shape in a way the permissive shared walk cannot absorb (e.g. a different command-field name — the identity hash reads `command`), stop and update the spec's Hooks section and this task before proceeding. The vocabulary is settled either way: every Cursor hook fixture in this task uses real camelCase Cursor event names (`postToolUse`), never Claude's PascalCase.

- [ ] **Step 3: Write the failing test — identity and host-tagging**

```python
# tests/test_parsers/test_claude_plugin.py

def test_cursor_plugin_uses_unqualified_identity_not_host_qualified(tmp_path):
    plugin_dir = tmp_path / ".cursor-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')
    from tools.parsers.claude_plugin import parse

    refs = parse(plugin_dir / "plugin.json", runtime_hosts=["cursor"])
    self_ref = next(r for r in refs if r.extra.get("component_type") == "plugin")
    assert self_ref.component_identity == "plugin/demo"
    assert self_ref.extra["runtime_hosts"] == ["cursor"]

def test_cursor_plugin_bundled_skill_tagged_cursor(tmp_path):
    plugin_dir = tmp_path / ".cursor-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')
    skills_dir = tmp_path / "skills" / "helper"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: helper\ndescription: d\n---\nrun\n")
    from tools.parsers.claude_plugin import parse

    refs = parse(plugin_dir / "plugin.json", runtime_hosts=["cursor"])
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert len(skill_refs) == 1
    assert skill_refs[0].extra["runtime_hosts"] == ["cursor"]

def test_cursor_plugin_default_mcp_is_root_mcp_json(tmp_path):
    # Cursor's default bundled MCP manifest is root `mcp.json`; Claude's
    # `.mcp.json` filename must NOT be read in a Cursor bundle.
    plugin_dir = tmp_path / ".cursor-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"decoy": {"command": "npx", "args": ["decoy-mcp@1.0.0"]}}}'
    )
    from tools.parsers.claude_plugin import parse

    refs = parse(plugin_dir / "plugin.json", runtime_hosts=["cursor"])
    mcp_names = {r.name for r in refs if r.extra.get("component_type") == "mcp_server"}
    assert mcp_names == {"weather"}

def test_claude_plugin_default_mcp_still_dot_mcp_json(tmp_path):
    # The mirror case: a Claude plugin reads `.mcp.json` only; a stray root
    # `mcp.json` is not its default bundled manifest.
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"decoy": {"command": "npx", "args": ["decoy-mcp@1.0.0"]}}}'
    )
    from tools.parsers.claude_plugin import parse

    refs = parse(plugin_dir / "plugin.json")
    mcp_names = {r.name for r in refs if r.extra.get("component_type") == "mcp_server"}
    assert mcp_names == {"weather"}

def test_cursor_plugin_inline_hooks_sourced_from_cursor_manifest(tmp_path):
    # Inline hooks must carry the real manifest as source_manifest — the
    # walker's historical `.claude-plugin/plugin.json` construction would
    # fabricate a path that does not exist for a Cursor plugin.
    plugin_dir = tmp_path / ".cursor-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        '{"name": "demo", "hooks": {"postToolUse": [{"command": "echo done"}]}}'
    )
    from tools.parsers.claude_plugin import parse

    refs = parse(plugin_dir / "plugin.json", runtime_hosts=["cursor"])
    hook_refs = [r for r in refs if r.extra.get("component_type") == "hook"]
    assert len(hook_refs) == 1
    assert hook_refs[0].source_manifest == str(plugin_dir / "plugin.json")
    assert hook_refs[0].extra["runtime_hosts"] == ["cursor"]
    assert hook_refs[0].extra["event"] == "postToolUse"
    assert hook_refs[0].component_identity.startswith("cursor-hook/")
```

```python
# tests/test_parsers/test_hooks_json.py

def test_cursor_format_hooks_camelcase_event_and_cursor_scheme():
    from tools.parsers.hooks_json import parse_plugin_hooks_inline

    refs = parse_plugin_hooks_inline(
        {"postToolUse": [{"command": "./check.sh"}]},
        "demo",
        "/p/.cursor-plugin/plugin.json",
        runtime_hosts=["cursor"],
        identity_scheme="cursor-hook",
    )
    assert len(refs) == 1
    assert refs[0].extra["event"] == "postToolUse"
    assert refs[0].extra["runtime_hosts"] == ["cursor"]
    assert refs[0].component_identity.startswith("cursor-hook/")

def test_unknown_event_names_recorded_not_dropped():
    # Cursor's vocabulary is larger than Claude's and still growing
    # (agent-lifecycle events); dropping unregistered event names would
    # silently lose real hooks, so the shared walk stays permissive.
    from tools.parsers.hooks_json import parse_plugin_hooks_inline

    refs = parse_plugin_hooks_inline(
        {"someFutureEvent": [{"command": "./x.sh"}]},
        "demo",
        "/p/.cursor-plugin/plugin.json",
        runtime_hosts=["cursor"],
        identity_scheme="cursor-hook",
    )
    assert len(refs) == 1
    assert refs[0].extra["event"] == "someFutureEvent"

def test_claude_format_defaults_unchanged():
    from tools.parsers.hooks_json import parse_plugin_hooks_inline

    refs = parse_plugin_hooks_inline(
        {"PostToolUse": [{"type": "command", "command": "echo done"}]},
        "demo",
        "/p/.claude-plugin/plugin.json",
    )
    assert len(refs) == 1
    assert "runtime_hosts" not in refs[0].extra
    assert refs[0].component_identity.startswith("claude-hook/")
```

Note `parse()`'s existing signature takes only `path: Path` today (repo-mode registry entrypoint) — this task adds `runtime_hosts` here too, same pattern as every other host-parameterized parser.

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsers/test_claude_plugin.py tests/test_parsers/test_hooks_json.py -v`
Expected: FAIL — `parse() got an unexpected keyword argument 'runtime_hosts'` and `parse_plugin_hooks_inline() got an unexpected keyword argument 'runtime_hosts'`

- [ ] **Step 5: Thread `runtime_hosts` and format context through `claude_plugin_root.py` and `hooks_json.py`**

Add `runtime_hosts: Optional[list[str]] = None` to `walk_plugin_root` and every private helper it calls (`_parse_manifest_refs`, `_parse_default_mcp`, `_parse_bundled_skills`, `_parse_bundled_hooks`, `_parse_bundled_command_agents`, `_enumerate_bundled_command_agent_dir`), forwarding it to each underlying parser call:
- `parse_mcp_servers(..., runtime_hosts=runtime_hosts)` (verify this parameter already exists — it does, per Task 3 above)
- `mcp_json.parse(referenced, runtime_hosts=runtime_hosts)`
- `claude_skill.parse(skill_md, runtime_hosts=runtime_hosts)` (verify this parameter already exists — it does, per Task 4 above)
- `hooks_json.parse_plugin_hooks(...)`/`parse_plugin_hooks_inline(...)` — this task makes them format-aware, not just host-tagged. Both gain `runtime_hosts: Optional[list[str]] = None` (set in `extra` only when provided, same pattern as above) **and** `identity_scheme: str = "claude-hook"`, threaded into `_hook_identity`'s prefix. A module-level `hook_identity_scheme_for_manifest(plugin_json_path: Path) -> str` returns `"cursor-hook"` when `plugin_json_path.parent.name == ".cursor-plugin"` and `"claude-hook"` otherwise (same keying as `default_mcp_filename_for_manifest` below); `_parse_bundled_hooks` derives the scheme from the `plugin_json_path` it receives and passes it to both hook parsers. The scheme label is safe to vary by format because it is occurrence-local display metadata, not identity: `tools/identity.py`'s `canonical_component_identity` routes `hook` refs through `_plugin_private_identity`, which never reads this string — so this is not host-in-identity (ADR-0029/ADR-0044 Decision #2 are about `openaca:identity`, which stays plugin-private for hooks). The `{event: [entry, ...]}` walk itself stays shared and permissive: any string event is accepted and recorded as-is (per Step 2, Cursor's vocabulary is larger and still growing — dropping unknown events would silently lose real hooks), and absent `type`/`matcher` fields are tolerated exactly as today (`entry.get(...)` → `None`; the identity hash degrades to a command-only digest). Rewrite the module docstring from its Claude-only contract to the shared-shape-with-format-context role, keeping the Claude wrapper/entry documentation and adding the Cursor camelCase vocabulary with a pointer to the spec's Hooks section.
- `claude_command_agent.parse_file(child, kind=kind, scope_owner=plugin_name, runtime_hosts=runtime_hosts)` (Task 10 added this parameter).

Where a helper builds a `ComponentRef` directly rather than delegating to another parser (`_parse_manifest_refs`'s `dependencies[]` handling), add the same `extra["runtime_hosts"] = runtime_hosts` pattern used in Task 10 — set only when `runtime_hosts is not None`.

Two of the helpers additionally need format context, not just host tagging — both currently hardcode a Claude-only assumption that silently breaks a `.cursor-plugin` bundle:

- **`_parse_default_mcp` gains `default_filename: str = ".mcp.json"`.** Add a module-level helper `default_mcp_filename_for_manifest(plugin_json_path: Path) -> str` returning `"mcp.json"` when `plugin_json_path.parent.name == ".cursor-plugin"` and `".mcp.json"` otherwise — Cursor's verified default bundled MCP manifest is root `mcp.json` (spec, Plugins section), Claude's is `.mcp.json`, and each format reads only its own default filename (the decoy tests in Step 3 pin both directions). `walk_plugin_root` passes `default_filename=default_mcp_filename_for_manifest(plugin_json_path)`.
- **`_parse_bundled_hooks` gains `plugin_json_path: Path`** and uses it as the inline-hooks `source_manifest`, deleting the internal `plugin_root / ".claude-plugin" / "plugin.json"` construction — for a Cursor plugin that hardcoded path does not exist and would fabricate a source manifest outside the matched manifest context. `walk_plugin_root` forwards the `plugin_json_path` it already resolved.

The defaults (`".mcp.json"`, `identity_scheme="claude-hook"`, and `walk_plugin_root`'s existing `plugin_json_path=None` → `.claude-plugin/plugin.json` fallback) keep every existing Claude call site byte-identical — that is what Step 8's regression run verifies.

- [ ] **Step 6: Host-parameterize `claude_plugin.py`'s `parse()`**

```python
def parse(path: Path, runtime_hosts: Optional[list[str]] = None) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    if not isinstance(data, dict):
        return refs

    raw_name = data.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else None
    version = data.get("version")
    if not isinstance(version, (str, type(None))):
        version = None
    if name:
        extra: dict = {"component_type": "plugin"}
        if runtime_hosts is not None:
            extra["runtime_hosts"] = runtime_hosts
        refs.append(
            ComponentRef(
                name=name,
                version=version,
                component_identity=f"plugin/{name}",
                source_manifest=str(path),
                source_locator="$",
                extra=extra,
            )
        )

    refs.extend(
        walk_plugin_root(
            path.parent.parent,
            plugin_name=name or "",
            plugin_data=data,
            plugin_json_path=path,
            runtime_hosts=runtime_hosts,
        )
    )
    return refs
```

**Do not** write `component_identity=f"plugin/{marketplace_or_host}/{name}"` anywhere in this task — the identity string stays exactly `plugin/{name}`, matching Claude's own existing scheme, per this plan's Global Constraints and ADR-0045 Decision #2.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsers/test_claude_plugin.py tests/test_parsers/test_hooks_json.py -v`
Expected: PASS (5 plugin tests + 3 hooks tests)

- [ ] **Step 8: Run the full plugin-parsing suite to confirm no regression**

Run: `uv run pytest tests/test_parsers/ -k "plugin or hook" -v`
Expected: PASS — every existing Claude plugin/hook test calls `parse`/`walk_plugin_root`/`parse_plugin_hooks*` with no new arguments, so output is unchanged.

- [ ] **Step 9: Add the `CURSOR_MANIFEST_REGISTRY` entry and registry-driven graph dispatch**

```python
CURSOR_MANIFEST_REGISTRY: list[tuple[str, ParserFn]] = [
    # ... existing entries from Tasks 2 and prior work ...
    (
        ".cursor-plugin/plugin.json",
        functools.partial(claude_plugin.parse, runtime_hosts=["cursor"]),
    ),
]
```

**A plugin is a placement boundary, not a flat direct-child surface — the MCP/Skills-style dispatch must NOT be reused for graph realization.** The parser's flat return list (`refs[0]` = the plugin's own ref, `refs[1:]` = every bundled component) is the *manifest-accounting* contract only. Graph realization must preserve ownership: the plugin ref becomes a `plugin` node that is a child of `target`, and every bundled ref becomes a node attached **under the plugin node**, never directly under `target`.

**The live realization is not parser-output realization, so extending manifest recognition alone is not enough — read the real path before editing.** `descend()`'s target branch calls `_find_plugin_roots` (matches only `.claude-plugin/plugin.json`, `tools/graph_build.py:823-841`) and hands `_descend_into_plugin` a hardcoded `plugin_root / ".claude-plugin" / "plugin.json"`; `_descend_into_plugin` calls `claude_plugin.parse` (hardcoded, no `runtime_hosts`) only to obtain the self ref, then descends; and the plugin branch's bundled-surface path **rereads the manifest from disk** — `_plugin_manifest_data` and `_plugin_custom_skills_field` both hardcode the `.claude-plugin/plugin.json` location, and `_add_bundled_plugin_surfaces`/`_add_bundled_skills` call the `claude_plugin_root`/skill helpers with no `runtime_hosts`. Registering `.cursor-plugin/plugin.json` without reworking that reread path would produce a Cursor-tagged plugin node whose bundled surfaces are silently missing (`_plugin_manifest_data` returns `{}` for a root with no `.claude-plugin/`) or re-created without Cursor provenance.

**The realization boundary: manifest context (path + host provenance) is resolved once per plugin root, then read from the plugin node itself — never re-derived from a hardcoded location.**

1. `_find_plugin_roots` returns `(plugin_root, manifest_path)` pairs and recognizes both `.claude-plugin/plugin.json` and `.cursor-plugin/plugin.json`, each gated on its owning host being selected — via a small `_PLUGIN_REGISTRY_PATTERNS` allowlist + `registry_pattern_matches(path, root, pattern)` lookup over the selected hosts' `manifest_registry` (same shape as `_mcp_parser_for_path`, used only for *choosing* the format/parser; placement stays the plugin branch's own).
2. `descend()`'s target branch passes the discovered `manifest_path` to `_descend_into_plugin`, which calls the registry-selected parser (Cursor's registry entry is already pre-bound to `runtime_hosts=["cursor"]`) instead of hardcoding `claude_plugin.parse`.
3. The bundled-surface path derives its context from the plugin node's own self ref: `Path(plugin_node.ref.source_manifest)` *is* the real manifest path for whichever format matched (`claude_plugin.parse` sets it from the path it was handed), and `plugin_node.ref.extra.get("runtime_hosts")` is the host provenance. `_plugin_manifest_data`/`_plugin_custom_skills_field` take the manifest path, not a root they append `.claude-plugin` to; `_add_bundled_plugin_surfaces` builds `plugin_manifest_path` from the self ref and forwards the derived `runtime_hosts` into `_parse_manifest_refs`/`_parse_default_mcp`/`_parse_bundled_hooks`/`_parse_bundled_command_agents` (host-parameterized in Step 5), passes `plugin_manifest_path` into `_parse_bundled_hooks` (so inline hooks are sourced from the real manifest, never a reconstructed `.claude-plugin` path, and the hook identity scheme derives from the real format via `hook_identity_scheme_for_manifest`), and passes `default_filename=default_mcp_filename_for_manifest(plugin_manifest_path)` into `_parse_default_mcp` (so a Cursor plugin's root `mcp.json` is realized and Claude's `.mcp.json` stays Claude-only); `_add_bundled_skills`/`_add_skills_from_dir` forward the same `runtime_hosts` into their skill parsing. A ref-derived `runtime_hosts` of `None` (every existing Claude node) keeps each helper's exact current output — that is what Step 8's regression run verifies. Verify at implementation time that every plugin node's self ref carries the manifest path as `source_manifest` on the endpoint path too (`_seed_active_plugins`); if any site diverges, thread the manifest context through that call chain explicitly rather than reintroducing a hardcoded location.

The registry entry and this realization rework land together in this task's single commit — a commit that counts `.cursor-plugin/plugin.json` in manifest accounting while the graph still builds an incomplete or untagged plugin subtree is exactly the accounting/graph divergence Task 6's opening paragraph rules out. Do not route `.cursor-plugin/plugin.json` through the flat MCP-style direct-child dispatch — that would attach bundled skills/agents/commands/hooks/MCP as target children and lose plugin attribution.

- [ ] **Step 10: Write the registry/graph tests**

```python
# tests/test_parsers/test_registry.py

def test_cursor_plugin_json_registered_and_discovered(tmp_path):
    plugin_dir = tmp_path / ".cursor-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    plugin_refs = [r for refs in [g[1] for g in grouped] for r in refs if r.extra.get("component_type") == "plugin"]
    assert plugin_refs[0].extra["runtime_hosts"] == ["cursor"]
    assert plugin_refs[0].component_identity == "plugin/demo"
```

```python
# tests/test_graph_build.py

def test_repo_cursor_plugin_graph_discoverable(tmp_path):
    plugin_dir = tmp_path / ".cursor-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin"]
    assert len(plugin_nodes) == 1
    assert plugin_nodes[0].ref is not None
    assert plugin_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]

def test_repo_cursor_plugin_bundled_components_nest_under_plugin_node(tmp_path):
    # The graph-realization contract: bundled components are children of the
    # plugin node, never direct children of target, and every bundled ref
    # carries Cursor provenance sourced from inside the plugin bundle. A
    # node-presence check can't detect flattening or dropped host tags;
    # assert the actual edges, runtime_hosts, and source manifests. The
    # plugin lives in a subdirectory so bundle-relative sourcing is a real
    # assertion, not trivially true of the whole scan root.
    plugin_root = tmp_path / "my-plugin"
    plugin_dir = plugin_root / ".cursor-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        '{"name": "demo", "hooks": {"postToolUse": [{"command": "echo done"}]}}'
    )
    skills_dir = plugin_root / "skills" / "helper"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: helper\ndescription: d\n---\nrun\n")
    (plugin_root / "commands").mkdir()
    (plugin_root / "commands" / "deploy.md").write_text("run\n")
    (plugin_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    plugin_node = next(n for n in g.nodes.values() if n.kind == "plugin")
    parent_of = {e.child: e.parent for e in g.edges}
    # "mcp_server" only exists if the realization used Cursor's root
    # `mcp.json` default, and "hook" only exists if the inline-hooks block in
    # `.cursor-plugin/plugin.json` was read — both fail against a
    # Claude-hardcoded reread. The hook's exact source path is pinned by the
    # Step 3 parser test (a fabricated `.claude-plugin` path would still sit
    # inside the bundle, so the relative check below can't distinguish it).
    for kind in ("skill", "command", "mcp_server", "hook"):
        nodes = [n for n in g.nodes.values() if n.kind == kind]
        assert nodes, f"no {kind} node found"
        for n in nodes:
            assert parent_of[n.key] == plugin_node.key, (
                f"{kind} node attached to {parent_of[n.key]}, not the plugin"
            )
            assert n.ref is not None
            assert n.ref.extra["runtime_hosts"] == ["cursor"], (
                f"{kind} bundled ref lost Cursor provenance"
            )
            assert Path(n.ref.source_manifest).resolve().is_relative_to(
                plugin_root.resolve()
            ), f"{kind} bundled ref sourced outside the plugin bundle"
```

(Adapt the edge-lookup idiom to `tools/graph.py`'s real `Graph`/`Edge` shape — read it first; if edges are stored differently, e.g. adjacency on nodes or a `parent_of()` helper, use that. The assertion's substance — every bundled component's parent is the plugin node — is the requirement, not the exact attribute names.)

- [ ] **Step 11: Run the full suite**

Run: `uv run pytest tests/ -v -k "plugin or hook"`
Expected: PASS, no regressions.

- [ ] **Step 12: Commit**

```bash
git add tools/parsers/claude_plugin.py tools/parsers/claude_plugin_root.py tools/parsers/hooks_json.py tools/parsers/__init__.py tools/graph_build.py tests/
git commit -m "feat: recognize Cursor Plugins (native format) in repo mode, with corrected unqualified identity"
```

Include in the commit message body what Step 2's hook-contract check confirmed (wrapper shape and entry fields, with the doc page consulted).

---

## Task 14: Agent Plugins (open standard)

**Files:**
- Create: `tools/parsers/agent_plugins.py`
- Modify: `tools/parsers/__init__.py` (`CURSOR_MANIFEST_REGISTRY`, or a new host-agnostic entry — see Step 4's note)
- Test: `tests/test_parsers/test_agent_plugins.py`, `tests/test_parsers/test_registry.py`

**Interfaces:**
- Consumes: `claude_skill.parse` (Skills), `mcp_json.parse` (MCP servers).
- Produces: `parse(path: Path, runtime_hosts: Optional[list[str]] = None) -> list[ComponentRef]` — self-identity plus `skills/` and `mcp.json` walking only, per ADR-0045 Decision #3's portable-contract scope.

- [ ] **Step 1: Re-verify the Agent Plugins schema against its authoritative source**

The `$schema` detection string and the skills+MCP portable-surface boundary are external, version-sensitive claims about a young standard — the spec verified them once at design time, but this task's detection and closed portable surface both depend on them still being true at implementation time. Before writing any code, fetch the current spec from the authoritative repository (`agentplugins/agent-plugins-spec` on GitHub — the `plugin.schema.json` under its schemas directory and the portable-surfaces section of its spec text) and confirm: (a) the `$schema` value's shape is still `https://agent-plugins.org/schemas/<version>/plugin.schema.json`, (b) `$schema` and `name` are still the only required fields, (c) `skills/` and root `mcp.json` are still the only portably standardized bundle surfaces. Record the schema version and spec commit checked in this task's commit message body. If any of the three has changed, stop and update the spec (`docs/specs/multi-host-support.md`, Plugins section) and ADR-0045 Decision #3 before proceeding — do not silently implement against the stale contract.

- [ ] **Step 2: Write the failing test — detection and scoped walking**

```python
# tests/test_parsers/test_agent_plugins.py
import json
from pathlib import Path

from tools.parsers.agent_plugins import is_agent_plugins_manifest, parse

def test_is_agent_plugins_manifest_detects_schema(tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    assert is_agent_plugins_manifest(manifest) is True

def test_is_agent_plugins_manifest_rejects_other_schema(tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({"name": "demo"}))
    assert is_agent_plugins_manifest(manifest) is False

def test_is_agent_plugins_manifest_rejects_same_origin_non_schema_urls(tmp_path):
    # Detection is the full authoritative URL shape, never an origin-prefix
    # match: Step 6 dispatches on every bare plugin.json in the tree, so a
    # loose prefix would classify unrelated same-origin documents as plugins.
    manifest = tmp_path / "plugin.json"
    for bad in (
        "https://agent-plugins.org/schemas/not-a-schema",
        "https://agent-plugins.org/schemas/1.0.0/other.schema.json",
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json?x=1",
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json#frag",
        "https://agent-plugins.org/schemas/1.0.0/extra/plugin.schema.json",
        "https://agent-plugins.org/schemas//plugin.schema.json",
    ):
        manifest.write_text(json.dumps({"$schema": bad, "name": "demo"}))
        assert is_agent_plugins_manifest(manifest) is False, bad

def test_is_agent_plugins_manifest_accepts_any_version_segment(tmp_path):
    # Version acceptance is syntactic, not enumerated — see Step 4's note.
    manifest = tmp_path / "plugin.json"
    manifest.write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/2.3.1/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    assert is_agent_plugins_manifest(manifest) is True

def test_parse_walks_skills_and_mcp_only(tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    skills_dir = tmp_path / "skills" / "helper"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: helper\ndescription: d\n---\nrun\n")
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    # A commands/ dir present must be IGNORED — not part of the portable v1 contract.
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "deploy.md").write_text("run\n")

    refs = parse(manifest, runtime_hosts=["cursor"])
    kinds = {r.extra.get("component_type") for r in refs}
    assert "skill" in kinds
    assert "mcp_server" in kinds
    assert "command" not in kinds

    self_ref = next(r for r in refs if r.extra.get("component_type") == "plugin")
    assert self_ref.component_identity == "plugin/demo"
    assert self_ref.extra["runtime_hosts"] == ["cursor"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsers/test_agent_plugins.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.parsers.agent_plugins'`

- [ ] **Step 4: Implement the parser**

```python
# tools/parsers/agent_plugins.py
"""Parse the Agent Plugins open standard's root plugin.json.

Only skills/ and mcp.json are portably standardized across every
compliant client per the v1.0.0 spec (verified directly against
agentplugins/agent-plugins-spec) — commands, agents, hooks, and rules
are explicitly left to client-private `extensions.<reverse-domain>`
namespacing this parser does not read. See ADR-0045 Decision #3.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from tools.component_ref import ComponentRef
from tools.parsers import claude_skill, mcp_json

# The complete authoritative URL shape, not an origin prefix: detection runs
# against every bare plugin.json in a tree (Step 6), so anything looser than
# the exact `/schemas/<version>/plugin.schema.json` path would classify
# unrelated same-origin documents as plugins.
_SCHEMA_RE = re.compile(
    r"^https://agent-plugins\.org/schemas/[^/]+/plugin\.schema\.json$"
)

def is_agent_plugins_manifest(path: Path) -> bool:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    schema = data.get("$schema")
    return isinstance(schema, str) and _SCHEMA_RE.fullmatch(schema) is not None

def parse(path: Path, runtime_hosts: Optional[list[str]] = None) -> list[ComponentRef]:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    refs: list[ComponentRef] = []
    raw_name = data.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else None
    version = data.get("version")
    if not isinstance(version, (str, type(None))):
        version = None
    if name:
        extra: dict = {"component_type": "plugin"}
        if runtime_hosts is not None:
            extra["runtime_hosts"] = runtime_hosts
        refs.append(
            ComponentRef(
                name=name,
                version=version,
                component_identity=f"plugin/{name}",
                source_manifest=str(path),
                source_locator="$",
                extra=extra,
            )
        )

    plugin_root = path.parent
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for skill_subdir in sorted(skills_dir.iterdir()):
            skill_md = skill_subdir / "SKILL.md"
            if skill_md.is_file():
                refs.extend(claude_skill.parse(skill_md, runtime_hosts=runtime_hosts))

    mcp_json_path = plugin_root / "mcp.json"
    if mcp_json_path.is_file():
        refs.extend(mcp_json.parse(mcp_json_path, runtime_hosts=runtime_hosts))

    return refs
```

Same identity rule as Task 13: `plugin/{name}`, never a host-qualified string.

**Version policy — decided here, not left open:** the version segment is accepted syntactically (any single non-empty path segment), not enumerated against a supported-versions list. The spec's own wording is "version string varies," Step 1 re-verifies the URL shape against the authoritative source at implementation time, and the parser's closed portable-surface walk (`skills/` + `mcp.json`) does not vary by schema version — enumerating versions would turn every point release of a young standard into a silent false negative with no behavioral payoff. If Step 1's re-verification finds the shape itself changed (a different path grammar, versioned portable surfaces), that is the stop-and-update trigger already defined there.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsers/test_agent_plugins.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Wire detection into the registry dispatch**

Agent Plugins' manifest (root `plugin.json`) isn't a fixed filename pattern the way every other registry entry is — it needs the `$schema` content check (`is_agent_plugins_manifest`), not just a path-glob match. This doesn't fit `manifest_registry`'s `(pattern: str, ParserFn)` shape directly. Add detection at the point `descend()`/`parse_repo_grouped` encounter a root-level `plugin.json` (verify: does either already have a bare `plugin.json` pattern registered anywhere today? grep `"plugin.json"` across `tools/parsers/__init__.py` and `tools/graph_build.py` before assuming — if not, this is new dispatch, not an extension of an existing one). Add a check: when a walk encounters a file literally named `plugin.json` at any depth that is **not** already matched by `.claude-plugin/plugin.json` or `.cursor-plugin/plugin.json`'s directory-scoped patterns, call `is_agent_plugins_manifest(path)`; if true, dispatch to `agent_plugins.parse(path, runtime_hosts=["cursor"])`. **This dispatch runs only when `"cursor"` is among the selected hosts, in both the accounting walk (`parse_repo_grouped`) and the graph walk (`descend()`).** Because Agent Plugins sits outside `manifest_registry`, this bespoke dispatch must reproduce the registry's selected-host gate explicitly rather than bypass it — without the gate, `hosts=["claude-code"]` would emit a Cursor-tagged component, breaking the host-filter contract every other Cursor surface honors and the plan's Claude-only backward-compatibility guarantee. This is host-agnostic detection (content-based, not path-based) feeding into a Cursor-tagged result for now: Cursor is the one registered host confirmed to install Agent Plugins, and the spec's Plugins section explicitly leaves host-agnostic treatment of the standard unresolved — so this task hardcodes `runtime_hosts=["cursor"]` at the one call site rather than generalizing across hosts prematurely. The identity string stays the unqualified `plugin/{name}` either way (Global Constraints; ADR-0045 Decision #2).

**Graph realization is a closed, parser-output realization — Task 13's plugin branch must NOT be reused for it.** Task 13's branch is a *descent*: after creating the self node, `descend()`'s plugin branch rereads the bundle from disk (`_add_bundled_skills`, then `_add_bundled_plugin_surfaces` → manifest `dependencies[]`, default MCP, hooks, commands, agents — `tools/graph_build.py:790-796,1530-1585`), which is exactly the richer surface set the portable v1 contract excludes. Routed through it, this task's own fixture would turn `commands/deploy.md` into a `command` node and Step 7's negative assertion could never pass. Instead, `descend()`'s target branch realizes an Agent Plugin from its parser output alone — define the closed boundary before (or with) the dispatch wiring above, in this task's single commit, so no intermediate state routes an Agent Plugin through the native descent:

1. Call `agent_plugins.parse(manifest_path, runtime_hosts=["cursor"])` once (via `_safe_parse`, same malformed-manifest tolerance as the native branch).
2. The self ref becomes the `plugin` node under `target`, reusing the node/edge helpers (`occurrence_key`, `_add_child`) — sharing those is fine; sharing the bundle reread is not.
3. Every remaining ref — skills and MCP servers only, closed by the parser's construction — becomes a node attached under the plugin node.
4. Descend into each bundled skill's directory with the skill node as parent, so Agent Plugin skills carry the same dependency-manifest chains as every other skill node (`descend`'s `skill` branch walks only dep manifests — safe to reuse).
5. Nothing else in the bundle is read at realization time: no hooks/commands/agents enumeration, no plugin-root dependency-manifest walk, no `extensions` read.

Implement items 1-5 as one named helper — `_realize_agent_plugin(graph, parent, manifest_path, normalize)` in `tools/graph_build.py` — called from `descend()`'s target branch here and reused verbatim by Task 17's endpoint seed for dev-linked Agent Plugins, so repo and endpoint mode cannot drift into two interpretations of the closed surface. The helper attaches nothing when the parse yields no refs or the first ref isn't the plugin self ref (a manifest without `name` emits bundled refs only, and `refs[0]` would then be a bundled component, not the plugin) — check `refs and refs[0].extra.get("component_type") == "plugin"` before creating the node.

The plugin root does **not** join the native branch's `realized_roots` exclusion: nothing the closed realization emits is reachable twice (a bare `skills/` dir matches no registry skill pattern, and the bundled refs attach only under the plugin node), while excluding the root would silently drop the existing target-level dep nodes of a `package.json` sitting beside a root-level `plugin.json` — a Claude-only-behavior regression the closed contract must not cause.

- [ ] **Step 7: Write the registry and graph tests**

```python
# tests/test_parsers/test_registry.py

def test_agent_plugins_root_plugin_json_detected_by_schema(tmp_path):
    (tmp_path / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "demo"}'
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1

def test_unrelated_root_plugin_json_not_matched_as_agent_plugins(tmp_path):
    (tmp_path / "plugin.json").write_text('{"name": "unrelated-config"}')
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 0

def test_agent_plugins_not_detected_when_cursor_not_selected(tmp_path):
    # The bespoke content-based dispatch honors the same selected-host gate
    # as registry entries: a Claude-only scan must not emit a Cursor-tagged
    # Agent Plugin.
    (tmp_path / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "demo"}'
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code"])
    assert n_found == 0
```

```python
# tests/test_graph_build.py

def test_agent_plugin_graph_nests_portable_surfaces_only(tmp_path):
    # Skills and MCP nest under the plugin node; every deliberately
    # unsupported surface present in the bundle — commands, agents, hooks
    # (inline and hooks/hooks.json), manifest dependencies, extensions —
    # produces NO node at all. The plugin lives in a subdirectory so the
    # assertions can't be satisfied by target-level accidents.
    plugin_root = tmp_path / "my-plugin"
    plugin_root.mkdir()
    (plugin_root / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "demo",
        "hooks": {"postToolUse": [{"command": "echo done"}]},
        "dependencies": ["left-pad@1.0.0"],
        "extensions": {"com.cursor": {"rules": ["r1"]}},
    }))
    skills_dir = plugin_root / "skills" / "helper"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: helper\ndescription: d\n---\nrun\n")
    (skills_dir / "package.json").write_text('{"dependencies": {"left-pad": "1.0.0"}}')
    (plugin_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    (plugin_root / "commands").mkdir()
    (plugin_root / "commands" / "deploy.md").write_text("run\n")
    (plugin_root / "agents").mkdir()
    (plugin_root / "agents" / "reviewer.md").write_text("---\nname: reviewer\n---\nreview\n")
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        '{"hooks": {"postToolUse": [{"command": "./check.sh"}]}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    plugin_node = next(n for n in g.nodes.values() if n.kind == "plugin")
    parent_of = {e.child: e.parent for e in g.edges}  # adapt per tools/graph.py, as in Task 13
    for kind in ("command", "agent", "hook"):
        assert not [n for n in g.nodes.values() if n.kind == kind], kind
    plugin_children = [n for n in g.nodes.values() if parent_of.get(n.key) == plugin_node.key]
    assert {n.kind for n in plugin_children} == {"skill", "mcp_server"}
    for n in plugin_children:
        assert n.ref is not None
        assert n.ref.extra["runtime_hosts"] == ["cursor"]
    # The bundled skill keeps its normal dep chain (closed realization
    # suppresses the plugin bundle walk, not skill-level analysis).
    skill_node = next(n for n in plugin_children if n.kind == "skill")
    skill_children = [n for n in g.nodes.values() if parent_of.get(n.key) == skill_node.key]
    assert any(n.kind == "package" for n in skill_children)

def test_agent_plugin_absent_from_graph_when_cursor_not_selected(tmp_path):
    (tmp_path / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "demo"}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code"])
    assert not [n for n in g.nodes.values() if n.kind == "plugin"]
```

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest tests/ -v -k plugin`
Expected: PASS, no regressions, including Task 13's Cursor Plugins tests (both formats coexisting in one directory — write one more test confirming this if not already covered by the spec's testing scenarios).

```python
# tests/test_parsers/test_registry.py

def test_both_plugin_formats_in_same_directory_both_parsed(tmp_path):
    cursor_plugin_dir = tmp_path / ".cursor-plugin"
    cursor_plugin_dir.mkdir()
    (cursor_plugin_dir / "plugin.json").write_text('{"name": "native-demo"}')
    (tmp_path / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "open-demo"}'
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 2
```

- [ ] **Step 9: Commit**

```bash
git add tools/parsers/agent_plugins.py tools/parsers/__init__.py tools/graph_build.py tests/
git commit -m "feat: recognize the Agent Plugins open standard, scoped to its portable skills+MCP contract"
```

Include in the commit message body the schema version and spec commit verified in Step 1.

---

## Task 15: Endpoint mode architecture — root contract, seed-module boundary, multi-root graph, per-host loop, `--host`

**Files:**
- Create: `tools/endpoint_seeds/__init__.py`, `tools/endpoint_seeds/claude_code.py`
- Create: `tools/endpoint_request.py` (shared CLI-side host/root resolution)
- Modify: `tools/hosts.py` (`EndpointSeedFn`'s real type, lazy `seed_endpoint` binding for `_CLAUDE_CODE`)
- Modify: `tools/graph_build.py` (`_seed_endpoint` body moves out; `build_graph()`'s endpoint branch becomes a per-host loop plus a cross-host subagent pass; `_make_normalizer` and `_attach_mcp_launch_deps` go multi-root while manifest-name indexes stay isolated per owning root)
- Modify: `tools/scan.py` (`--host` on `endpoint` command, `host_surface` render fix)
- Test: `tests/test_hosts.py`, `tests/test_graph_build.py`, `tests/test_scan.py`, `tests/test_endpoint_request.py`

**Interfaces:**
- Consumes: `resolve_host_selection`, `detected_hosts()` (already exist, from Task 6 above); `resolve_subagent_occurrences` (Task 12).
- Produces: the endpoint request contract below; `EndpointSeedFn`; `tools.endpoint_seeds.claude_code.seed_endpoint`; `resolve_endpoint_request`; `shared_agents_root` / `endpoint_auxiliary_roots` / `endpoint_discovery_roots` (contract item 3); `build_graph(..., host_config_roots=...)`; `openaca scan endpoint --host <id>`.

**The endpoint request contract — fixed here, not left to implementation.** Every endpoint entry point (scan, BOM, remote sync in Task 16) resolves hosts and roots the same way, through one function:

1. **Selection and roots resolve in the CLI layer**, via `tools/endpoint_request.py`:

   ```python
   def resolve_endpoint_request(
       host_values: tuple[str, ...], config_dir: Path | None
   ) -> tuple[list[str], dict[str, Path]]:
       """Returns (selected_host_ids, ordered {host_id: config_root}).

       - Explicit --host values: validated against HOSTS, deduped, ordered by
         HOSTS registration order. Omitted: every detected host.
       - Nothing selected/detected -> ClickException naming registered hosts.
       - config_dir (explicit --config-dir) is allowed only when exactly ONE
         host ends up selected; it becomes that host's config root and counts
         as detected -- detect() is NOT consulted for an explicit override
         (the supplied directory IS the root; requiring the default
         ~/.cursor to also exist would reject valid overrides). With 2+
         selected hosts, --config-dir is a hard error telling the user to
         pass --host to disambiguate.
       - Explicit --host X with NO --config-dir and detect() false -> hard
         error (unchanged from the spec's "--host cursor with no ~/.cursor"
         case; the error now applies only when there is no override).
       """
   ```

2. **`build_graph` keeps its signature backward-compatible** and gains the root map: `build_graph(target, mode="endpoint", project_root=..., hosts=..., host_config_roots: dict[str, Path] | None = None)`. `host_config_roots=None` means `{"claude-code": Path(target)}` — exactly today's behavior, so every existing call site is untouched. When provided, `target` must equal the first entry's root (it stays the API-compatibility anchor and the BOM `target` string for the primary host); `build_graph` asserts this.

3. **Normalization is per-root with per-host labels — and covers the auxiliary discovery roots.** `_make_normalizer`'s endpoint branch takes one ordered `{normalization_label: root}` discovery-root descriptor instead of one `install_root`; it does **not** also accept `host_config_roots`. Order of matching: `project_root` first (unchanged), then each discovery root in descriptor order. Labels: `claude-code`'s root keeps the existing `endpoint/` prefix (Claude-only output stays byte-identical, and Claude keys don't change when a second host appears); every other host's root gets `endpoint-<host_id>/` (e.g. `endpoint-cursor/mcp.json`). `host_config_roots` remains a separate `build_graph` input only for seed dispatch, manifest-name indexes, and launch-dependency ownership — normalization never reconciles two overlapping root maps.

   Endpoint composition also reads two directories that need not lie under any selected host root or the project: the cross-tool home-scoped `~/.agents` tree (Cursor's shared Skills root, Task 17) and Claude Code's default config root when it supplies Cursor's compatibility Subagent read without Claude Code being selected (item 7). Left out of the root map, every component discovered there would keep a machine-absolute occurrence key — breaking reproducible keys exactly where the per-root labels exist to guarantee them. So the full endpoint discovery-root set is modeled once, next to `resolve_endpoint_request` in `tools/endpoint_request.py`, and consumed by both graph normalization (here) and remote relativization (Task 16):

   ```python
   def shared_agents_root() -> Path:
       """~/.agents — a cross-tool home-scoped convention (Cursor and Codex
       read it). Not owned by any host, so no host's --config-dir override
       relocates it. Path.home() honors $HOME on POSIX (V0 is POSIX-only,
       ADR-0005), which is also how tests pin it hermetically."""
       return Path.home() / ".agents"

   def endpoint_auxiliary_roots(
       selected: list[str], roots: dict[str, Path]
   ) -> dict[str, Path]:
       """Ordered {label: root} of the non-host discovery roots implied by a
       selection. Deterministic and derived — never CLI input:
       - "shared-agents" -> shared_agents_root(), when "cursor" is selected.
       - "claude-compat" -> HOSTS["claude-code"].config_root(None), when
         "cursor" is selected and "claude-code" is NOT (when Claude Code IS
         selected, its own host root already covers the path).
       Asserts no registered host id equals an auxiliary label.
       """

   def endpoint_discovery_roots(
       selected: list[str], roots: dict[str, Path]
   ) -> dict[str, Path]:
       """Ordered {normalization_label: root} for every endpoint path the
       selected request can discover. Host entries come first (`endpoint`
       for Claude Code, otherwise `endpoint-<host-id>`), followed by
       endpoint_auxiliary_roots with `endpoint-<aux-label>` labels. Reject
       duplicate labels and duplicate resolved roots instead of allowing
       matching order to make provenance implicit.
       """
   ```

   `build_graph`'s endpoint branch calls `endpoint_discovery_roots` once and hands that complete ordered map to `_make_normalizer`; Task 16 hands the same descriptor to remote relativization. An auxiliary root labels as `endpoint-<label>/` (`endpoint-shared-agents/`, `endpoint-claude-compat/`), the same namespace as host labels. Auxiliary roots are discovery/normalization inputs only — they contribute no manifest-name index and no launch-dependency resolution (items 4-5 stay scoped to host roots plus `project_root`). Distinct labels per root make cross-root key collisions structurally impossible; the fallback for a path under no mapped root stays the absolute path — now reserved for the genuinely unknown case (e.g. a plugin installPath outside every root, which the remote upload boundary already redacts to basename+digest), never for a directory endpoint composition itself chose to read. With the default Claude-only map the auxiliary helper returns `{}`, the discovery descriptor contains only `{"endpoint": <claude-root>}`, and every key is byte-identical to today.

4. **Manifest-name indexes stay per root — lookup is by owning root, never a cross-host union.** `build_manifest_name_index(root, include_gitignored=True)` runs once per host root and once for `project_root`, and the results are kept separate. A merged global map would let a Cursor MCP launching `npx server` silently bind a same-named package that exists only under Claude's root — a cross-host misattribution, not a fallback worth having. Resolution for any given MCP node uses `{**owning_host_index, **project_index}`: the owning host's index with `project_root` entries taking precedence (today's project-over-install rule, unchanged). A name present only under a *different* host's root does not resolve — same outcome as the name being absent. With the default single-entry root map this is exactly today's `{**install, **project}` behavior.

5. **Launch-dependency attachment resolves each MCP node against the root that seeded it.** The owning root is recoverable from the node key's label prefix (`endpoint/` vs `endpoint-<host_id>/` — deterministic, no extra bookkeeping): `_attach_mcp_launch_deps` takes the root map plus the per-root name indexes from item 4 and, per node, picks both the matching `scan_root` and the matching `{**owning_host_index, **project_index}` lookup map instead of the single `Path(target)` and one global index.

6. **Seed ownership and imports.** `_seed_endpoint`'s body moves to `tools/endpoint_seeds/claude_code.py` as `seed_endpoint` (per the spec's `claude_code.seed_endpoint()` boundary). Its Claude-specific helpers (`load_settings` machinery, `_seed_active_plugins`, `_add_skills_from_dir`, etc.) stay in `graph_build.py` and are imported *by* the seed module — dependency direction: `endpoint_seeds.* → graph_build → hosts`, acyclic because `hosts.py` never imports `graph_build` or `endpoint_seeds` at module level. `hosts.py` types `EndpointSeedFn` using `Graph`/`Node` from `tools.graph` (which `hosts.py` may import freely — `tools/graph.py` has no path back to `hosts`) and a local one-line `SourceNormalizer = Callable[[str], str]` alias (do **not** import it from `graph_build`). The adapter's `seed_endpoint` value is a lazy wrapper defined in `hosts.py` itself:

   ```python
   def _claude_code_seed_endpoint(*args, **kwargs) -> None:
       from tools.endpoint_seeds.claude_code import seed_endpoint

       seed_endpoint(*args, **kwargs)
   ```

   The deferred import runs at call time, so the frozen adapter is constructible at module init with no cycle. (A top-level `from tools.endpoint_seeds.claude_code import seed_endpoint` in `hosts.py` would cycle: `hosts → endpoint_seeds.claude_code → graph_build → hosts`.)

7. **Subagents seed cross-host, in the endpoint branch itself — not inside any single host's `seed_endpoint`.** A shared-file occurrence (`~/.claude/agents/helper.md` readable by both hosts) can span hosts, so no one host's seed can own it: if each host seeded its own view, the same file would produce two nodes with the same occurrence key (or a dedup'd node with a selection-order-dependent host tag). Instead `build_graph`'s endpoint branch resolves subagents with the **full selected host set**, once per scope. **Global scope passes each host's agents directory explicitly** — `resolve_subagent_occurrences_for_dirs(claude_agents_dir, cursor_agents_dir, hosts)` (Task 12's explicit-dirs entry point), with the Claude dir being `roots["claude-code"] / "agents"` when Claude Code is selected and otherwise the `claude-compat` auxiliary root's `agents/` dir — item 3's `endpoint_auxiliary_roots` entry, the same `HOSTS["claude-code"].config_root(None)` path, registered in the normalizer so a compatibility-read occurrence keys as `endpoint-claude-compat/agents/<rel>.md` rather than a machine-absolute path (Cursor's compatibility read of `~/.claude/agents/` doesn't depend on OpenACA's host selection; `config_root(None)` honors `CLAUDE_CONFIG_DIR`, so tests pin it hermetically) — and the Cursor dir being `roots["cursor"] / "agents"` when Cursor is selected. The dirs are **never reconstructed from directory basenames**: an endpoint config root is an arbitrary path (`--host cursor --config-dir /fixture/cursor` is valid, and this plan's own fixtures use `tmp_path / "claude"` and `tmp_path / "cursor"`), so dot-directory discovery from a common parent would find nothing. **Project scope** uses the repo-style resolver, `resolve_subagent_occurrences(project_root, hosts)`, where the `.claude`/`.cursor` dot-directory convention genuinely holds. Claude's `seed_endpoint` correspondingly **drops its own `agents/` walks** (the install-root `agents/` walk and the project `.claude/agents` walk) — moved into the cross-host pass, not duplicated; everything else in the moved function stays behaviorally unchanged, and the snapshot test in Step 2 is the proof the composed result is identical for a Claude-only selection.

8. **The per-host loop.** For each selected host in map order: skip if `adapter.seed_endpoint is None`; call `adapter.seed_endpoint(graph, root_node, host_config_roots[host_id], project_root, normalize, warnings=warnings)`. No `detect()` call here — detection already happened in `resolve_endpoint_request`; `build_graph` trusts the root map it was handed (which is also what makes explicit `--config-dir` overrides work without a `~/.cursor`).

- [ ] **Step 1: Read `_seed_endpoint`'s exact current signature and every call site**

```bash
grep -n "_seed_endpoint\b" tools/graph_build.py
```

Confirm the exact parameter list and every internal call it makes (`load_settings`, `claude_install._load_plugins_map`, `_seed_active_plugins`, `_add_project_skills`, `_add_skills_from_dir`, `_seed_remote_mcps`, `_seed_direct_components`, `_add_endpoint_command_agents`) before moving anything. Identify precisely which calls are the `agents/`-directory walks that Step 5 hoists into the cross-host subagent pass.

- [ ] **Step 2: Write the Claude-only equivalence snapshot test — against CURRENT behavior, before any refactor**

This is the regression bar for the whole task, so it is written and made green **first**, against the unmodified `_seed_endpoint`, and must stay green untouched through every later step. It asserts the exact composed graph, not a smoke condition:

```python
# tests/test_graph_build.py

def _minimal_claude_install_root(tmp_path: Path) -> Path:
    install_root = tmp_path / "claude"
    (install_root / "plugins").mkdir(parents=True)
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins" / "installed_plugins.json").write_text(
        '{"version": 1, "plugins": {}}'
    )
    (install_root / "skills" / "helper").mkdir(parents=True)
    (install_root / "skills" / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: d\n---\nrun\n"
    )
    (install_root / "agents").mkdir()
    (install_root / "agents" / "reviewer.md").write_text("---\nname: reviewer\n---\nr\n")
    (install_root / "commands").mkdir()
    (install_root / "commands" / "deploy.md").write_text("run\n")
    return install_root

def test_endpoint_claude_only_graph_snapshot_unchanged(tmp_path):
    install_root = _minimal_claude_install_root(tmp_path)
    g = build_graph(install_root, mode="endpoint")
    snapshot = sorted((n.kind, n.key) for n in g.nodes.values())
    assert snapshot == [
        # Capture this list by running the test ONCE against the pre-refactor
        # code and pasting the actual output here; it then stays frozen. It
        # must contain the skill, agent, and command nodes with endpoint/-
        # prefixed keys and the target node — no endpoint-cursor/ key ever.
    ]
    assert not any(key.startswith("endpoint-") for _, key in snapshot)
```

Also assert the refs' `extra` dicts carry no `runtime_hosts` key (Claude-only endpoint output is byte-identical to today, including after Step 5 moves agent seeding into the cross-host pass — the Task 12 resolver's claude-only row guarantees the same `extra`). Run it now; expected: PASS against unmodified code once the snapshot list is filled in.

- [ ] **Step 3: Create `tools/endpoint_seeds/claude_code.py` and the lazy adapter binding**

Move `_seed_endpoint`'s body to `tools/endpoint_seeds/claude_code.py` as `seed_endpoint`, importing the helpers it calls from `tools.graph_build` (helpers stay put — the move is the composition function only). In `tools/hosts.py`: pin `EndpointSeedFn` per the Interfaces contract above (import `Graph`/`Node` from `tools.graph`, define the local `SourceNormalizer` alias, never import `tools.graph_build`), add the `_claude_code_seed_endpoint` lazy wrapper from item 6 of the contract, and set it as `_CLAUDE_CODE`'s `seed_endpoint`. In `graph_build.py`, the endpoint branch temporarily calls the moved function through the adapter (`HOSTS["claude-code"].seed_endpoint(...)`) with unchanged arguments. Adapter tests:

```python
# tests/test_hosts.py

def test_claude_code_adapter_has_seed_endpoint():
    from tools.hosts import HOSTS

    assert HOSTS["claude-code"].seed_endpoint is not None

def test_cursor_adapter_seed_endpoint_none_until_cursor_endpoint_task():
    from tools.hosts import HOSTS

    assert HOSTS["cursor"].seed_endpoint is None

def test_hosts_module_has_no_static_graph_build_dependency():
    import sys

    for mod in list(sys.modules):
        if mod.startswith("tools."):
            del sys.modules[mod]
    import tools.hosts  # noqa: F401

    assert "tools.graph_build" not in sys.modules
    assert "tools.endpoint_seeds.claude_code" not in sys.modules
```

Run `uv run pytest tests/test_hosts.py tests/test_graph_build.py -k "seed_endpoint or snapshot" -v`; the snapshot test from Step 2 must still pass byte-identically.

- [ ] **Step 4: Make the graph's root-sensitive stages multi-root**

Implement contract items 2-5: `host_config_roots` on `build_graph` (default `{"claude-code": Path(target)}`), the labeled multi-root `_make_normalizer` endpoint branch, the per-root name indexes, and root-map-aware `_attach_mcp_launch_deps`. Tests (verify `_make_normalizer`'s real post-change signature when writing these — the assertions below are the contract, the call shape is illustrative):

```python
# tests/test_graph_build.py

def test_endpoint_normalizer_single_root_unchanged(tmp_path):
    # host_config_roots omitted -> keys identical to today's endpoint/<rel> form.
    claude_root = tmp_path / "claude"
    normalize = _make_normalizer(
        "endpoint", claude_root, None,
        discovery_roots={"endpoint": claude_root},
    )
    assert normalize(str(claude_root / "mcp.json")) == "endpoint/mcp.json"

def test_endpoint_normalizer_two_roots_source_manifest_to_prefix_mapping(tmp_path):
    # Each source manifest maps to its OWNING root's prefix: claude root ->
    # endpoint/, cursor root -> endpoint-cursor/, and a path under neither
    # root stays absolute. Same relative path (mcp.json) under both roots
    # yields two distinct keys — no collision, deterministic.
    claude_root, cursor_root = tmp_path / "claude", tmp_path / "cursor"
    discovery_roots = endpoint_discovery_roots(
        ["claude-code", "cursor"],
        {"claude-code": claude_root, "cursor": cursor_root},
    )
    normalize = _make_normalizer(
        "endpoint", claude_root, None, discovery_roots=discovery_roots,
    )
    assert normalize(str(claude_root / "mcp.json")) == "endpoint/mcp.json"
    assert normalize(str(cursor_root / "mcp.json")) == "endpoint-cursor/mcp.json"
    outside = tmp_path / "elsewhere" / "x.json"
    assert normalize(str(outside)) == str(outside)

def test_endpoint_normalizer_labels_cursor_auxiliary_roots(tmp_path, monkeypatch):
    # Cursor's two home-scoped auxiliary roots are unrelated to both the
    # explicit config override and project root. They still receive stable,
    # distinct labels; neither may fall through to a machine-absolute key.
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "claude-compat"))
    cursor_root = tmp_path / "overrides" / "cursor-config"
    discovery_roots = endpoint_discovery_roots(
        ["cursor"], {"cursor": cursor_root}
    )
    normalize = _make_normalizer(
        "endpoint", cursor_root, None, discovery_roots=discovery_roots,
    )
    shared = home / ".agents" / "skills" / "helper" / "SKILL.md"
    compat = home / "claude-compat" / "agents" / "helper.md"
    assert normalize(str(shared)) == (
        "endpoint-shared-agents/skills/helper/SKILL.md"
    )
    assert normalize(str(compat)) == (
        "endpoint-claude-compat/agents/helper.md"
    )
    assert not Path(normalize(str(shared))).is_absolute()
    assert not Path(normalize(str(compat))).is_absolute()

def test_endpoint_launch_dep_binds_owning_root_not_other_host(tmp_path):
    # Contract item 4's no-cross-host rule: BOTH roots contain a package dir
    # with the SAME name ("server"), each declaring a different dependency;
    # each root's mcp.json launches "server" by name. Each MCP node must
    # attach the dependency from its own root — the claude MCP's package
    # children come from claude_root/server, the cursor MCP's from
    # cursor_root/server, and neither carries the other root's dependency.
    # (Like the two-host coexistence test, the cursor half activates when
    # Task 17 fills in HOSTS["cursor"].seed_endpoint — land it guarded here
    # or in Task 17; it must exist and pass by the end of Task 17.)
    ...
```

The Step 2 snapshot test must still pass (single-root path bit-for-bit unchanged).

- [ ] **Step 5: Per-host loop + cross-host subagent pass in `build_graph`'s endpoint branch**

Replace the endpoint branch per contract items 7-8:

```python
if mode == "endpoint":
    roots = host_config_roots or {"claude-code": Path(target)}
    selected = list(roots)
    for host_id, host_root in roots.items():
        adapter = HOSTS.get(host_id)
        if adapter is None or adapter.seed_endpoint is None:
            continue
        adapter.seed_endpoint(graph, root, host_root, project_root, normalize, warnings=warnings)
    _seed_endpoint_subagents(graph, root, roots, project_root, normalize, hosts=selected)
    attach_include_gitignored = True
```

`_seed_endpoint_subagents` implements contract item 7: at the global scope it calls `resolve_subagent_occurrences_for_dirs` with each selected host's `<config_root>/agents` dir taken straight from the root map (Claude's default root standing in for the compatibility read when Claude Code isn't selected); at `project_root` (when given) it calls `resolve_subagent_occurrences(project_root, hosts)`. Each ref attaches as a target child (same node construction as other direct components). In the same step, delete the `agents/` walks from `endpoint_seeds/claude_code.py`'s moved body — the cross-host pass now owns them. The Step 2 snapshot test is the proof of equivalence for the Claude-only selection; it must pass unchanged.

Then the two-host coexistence test — the spec's explicit endpoint criteria:

```python
# tests/test_graph_build.py

def test_endpoint_two_hosts_coexist_under_one_target(tmp_path):
    claude_root = _minimal_claude_install_root(tmp_path)
    cursor_root = tmp_path / "cursor"
    (cursor_root / "skills").mkdir(parents=True)
    (cursor_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(
        claude_root,
        mode="endpoint",
        host_config_roots={"claude-code": claude_root, "cursor": cursor_root},
    )
    targets = [n for n in g.nodes.values() if n.kind == "target"]
    assert len(targets) == 1
    keys = [n.key for n in g.nodes.values()]
    assert len(keys) == len(set(keys))
    assert any(k.startswith("endpoint/") for k in keys)
    assert any(k.startswith("endpoint-cursor/") for k in keys)
```

(The cursor half seeds nothing until Task 17 fills in `HOSTS["cursor"].seed_endpoint`; write the test now with the claude assertions plus the key-prefix assertions guarded to activate then, or land it in Task 17 — either way it must exist and pass by the end of Task 17. The endpoint dual-host *subagent* case — `~/.claude/agents/helper.md` with and without a `~/.cursor/agents/` override → one dual-host vs. two single-host occurrences, independently at global and project scope — is testable now via `host_config_roots` with a stub cursor entry, since the cross-host pass, not cursor's seed, owns it.)

One more test locks in the explicit-dirs contract — a config root whose basename carries no host convention still gets its `agents/` read:

```python
# tests/test_graph_build.py

def test_endpoint_subagents_found_under_nonstandard_explicit_root(tmp_path, monkeypatch):
    # An explicit override root is an arbitrary path; its agents dir is
    # <config_root>/agents, never rediscovered via dot-directory names.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude-here"))
    cursor_root = tmp_path / "cursor"  # deliberately NOT named ".cursor"
    (cursor_root / "agents").mkdir(parents=True)
    (cursor_root / "agents" / "helper.md").write_text("---\nname: helper\n---\nh\n")
    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})
    agents = [n for n in g.nodes.values() if n.ref and n.ref.extra.get("component_type") == "agent"]
    assert len(agents) == 1
    assert agents[0].ref.extra["runtime_hosts"] == ["cursor"]

def test_endpoint_cursor_only_claude_compat_subagent_has_stable_key(tmp_path, monkeypatch):
    # Cursor reads Claude's agents directory even when Claude Code is not
    # selected. The source lies under a named auxiliary root, so the
    # occurrence key must be reproducible rather than machine-absolute.
    claude_compat = tmp_path / "home" / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_compat))
    (claude_compat / "agents").mkdir(parents=True)
    (claude_compat / "agents" / "helper.md").write_text(
        "---\nname: helper\n---\nh\n"
    )
    cursor_root = tmp_path / "elsewhere" / "cursor-config"
    cursor_root.mkdir(parents=True)
    g = build_graph(
        cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root}
    )
    agents = [
        n for n in g.nodes.values()
        if n.ref and n.ref.extra.get("component_type") == "agent"
    ]
    assert len(agents) == 1
    assert agents[0].ref.extra["runtime_hosts"] == ["cursor"]
    assert "endpoint-claude-compat/agents/helper.md" in agents[0].key
    assert str(claude_compat) not in agents[0].key
```

(Verify the agent node's kind/`component_type` value against the real graph helpers when implementing — the assertion's substance is one cursor-tagged occurrence from `<config_root>/agents`.)

- [ ] **Step 6: `resolve_endpoint_request` + CLI wiring on `scan endpoint`**

Implement `tools/endpoint_request.py` per contract item 1 with direct unit tests in `tests/test_endpoint_request.py` covering: default = detected; explicit `--host` validated; `--config-dir` + single host accepted **without** `detect()` (the override IS the root); `--config-dir` + two hosts → error; explicit `--host cursor`, no override, not detected → error. In `tools/scan.py`: add `@_host_option` to the `endpoint` command (currently only on `repo` — verify via `grep -n "@_host_option" tools/scan.py`), replace `_resolve_endpoint_config_dir(config_dir)` with `resolve_endpoint_request(host_values, config_dir)`, pass the root map to `build_graph`, and fix `tools/scan.py:1069`'s hardcoded `host_surface="Claude Code"` to render from the selected host list (check existing multi-value label conventions in `scan.py` before inventing a format); the card's `config` row lists each selected host's root. CLI tests — note `HostAdapter` is a **frozen** dataclass, so `monkeypatch.setattr(HOSTS["cursor"], "detect", ...)` raises `FrozenInstanceError`; replace the registry entry instead:

```python
# tests/test_scan.py
import dataclasses

def _with_detect(monkeypatch, host_id: str, value: bool) -> None:
    from tools.hosts import HOSTS

    monkeypatch.setitem(
        HOSTS, host_id, dataclasses.replace(HOSTS[host_id], detect=lambda: value)
    )

def test_scan_endpoint_default_skips_undetected_cursor(tmp_path, monkeypatch):
    _with_detect(monkeypatch, "cursor", False)
    # Claude-only fixture; default invocation must not seed any cursor node
    # and host_surface must name only Claude Code.
    ...

def test_scan_endpoint_explicit_host_cursor_no_root_no_override_hard_error(tmp_path, monkeypatch):
    _with_detect(monkeypatch, "cursor", False)
    runner = CliRunner()
    result = runner.invoke(main, ["scan", "endpoint", "--host", "cursor"])
    assert result.exit_code != 0

def test_scan_endpoint_explicit_config_dir_with_host_cursor_accepted(tmp_path, monkeypatch):
    _with_detect(monkeypatch, "cursor", False)
    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()
    (cursor_root / "mcp.json").write_text('{"mcpServers": {}}')
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["scan", "endpoint", "--host", "cursor", "--config-dir", str(cursor_root)],
    )
    assert result.exit_code == 0
```

- [ ] **Step 7: Run the full graph_build + scan suites**

Run: `uv run pytest tests/test_graph_build.py tests/test_scan.py tests/test_hosts.py tests/test_endpoint_request.py -v`
Expected: PASS — the Step 2 snapshot test green and untouched is the task's exit criterion.

- [ ] **Step 8: Commit**

```bash
git add tools/endpoint_seeds/ tools/endpoint_request.py tools/hosts.py tools/graph_build.py tools/scan.py tests/
git commit -m "feat(endpoint): per-host root contract, endpoint seed modules, multi-root graph stages, --host on scan endpoint"
```

---

## Task 16: Host-aware endpoint posture, `bom endpoint`, and `remote sync endpoint`

The spec's Goal names scan endpoint, BOM emit, exposure report, and remote sync as supported surfaces. The graph change alone doesn't deliver them: endpoint posture collection reads one Claude-shaped `config_dir` (`tools/scan.py`'s `collect_endpoint_mcp_manifests(config_dir, project, refs)` call), `bom endpoint` independently resolves one Claude root with no host option (`tools/bom_cli.py`), and `remote sync endpoint` does the same through `tools/remote/collector.py`. This task wires all of them through Task 15's shared request contract, **before** Cursor's seed lands (Task 17) — so Cursor components can never appear in inventory while posture still reads only the Claude root. The exposure report needs no separate wiring: `--report` flows through `scan endpoint` itself (verify by reading `report_kind`'s path in `tools/scan.py` — it consumes the same refs/findings this task makes host-aware; add the verification note to the test, not new code).

One consequence of landing before Task 17: `HOSTS["cursor"].seed_endpoint` is still `None` here, so no real Cursor graph component can exist yet — the per-host loop skips seedless adapters. This task therefore proves the host plumbing with stubbed adapter entries wherever a test needs a Cursor component in the graph (the `_with_detect`-style `monkeypatch.setitem(HOSTS, ...)` pattern, substituting a stub `seed_endpoint`); acceptance against *real* Cursor components runs in Task 17 once the seed exists. Posture is different: the `collect_endpoint_posture_manifests` adapters land for real in Step 1, so Cursor *posture* findings are fully testable in this task without any stub.

**Files:**
- Modify: `tools/hosts.py` (add the `collect_endpoint_posture_manifests` adapter field from the spec's adapter shape)
- Modify: `tools/posture/__init__.py` (`load_manifest_files` helper; `collect_endpoint_posture_inputs` shared collection helper; optional `manifest_hosts` on `run_posture_rules`)
- Modify: `tools/posture/rules/insecure_transport.py`, `tools/posture/rules/mcp_auto_approve.py` (optional `manifest_hosts` provenance map)
- Modify: `tools/scan.py` (endpoint posture collection over every selected host's collector)
- Modify: `tools/bom_cli.py` (`--host` on `bom endpoint`, via `resolve_endpoint_request`)
- Modify: `tools/remote/cli.py`, `tools/remote/collector.py` (`--host` on `remote sync endpoint`; host-aware graph/posture collection through the shared helper; multi-root path relativization)
- Test: `tests/test_scan.py`, `tests/test_bom_cli.py` (check the actual BOM CLI test file name first), `tests/test_remote/` (same), `tests/test_hosts.py`

**Interfaces:**
- Consumes: `resolve_endpoint_request`, `build_graph(..., host_config_roots=...)` (Task 15).
- Produces: `HostAdapter.collect_endpoint_posture_manifests: Callable[[Path, Optional[Path], list[ComponentRef]], list[tuple[Path, dict]]]` — given `(config_root, project_root, refs)`, the **parsed** MCP-shaped manifests posture should evaluate for that host, as the exact `(Path, dict)` tuples `run_posture_rules` accepts. Parsed tuples rather than paths or directory roots because the live posture API leaves no room for a path list: `run_posture_rules` consumes parsed tuples, and Claude's endpoint collection (`collect_endpoint_mcp_manifests`) derives plugin-install roots from `refs` and walks them — it cannot be expressed as a `(config_root, project_root) -> list[Path]` function at all. Also: `openaca bom endpoint --host`, `openaca remote sync endpoint --host`.

- [ ] **Step 1: Add `collect_endpoint_posture_manifests` to `HostAdapter` and both adapters — define and test the collector boundary before any CLI caller changes**

Claude's binds to the existing `collect_endpoint_mcp_manifests(config_root, project_root, refs)` unchanged — same files, same parsed output, no behavior change. Cursor's reads and parses `config_root / "mcp.json"` plus `project_root / ".cursor" / "mcp.json"` (when `project_root` is given) through a new `tools/posture` helper, `load_manifest_files(paths: list[Path]) -> list[tuple[Path, dict]]`, factored from the read-parse-skip guard `collect_mcp_manifests` already uses so malformed or missing files are silently dropped identically in both paths. Both adapter values bind through lazy wrappers in `tools/hosts.py` — the same cycle-avoidance pattern as `seed_endpoint`, keeping `hosts.py` from importing the posture package at module init. Settings-manifest collection (`collect_endpoint_settings_manifests`) stays Claude-only, gated on `"claude-code"` being selected — Cursor has no settings.json surface in this design.

Unit tests, landed before Steps 2-4: Claude's collector output equals `collect_endpoint_mcp_manifests`'s for a fixture with a plugin `installPath` and a direct `.mcp.json`; Cursor's returns the parsed global+project tuples for a fixture root; a malformed Cursor `mcp.json` is dropped without error.

- [ ] **Step 2: One shared collection helper; make `scan endpoint` posture collection host-aware through it**

The orchestration lives in **one shared helper**, not inline in `tools/scan.py` — because two callers need byte-identical behavior: `scan endpoint` locally and `remote sync endpoint`'s collector (Step 4). Two hand-rolled copies of the union/dedupe/provenance/gating rules would let local and uploaded findings drift apart. Add to `tools/posture/__init__.py`:

```python
def collect_endpoint_posture_inputs(
    host_config_roots: dict[str, Path],
    project_root: Path | None,
    refs: list[ComponentRef],
) -> tuple[list[tuple[Path, dict]], dict[Path, str], list[tuple[Path, dict]]]:
    """Returns (mcp_manifests, manifest_hosts, settings_manifests).

    - mcp_manifests: the concatenation of every selected host's
      collect_endpoint_posture_manifests(root, project_root, refs) output,
      in root-map order, deduped by resolved path (first collector wins).
    - manifest_hosts: each collected path -> the host whose collector
      produced it (collection provenance, for rule attribution).
    - settings_manifests: collect_endpoint_settings_manifests(...) when
      "claude-code" is in the root map, else [] — Cursor has no
      settings.json surface.
    """
```

In `tools/scan.py`'s endpoint command, replace the single-root `collect_endpoint_mcp_manifests(config_dir, ...)` and `collect_endpoint_settings_manifests(config_dir, ...)` calls with one `collect_endpoint_posture_inputs(host_config_roots, project, refs)` call. `run_posture_rules` still runs **once** over the union (see this plan's Posture rule dispatch section — per-host passes would double-count the ref-keyed rules).

Host gating and `active_in` attribution use **collection provenance, not path inference**: while concatenating, the helper builds `manifest_hosts: dict[Path, str]` mapping each manifest path to the host whose collector produced it. `run_posture_rules` gains an optional `manifest_hosts=None` keyword, threaded to the two manifest-keyed host-sensitive rules (`check_insecure_transport`, `check_mcp_auto_approve`, each gaining the same optional keyword): when the map is present it replaces `owning_host(path)` — `active_in` labels come from the map, and `mcp_auto_approve`'s Claude-only per-manifest skip keys on the mapped host. When absent (repo mode, every existing caller), behavior is byte-identical to today. This is load-bearing, not tidiness: `owning_host` classifies by the literal `(".cursor", "mcp.json")` path tail, so any explicit `--config-dir` root with another basename (this plan's fixtures use `tmp_path / "cursor"`) would otherwise have every Cursor manifest misattributed to `claude-code` — wrong `active_in`, and `mcp_auto_approve` applied to a manifest Cursor can't act on. `api_endpoint_override` needs no map: it consumes only the Claude-gated settings manifests. Tests:

```python
# tests/test_scan.py

def test_endpoint_posture_cursor_manifest_active_in_cursor(tmp_path, monkeypatch):
    # Cursor root deliberately named "cursor", not ".cursor" — provenance,
    # not path shape, must drive attribution. http:// MCP url; scan endpoint
    # --host cursor --config-dir <root> --include-posture ->
    # insecure_transport finding with active_in == ["cursor"].
    ...

def test_endpoint_two_host_posture_findings_not_duplicated(tmp_path, monkeypatch):
    # Both hosts' roots via default detection (monkeypatched adapters);
    # ref-keyed rules (mutable_install / skill_capability) fire at most once
    # per ref — assert no duplicate (rule_id, ref) pairs in the output.
    ...

def test_endpoint_posture_dispatch_runs_once_over_union(tmp_path, monkeypatch):
    # Spy on run_posture_rules: exactly ONE call; its manifests argument is
    # the deduped (path, dict) union of both hosts' collector outputs, and
    # manifest_hosts maps each path to the host that collected it.
    ...

def test_endpoint_posture_claude_settings_layer_unchanged(tmp_path, monkeypatch):
    # A claude settings.json mcpServers autoApprove entry still produces the
    # mcp_auto_approve finding through collect_endpoint_settings_manifests —
    # the settings path is untouched by this refactor.
    ...
```

- [ ] **Step 3: `--host` on `bom endpoint`**

In `tools/bom_cli.py`'s `endpoint` command: add the same `--host` option, resolve through `resolve_endpoint_request`, pass `host_config_roots` to `build_graph`. `target` in the emitted BOM stays the first selected host's root string.

Test the *plumbing*, not real Cursor components — those can't exist until Task 17 registers `HOSTS["cursor"].seed_endpoint` (the per-host loop skips seedless adapters, so a bare `bom endpoint --host cursor` here would legitimately emit zero Cursor components): replace the registry entry with a stubbed adapter (`monkeypatch.setitem(HOSTS, "cursor", dataclasses.replace(HOSTS["cursor"], seed_endpoint=<stub adding one mcp_server node>))`), then assert `bom endpoint --host cursor --config-dir <fixture>` exits 0 and emits a CycloneDX doc whose stub component carries `openaca:runtime_hosts` `["cursor"]` and an `endpoint-cursor/`-prefixed bom-ref — proving the request resolution, root map, and BOM emit are wired. The real-component acceptance (dev-linked plugin, no `enabled`/`active` property) runs in Task 17's BOM/remote acceptance step, after the seed exists.

- [ ] **Step 4: `--host` on `remote sync endpoint` and host-aware collection in the remote collector**

Threading the request into `collector.build_endpoint_collection` is not enough on its own: the collector independently makes **four** Claude-shaped calls today — `build_graph(config_dir, mode="endpoint", ...)` (via `_collect_endpoint_components`), `collect_endpoint_mcp_manifests(config_dir, ...)`, `collect_endpoint_settings_manifests(config_dir, ...)`, and `run_posture_rules(refs, mcp_manifests, settings_manifests)` — and each must change explicitly, or remote sync uploads a multi-host BOM whose posture findings still come from the Claude root only (or misattribute a nonstandard Cursor root). Concretely:

1. `build_endpoint_collection` (and `collect_endpoint`) gain the resolved request: `host_config_roots: dict[str, Path]`, threaded from `tools/remote/cli.py`'s `sync endpoint` via the same `--host` option and `resolve_endpoint_request`. `config_dir` stays the first selected root (the API-compatibility anchor, same rule as `build_graph`'s `target`).
2. `_collect_endpoint_components` passes `host_config_roots` through to `build_graph`.
3. The two manifest-collection calls and the `run_posture_rules` inputs are replaced with Step 2's shared `collect_endpoint_posture_inputs(host_config_roots, project, refs)` — the *same* helper `scan endpoint` uses, with `manifest_hosts` passed to `run_posture_rules`, so local and uploaded findings cannot drift (union, dedupe, provenance, and Claude-only settings gating included).
4. The collector's path relativization (`_redact_payload_for_remote`, which strips `config_dir`/`project` prefixes before upload) takes Task 15's `endpoint_discovery_roots(list(host_config_roots), host_config_roots)` result — the exact same complete, labeled descriptor graph normalization consumes — with `project_root` remaining its existing separate root. Preserve those labels (`endpoint/`, `endpoint-<host-id>/`, `endpoint-shared-agents/`, `endpoint-claude-compat/`) when replacing each prefix; do not collapse them to an unlabeled relative path. Selected host roots alone are insufficient: a Cursor-only endpoint reads both `~/.agents/skills` and the Claude compatibility agents directory without either path being under the Cursor root. Leaving either absolute makes a valid collection expose an unhandled machine path to `enforce_remote_upload_contract` and hard-fail.

This extends the CLI-side collection/normalization inputs only; `enforce_remote_upload_contract` and the backend's `validate_upload_privacy` are **not** modified — both validation layers stay exactly as they are, and the contract check remains the backstop that proves the relativization worked. Tests (posture collectors are real as of Step 1, so no stub needed for these):

- A Cursor-only collection through an explicit root deliberately *not* named `.cursor` produces an insecure-transport posture finding with `active_in == ["cursor"]` in the payload; the same collection contains no Claude settings-rule finding (`api_endpoint_override`/settings-layer `mcp_auto_approve` absent).
- A two-host-root collection produces a serialized payload containing neither absolute host root, and the upload-contract check passes.
- A Cursor-only fixture pins `HOME` and `CLAUDE_CONFIG_DIR` under one parent and the explicit Cursor root under an unrelated parent, then includes both a shared global Skill from `~/.agents/skills` and a compatibility Subagent from `<CLAUDE_CONFIG_DIR>/agents`. Assert the serialized payload contains none of the three absolute roots, both refs use their Task 15 auxiliary labels, and `enforce_remote_upload_contract` passes.
- A focused `_redact_payload_for_remote` test supplies synthetic source manifests with the same relative path under the `shared-agents` and `claude-compat` roots. Assert both survive as distinct component occurrences (`endpoint-shared-agents/<rel>` and `endpoint-claude-compat/<rel>`) rather than colliding after relativization. This directly pins provenance-label preservation independently of the two surfaces' different real directory layouts.

- [ ] **Step 5: Run the affected suites**

Run: `uv run pytest tests/test_scan.py tests/ -k "bom or remote or posture" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/hosts.py tools/scan.py tools/bom_cli.py tools/remote/ tests/
git commit -m "feat(endpoint): host-aware posture collection, --host on bom endpoint and remote sync endpoint"
```

---

## Task 17: Cursor's `seed_endpoint` — compose MCP, Skills, Commands, dev-linked Plugins

**Files:**
- Create: `tools/endpoint_seeds/cursor.py`
- Modify: `tools/hosts.py` (`_CURSOR`'s `seed_endpoint` value, same lazy-wrapper pattern as Claude's)
- Test: `tests/test_graph_build.py`, `tests/test_e2e.py`

**Interfaces:**
- Consumes: every host-parameterized parser from Tasks 10-14, plus the `EndpointSeedFn` signature and endpoint contract from Task 15.
- Produces: `seed_endpoint(graph, target, config_root, project_root, normalize, *, warnings=None) -> None` matching `EndpointSeedFn` exactly; registered as `HOSTS["cursor"].seed_endpoint` via a lazy wrapper in `hosts.py`.

**Not composed here: Subagents.** Task 15's cross-host pass in `build_graph`'s endpoint branch owns subagent seeding for every selected host (a shared-file occurrence can span hosts, so no single host's seed can own it); this function must not seed agents at all.

- [ ] **Step 1: Implement the composition function**

```python
# tools/endpoint_seeds/cursor.py
"""Cursor's endpoint-mode composition — the seed_endpoint value for
HOSTS["cursor"]. Unlike Claude Code's, this has no lockfile-backed
install-state to resolve: MCP servers, Skills, and Commands are direct
file reads; Plugins are scoped to dev-linked presence only, with no
enabled-state property, per ADR-0045 Decision #7. Subagents are seeded
by build_graph's cross-host pass, never here.
"""

from __future__ import annotations

import functools

from pathlib import Path
from typing import Optional

from tools.graph import Graph, Node
from tools.graph_build import (
    SourceNormalizer,
    _add_child,
    _realize_agent_plugin,
    _safe_parse,
    descend,
    occurrence_key,
)
from tools.parsers import claude_plugin, mcp_json
from tools.parsers.agent_plugins import is_agent_plugins_manifest
from tools.parsers.claude_skill import parse as parse_skill

def seed_endpoint(
    graph: Graph,
    target: Node,
    config_root: Path,
    project_root: Optional[Path],
    normalize: SourceNormalizer,
    *,
    warnings: Optional[list[str]] = None,
) -> None:
    _seed_remote_mcps(graph, target, config_root, project_root, normalize)
    _seed_direct_skills(graph, target, config_root, project_root, normalize)
    _seed_commands(graph, target, project_root, normalize)
    _seed_dev_linked_plugins(graph, target, config_root, normalize, warnings=warnings)

def _seed_remote_mcps(graph, target, config_root, project_root, normalize) -> None:
    mcp_paths = [config_root / "mcp.json"]
    if project_root is not None:
        mcp_paths.append(project_root / ".cursor" / "mcp.json")
    for mcp_path in mcp_paths:
        if mcp_path.is_file():
            for ref in mcp_json.parse(mcp_path, runtime_hosts=["cursor"]):
                node = Node(key=occurrence_key(ref, normalize), kind=_kind_of(ref), ref=ref)
                _add_child(graph, target, node)

def _shared_agents_skills_root() -> Path:
    # `~/.agents/skills` is a cross-tool, home-scoped convention (Cursor and
    # Codex both read it) — it is NOT Cursor-owned state, so a Cursor
    # --config-dir override must not relocate it: deriving it from
    # config_root.parent would scan <override-parent>/.agents/skills and
    # silently miss the user's real ~/.agents/skills. Path.home() honors
    # $HOME on POSIX (V0 is POSIX-only, ADR-0005), which is also how tests
    # pin it hermetically.
    return Path.home() / ".agents" / "skills"

def _skill_roots(config_root: Path, project_root: Optional[Path]) -> list[Path]:
    # All four Cursor skill roots (spec, Skills section).
    roots = [config_root / "skills", _shared_agents_skills_root()]
    if project_root is not None:
        roots.append(project_root / ".cursor" / "skills")
        roots.append(project_root / ".agents" / "skills")
    return roots

def _seed_direct_skills(graph, target, config_root, project_root, normalize) -> None:
    for skills_root in _skill_roots(config_root, project_root):
        if not skills_root.is_dir():
            continue
        for skill_subdir in sorted(skills_root.iterdir()):
            skill_md = skill_subdir / "SKILL.md"
            if skill_md.is_file():
                for ref in parse_skill(skill_md, runtime_hosts=["cursor"]):
                    node = Node(key=occurrence_key(ref, normalize), kind=_kind_of(ref), ref=ref)
                    _add_child(graph, target, node)

def _seed_commands(graph, target, project_root, normalize) -> None:
    if project_root is None:
        return
    commands_dir = project_root / ".cursor" / "commands"
    if not commands_dir.is_dir():
        return
    from tools.parsers.claude_command_agent import enumerate_dir

    for ref in enumerate_dir(commands_dir, kind="command", scope_owner=None, runtime_hosts=["cursor"]):
        node = Node(key=occurrence_key(ref, normalize), kind="command", ref=ref)
        _add_child(graph, target, node)

_NATIVE_PARSE = functools.partial(claude_plugin.parse, runtime_hosts=["cursor"])

def _seed_dev_linked_plugins(graph, target, config_root, normalize, *, warnings) -> None:
    plugins_local = config_root / "plugins" / "local"
    if not plugins_local.is_dir():
        return
    for plugin_dir in sorted(plugins_local.iterdir()):
        # Both formats, per the spec's Plugins endpoint-mode note: the native
        # .cursor-plugin/plugin.json AND an Agent Plugins root plugin.json.
        # A directory containing both gets both parsed (no silent precedence
        # — same rule as repo mode).
        native = plugin_dir / ".cursor-plugin" / "plugin.json"
        if native.is_file():
            refs = _safe_parse(_NATIVE_PARSE, native)
            if refs and refs[0].extra.get("component_type") == "plugin":
                plugin_node = Node(
                    key=occurrence_key(refs[0], normalize), kind="plugin", ref=refs[0]
                )
                _add_child(graph, target, plugin_node)
                descend(graph, plugin_node, plugin_dir, normalize)
        root_manifest = plugin_dir / "plugin.json"
        if root_manifest.is_file() and is_agent_plugins_manifest(root_manifest):
            _realize_agent_plugin(graph, target, root_manifest, normalize)
```

Plugin realization reuses the two repo-mode contracts rather than defining a third, weaker one:

- **Native format**: mirror `_seed_active_plugins`'s established shape (`tools/graph_build.py:471-481`, Claude's own endpoint plugin seeding) — parse for the self ref, attach the `plugin` node under `target`, then `descend(graph, plugin_node, plugin_dir, normalize)`. Task 13's format-aware plugin branch derives manifest path and Cursor provenance from the self ref, realizes root `mcp.json`/commands/agents/hooks under the plugin node, and descends each bundled skill so its dependency manifests become children of the skill node — the same graph shape repo mode produces for the same plugin. Unlike Claude's endpoint call, `emit_own_root_deps` stays at its default (`True`): Claude suppresses own-root dep manifests only because its tier-2 lockfile walk supplies them, and Cursor has no lockfile — repo mode emits them for this plugin, so endpoint mode must too.
- **Agent Plugins**: call Task 14's `_realize_agent_plugin` — the closed, skills+MCP-only realization with bundled-skill dependency descent — never the native descent (which would enumerate the client-private surfaces the portable contract excludes).

Both parses tolerate a malformed manifest: `claude_plugin.parse` raises on bad JSON (unlike `parse_at_install_root`, whose `[]`-on-failure docstring is the explicit precedent that one bad `plugin.json` must not abort the wider scan), so the native call goes through `_safe_parse`; `is_agent_plugins_manifest`/`agent_plugins.parse` already return `False`/`[]` on read/JSON failure, and `_realize_agent_plugin` guards the empty/self-ref-missing cases internally. A corrupt entry under `plugins/local` is skipped — the same silent-skip convention every other `_safe_parse` site in graph build uses — and every other plugin and endpoint surface still seeds. The self-ref guard (`refs[0].extra.get("component_type") == "plugin"`) also covers a well-formed manifest with no `name`: the parser then emits bundled refs only, and attaching `refs[0]` would turn a bundled component into the plugin node.

**Verify `_add_child`, `occurrence_key`, `_kind_of` (or whatever the actual node-kind-from-ref helper is named) against `tools/graph_build.py`'s real, current implementation before writing this file** — the exact helper names and signatures must be confirmed by reading the module directly, not assumed from this plan text. The one **hard requirement**, independent of exact helper names: no `enabled`/`active` key is ever set in a dev-linked plugin ref's `extra` dict — `claude_plugin.parse` doesn't set one today and this function must not add one either.

- [ ] **Step 2: Write the failing test — Cursor endpoint composition across every surface**

```python
# tests/test_graph_build.py

def _cursor_endpoint_fixture(tmp_path):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    (cursor_root / "skills" / "global-skill").mkdir(parents=True)
    (cursor_root / "skills" / "global-skill" / "SKILL.md").write_text(
        "---\nname: global-skill\ndescription: d\n---\nrun\n"
    )
    (home / ".agents" / "skills" / "shared-skill").mkdir(parents=True)
    (home / ".agents" / "skills" / "shared-skill" / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: d\n---\nrun\n"
    )
    (cursor_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    native_root = cursor_root / "plugins" / "local" / "demo"
    (native_root / ".cursor-plugin").mkdir(parents=True)
    (native_root / ".cursor-plugin" / "plugin.json").write_text(
        '{"name": "demo", "hooks": {"postToolUse": [{"command": "echo done"}]}}'
    )
    (native_root / "skills" / "bundled-skill").mkdir(parents=True)
    (native_root / "skills" / "bundled-skill" / "SKILL.md").write_text(
        "---\nname: bundled-skill\ndescription: d\n---\nrun\n"
    )
    (native_root / "skills" / "bundled-skill" / "package.json").write_text(
        '{"dependencies": {"left-pad": "1.0.0"}}'
    )
    (native_root / "mcp.json").write_text(
        '{"mcpServers": {"bundled-mcp": {"command": "npx", "args": ["bundled-mcp@1.0.0"]}}}'
    )
    (native_root / "commands").mkdir()
    (native_root / "commands" / "plugin-cmd.md").write_text("run\n")
    (native_root / "agents").mkdir()
    (native_root / "agents" / "plugin-agent.md").write_text("---\nname: plugin-agent\n---\nbody\n")
    open_root = cursor_root / "plugins" / "local" / "open-demo"
    open_root.mkdir(parents=True)
    (open_root / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "open-demo"}'
    )
    (open_root / "skills" / "ap-skill").mkdir(parents=True)
    (open_root / "skills" / "ap-skill" / "SKILL.md").write_text(
        "---\nname: ap-skill\ndescription: d\n---\nrun\n"
    )
    (open_root / "skills" / "ap-skill" / "package.json").write_text(
        '{"dependencies": {"left-pad": "1.0.0"}}'
    )
    (open_root / "mcp.json").write_text(
        '{"mcpServers": {"open-mcp": {"command": "npx", "args": ["open-mcp@1.0.0"]}}}'
    )
    (open_root / "commands").mkdir()
    (open_root / "commands" / "not-portable.md").write_text("run\n")
    broken_dir = cursor_root / "plugins" / "local" / "broken" / ".cursor-plugin"
    broken_dir.mkdir(parents=True)
    (broken_dir / "plugin.json").write_text("{not json")
    project = tmp_path / "project"
    (project / ".cursor" / "skills" / "proj-skill").mkdir(parents=True)
    (project / ".cursor" / "skills" / "proj-skill" / "SKILL.md").write_text(
        "---\nname: proj-skill\ndescription: d\n---\nrun\n"
    )
    (project / ".agents" / "skills" / "proj-shared").mkdir(parents=True)
    (project / ".agents" / "skills" / "proj-shared" / "SKILL.md").write_text(
        "---\nname: proj-shared\ndescription: d\n---\nrun\n"
    )
    (project / ".cursor" / "commands").mkdir(parents=True)
    (project / ".cursor" / "commands" / "deploy.md").write_text("run\n")
    return home, cursor_root, project

def test_endpoint_cursor_seed_endpoint_composes_all_surfaces(tmp_path, monkeypatch):
    home, cursor_root, project = _cursor_endpoint_fixture(tmp_path)
    monkeypatch.setenv("HOME", str(home))  # pins Path.home() -> ~/.agents/skills
    g = build_graph(
        cursor_root,
        mode="endpoint",
        project_root=project,
        host_config_roots={"cursor": cursor_root},
    )
    skill_names = {n.ref.name for n in g.nodes.values() if n.kind == "skill" and n.ref}
    assert skill_names == {
        "global-skill", "shared-skill", "proj-skill", "proj-shared",
        "bundled-skill", "ap-skill",
    }
    assert any(n.kind == "mcp_server" for n in g.nodes.values())
    assert any(n.kind == "command" for n in g.nodes.values())
    plugin_names = {n.ref.name for n in g.nodes.values() if n.kind == "plugin" and n.ref}
    assert plugin_names == {"demo", "open-demo"}  # "broken" skipped, scan not aborted
    for n in g.nodes.values():
        if n.kind == "plugin" and n.ref is not None:
            assert "enabled" not in n.ref.extra
            assert "active" not in n.ref.extra
    parent_of = {e.child: e.parent for e in g.edges}
    plugins = {n.ref.name: n for n in g.nodes.values() if n.kind == "plugin" and n.ref}
    def _children_by_kind(plugin_node):
        kids = [n for n in g.nodes.values() if parent_of.get(n.key) == plugin_node.key]
        return {n.kind for n in kids}, kids
    demo_kinds, demo_kids = _children_by_kind(plugins["demo"])
    # Native bundle fully realized under the plugin, repo-parity: every
    # surface plus the inline camelCase hook.
    assert {"skill", "mcp_server", "command", "agent", "hook"} <= demo_kinds
    open_kinds, open_kids = _children_by_kind(plugins["open-demo"])
    # Agent Plugins closed surface: skills+MCP only — the commands/ dir
    # produces no node (would fail if endpoint realization reused the
    # native descent).
    assert open_kinds == {"skill", "mcp_server"}
    for kid in demo_kids + open_kids:
        assert kid.ref is not None and kid.ref.extra["runtime_hosts"] == ["cursor"]
    # Bundled skills keep their dependency-manifest chains in endpoint mode,
    # same as repo mode: each bundled skill node has a package child.
    for skill_node in [n for n in demo_kids + open_kids if n.kind == "skill"]:
        dep_kinds = {
            n.kind for n in g.nodes.values() if parent_of.get(n.key) == skill_node.key
        }
        assert "package" in dep_kinds, f"{skill_node.ref.name} lost its dep chain"
```

(Adapt the edge-lookup idiom to `tools/graph.py`'s real `Graph`/`Edge` shape — read it first, same note as Task 13's graph test.) The six-way `skill_names` assertion is the point: it fails if any of the four direct skill roots (global `.cursor/skills`, global `.agents/skills`, project `.cursor/skills`, project `.agents/skills`) or either plugin's bundled skill is skipped. The two-way `plugin_names` assertion fails if Agent Plugins are missing from dev-linked discovery *and* if the malformed `broken` entry aborts the scan (the test would error before reaching it) — one corrupt dev-linked manifest must cost exactly one plugin, nothing else. The per-plugin child assertions fail on flattening, dropped Cursor provenance, a native bundle realized attach-only (no `hook`/`command`/`agent` children, no skill dep chain), or an Agent Plugin leaking non-portable surfaces.

One more test locks in the home-scoped shared root: an explicit `--config-dir` that is *not* under the home directory must neither relocate `~/.agents/skills` nor drag in a `.agents` sibling of the override:

```python
# tests/test_graph_build.py

def test_shared_agents_skills_root_is_home_scoped_not_override_relative(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".agents" / "skills" / "home-shared").mkdir(parents=True)
    (home / ".agents" / "skills" / "home-shared" / "SKILL.md").write_text(
        "---\nname: home-shared\ndescription: d\n---\nrun\n"
    )
    monkeypatch.setenv("HOME", str(home))
    override_root = tmp_path / "elsewhere" / "cursor-config"  # NOT under home
    (override_root / "skills").mkdir(parents=True)
    # A .agents sibling of the override — must NOT be scanned.
    (tmp_path / "elsewhere" / ".agents" / "skills" / "decoy").mkdir(parents=True)
    (tmp_path / "elsewhere" / ".agents" / "skills" / "decoy" / "SKILL.md").write_text(
        "---\nname: decoy\ndescription: d\n---\nrun\n"
    )
    g = build_graph(
        override_root, mode="endpoint", host_config_roots={"cursor": override_root}
    )
    skill_names = {n.ref.name for n in g.nodes.values() if n.kind == "skill" and n.ref}
    assert "home-shared" in skill_names
    assert "decoy" not in skill_names
    shared = next(
        n for n in g.nodes.values()
        if n.kind == "skill" and n.ref and n.ref.name == "home-shared"
    )
    assert "endpoint-shared-agents/skills/home-shared/SKILL.md" in shared.key
    assert str(home) not in shared.key
    assert str(override_root) not in shared.key
```

The key assertions are the BOM-ref stability contract: graph node keys feed BOM refs, so merely finding `home-shared` is insufficient. With `HOME` and the explicit config override under unrelated parents, this test must prove the selected auxiliary label replaces the machine path.

- [ ] **Step 3: Run tests to verify they fail, then implement until they pass**

Run: `uv run pytest tests/test_graph_build.py -k cursor_seed_endpoint -v`
Iterate Step 1's implementation against real helper signatures until this passes. Expected final: PASS.

- [ ] **Step 4: Wire `HOSTS["cursor"].seed_endpoint`**

In `tools/hosts.py`, add a `_cursor_seed_endpoint` lazy wrapper (deferred `from tools.endpoint_seeds.cursor import seed_endpoint` inside the function body — same cycle-avoidance as Claude's wrapper in Task 15) and set it as `_CURSOR`'s `seed_endpoint`. Update Task 15's `test_cursor_adapter_seed_endpoint_none_until_cursor_endpoint_task` to assert it is now non-None (rename accordingly).

- [ ] **Step 5: Run the full endpoint-mode suite, including the two-host coexistence test**

Run: `uv run pytest tests/test_graph_build.py -k endpoint -v`
Expected: PASS — Task 15's Claude-only snapshot test unchanged, this task's composition test green, and Task 15's `test_endpoint_two_hosts_coexist_under_one_target` now exercising real Cursor children (un-guard its cursor-side key-prefix assertions if they were landed guarded).

- [ ] **Step 6: Write the cross-layer e2e test**

```python
# tests/test_e2e.py

def test_cursor_endpoint_scan_end_to_end(tmp_path, monkeypatch):
    """Endpoint-mode Cursor scan through the real CLI: MCP + Skills +
    dev-linked Plugin discovered, host-labeled, no enabled-state asserted
    for the plugin, findings attributed to cursor."""
    monkeypatch.setenv("HOME", str(tmp_path))  # ~/.agents/skills is home-scoped; keep the scan hermetic
    cursor_root = tmp_path / "cursor"
    (cursor_root / "skills" / "helper").mkdir(parents=True)
    (cursor_root / "skills" / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: d\n---\nrun\n"
    )
    (cursor_root / "mcp.json").write_text(
        '{"mcpServers": {"git": {"command": "npx", "args": ["@cyanheads/git-mcp-server@1.1.0"]},'
        ' "insecure-api": {"url": "http://insecure.example/mcp"}}}'
    )
    plugin_dir = cursor_root / "plugins" / "local" / "demo" / ".cursor-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan", "endpoint",
            "--host", "cursor",
            "--config-dir", str(cursor_root),
            "--include-posture", "--format", "json",
        ],
    )
    doc = json.loads(result.output)

    vuln = [
        f for f in doc["findings"]
        if f.get("finding_type") == "vulnerability" and f.get("id") == "GHSA-3q26-f695-pp76"
    ]
    assert vuln and vuln[0]["active_in"] == ["cursor"]
    posture = [
        f for f in doc["findings"]
        if f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture and posture[0]["active_in"] == ["cursor"]
    assert not [
        f for f in doc["findings"] if f.get("rule_id") == "openaca-posture-mcp-auto-approve"
    ]
```

Uses conftest's offline-OSV fixture map (`@cyanheads/git-mcp-server@1.1.0` → `GHSA-3q26-f695-pp76`, same as the repo-mode e2e test above). Before finalizing, verify the endpoint command's actual `click.option` names (`--config-dir`, `--include-posture`, `--format`) against `tools/scan.py` — adjust the invocation if any differ, and extend the JSON-shape assertions to however the output names scanned hosts (assert `cursor` appears there too, once the field name is confirmed by reading the render code).

- [ ] **Step 7: BOM and remote acceptance against real Cursor components**

Task 16 proved the `bom endpoint`/`remote sync endpoint` plumbing with a stubbed Cursor seed; now the seed is real, land the component-level acceptance it deferred:

- `bom endpoint --host cursor --config-dir <fixture>` against a dev-linked plugin fixture emits a CycloneDX doc whose plugin component carries `openaca:runtime_hosts` `["cursor"]` and **no** `enabled`/`active` property (the Task 16 Step 3 assertion, now against real output).
- `build_endpoint_collection` for a Cursor-only request through an explicit root not named `.cursor` produces a payload containing the Cursor graph components (MCP + plugin bom-refs present, `endpoint-cursor/`-prefixed) alongside the Cursor-attributed posture findings Task 16 already asserted — real components and findings reaching one upload payload together.

- [ ] **Step 8: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS, full suite green.

- [ ] **Step 9: Commit**

```bash
git add tools/endpoint_seeds/cursor.py tools/hosts.py tests/
git commit -m "feat(endpoint): Cursor seed_endpoint — MCP, Skills, Commands, dev-linked Plugins (both formats)"
```

---

## Task 18: Documentation updates

**Files:**
- Modify: `README.md`, `docs/reference/coverage.md`, `docs/reference/cli.md`

**Interfaces:** None (documentation only).

- [ ] **Step 1: Update coverage/README claims**

Read the current text of each file's Cursor-related claims (added during this plan's earlier Task 1-9 execution) and update them: repo-mode coverage now includes Plugins/Subagents/Commands, not just MCP/Skills; endpoint mode is no longer "not yet supported for Cursor" — it now covers every surface with the one named exception (marketplace-installed Plugin enabled-state, dev-linked-only). Claims about `bom endpoint` and `remote sync endpoint` supporting Cursor may only be written if Task 16's plumbing tests *and* Task 17's real-component acceptance for those commands landed and pass — documentation follows verified behavior, never the other way around.

- [ ] **Step 2: Update `cli.md`'s Host selection section**

Document `--host` now applying to `openaca scan endpoint`, `openaca bom endpoint`, and `openaca remote sync endpoint`, with the detected-hosts default and the explicit `--config-dir` single-host rule (Task 15's contract), alongside repo mode's registered-hosts default already documented there from Task 8 above.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/reference/coverage.md docs/reference/cli.md
git commit -m "docs: update Cursor coverage claims for Plugins, Subagents, Commands, and endpoint mode"
```

---

## Design context bound to this plan

The four sections below carry the dispatch, contract, migration, and
acceptance-scenario context the tasks above implement and the
Verification section checks. They live here rather than in
`docs/specs/multi-host-support.md` because they specify *how the
implementation must behave*, not what the design aims for.

## Posture rule dispatch

Of the five existing posture rules, only one needs a host gate at the
call site: `api_endpoint_override.py` matches literal Anthropic-settings
env-key names (`anthropic_base_url`, `anthropic_auth_token`) and hardcodes
`active_in=["claude-code"]` — genuinely Claude-schema-specific.
`skill_capability.py` and `mutable_install.py` already read
`runtime_hosts` from `ref.extra` rather than hardcoding; they need no
change and correctly fire against Cursor components across every
surface in scope.
`skill_capability` gates on `component_type == "skill"` explicitly, so
Commands/Subagents/Plugins refs never reach it regardless of host.
`mutable_install`'s plugin branch only fires when `"gitCommitSha" in
ref.extra` — an explicit marker only Claude's `installed_plugins.json`
path sets — so Cursor's presence-only, lockfile-less plugin refs
(dev-linked, no `gitCommitSha` key at all) correctly never trigger a
mutable-install finding either; verified by reading
`_mutable_install_source_for` directly, not assumed from the rule's
name. `insecure_transport.py` and `mcp_auto_approve.py` need the
mechanical `_infer_hosts()` fix described in the spec's Hooks section,
but the two rules land
differently on Cursor once that fix is in: `insecure_transport` is a
host-agnostic transport check and correctly fires against a Cursor
manifest with `active_in=["cursor"]`. `mcp_auto_approve` does **not**.
Checked against Cursor's own MCP documentation (no manifest-level
`autoApprove` field exists there — see the field-comparison note two
paragraphs above): the rule has nothing to key on for a Cursor manifest,
so it skips Cursor-owned manifests entirely and produces no finding, not
a finding with reduced confidence — this isn't an observability gap (the
rule still applies, we just can't always see the true state), it's that
Cursor has no applicable state at all. `mcp_auto_approve` remains
Claude-only, same as `api_endpoint_override`, just via a per-manifest
skip inside the rule rather than a call-site gate.

**`run_posture_rules` keeps running once per scan, not once per host.**
Running it once per detected host would double- or triple-count findings
from the two rules that key off
`refs` rather than manifests: `mutable_install.check_mutable_install(refs)`
and `skill_capability.check_skill_executable_tools(refs)` both scan the
full ref list with no manifest filter, so calling the dispatcher once per
host would re-run them against the same, already-host-tagged refs N
times. Instead: the endpoint caller concatenates every selected host's
parsed manifests via each host's `collect_endpoint_posture_manifests`,
deduped by resolved path, and `run_posture_rules` runs once over the
union. There are two endpoint callers — `scan endpoint` locally and
`remote sync endpoint`'s collector — and both consume one shared
collection helper rather than reimplementing the union/dedupe/
provenance/gating rules, so locally rendered and uploaded findings
cannot drift apart. Host attribution and Claude-only skips inside the manifest-keyed
rules (`insecure_transport`, `mcp_auto_approve`) come from **collection
provenance**: the caller passes an optional `manifest_hosts` map (each
collected manifest path → the host whose collector produced it), which
overrides the rules' path-based `owning_host` inference. Provenance
rather than path shape because an explicit `--config-dir` root need not
be named `.cursor` — `owning_host` would misattribute every manifest
under such a root to `claude-code`. Repo mode and every existing caller
omit the map, keeping today's path-based behavior byte-identical. The
manifest-keyed rules never see refs — they take `(path, dict)` tuples
only — so host labels cannot come from `runtime_hosts` on a matched
ref. The caller (which already knows which hosts are selected) skips
`api_endpoint_override`'s manifests for non-Claude hosts via
`HostAdapter.posture_rule_ids`; the rule module itself never imports a
host concept.

## Downstream contracts

- **Remote ingest consumers don't persist `openaca:runtime_hosts`
  today.** A consumer that wants per-host rollups needs its own
  coordinated change (persist the property, keep it out of any cross-BOM
  join key exactly the way host is already kept out of
  `openaca:identity`);
  until one lands, this design's aggregated-view payoff — "which
  endpoints run this server in Cursor vs. Claude Code" — is unrealized.
  That consumer-side change lives in its own repository, not here.
- **The redaction contract needs no changes, including for endpoint
  mode.** `tools/remote/upload_contract.py` operates on `openaca:*`
  property names generically and doesn't special-case `claude-code`.
  Cursor's `auth`/`envFile` fields fall under the existing forbidden-name
  pattern; needs fixtures, not new logic. Endpoint mode reads real
  machine paths (`~/.cursor/...`) for the first time for Cursor, but the
  same path-normalization (`_make_normalizer` in `graph_build.py`, which
  already strips the machine-specific scan root before it becomes a
  `bom-ref`) and the same absolute-path/secret-shape validation apply
  uniformly regardless of host — this is a generic, host-agnostic
  pipeline stage, not something that needs a Cursor-specific branch.
- **`bom diff` / rollups**: verified against `bom_diff.py` — diffing is
  keyed strictly by `bom-ref`, and Claude occurrence keys are stable
  across the multi-host change, so a Claude-only → Claude+Cursor diff
  reports Cursor components as added (they are genuinely newly scanned
  occurrences) without perturbing existing Claude entries. The
  content-identity comparison includes `openaca:runtime_hosts`, so an
  existing occurrence whose host set changes without any other content
  change (e.g. a `.claude/agents` file gaining or losing Cursor
  readability through an override) surfaces as changed. Consequence: a
  machine newly detecting a second host produces one legitimate wave of
  changed components across its existing occurrences, not silence.
- **No overlay schema change anywhere.** This entire project is
  scanner/BOM-side; `schema/openaca.schema.json` and `overlays/*.yaml` are
  untouched, since overlays are keyed on upstream IDs and never reference
  host.

## Migration

`openaca:runtime_hosts` already exists in the current BOM schema
(`docs/openaca-bom-schema.md`) — this design populates an existing
optional field more completely and fixes the `_infer_hosts` bug, it
doesn't change that field's semantics.

The BOM schema does bump `0.4` → `0.5`, for a separate reason: this work
removed the derived singular `openaca:agent_host` companion property
(ADR-0044 Decision #2), leaving `openaca:runtime_hosts` as the sole
host-provenance property. Removing a property from the `openaca:*`
vocabulary is what `openaca:schema_version` exists to signal.
`schema/openaca-bom.schema.json` accepts `0.1`-`0.5`, so BOMs emitted at
earlier versions still validate, and CycloneDX `specVersion` stays `1.7`
— it names the upstream format, which is unchanged.

Host provenance is derived from what was parsed, not independently
stored, so absence on old BOMs is already meaningful (consistent with
ADR-0042's "absent is meaningful" stance). **Don't backfill old rows to
`claude-code`** — that invents provenance that was never actually
recorded distinctly, even though it happens to be true today.

## Testing

Mirrors `composition-graph.md`'s per-layout-assertion style:

- Cursor `mcp.json` (global + project) → `mcp_server` refs,
  `runtime_hosts=["cursor"]`, top-level children of `target`.
- Same npm MCP server in both `~/.claude/.mcp.json` and
  `~/.cursor/mcp.json` → two occurrences, one `openaca:identity`, distinct
  `bom-ref`, each with its own single-element `runtime_hosts`.
- `insecure_transport` against a Cursor manifest → `active_in=["cursor"]`,
  not `["claude-code"]`.
- `mcp_auto_approve` against a Cursor manifest → no finding at all, not
  `active_in=["cursor"]` — `mcp_auto_approve` has nothing to key on for
  Cursor (see the Posture rule dispatch section above) and skips
  Cursor-owned manifests entirely, same as `api_endpoint_override` does
  for the next bullet.
- `api_endpoint_override` never fires on Cursor settings.
- `--host cursor` with no `~/.cursor` present → hard error.
- Claude-only machine, default invocation → byte-identical output to
  pre-Cursor behavior (regression guard for the default-detection
  change).
- Both hosts present, default invocation → both scanned, output states
  which hosts (including the endpoint-card `host_surface` label, not
  just the text/JSON summary line).
- `mutable_install` / `skill_capability` (ref-keyed, not manifest-keyed)
  against a two-host scan → each finding appears once, not once per
  host pass.
- Repo containing only `.cursor/mcp.json`, scanning machine has no
  `~/.cursor` → scans successfully; `detect()`'s config-root check does
  not gate repo-mode manifest discovery.
- Cursor `.cursor-plugin/plugin.json` → `plugin` refs using the same
  unqualified `plugin/{name}` identity scheme Claude's own plugins use
  (not a Cursor-specific namespace), `openaca:identity` absent (no
  marketplace info in repo mode, same as Claude's repo-mode plugins),
  bundled skills/agents/commands/MCP walked via the same reused
  parsers as direct components, hooks through the format-aware layer
  (camelCase events, `cursor-hook` occurrence label),
  `rules`/`variables` not walked.
- Root `plugin.json` with `$schema` pointing at `agent-plugins.org`
  (Agent Plugins format) → same `plugin` ref shape/identity as Cursor
  Plugins, detected by manifest location and `$schema`, never guessed
  from content; only its `skills/` and `mcp.json` are walked —
  `agents`/`commands`/`hooks` are not (not part of the portable v1
  contract), even if the plugin author populated Cursor-Plugins-shaped
  fields the Agent Plugins schema doesn't define.
- A directory containing both a root `plugin.json` and a
  `.cursor-plugin/plugin.json` → manifest accounting parses both files
  (each is a real manifest on disk), but graph realization gives the
  **native format precedence**: one plugin node, realized from
  `.cursor-plugin/plugin.json`, and the root `plugin.json` is not
  realized. The two formats share a bundle layout (`skills/`, root
  `mcp.json`), so realizing both would place one occurrence under two
  plugin parents and abort the scan. Repo and endpoint mode apply the
  same rule. A native manifest that fails to realize (corrupt JSON, no
  `name`) claims nothing, so the Agent Plugins manifest still realizes.
- A `plugin.json` sitting directly in a host-owned config directory
  (`.claude/`, `.cursor/`, `.agents/`) is **not** an Agent Plugins bundle
  root for graph realization: that directory's `skills/<name>/SKILL.md`
  is the host's own project-skill shape, already discovered and parented
  to the target by the registry walk. Manifest accounting still counts
  the file.
- `.cursor/commands/*.md` → `command` refs, `runtime_hosts=["cursor"]`,
  same identity scheme as Claude commands (no host qualifier),
  `openaca:identity` absent for repo-local (no plugin owner), same as
  Claude's own repo-local commands today.
- `.claude/agents/helper.md` alone, no `.cursor/agents/` counterpart,
  both hosts selected → **one** occurrence,
  `runtime_hosts=["claude-code","cursor"]`.
- `.claude/agents/helper.md` **and** `.cursor/agents/helper.md` both
  present, same relative path, both hosts selected → **two** single-host
  occurrences (`["claude-code"]` and `["cursor"]` respectively) — Cursor
  does not read Claude's copy when its own override exists.
- Same subagent-override scenario, but only `claude-code` selected (not
  `cursor`) → the single-occurrence/override distinction above must not
  apply; a non-selected host's hypothetical read never changes another
  host's occurrence count *or metadata* — the Claude occurrence keeps
  its exact pre-Cursor `extra`, with no `runtime_hosts` key.
- Endpoint mode, both hosts present → single `Graph`, single target
  `Node`, children from both hosts' `seed_endpoint` calls coexist under
  it; no duplicate target nodes, no `bom-ref` collisions; output states
  both hosts scanned, including the endpoint-card `host_surface` label.
- Endpoint mode, Claude-only machine (no `~/.cursor`), default
  invocation → byte-identical output to single-host Claude-only
  behavior (regression guard for endpoint-mode default detection).
- Endpoint mode, subagent precedence at **both** global
  (`~/.cursor/agents/` vs. `~/.claude/agents/`) and project scope
  (`<project_root>/.cursor/agents/` vs. `<project_root>/.claude/agents/`)
  → the resolver runs independently at each scope; an override at one
  scope must not affect occurrence counting at the other.
- `~/.cursor/plugins/local/<name>/.cursor-plugin/plugin.json` present
  (dev-linked) → `plugin` ref reported, **no** `enabled`/`active`
  property emitted at all (property absent, not `false`) — distinct
  from Claude's endpoint-mode plugin refs, which do carry a real
  enabled-state signal.
- `~/.cursor/plugins/cache/<marketplace>/<name>/<sha>/` with
  `.cache-complete` → `plugin` ref reported with bundled surfaces
  nested under it, `extra["cursor_marketplace_dir"]` carrying the
  marketplace segment, no `enabled`/`active` property, identity the
  unqualified `plugin/{name}`; a version dir without `.cache-complete`
  seeds nothing; a manifest-less bundle seeds the synthesized
  presence-only ref (`extra["manifest"] = "absent"`) with its
  `skills/`/`commands/` walked.
- Endpoint mode, `--host cursor` explicit on a machine with no
  `~/.cursor` → hard error, same as repo mode's equivalent case.

## Verification

After all tasks: run the full CI-equivalent matrix, matching the discipline established after this project's earlier "did you run all the tests CI runs" gap —

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest --cov=tools --cov-report=term-missing
uv run openaca lint overlays/
uv run openaca lint capabilities/
```

Then the `smoke-install` job's equivalent: `uv build --wheel`, install into a fresh venv with `--prerelease=explicit`, then:

1. `openaca --version`; `openaca scan repo` and `openaca bom repo` against a fixture repo containing both hosts' manifests (`.cursor/mcp.json` + `.claude/skills/`) — output must name both hosts.
2. Endpoint two-host detection: create a fixture home dir containing both `.claude/` (settings.json + empty plugins lockfile) and `.cursor/` (mcp.json), run `HOME=<fixture-home> CLAUDE_CONFIG_DIR=<fixture-home>/.claude openaca scan endpoint --format json` — assert (by inspecting the JSON) that both hosts appear in the scanned-hosts output and that at least one component from each host's root is present. Cursor's default root derives from the home directory, so the `HOME` override is what makes `detect()` find the fixture's `.cursor`.
3. `HOME=<fixture-home> openaca scan endpoint --host cursor --config-dir <fixture-home>/.cursor --format json` — exit 0, cursor-only output (the `HOME` override keeps the home-scoped `~/.agents/skills` root inside the fixture instead of the runner's real home).
4. `HOME=<fixture-home> openaca bom endpoint --host cursor --config-dir <fixture-home>/.cursor` — valid CycloneDX, components carry `openaca:runtime_hosts` `["cursor"]`.
