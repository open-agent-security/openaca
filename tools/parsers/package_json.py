"""Parse Node.js package.json declared dependencies."""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef

DEP_FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    if not isinstance(data, dict):
        return refs
    for field_name in DEP_FIELDS:
        deps = data.get(field_name) or {}
        if not isinstance(deps, dict):
            continue
        for name, version in deps.items():
            refs.append(
                ComponentRef(
                    ecosystem="npm",
                    name=name,
                    version=version if isinstance(version, str) else None,
                    source_manifest=str(path),
                    source_locator=field_name,
                )
            )
    return refs
