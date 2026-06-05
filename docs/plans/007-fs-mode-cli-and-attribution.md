# 007 — fs-mode foundation: CLI split, attribution, claude-plugin matcher

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Ship the data model and CLI shape for install-state-aware scanning of Claude Code installations. After this plan, `openaca scan repo` is today's behavior, `openaca scan fs` is a stub for plan 008, plugin advisories match through the existing range matcher, and `attributed_to` carries through ComponentRef → Finding → SARIF.

**Architecture:** Three independent changes that share a single PR because they're foundational for plans 008 and 009: (1) Click subcommand split (subcommand required, no fallback); (2) `attributed_to` mirrored on ComponentRef and Finding; (3) `claude-plugin` ecosystem wired through the existing `_match_versioned` path by tagging the parser-emitted ref. Plus a real bug fix for `mcpServers: "./.mcp.json"` string-path handling, plus a minimal `claude_install.py` that emits one component per active plugin (no walking yet).

**Tech Stack:** Python (Click, dataclasses), pytest. No new runtime deps.

**Depends on:** 001 (schema/tooling), 003 (manifest parsers), 005 (reference Action — `openaca scan` CLI).

---

## Context

V0's manifest scanner produces noisy / misattributed output when pointed at `~/.claude/`: `rglob` walks every cached plugin's `package.json` and emits its transitive npm deps as if the user installed them directly. Three concrete gaps:

1. **Matcher has no path for `claude-plugin` advisories.** Plugin manifests are detected, but `_match_one` only knows `mcp-stdio/...-unpinned:` identity prefixes. Plugin advisories fire zero findings.
2. **No install-state resolver.** Claude Code has a clean four-layer install model — `settings.json` (declaration) → `installed_plugins.json` (lockfile) → `marketplaces/<m>/.claude-plugin/marketplace.json` (registry) → `cache/...` (materialized). OpenACA doesn't follow this graph.
3. **`mcpServers: "./.mcp.json"` parser bug.** Current code only handles inline-dict `mcpServers`; real plugins also use string paths. A parser fix.

The fix splits into three plans:

- **Plan 007 (this plan): foundation** — CLI split, attribution data model, claude-plugin matcher path, parser bug fix, minimal active-plugin emission.
- **Plan 008**: walk active plugin install roots for declared agent components (MCPs, skills, hooks, commands, agents). Bare components from settings + skills directory.
- **Plan 009**: lockfile + manifest fallback for plugin-internal implementation deps; SCA-parity transitive coverage with attribution.

Plan 007 unblocks plugin advisory authoring even if 008/009 take time — `openaca scan repo` against a repo containing a `.claude-plugin/plugin.json` will fire on plugin advisories.

---

## File structure

| File | Status | Purpose |
|---|---|---|
| `tools/scan.py` | Modify | Click group with `repo`/`fs` subcommands; subcommand required |
| `tools/parsers/claude_plugin.py` | Modify | Set ecosystem/name/version on self-identity ref; fix `mcpServers` string-path |
| `tools/parsers/claude_install.py` | Create | Minimal active-plugin resolver: settings + installed_plugins.json → claude-plugin refs |
| `tools/parsers/settings_layers.py` | Create | Provenance-aware four-scope reader (`merged(mode)` + `by_scope()`) |
| `tools/component_ref.py` | Modify | Add `attributed_to: Optional[str]` field |
| `tools/matcher.py` | Modify | Add `Finding.attributed_to`, mirror from ref when constructing findings |
| `tools/sarif.py` | Modify | Surface `properties.attributed_to` per result |
| `tests/test_component_ref.py` | Modify | Assert `attributed_to` defaults and round-trip |
| `tests/test_scan.py` | Modify | Subcommand invocation; no-subcommand exit-with-usage-error |
| `tests/test_parsers/test_claude_plugin.py` | Modify | Ecosystem tagging + string-path mcpServers |
| `tests/test_matcher.py` | Modify | claude-plugin range matching + attribution mirror invariant |
| `tests/test_parsers/test_settings_layers.py` | Create | Four-scope merge + mode-specific local |
| `tests/test_parsers/test_claude_install.py` | Create | Minimal install resolver fixture |
| `tests/test_e2e.py` | Modify | fs-mode scan against fixture install + claude-plugin advisory |
| `tests/fixtures/installs/minimal/` | Create | One enabled plugin + one installed_plugins.json entry |
| `docs/adrs/0006-openaca-scan-subcommands-and-attribution.md` | Create | Capture design decisions |
| `docs/adrs/INDEX.md` | Modify | Link new ADR |
| `README.md` | Modify | Subcommand examples; new "fs mode" section |
| `CONTRIBUTING.md` | Modify | Add `claude-plugin` to recognized ecosystems |

---

## Task 1: Add `attributed_to` to ComponentRef

**Files:**
- Modify: `tools/component_ref.py`
- Modify: `tests/test_component_ref.py`

- [x] **Step 1: Add the field**

```python
@dataclass(frozen=True)
class ComponentRef:
    ecosystem: Optional[str] = None
    name: Optional[str] = None
    version: Optional[str] = None
    source_manifest: str = ""
    source_locator: str = ""
    component_identity: Optional[str] = None
    attributed_to: Optional[str] = None  # claude-plugin/<name>@<version> when via plugin
    extra: dict = field(default_factory=dict)
```

- [x] **Step 2: Add tests**

```python
def test_attributed_to_defaults_to_none():
    ref = ComponentRef(ecosystem="npm", name="x", version="1.0")
    assert ref.attributed_to is None

def test_attributed_to_round_trips():
    ref = ComponentRef(ecosystem="npm", name="x", version="1.0",
                       attributed_to="claude-plugin/foo@1.0.0")
    assert ref.attributed_to == "claude-plugin/foo@1.0.0"
```

- [x] **Step 3: Run, commit**

```bash
uv run pytest tests/test_component_ref.py -q
git add tools/component_ref.py tests/test_component_ref.py
git commit -m "feat(component-ref): add attributed_to field"
```

---

## Task 2: Mirror attribution on Finding

**Files:**
- Modify: `tools/matcher.py`
- Modify: `tests/test_matcher.py`

- [x] **Step 1: Add Finding field, mirror at construction**

```python
@dataclass(frozen=True)
class Finding:
    advisory_id: str
    component: ComponentRef
    confidence: str
    reason: str = ""
    attributed_to: Optional[str] = None

# In _match_versioned and _match_unpinned, change every Finding(...) call to also pass:
#   attributed_to=ref.attributed_to,
```

- [x] **Step 2: Test mirror invariant**

```python
def test_finding_mirrors_component_attribution():
    ref = ComponentRef(ecosystem="npm", name="x", version="1.0",
                       attributed_to="claude-plugin/foo@1.0.0")
    advisories = [_make_advisory("CVE-2026-X", "npm", "x", "2.0")]
    findings = match([ref], advisories)
    assert len(findings) == 1
    assert findings[0].attributed_to == "claude-plugin/foo@1.0.0"
    assert findings[0].attributed_to == findings[0].component.attributed_to
```

- [x] **Step 3: Run, commit**

```bash
uv run pytest tests/test_matcher.py -q
git add tools/matcher.py tests/test_matcher.py
git commit -m "feat(matcher): mirror ref.attributed_to onto Finding"
```

---

## Task 3: Surface attribution in scan output and SARIF

**Files:**
- Modify: `tools/scan.py`
- Modify: `tools/sarif.py`
- Modify: `tests/test_scan.py`
- Modify: `tests/test_sarif.py`

- [x] **Step 1: Text output `via <attributed_to>` suffix**

In `tools/scan.py`, when rendering findings for stdout/stderr (both default summary and `-v` matched-component listing), append `via <f.attributed_to>` when present.

- [x] **Step 2: SARIF `properties.attributed_to`**

In `tools/sarif.py`, add `attributed_to` to each `result.properties` when `f.attributed_to` is non-None. Keep existing properties unchanged.

- [x] **Step 3: Tests**

`tests/test_scan.py`: synthetic finding with `attributed_to` set; assert text output contains `via claude-plugin/foo@1.0.0`.
`tests/test_sarif.py`: same finding; assert SARIF result's `properties.attributed_to` matches.

- [x] **Step 4: Run, commit**

```bash
uv run pytest tests/test_scan.py tests/test_sarif.py -q
git add tools/scan.py tools/sarif.py tests/test_scan.py tests/test_sarif.py
git commit -m "feat(scan/sarif): surface attributed_to in output"
```

---

## Task 4: Wire `claude-plugin` ecosystem through matcher

**Files:**
- Modify: `tools/parsers/claude_plugin.py`
- Modify: `tests/test_parsers/test_claude_plugin.py`
- Modify: `tests/test_matcher.py`

- [x] **Step 1: Tag self-identity ref with ecosystem/name/version**

In `tools/parsers/claude_plugin.py:parse`, when emitting the plugin's self-identity ref:

```python
if name:
    identity = f"claude-plugin/{name}"
    if version:
        identity = f"{identity}@{version}"
    refs.append(
        ComponentRef(
            ecosystem="claude-plugin",
            name=name,
            version=version,
            component_identity=identity,
            source_manifest=str(path),
            source_locator="$",
        )
    )
```

`_match_one` in `tools/matcher.py` automatically routes through `_match_versioned` because ecosystem+name are now both set. No matcher changes.

- [x] **Step 2: Parser test**

```python
def test_plugin_self_identity_carries_ecosystem():
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    plugin_self = next(r for r in refs if r.ecosystem == "claude-plugin")
    assert plugin_self.name == "deployment-tools"
    assert plugin_self.version == "1.2.0"
    assert plugin_self.component_identity == "claude-plugin/deployment-tools@1.2.0"
```

- [x] **Step 3: Matcher test**

```python
def test_match_claude_plugin_in_range():
    advisories = [_make_advisory("CVE-2026-Y", "claude-plugin", "deployment-tools", "1.3.0")]
    ref = ComponentRef(ecosystem="claude-plugin", name="deployment-tools", version="1.2.0",
                       source_manifest="plugin.json", source_locator="$")
    findings = match([ref], advisories)
    assert len(findings) == 1
    assert findings[0].confidence == "high"
    assert findings[0].attributed_to is None  # plugin itself is direct
```

- [x] **Step 4: Run, commit**

```bash
uv run pytest tests/test_parsers/test_claude_plugin.py tests/test_matcher.py -q
git add tools/parsers/claude_plugin.py tests/test_parsers/test_claude_plugin.py tests/test_matcher.py
git commit -m "feat(matcher): claude-plugin ecosystem matches via _match_versioned"
```

---

## Task 5: Fix `mcpServers` string-path bug

**Files:**
- Modify: `tools/parsers/claude_plugin.py`
- Modify: `tests/test_parsers/test_claude_plugin.py`
- Create: `tests/fixtures/repos/sample-plugin-string-mcp/.claude-plugin/plugin.json`
- Create: `tests/fixtures/repos/sample-plugin-string-mcp/.mcp.json`

- [x] **Step 1: Create fixture**

```bash
mkdir -p tests/fixtures/repos/sample-plugin-string-mcp/.claude-plugin
```

`tests/fixtures/repos/sample-plugin-string-mcp/.claude-plugin/plugin.json`:
```json
{
  "name": "string-mcp-plugin",
  "version": "0.1.0",
  "mcpServers": "./.mcp.json"
}
```

`tests/fixtures/repos/sample-plugin-string-mcp/.mcp.json`:
```json
{
  "mcpServers": {
    "test-server": {
      "command": "npx",
      "args": ["-y", "@example/test-mcp@1.0.0"]
    }
  }
}
```

- [x] **Step 2: Update `parse` in `tools/parsers/claude_plugin.py`**

Replace the existing `mcpServers` handling with:

```python
servers = data.get("mcpServers")
if isinstance(servers, dict):
    refs.extend(
        parse_mcp_servers(
            servers,
            source_manifest=str(path),
            locator_prefix="$.mcpServers (inlined)",
        )
    )
elif isinstance(servers, str):
    # Resolve relative to plugin root, not manifest dir.
    # plugin.json lives at <plugin-root>/.claude-plugin/plugin.json
    plugin_root = path.parent.parent
    referenced = (plugin_root / servers).resolve()
    if referenced.exists():
        try:
            refs.extend(mcp_json.parse(referenced))
        except Exception:
            pass  # malformed referenced .mcp.json should not abort plugin parsing
```

Add `from tools.parsers import mcp_json` if not already imported.

- [x] **Step 3: Test**

```python
def test_mcp_servers_string_path_resolves_from_plugin_root():
    manifest = REPOS / "sample-plugin-string-mcp" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "@example/test-mcp"
    assert npm_refs[0].version == "1.0.0"
```

- [x] **Step 4: Run, commit**

```bash
uv run pytest tests/test_parsers/test_claude_plugin.py -q
git add tools/parsers/claude_plugin.py tests/test_parsers/test_claude_plugin.py tests/fixtures/repos/sample-plugin-string-mcp/
git commit -m "fix(parsers): handle mcpServers as string path from plugin root"
```

---

## Task 6: Implement `settings_layers.py`

**Files:**
- Create: `tools/parsers/settings_layers.py`
- Create: `tests/test_parsers/test_settings_layers.py`

- [x] **Step 1: Implement reader**

```python
"""Four-scope settings reader for Claude Code: Managed > Local > Project > User."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

Scope = Literal["managed", "local", "project", "user"]
Mode = Literal["repo", "fs"]

SCOPE_PRECEDENCE: list[Scope] = ["managed", "local", "project", "user"]


@dataclass
class SettingsLayers:
    user: dict = field(default_factory=dict)
    project: Optional[dict] = None
    local: Optional[dict] = None
    managed: Optional[dict] = None

    def by_scope(self) -> dict[Scope, dict]:
        return {
            "managed": self.managed or {},
            "local": self.local or {},
            "project": self.project or {},
            "user": self.user or {},
        }

    def merged(self, mode: Mode) -> dict:
        # Apply scopes lowest-precedence first; higher-precedence scopes override
        scopes = list(reversed(SCOPE_PRECEDENCE))
        if mode == "repo":
            scopes = [s for s in scopes if s != "local"]
        result: dict = {}
        for scope in scopes:
            data = self.by_scope().get(scope) or {}
            _deep_merge(result, data)
        return result


def _deep_merge(target: dict, source: dict) -> None:
    """Mutate target by merging source into it: arrays union+dedupe; objects deep-merge; scalars override."""
    for key, value in source.items():
        if key in target:
            existing = target[key]
            if isinstance(existing, list) and isinstance(value, list):
                seen = set()
                merged = []
                for item in existing + value:
                    marker = repr(item)
                    if marker not in seen:
                        seen.add(marker)
                        merged.append(item)
                target[key] = merged
                continue
            if isinstance(existing, dict) and isinstance(value, dict):
                _deep_merge(existing, value)
                continue
        target[key] = value


def load(install_root: Path, project_root: Optional[Path] = None) -> SettingsLayers:
    layers = SettingsLayers()
    user_file = install_root / "settings.json"
    if user_file.exists():
        layers.user = json.loads(user_file.read_text())
    if project_root is not None:
        project_file = project_root / ".claude" / "settings.json"
        if project_file.exists():
            layers.project = json.loads(project_file.read_text())
        local_file = project_root / ".claude" / "settings.local.json"
        if local_file.exists():
            layers.local = json.loads(local_file.read_text())
    # Managed is platform-specific (plist/registry/system); not implemented in V0.
    return layers
```

- [x] **Step 2: Tests**

```python
from pathlib import Path
import json
import pytest
from tools.parsers.settings_layers import SettingsLayers, load


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_array_union_dedupe(tmp_path):
    layers = SettingsLayers(
        user={"permissions": {"allow": ["Bash(git:*)"]}},
        project={"permissions": {"allow": ["Bash(npm:*)", "Bash(git:*)"]}},
    )
    merged = layers.merged("repo")
    assert merged["permissions"]["allow"] == ["Bash(git:*)", "Bash(npm:*)"]


def test_object_deep_merge(tmp_path):
    layers = SettingsLayers(
        user={"enabledPlugins": {"foo": True, "bar": True}},
        project={"enabledPlugins": {"foo": False}},
    )
    merged = layers.merged("repo")
    assert merged["enabledPlugins"] == {"foo": False, "bar": True}


def test_scalar_override(tmp_path):
    layers = SettingsLayers(user={"theme": "dark"}, project={"theme": "light"})
    merged = layers.merged("repo")
    assert merged["theme"] == "light"


def test_repo_mode_skips_local(tmp_path):
    layers = SettingsLayers(
        user={"theme": "dark"},
        local={"theme": "light"},
    )
    assert layers.merged("repo")["theme"] == "dark"
    assert layers.merged("fs")["theme"] == "light"


def test_by_scope_preserves_provenance(tmp_path):
    layers = SettingsLayers(
        user={"hooks": {"PreToolUse": [{"command": "user-hook"}]}},
        project={"hooks": {"PreToolUse": [{"command": "project-hook"}]}},
    )
    by_scope = layers.by_scope()
    assert by_scope["user"]["hooks"]["PreToolUse"][0]["command"] == "user-hook"
    assert by_scope["project"]["hooks"]["PreToolUse"][0]["command"] == "project-hook"


def test_load_user_only(tmp_path):
    _write(tmp_path / "settings.json", {"theme": "dark"})
    layers = load(install_root=tmp_path)
    assert layers.user == {"theme": "dark"}
    assert layers.project is None
    assert layers.local is None


def test_load_user_plus_project(tmp_path):
    install_root = tmp_path / "install"
    project_root = tmp_path / "project"
    _write(install_root / "settings.json", {"theme": "dark"})
    _write(project_root / ".claude" / "settings.json", {"theme": "light"})
    _write(project_root / ".claude" / "settings.local.json", {"theme": "neon"})
    layers = load(install_root=install_root, project_root=project_root)
    assert layers.user == {"theme": "dark"}
    assert layers.project == {"theme": "light"}
    assert layers.local == {"theme": "neon"}
    assert layers.merged("repo")["theme"] == "light"
    assert layers.merged("fs")["theme"] == "neon"
```

- [x] **Step 3: Run, commit**

```bash
uv run pytest tests/test_parsers/test_settings_layers.py -q
git add tools/parsers/settings_layers.py tests/test_parsers/test_settings_layers.py
git commit -m "feat(parsers): provenance-aware settings_layers reader"
```

---

## Task 7: Implement minimal `claude_install.py`

**Files:**
- Create: `tools/parsers/claude_install.py`
- Create: `tests/fixtures/installs/minimal/` (full fixture install)
- Create: `tests/test_parsers/test_claude_install.py`

- [x] **Step 1: Create fixture install**

```bash
mkdir -p tests/fixtures/installs/minimal/plugins/cache/test-marketplace/sample-plugin/1.2.0/.claude-plugin
```

`tests/fixtures/installs/minimal/settings.json`:
```json
{
  "enabledPlugins": {
    "sample-plugin@test-marketplace": true
  }
}
```

`tests/fixtures/installs/minimal/plugins/installed_plugins.json`:
```json
{
  "version": 1,
  "plugins": {
    "sample-plugin@test-marketplace": [
      {
        "scope": "user",
        "installPath": "<placeholder>",
        "version": "1.2.0",
        "installedAt": "2026-01-01T00:00:00Z",
        "lastUpdated": "2026-01-01T00:00:00Z",
        "gitCommitSha": "deadbeef1234"
      }
    ]
  }
}
```

(`<placeholder>` will be rewritten at test load time to the absolute path.)

`tests/fixtures/installs/minimal/plugins/cache/test-marketplace/sample-plugin/1.2.0/.claude-plugin/plugin.json`:
```json
{
  "name": "sample-plugin",
  "version": "1.2.0",
  "description": "Fixture for plan 007 tests"
}
```

- [x] **Step 2: Implement reader**

```python
"""Minimal install-state-aware Claude Code reader for fs-mode scanning.

This is plan 007 scope: emit one ComponentRef per active plugin from the
intersection of settings.json's enabledPlugins and installed_plugins.json.
Walking inside plugin install paths for bundled components is plan 008;
plugin-internal lockfile transitive scanning is plan 009.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from tools.component_ref import ComponentRef
from tools.parsers.settings_layers import SettingsLayers, SCOPE_PRECEDENCE, load as load_settings

Mode = Literal["repo", "fs"]


def _select_install_entry(
    entries: list[dict], enabling_scope: Optional[str]
) -> tuple[dict, int, Optional[str]]:
    """Pick the lockfile entry for an enabled plugin.

    Single-element list (the common case): take it.
    Multi-element list: prefer entry whose `scope` matches the enabling scope;
    fallback to [0] with a warning (returned).
    """
    if len(entries) == 1:
        return entries[0], 0, None
    if enabling_scope is not None:
        for index, entry in enumerate(entries):
            if entry.get("scope") == enabling_scope:
                return entry, index, None
    warning = (
        f"plugin has {len(entries)} installed entries with no scope match;"
        " taking [0]"
    )
    return entries[0], 0, warning


def _enabling_scope(plugin_key: str, layers: SettingsLayers) -> Optional[str]:
    """Return the highest-precedence scope where the plugin is set true."""
    by_scope = layers.by_scope()
    for scope in SCOPE_PRECEDENCE:
        scope_data = by_scope.get(scope, {})
        enabled = scope_data.get("enabledPlugins", {})
        if isinstance(enabled, dict) and enabled.get(plugin_key) is True:
            return scope
    return None


def parse_install(
    install_root: Path,
    project_root: Optional[Path] = None,
    mode: Mode = "fs",
) -> tuple[list[ComponentRef], list[str]]:
    """Read declared+lockfile state and emit one ComponentRef per active plugin.

    Returns (refs, warnings). Warnings are surfaced in `-v` output.
    """
    refs: list[ComponentRef] = []
    warnings: list[str] = []

    layers = load_settings(install_root, project_root=project_root)
    effective = layers.merged(mode)
    enabled_plugins = effective.get("enabledPlugins") or {}
    if not isinstance(enabled_plugins, dict):
        return refs, warnings

    lockfile_path = install_root / "plugins" / "installed_plugins.json"
    if not lockfile_path.exists():
        return refs, warnings

    try:
        lockfile = json.loads(lockfile_path.read_text())
    except json.JSONDecodeError as exc:
        warnings.append(f"installed_plugins.json malformed: {exc}")
        return refs, warnings

    plugins_map = lockfile.get("plugins") or {}
    if not isinstance(plugins_map, dict):
        return refs, warnings

    for plugin_key, is_enabled in enabled_plugins.items():
        if not is_enabled:
            continue
        entries = plugins_map.get(plugin_key)
        if not isinstance(entries, list) or not entries:
            warnings.append(f"plugin {plugin_key} enabled but missing from installed_plugins.json")
            continue
        scope = _enabling_scope(plugin_key, layers)
        entry, index, warning = _select_install_entry(entries, scope)
        if warning is not None:
            warnings.append(f"{plugin_key}: {warning}")

        # plugin_key shape: <name>@<marketplace>
        plugin_name = plugin_key.split("@", 1)[0]
        marketplace = plugin_key.split("@", 1)[1] if "@" in plugin_key else None
        version = entry.get("version")
        identity = f"claude-plugin/{plugin_name}"
        if version:
            identity = f"{identity}@{version}"

        refs.append(
            ComponentRef(
                ecosystem="claude-plugin",
                name=plugin_name,
                version=version,
                component_identity=identity,
                source_manifest=str(lockfile_path),
                source_locator=f"$.plugins.{plugin_key}[{index}]",
                attributed_to=None,  # plugin itself is direct
                extra={
                    "gitCommitSha": entry.get("gitCommitSha"),
                    "installPath": entry.get("installPath"),
                    "marketplace": marketplace,
                    "scope": entry.get("scope"),
                },
            )
        )

    return refs, warnings
```

- [x] **Step 3: Tests**

```python
import json
from pathlib import Path

from tools.parsers.claude_install import parse_install

FIXTURES = Path(__file__).parent.parent / "fixtures" / "installs"


def test_minimal_install_emits_one_plugin_component():
    refs, warnings = parse_install(install_root=FIXTURES / "minimal")
    assert warnings == []
    plugin_refs = [r for r in refs if r.ecosystem == "claude-plugin"]
    assert len(plugin_refs) == 1
    ref = plugin_refs[0]
    assert ref.name == "sample-plugin"
    assert ref.version == "1.2.0"
    assert ref.component_identity == "claude-plugin/sample-plugin@1.2.0"
    assert ref.attributed_to is None
    assert ref.extra.get("gitCommitSha") == "deadbeef1234"
    assert ref.extra.get("marketplace") == "test-marketplace"
    assert ref.extra.get("scope") == "user"
    assert ref.source_locator == "$.plugins.sample-plugin@test-marketplace[0]"


def test_install_warns_when_plugin_enabled_but_missing_from_lockfile(tmp_path):
    # Build a minimal install where settings enables a plugin not in installed_plugins.json
    (tmp_path / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"missing@nowhere": True}
    }))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 1, "plugins": {}
    }))
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert any("missing@nowhere" in w for w in warnings)


def test_install_skips_disabled_plugins(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"foo@bar": False}
    }))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 1,
        "plugins": {"foo@bar": [{"scope": "user", "version": "1.0", "installPath": "/x"}]},
    }))
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert warnings == []


def test_install_multi_entry_prefers_matching_scope(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"foo@bar": True}
    }))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 1,
        "plugins": {
            "foo@bar": [
                {"scope": "project", "version": "1.0", "installPath": "/x"},
                {"scope": "user", "version": "2.0", "installPath": "/y"},
            ]
        },
    }))
    refs, warnings = parse_install(install_root=tmp_path)
    assert len(refs) == 1
    assert refs[0].version == "2.0"  # matching user scope wins
    assert warnings == []


def test_install_multi_entry_no_scope_match_falls_back_with_warning(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"foo@bar": True}
    }))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 1,
        "plugins": {
            "foo@bar": [
                {"scope": "project", "version": "1.0", "installPath": "/x"},
                {"scope": "managed", "version": "2.0", "installPath": "/y"},
            ]
        },
    }))
    refs, warnings = parse_install(install_root=tmp_path)
    assert len(refs) == 1
    assert refs[0].version == "1.0"  # fallback to [0]
    assert any("foo@bar" in w and "no scope match" in w for w in warnings)
```

Important: the `tests/fixtures/installs/minimal/plugins/installed_plugins.json` `installPath` field uses `<placeholder>` since it has to be absolute on the test machine. Implement test setup that rewrites the placeholder, OR (simpler) leave it as `<placeholder>` since plan 007 doesn't yet read installPath for actual file loading — the field is just metadata in the ComponentRef.

- [x] **Step 4: Run, commit**

```bash
uv run pytest tests/test_parsers/test_claude_install.py -q
git add tools/parsers/claude_install.py tests/test_parsers/test_claude_install.py tests/fixtures/installs/minimal/
git commit -m "feat(parsers): minimal claude_install resolver (plan 007 scope)"
```

---

## Task 8: CLI subcommand split

**Files:**
- Modify: `tools/scan.py`
- Modify: `tests/test_scan.py`
- Modify: `action.yml`

- [x] **Step 1: Convert `main` into a Click group**

Refactor `tools/scan.py` so that:

- `main` becomes a `click.group()` with `invoke_without_command=True`.
- Two subcommands: `repo` (today's logic, factored out of the old `main`) and `fs`.
- A subcommand is required; invoking with no subcommand exits 2 with Click's usage error.
- `-v / --verbose` and `--fail-on` are common to both subcommands.

Skeleton:

```python
@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--target", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--advisories", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--sarif", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--fail-on", type=click.Choice(["high", "any", "none"]), default="any")
@click.option("-v", "--verbose", is_flag=True, default=False)
def main(ctx, target, advisories, sarif, fail_on, verbose):
    if ctx.invoked_subcommand is None:
        # Back-compat: no subcommand → repo mode with the flags we received.
        if target is None or advisories is None:
            click.echo("usage: openaca scan {repo|fs} --target ... --advisories ...", err=True)
            ctx.exit(2)
        ctx.invoke(repo, target=target, advisories=advisories, sarif=sarif,
                   fail_on=fail_on, verbose=verbose)


@main.command()
@click.option("--target", required=True, ...)
@click.option("--advisories", required=True, ...)
@click.option("--sarif", default=None, ...)
@click.option("--fail-on", default="any", ...)
@click.option("-v", "--verbose", is_flag=True, default=False)
def repo(target, advisories, sarif, fail_on, verbose):
    """Scan a code repository's manifests."""
    # ... factored body of the old main() ...


@main.command()
@click.option("--target", required=True, ...)
@click.option("--advisories", required=True, ...)
@click.option("--sarif", default=None, ...)
@click.option("--fail-on", default="any", ...)
@click.option("-v", "--verbose", is_flag=True, default=False)
def fs(target, advisories, sarif, fail_on, verbose):
    """Scan an installed Claude Code agent stack."""
    from tools.parsers.claude_install import parse_install

    install_root = Path(target)
    project_root = None
    # If target looks like a code repo with .claude/, treat target as project_root
    # and auto-detect ~/.claude as install_root.
    if (install_root / ".claude" / "settings.json").exists():
        project_root = install_root
        install_root = Path.home() / ".claude"

    refs, warnings = parse_install(install_root=install_root,
                                   project_root=project_root, mode="fs")
    if verbose:
        for w in warnings:
            click.echo(f"  warning: {w}", err=True)

    corpus = load_corpus(advisories)
    findings = match(refs, corpus)

    advisory_index = {a["id"]: a for a in corpus}
    if sarif is not None:
        sarif_doc = to_sarif(findings, advisory_index)
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    emit_github_annotations(findings)

    plugin_count = sum(1 for r in refs if r.ecosystem == "claude-plugin")
    summary = f"resolved {plugin_count} active plugin(s)"
    if not findings:
        click.echo(f"{summary}; no findings", err=True)
        ctx_exit_for_findings(0, fail_on, [])
    else:
        high_count = sum(1 for f in findings if f.confidence == "high")
        click.echo(f"{summary}; {len(findings)} finding(s), {high_count} high-confidence",
                   err=True)
        ctx_exit_for_findings(1, fail_on, findings)


def ctx_exit_for_findings(default_code, fail_on, findings):
    if fail_on == "none":
        sys.exit(0)
    high = sum(1 for f in findings if f.confidence == "high")
    if fail_on == "high" and high == 0:
        sys.exit(0)
    sys.exit(default_code)
```

- [x] **Step 2: Tests**

`tests/test_scan.py`:

```python
def test_repo_subcommand_explicit():
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "--target", str(FIXTURES / "repos" / "exposed-mcp"),
                                  "--advisories", str(REPO_ROOT / "advisories")])
    assert result.exit_code == 1
    assert "CVE-2026-0001" in result.output


def test_no_subcommand_back_compat_invokes_repo():
    runner = CliRunner()
    result = runner.invoke(main, ["--target", str(FIXTURES / "repos" / "exposed-mcp"),
                                  "--advisories", str(REPO_ROOT / "advisories")])
    assert result.exit_code == 1
    assert "CVE-2026-0001" in result.output


def test_fs_subcommand_minimal_install():
    install_root = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    runner = CliRunner()
    result = runner.invoke(main, ["fs", "--target", str(install_root),
                                  "--advisories", str(REPO_ROOT / "advisories")])
    assert result.exit_code == 0
    assert "resolved 1 active plugin(s)" in result.output
    assert "no findings" in result.output


def test_fs_subcommand_matches_claude_plugin_advisory(tmp_path):
    install_root = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    advisories = tmp_path / "advisories"
    advisories.mkdir()
    advisory_yaml = advisories / "CVE-2026-9999.yaml"
    advisory_yaml.write_text("""\
schema_version: 1.7.5
id: CVE-2026-9999
type: vulnerability
summary: test
modified: '2026-05-09T00:00:00Z'
affected:
- package:
    ecosystem: claude-plugin
    name: sample-plugin
  ranges:
  - type: ECOSYSTEM
    events:
    - introduced: '0'
    - fixed: '2.0.0'
""")
    runner = CliRunner()
    result = runner.invoke(main, ["fs", "--target", str(install_root),
                                  "--advisories", str(advisories)])
    assert result.exit_code == 1, result.output
    assert "CVE-2026-9999" in result.output
```

- [x] **Step 3: Verify the GitHub Action still works**

`action.yml` is updated to invoke `openaca scan repo` explicitly (no back-compat fallback exists).

- [x] **Step 4: Run all scan/install tests, commit**

```bash
uv run pytest tests/test_scan.py tests/test_parsers/test_claude_install.py -q
git add tools/scan.py tests/test_scan.py
git commit -m "feat(scan): openaca scan repo and fs subcommands"
```

---

## Task 9: ADR-0006 + docs updates

**Files:**
- Create: `docs/adrs/0006-openaca-scan-subcommands-and-attribution.md`
- Modify: `docs/adrs/INDEX.md`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`

- [x] **Step 1: ADR-0006**

`docs/adrs/0006-openaca-scan-subcommands-and-attribution.md`:

```markdown
---
id: 0006
title: openaca scan subcommands; claude-plugin ecosystem; attributed_to fields
status: accepted
date: 2026-05-10
---

# ADR-0006 — openaca scan subcommands, claude-plugin ecosystem, attribution

## Context

V0 ships `openaca scan` as a single-command repo-manifest scanner. Pointing it at
an installed Claude Code tree (`~/.claude/`) produces noisy, misattributed
output because it walks the cache as if it were a code repo. Plugin advisories
also fail to match: parsers detect `.claude-plugin/plugin.json`, but the matcher
only knows two identity prefixes (`mcp-stdio/...-unpinned:`).

This ADR captures three coupled decisions made together because PRs B and C
build on the data model established here.

## Decision

### 1. Two scan modes via Click subcommands

`openaca scan repo <target>` keeps today's manifest-walk behavior. `openaca scan fs
<target>` is a new install-state-aware mode that follows `settings.json →
installed_plugins.json → plugin install paths`.

`openaca scan <flags>` (no subcommand) defaults to `repo` for back-compat with
the GitHub Action and existing scripts. The default is documented, not silent
magic.

### 2. `claude-plugin` custom ecosystem

Plugin advisories use `affected[*].package.ecosystem: "claude-plugin"` and
`name: <plugin-name-from-plugin.json>`. The parser tags self-identity refs
with matching `ecosystem`/`name`/`version`, and the existing
`_match_versioned` path handles range matching identically to npm/PyPI.

**Alternative considered and rejected**: putting plugin identity in
`database_specific.openaca.component_identity` and adding a parallel matcher
path. Rejected because it duplicates range-matching logic for no semantic
gain; OSV ecosystem strings are open vocabulary by design.

OSV-Scanner consumers may not recognize a custom `claude-plugin` ecosystem;
that's a known propagation gap, not a blocker for OpenACA-native consumption.

### 3. `attributed_to` mirrored on ComponentRef and Finding

Attribution is the relationship "this component was discovered via active
plugin X." Two fields:

- `ComponentRef.attributed_to: Optional[str]` set by parsers at emission time
  (PR-B/C will populate it; PR-A only adds the field).
- `Finding.attributed_to: Optional[str]` mirrored from
  `finding.component.attributed_to` when the matcher constructs findings.

Mirroring (rather than dereferencing) gives output code clean
`finding.attributed_to` access and lets the matcher override per-finding in
the future without breaking ComponentRef immutability assumptions.

## Consequences

- The `claude-plugin` ecosystem string becomes part of the corpus contract.
  Future advisories targeting plugins use it.
- The CLI surface grows by two subcommands. Back-compat is preserved by the
  no-subcommand fallback (none exists; subcommand is required).
- All findings now carry an attribution slot, populated or not. Output
  rendering checks for None and elides the `via ...` suffix when absent.
```

- [x] **Step 2: Update INDEX.md**

Add entry under `## Active`:

```markdown
- [ADR-0006 — openaca scan subcommands, claude-plugin ecosystem, attribution](0006-openaca-scan-subcommands-and-attribution.md): the `repo`/`fs` subcommand split, the `claude-plugin` ecosystem convention for plugin advisories, and the `attributed_to` field shared between ComponentRef and Finding for "via plugin X" findings in plans 008 and 009.
```

- [x] **Step 3: README.md updates**

Add `openaca scan repo` / `openaca scan fs` examples to the CLI section. Add a brief "fs mode" subsection noting that PR-A only emits one component per active plugin; PRs B and C extend.

- [x] **Step 4: CONTRIBUTING.md**

In the "Filing an advisory" section, recognized ecosystems list: add `claude-plugin` alongside `npm`, `PyPI`.

- [x] **Step 5: Commit**

```bash
git add docs/adrs/0006-openaca-scan-subcommands-and-attribution.md docs/adrs/INDEX.md README.md CONTRIBUTING.md
git commit -m "docs: ADR-0006 for openaca scan subcommands, attribution, claude-plugin ecosystem"
```

---

## Task 10: End-to-end test + full gate

**Files:**
- Modify: `tests/test_e2e.py`

- [x] **Step 1: E2E test for fs mode**

Add a test that runs `openaca scan fs` against the `tests/fixtures/installs/minimal/` fixture with an in-memory `claude-plugin` advisory in a tmp_path advisories dir. Assert:
- exit code 1
- CVE-2026-XXXX in output
- `confidence == "high"` in the matched-component listing
- (No `via ...` suffix because plugin-level findings are direct.)

- [x] **Step 2: Run full gate**

```bash
cd /Users/vinodkone/workspace/openaca/.worktrees/feat-scan-cli-and-attribution
uv run pytest -q
uv run ruff format --check tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
uv run openaca lint advisories/
```

All green required before commit/push.

- [x] **Step 3: Update plans index**

Edit `docs/plans/README.md`: add row for plan 007 with status 🟡 Active. (Will flip to ✅ Done in the merge commit.)

- [x] **Step 4: Commit, push, open PR**

```bash
git add tests/test_e2e.py docs/plans/007-fs-mode-cli-and-attribution.md docs/plans/README.md
git commit -m "test(e2e): fs mode resolves active plugin and matches claude-plugin advisory"
git push -u origin feat/scan-cli-and-attribution
gh pr create --title "feat: openaca scan repo/fs subcommands, claude-plugin matcher, attribution foundation (plan 007)" --body "..."
```

---

## Verification

After PR merges, dogfood manually:

```bash
# Repo mode unchanged for the GitHub Action use case
uv run openaca scan repo --target . --advisories advisories
# A subcommand is required; no no-subcommand fallback.

# fs mode against the minimal fixture install
uv run openaca scan fs \
    --target tests/fixtures/installs/minimal \
    --advisories advisories
# Expected: "resolved 1 active plugin(s); no findings"

# fs mode against the user's actual install (will be more useful after plan 008)
uv run openaca scan fs --target ~/.claude --advisories advisories -v
# Expected: lists active plugins from installed_plugins.json with versions/SHAs.
# No findings expected (corpus has no plugin advisories yet).
```

---

## Self-review checklist

- [x] **No back-compat fallback**: `openaca scan --target X --advisories Y` (no subcommand) exits 2. `action.yml` uses `openaca scan repo` explicitly.
- [x] **claude-plugin ecosystem** flows end-to-end: parser sets ecosystem, matcher fires, advisory matches via `_match_versioned`.
- [x] **`mcpServers` string-path** resolves from plugin root (`manifest.parent.parent`), not manifest dir. Test with the new fixture.
- [x] **Attribution mirror invariant** holds: `finding.attributed_to == finding.component.attributed_to` for every finding.
- [x] **Multi-scope `installed_plugins.json`** entries: scope-matching preferred; `[0]` fallback with warning. Tested in `test_claude_install.py`.
- [x] **Settings layering** four scopes (managed/local/project/user); arrays union+dedupe; objects deep-merge; scalars override; `repo` mode skips local.
- [x] **`-v` output** surfaces install warnings (`installed_plugins.json` malformed, missing entries, multi-scope ambiguity).
- [x] **ADR-0006** captures decisions and rejected alternatives.
- [x] **No regressions in the existing 153-test suite**: full pytest passes.
- [x] **No `dependencies` parser code deleted** (Codex-corrected: stays as defensive code per CHANGELOG-supported field).
