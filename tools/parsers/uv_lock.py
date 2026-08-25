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


def parse(path: Path, *, strict: bool = False) -> list[ComponentRef]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError):
        if strict:
            raise
        return []
    if not isinstance(data, dict):
        if strict:
            raise ValueError("uv.lock must contain an object")
        return []
    packages = data.get("package")
    if not isinstance(packages, list):
        if strict:
            raise ValueError("uv.lock package must be an array")
        return []
    refs: list[ComponentRef] = []
    for i, entry in enumerate(packages):
        if not isinstance(entry, dict):
            if strict:
                raise ValueError(f"uv.lock package[{i}] must be an object")
            continue
        name = entry.get("name")
        version = entry.get("version")
        if not isinstance(name, str) or not name:
            if strict:
                raise ValueError(f"uv.lock package[{i}] has no name")
            continue
        if not isinstance(version, str) or not version:
            if strict:
                raise ValueError(f"uv.lock package[{i}] has no version")
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
