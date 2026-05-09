"""Parse pyproject.toml declared dependencies (PEP 621 + PEP 735).

Three locations get scanned:

- `[project] dependencies = [...]` — the standard runtime dep array.
- `[project.optional-dependencies] <extra> = [...]` — optional/extra
  installs (e.g., `pip install foo[dev]`); each extra is a separate
  source_locator.
- `[dependency-groups] <group> = [...]` — PEP 735 groups (used by uv,
  pdm, hatch). Same dep-spec shape, different table.

Each spec is parsed via `packaging.requirements.Requirement`, which
handles PEP 508 properly (extras, environment markers, multi-clause
specifiers). Version handling matches `package.json`: only emit a
concrete version when the spec is a single `==<value>` pin; otherwise
leave version unset so the matcher emits low-confidence findings the
consumer can resolve.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Iterable

from packaging.requirements import InvalidRequirement, Requirement

from tools.component_ref import ComponentRef


def _pinned_version(req: Requirement) -> str | None:
    """Return the pinned version if the spec is a single `==` clause, else None."""
    specs = list(req.specifier)
    if len(specs) == 1 and specs[0].operator == "==":
        return specs[0].version
    return None


def _emit_specs(specs: Iterable[object], source_manifest: str, locator: str) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    for spec in specs:
        if not isinstance(spec, str):
            continue
        try:
            req = Requirement(spec)
        except InvalidRequirement:
            continue
        refs.append(
            ComponentRef(
                ecosystem="PyPI",
                name=req.name,
                version=_pinned_version(req),
                source_manifest=source_manifest,
                source_locator=locator,
            )
        )
    return refs


def parse(path: Path) -> list[ComponentRef]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return []
    refs: list[ComponentRef] = []
    source = str(path)

    project = data.get("project") or {}
    if isinstance(project, dict):
        deps = project.get("dependencies")
        if isinstance(deps, list):
            refs.extend(_emit_specs(deps, source, "project.dependencies"))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for extra, specs in optional.items():
                if isinstance(specs, list):
                    refs.extend(
                        _emit_specs(specs, source, f"project.optional-dependencies.{extra}")
                    )

    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group, specs in groups.items():
            if isinstance(specs, list):
                refs.extend(_emit_specs(specs, source, f"dependency-groups.{group}"))

    return refs
