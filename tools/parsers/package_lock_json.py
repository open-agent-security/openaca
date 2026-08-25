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


def parse(path: Path, *, strict: bool = False) -> list[ComponentRef]:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        if strict:
            raise
        return []
    if not isinstance(data, dict):
        if strict:
            raise ValueError("package-lock.json must contain an object")
        return []
    packages = data.get("packages")
    if not isinstance(packages, dict):
        if strict and data.get("lockfileVersion") not in {1, 2}:
            raise ValueError("package-lock.json packages must be an object")
        return []
    refs: list[ComponentRef] = []
    for key, entry in packages.items():
        if not key:
            continue  # host package
        if not isinstance(entry, dict):
            if strict:
                raise ValueError(f"package-lock.json package {key!r} must be an object")
            continue
        if entry.get("dev") is True:
            continue
        name = _name_from_key(key)
        version = entry.get("version")
        if not name or not isinstance(version, str) or not version:
            if strict and name and entry.get("link") is not True:
                raise ValueError(f"package-lock.json package {key!r} has no version")
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
