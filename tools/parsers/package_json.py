"""Parse Node.js package.json declared dependencies."""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef

DEP_FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


def parse(path: Path, *, strict: bool = False) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    if not isinstance(data, dict):
        if strict:
            raise ValueError("package.json must contain an object")
        return refs
    for field_name in DEP_FIELDS:
        if field_name not in data:
            continue
        deps = data[field_name]
        if not isinstance(deps, dict):
            if strict:
                raise ValueError(f"package.json {field_name} must be an object")
            continue
        for name, version in deps.items():
            if strict and (not isinstance(name, str) or not name):
                raise ValueError(f"package.json {field_name} names must be non-empty strings")
            if strict and (not isinstance(version, str) or not version):
                raise ValueError(f"package.json {field_name} versions must be non-empty strings")
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
