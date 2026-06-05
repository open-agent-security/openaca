# 003 — Manifest Parsers

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the four V0 manifest parsers that read a target repository's agent-installation manifests and emit a normalized stream of `ComponentRef` records. Each parser owns one file format. The reference Action (Plan 005) consumes the combined output.

**Architecture:** A single `ComponentRef` dataclass captures the result. Each parser is a small module exposing `parse(path: Path) -> list[ComponentRef]`. A registry maps file-name patterns to parsers. Standard PURL is emitted whenever the manifest declares a known ecosystem (`pkg:npm`, `pkg:pypi`, `pkg:github`, `pkg:docker`); when it doesn't (Claude Code plugin marketplaces, MCP-stdio launches), the ref carries an OpenACA-native identity string under `component_identity` and leaves `purl` empty.

**Tech Stack:** Python 3.11+, stdlib JSON, `pyyaml`, `tomllib` (stdlib in 3.11+) for `pyproject.toml` if added later. No new runtime deps.

**Depends on:** 001 (project setup, `tools/` package, pytest).

---

## File structure

| File | Purpose |
|---|---|
| `tools/component_ref.py` | `ComponentRef` dataclass + `to_purl` helpers |
| `tools/parsers/__init__.py` | Registry of `(filename_pattern, parser_fn)` |
| `tools/parsers/package_json.py` | Parse `package.json` (and lockfiles in V0.1, optional) |
| `tools/parsers/mcp_json.py` | Parse `mcp.json` and `.mcp.json` (`mcpServers` map) |
| `tools/parsers/claude_plugin.py` | Parse `.claude-plugin/plugin.json` |
| `tools/parsers/claude_settings.py` | Parse `.claude/settings.json` (installed plugin enumeration) |
| `tests/test_component_ref.py` | Unit tests for purl helpers |
| `tests/test_parsers/test_package_json.py` | `package.json` parser tests |
| `tests/test_parsers/test_mcp_json.py` | `mcp.json` parser tests |
| `tests/test_parsers/test_claude_plugin.py` | Plugin manifest tests |
| `tests/test_parsers/test_claude_settings.py` | Settings parser tests |
| `tests/fixtures/repos/<name>/...` | Fixture repos exercising each manifest |

---

## Task 1: `ComponentRef` dataclass and PURL helpers

**Files:**
- Create: `tools/component_ref.py`
- Create: `tests/test_component_ref.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_component_ref.py
import pytest

from tools.component_ref import ComponentRef, encode_purl_name


@pytest.mark.parametrize("name, expected", [
    ("simple", "simple"),
    ("@scope/name", "%40scope/name"),
    ("name with spaces", "name%20with%20spaces"),
])
def test_encode_purl_name(name, expected):
    assert encode_purl_name(name) == expected


def test_purl_for_npm_with_scope():
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.2.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    assert ref.purl == "pkg:npm/%40cyanheads/git-mcp-server@1.2.0"


def test_purl_for_pypi():
    ref = ComponentRef(
        ecosystem="PyPI",
        name="aws-mcp-server",
        version="0.3.1",
        source_manifest="requirements.txt",
        source_locator="line:5",
    )
    assert ref.purl == "pkg:pypi/aws-mcp-server@0.3.1"


def test_native_identity_for_unknown_ecosystem():
    ref = ComponentRef(
        ecosystem=None,
        name=None,
        version=None,
        source_manifest="mcp.json",
        source_locator="$.mcpServers.gh",
        component_identity="mcp-stdio/uvx-launch:some-package@unpinned",
    )
    assert ref.purl is None
    assert ref.component_identity == "mcp-stdio/uvx-launch:some-package@unpinned"
```

- [x] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_component_ref.py -v`
Expected: fails — module does not exist.

- [x] **Step 3: Implement `tools/component_ref.py`**

```python
"""Normalized representation of a detected agent-stack component."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

PURL_ECOSYSTEM_MAP = {
    "npm": "npm",
    "PyPI": "pypi",
    "pypi": "pypi",
    "GitHub": "github",
    "github": "github",
    "Docker": "docker",
    "docker": "docker",
}


def encode_purl_name(name: str) -> str:
    return quote(name, safe="/")


@dataclass(frozen=True)
class ComponentRef:
    """A single component installation discovered in a repository.

    Either (ecosystem + name + version) is set with a derivable standard PURL,
    or component_identity is set with an OpenACA-native identifier.
    """
    ecosystem: Optional[str] = None
    name: Optional[str] = None
    version: Optional[str] = None
    source_manifest: str = ""
    source_locator: str = ""
    component_identity: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def purl(self) -> Optional[str]:
        if not (self.ecosystem and self.name):
            return None
        purl_eco = PURL_ECOSYSTEM_MAP.get(self.ecosystem)
        if not purl_eco:
            return None
        encoded = encode_purl_name(self.name)
        if self.version:
            return f"pkg:{purl_eco}/{encoded}@{self.version}"
        return f"pkg:{purl_eco}/{encoded}"
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_component_ref.py -v`
Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add tools/component_ref.py tests/test_component_ref.py
git commit -m "feat: ComponentRef dataclass with PURL derivation"
```

---

## Task 2: `package.json` parser

**Files:**
- Create: `tools/parsers/__init__.py`
- Create: `tools/parsers/package_json.py`
- Create: `tests/test_parsers/__init__.py`
- Create: `tests/test_parsers/test_package_json.py`
- Create: `tests/fixtures/repos/sample-npm/package.json`

- [x] **Step 1: Write fixture**

`tests/fixtures/repos/sample-npm/package.json`:

```json
{
  "name": "sample",
  "version": "0.0.0",
  "dependencies": {
    "@cyanheads/git-mcp-server": "1.1.0",
    "mcp-remote": "^0.4.2"
  },
  "devDependencies": {
    "typescript": "^5.0.0"
  }
}
```

- [x] **Step 2: Write the failing test**

```python
# tests/test_parsers/test_package_json.py
from pathlib import Path

from tools.parsers.package_json import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_parses_dependencies_and_devDependencies():
    refs = parse(REPOS / "sample-npm" / "package.json")
    purls = {r.purl for r in refs}
    assert "pkg:npm/%40cyanheads/git-mcp-server@1.1.0" in purls
    # caret-ranged versions are emitted as the literal range; downstream
    # matchers handle ranges. We do NOT resolve to a concrete version.
    assert "pkg:npm/mcp-remote@^0.4.2" in purls
    assert "pkg:npm/typescript@^5.0.0" in purls


def test_emits_source_metadata():
    refs = parse(REPOS / "sample-npm" / "package.json")
    by_name = {r.name: r for r in refs}
    cyanheads = by_name["@cyanheads/git-mcp-server"]
    assert cyanheads.source_manifest.endswith("package.json")
    assert cyanheads.source_locator == "dependencies"
    assert by_name["typescript"].source_locator == "devDependencies"
```

- [x] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_parsers/test_package_json.py -v`
Expected: fails — module does not exist.

- [x] **Step 4: Implement `tools/parsers/__init__.py`**

```python
"""Manifest parser registry."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from tools.component_ref import ComponentRef
from tools.parsers import package_json, mcp_json, claude_plugin, claude_settings

ParserFn = Callable[[Path], list[ComponentRef]]

REGISTRY: list[tuple[str, ParserFn]] = [
    ("package.json", package_json.parse),
    ("mcp.json", mcp_json.parse),
    (".mcp.json", mcp_json.parse),
    (".claude-plugin/plugin.json", claude_plugin.parse),
    (".claude/settings.json", claude_settings.parse),
]


def parse_repo(root: Path) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    for pattern, parser in REGISTRY:
        for path in root.rglob(pattern):
            refs.extend(parser(path))
    return refs
```

> ⚠️ This file imports `mcp_json`, `claude_plugin`, and `claude_settings` modules that don't exist yet (Tasks 3, 4, 5 below). Create empty placeholders first to make imports succeed:
>
> ```python
> # tools/parsers/mcp_json.py
> from pathlib import Path
> from tools.component_ref import ComponentRef
>
>
> def parse(path: Path) -> list[ComponentRef]:
>     return []
> ```
>
> Same for `claude_plugin.py` and `claude_settings.py`. Replace each with the real implementation in its respective task.

- [x] **Step 5: Implement `tools/parsers/package_json.py`**

```python
"""Parse Node.js package.json declared dependencies."""
from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef

DEP_FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    for field_name in DEP_FIELDS:
        deps = data.get(field_name) or {}
        if not isinstance(deps, dict):
            continue
        for name, version in deps.items():
            refs.append(ComponentRef(
                ecosystem="npm",
                name=name,
                version=version if isinstance(version, str) else None,
                source_manifest=str(path),
                source_locator=field_name,
            ))
    return refs
```

- [x] **Step 6: Run tests**

Run: `uv run pytest tests/test_parsers/test_package_json.py -v`
Expected: all pass.

- [x] **Step 7: Commit**

```bash
git add tools/parsers/ tests/test_parsers/__init__.py tests/test_parsers/test_package_json.py tests/fixtures/repos/sample-npm/package.json
git commit -m "feat: parser for package.json with PURL emission"
```

---

## Task 3: `mcp.json` parser

`mcp.json` declares MCP servers by `command` + `args`. The parser must extract:
- The package referenced by `npx`/`uvx` invocations (with version pin if present).
- A fallback `mcp-stdio/...` OpenACA-native identity when the command is a binary path.

**Files:**
- Modify: `tools/parsers/mcp_json.py`
- Create: `tests/test_parsers/test_mcp_json.py`
- Create: `tests/fixtures/repos/sample-mcp/mcp.json`

- [x] **Step 1: Write fixture**

`tests/fixtures/repos/sample-mcp/mcp.json`:

```json
{
  "mcpServers": {
    "git": {
      "command": "npx",
      "args": ["@cyanheads/git-mcp-server@1.1.0"]
    },
    "weather": {
      "command": "uvx",
      "args": ["weather-mcp==0.5.0"]
    },
    "unpinned": {
      "command": "uvx",
      "args": ["sketchy-mcp"]
    },
    "binary": {
      "command": "/opt/local/bin/custom-mcp-server",
      "args": []
    }
  }
}
```

- [x] **Step 2: Write the failing test**

```python
# tests/test_parsers/test_mcp_json.py
from pathlib import Path

from tools.parsers.mcp_json import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_npx_emits_npm_purl():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    by_name = {r.name: r for r in refs if r.ecosystem == "npm"}
    assert by_name["@cyanheads/git-mcp-server"].version == "1.1.0"
    assert by_name["@cyanheads/git-mcp-server"].purl == \
        "pkg:npm/%40cyanheads/git-mcp-server@1.1.0"


def test_uvx_emits_pypi_purl_when_pinned():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    by_name = {r.name: r for r in refs if r.ecosystem == "PyPI"}
    assert by_name["weather-mcp"].version == "0.5.0"
    assert by_name["weather-mcp"].purl == "pkg:pypi/weather-mcp@0.5.0"


def test_uvx_unpinned_emits_native_identity():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    unpinned = [r for r in refs if r.component_identity and "unpinned" in r.source_locator]
    assert len(unpinned) == 1
    assert unpinned[0].component_identity == "mcp-stdio/uvx-unpinned:sketchy-mcp"


def test_binary_command_emits_native_identity():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    binary = [r for r in refs if r.component_identity and r.component_identity.startswith("mcp-stdio/binary:")]
    assert len(binary) == 1
    assert "/opt/local/bin/custom-mcp-server" in binary[0].component_identity


def test_source_locator_jsonpath():
    refs = parse(REPOS / "sample-mcp" / "mcp.json")
    git = [r for r in refs if r.name == "@cyanheads/git-mcp-server"][0]
    assert git.source_locator == "$.mcpServers.git"
```

- [x] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_parsers/test_mcp_json.py -v`
Expected: fails — placeholder parser returns `[]`.

- [x] **Step 4: Implement `tools/parsers/mcp_json.py`**

```python
"""Parse mcp.json / .mcp.json files: extract MCP server installations."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tools.component_ref import ComponentRef

NPM_PINNED_RE = re.compile(r"^(?P<name>(?:@[^/]+/)?[^@]+)@(?P<version>[^@\s]+)$")
PYPI_PINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+-]+)$")
PYPI_UNPINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)$")


def _parse_npx_args(args: list[str]) -> tuple[str | None, str | None]:
    """Return (name, version) for the package npx is launching, or (None, None)."""
    real_args = [a for a in args if not a.startswith("-")]
    if not real_args:
        return None, None
    spec = real_args[0]
    m = NPM_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version")
    return spec, None  # unpinned npm package


def _parse_uvx_args(args: list[str]) -> tuple[str | None, str | None, bool]:
    """Return (name, version, pinned) for the package uvx is launching."""
    real_args = [a for a in args if not a.startswith("-")]
    if not real_args:
        return None, None, False
    spec = real_args[0]
    m = PYPI_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version"), True
    m = PYPI_UNPINNED_RE.match(spec)
    if m:
        return m.group("name"), None, False
    return None, None, False


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    servers = data.get("mcpServers") or {}
    refs: list[ComponentRef] = []
    for server_name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        args = entry.get("args") or []
        locator = f"$.mcpServers.{server_name}"
        if command == "npx":
            name, version = _parse_npx_args(args)
            if name and version:
                refs.append(ComponentRef(
                    ecosystem="npm", name=name, version=version,
                    source_manifest=str(path), source_locator=locator,
                ))
            elif name:
                # unpinned npm package launched via npx: still record native identity
                refs.append(ComponentRef(
                    component_identity=f"mcp-stdio/npx-unpinned:{name}",
                    source_manifest=str(path), source_locator=locator,
                ))
        elif command == "uvx":
            name, version, pinned = _parse_uvx_args(args)
            if name and pinned:
                refs.append(ComponentRef(
                    ecosystem="PyPI", name=name, version=version,
                    source_manifest=str(path), source_locator=locator,
                ))
            elif name:
                refs.append(ComponentRef(
                    component_identity=f"mcp-stdio/uvx-unpinned:{name}",
                    source_manifest=str(path), source_locator=locator,
                ))
        else:
            # binary path or unknown command
            refs.append(ComponentRef(
                component_identity=f"mcp-stdio/binary:{command}",
                source_manifest=str(path), source_locator=locator,
            ))
    return refs
```

- [x] **Step 5: Run tests**

Run: `uv run pytest tests/test_parsers/test_mcp_json.py -v`
Expected: all pass.

- [x] **Step 6: Commit**

```bash
git add tools/parsers/mcp_json.py tests/test_parsers/test_mcp_json.py tests/fixtures/repos/sample-mcp/mcp.json
git commit -m "feat: parser for mcp.json with npx/uvx and binary fallback"
```

---

## Task 4: `.claude-plugin/plugin.json` parser

**Files:**
- Modify: `tools/parsers/claude_plugin.py`
- Create: `tests/test_parsers/test_claude_plugin.py`
- Create: `tests/fixtures/repos/sample-plugin/.claude-plugin/plugin.json`

- [x] **Step 1: Write fixture**

`tests/fixtures/repos/sample-plugin/.claude-plugin/plugin.json`:

```json
{
  "name": "deployment-tools",
  "version": "1.2.0",
  "description": "Sample plugin",
  "dependencies": [
    "helper-lib",
    {"name": "secrets-vault", "version": "~2.1.0"}
  ],
  "mcpServers": {
    "db": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": []
    },
    "api": {
      "command": "npx",
      "args": ["@company/mcp-server@1.0.4"]
    }
  }
}
```

- [x] **Step 2: Write the failing test**

```python
# tests/test_parsers/test_claude_plugin.py
from pathlib import Path

from tools.parsers.claude_plugin import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_plugin_self_identity():
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    plugin_self = [r for r in refs if r.component_identity and r.component_identity.startswith("claude-plugin/")]
    assert len(plugin_self) == 1
    assert plugin_self[0].component_identity == "claude-plugin/deployment-tools@1.2.0"


def test_plugin_dependencies():
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    deps = [r for r in refs if r.component_identity and r.component_identity.startswith("claude-plugin-dep/")]
    identities = {r.component_identity for r in deps}
    assert "claude-plugin-dep/helper-lib" in identities
    assert "claude-plugin-dep/secrets-vault@~2.1.0" in identities


def test_plugin_inlined_mcp_servers():
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    npm_mcp = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_mcp) == 1
    assert npm_mcp[0].name == "@company/mcp-server"
    assert npm_mcp[0].version == "1.0.4"
    binary_mcp = [r for r in refs if r.component_identity and r.component_identity.startswith("mcp-stdio/binary:")]
    assert len(binary_mcp) == 1
```

- [x] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_parsers/test_claude_plugin.py -v`
Expected: fails.

- [x] **Step 4: Implement `tools/parsers/claude_plugin.py`**

```python
"""Parse .claude-plugin/plugin.json — plugin self-identity, deps, inlined MCP."""
from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers.mcp_json import parse as parse_mcp_block


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []

    # Plugin self-identity
    name = data.get("name")
    version = data.get("version")
    if name:
        identity = f"claude-plugin/{name}"
        if version:
            identity = f"{identity}@{version}"
        refs.append(ComponentRef(
            component_identity=identity,
            source_manifest=str(path),
            source_locator="$",
        ))

    # Dependencies (mix of strings and {name, version} objects)
    for i, dep in enumerate(data.get("dependencies") or []):
        locator = f"$.dependencies[{i}]"
        if isinstance(dep, str):
            refs.append(ComponentRef(
                component_identity=f"claude-plugin-dep/{dep}",
                source_manifest=str(path),
                source_locator=locator,
            ))
        elif isinstance(dep, dict) and dep.get("name"):
            ident = f"claude-plugin-dep/{dep['name']}"
            if dep.get("version"):
                ident = f"{ident}@{dep['version']}"
            refs.append(ComponentRef(
                component_identity=ident,
                source_manifest=str(path),
                source_locator=locator,
            ))

    # Inlined mcpServers — reuse the mcp.json parser logic via a temp file.
    if data.get("mcpServers"):
        mcp_block = {"mcpServers": data["mcpServers"]}
        tmp = path.parent / "._inlined_mcp.json"
        try:
            tmp.write_text(json.dumps(mcp_block))
            for ref in parse_mcp_block(tmp):
                # Rewrite source_manifest back to plugin.json for clarity.
                refs.append(ComponentRef(
                    ecosystem=ref.ecosystem,
                    name=ref.name,
                    version=ref.version,
                    source_manifest=str(path),
                    source_locator=ref.source_locator.replace("$.mcpServers", "$.mcpServers (inlined)"),
                    component_identity=ref.component_identity,
                    extra=ref.extra,
                ))
        finally:
            if tmp.exists():
                tmp.unlink()

    return refs
```

- [x] **Step 5: Run tests**

Run: `uv run pytest tests/test_parsers/test_claude_plugin.py -v`
Expected: all pass.

- [x] **Step 6: Commit**

```bash
git add tools/parsers/claude_plugin.py tests/test_parsers/test_claude_plugin.py tests/fixtures/repos/sample-plugin/
git commit -m "feat: parser for .claude-plugin/plugin.json"
```

---

## Task 5: `.claude/settings.json` parser

`.claude/settings.json` carries an `enabledPlugins` map (and similar plugin enumeration). Per the Claude Code docs, plugins are referenced by `<author>/<name>@<version>` or by a marketplace + plugin pair.

**Files:**
- Modify: `tools/parsers/claude_settings.py`
- Create: `tests/test_parsers/test_claude_settings.py`
- Create: `tests/fixtures/repos/sample-settings/.claude/settings.json`

- [x] **Step 1: Write fixture**

```json
{
  "enabledPlugins": {
    "deployment-tools@1.2.0": true,
    "anthropics/dev-tools@2.0.1": true,
    "experimental@unstable": false
  }
}
```

- [x] **Step 2: Write the failing test**

```python
# tests/test_parsers/test_claude_settings.py
from pathlib import Path

from tools.parsers.claude_settings import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_enabled_plugins_emitted():
    manifest = REPOS / "sample-settings" / ".claude" / "settings.json"
    refs = parse(manifest)
    identities = {r.component_identity for r in refs}
    assert "claude-plugin/deployment-tools@1.2.0" in identities
    assert "claude-plugin/anthropics/dev-tools@2.0.1" in identities
    # disabled plugins are skipped
    assert not any("experimental" in (r.component_identity or "") for r in refs)


def test_source_locator():
    manifest = REPOS / "sample-settings" / ".claude" / "settings.json"
    refs = parse(manifest)
    locators = {r.source_locator for r in refs}
    assert any("$.enabledPlugins" in loc for loc in locators)
```

- [x] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_parsers/test_claude_settings.py -v`
Expected: fails.

- [x] **Step 4: Implement `tools/parsers/claude_settings.py`**

```python
"""Parse .claude/settings.json — enumerate enabled Claude Code plugins."""
from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    enabled = data.get("enabledPlugins") or {}
    for plugin_spec, is_enabled in enabled.items():
        if not is_enabled:
            continue
        refs.append(ComponentRef(
            component_identity=f"claude-plugin/{plugin_spec}",
            source_manifest=str(path),
            source_locator=f"$.enabledPlugins[{plugin_spec!r}]",
        ))
    return refs
```

- [x] **Step 5: Run tests**

Run: `uv run pytest tests/test_parsers/test_claude_settings.py -v`
Expected: all pass.

- [x] **Step 6: Commit**

```bash
git add tools/parsers/claude_settings.py tests/test_parsers/test_claude_settings.py tests/fixtures/repos/sample-settings/
git commit -m "feat: parser for .claude/settings.json enabledPlugins"
```

---

## Task 6: Registry integration test

**Files:**
- Create: `tests/test_parsers/test_registry.py`

- [x] **Step 1: Write the test**

```python
# tests/test_parsers/test_registry.py
from pathlib import Path

from tools.parsers import parse_repo

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_parse_repo_combines_all_manifests():
    # Composite repo: copy the sample fixtures into one tree and parse.
    refs = []
    for sample in ["sample-npm", "sample-mcp", "sample-plugin", "sample-settings"]:
        refs += parse_repo(REPOS / sample)

    purls = {r.purl for r in refs if r.purl}
    identities = {r.component_identity for r in refs if r.component_identity}

    assert "pkg:npm/%40cyanheads/git-mcp-server@1.1.0" in purls
    assert "pkg:pypi/weather-mcp@0.5.0" in purls
    assert any(i.startswith("claude-plugin/") for i in identities)
    assert any(i.startswith("mcp-stdio/uvx-unpinned:") for i in identities)
```

- [x] **Step 2: Run all parser tests**

Run: `uv run pytest tests/test_parsers/ tests/test_component_ref.py -v`
Expected: all pass.

- [x] **Step 3: Commit**

```bash
git add tests/test_parsers/test_registry.py
git commit -m "test: end-to-end manifest parser registry"
```

---

## Verification

```bash
uv run pytest tests/test_parsers/ tests/test_component_ref.py -v
uv run python -c "from tools.parsers import parse_repo; from pathlib import Path; print(len(parse_repo(Path('tests/fixtures/repos/sample-npm'))))"
```

---

## Self-review checklist

- [x] **Four parsers** registered: `package.json`, `mcp.json`, `.claude-plugin/plugin.json`, `.claude/settings.json`. Cursor + Windsurf are explicitly out of V0.
- [x] **PURL emission** is correct for known ecosystems; OpenACA-native identity for unknown.
- [x] **Source metadata** (`source_manifest`, `source_locator`) is on every ref so the Action can produce useful annotations.
- [x] **mcp.json edge cases**: pinned vs unpinned (`uvx X==1.0` vs `uvx X`); binary path; npx scoped vs unscoped.
- [x] **Plugin manifest dependencies** (string vs object form) both produce identities.
- [x] **Disabled plugins** in `.claude/settings.json` are skipped.
- [x] **No commercial / competitor framing** in code, comments, or fixtures.
