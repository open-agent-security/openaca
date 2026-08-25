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

import re
import tomllib
from pathlib import Path
from typing import Iterable

from packaging.requirements import InvalidRequirement, Requirement

from tools.component_ref import ComponentRef


def _canonical_name(name: str) -> str:
    """PEP 503 canonical form: lowercase, collapse [-_.] runs to a single hyphen."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pinned_version(req: Requirement) -> str | None:
    """Return the pinned version if the spec is a single exact `==` clause, else None.

    `==1.*` is a PEP 440 prefix match (range), not a pin — excluded here.
    """
    specs = list(req.specifier)
    if len(specs) == 1 and specs[0].operator == "==" and not specs[0].version.endswith(".*"):
        return specs[0].version
    return None


def _emit_specs(
    specs: Iterable[object], source_manifest: str, locator: str, *, strict: bool = False
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    for spec in specs:
        if not isinstance(spec, str):
            if strict:
                raise ValueError(f"{locator} entries must be strings")
            continue
        try:
            req = Requirement(spec)
        except InvalidRequirement as exc:
            if strict:
                raise ValueError(f"{locator} contains an invalid requirement") from exc
            continue
        if req.url:
            # Direct URL/VCS/local references (PEP 440 URL reqs) are not
            # PyPI packages — skip to avoid false-positive purl matches.
            continue
        refs.append(
            ComponentRef(
                ecosystem="PyPI",
                name=_canonical_name(req.name),
                version=_pinned_version(req),
                source_manifest=source_manifest,
                source_locator=locator,
            )
        )
    return refs


def parse(path: Path, *, strict: bool = False) -> list[ComponentRef]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        if strict:
            raise ValueError("pyproject.toml must contain an object")
        return []
    refs: list[ComponentRef] = []
    source = str(path)

    project = data.get("project") or {}
    if "project" in data and not isinstance(project, dict):
        if strict:
            raise ValueError("pyproject.toml project must be an object")
        project = {}
    if isinstance(project, dict):
        deps = project.get("dependencies")
        if isinstance(deps, list):
            refs.extend(_emit_specs(deps, source, "project.dependencies", strict=strict))
        elif "dependencies" in project and strict:
            raise ValueError("project.dependencies must be an array")
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for extra, specs in optional.items():
                if isinstance(specs, list):
                    refs.extend(
                        _emit_specs(
                            specs,
                            source,
                            f"project.optional-dependencies.{extra}",
                            strict=strict,
                        )
                    )
                elif strict:
                    raise ValueError(f"project.optional-dependencies.{extra} must be an array")
        elif "optional-dependencies" in project and strict:
            raise ValueError("project.optional-dependencies must be an object")

    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group, specs in groups.items():
            if isinstance(specs, list):
                refs.extend(_emit_specs(specs, source, f"dependency-groups.{group}", strict=strict))
            elif strict:
                raise ValueError(f"dependency-groups.{group} must be an array")
    elif "dependency-groups" in data and strict:
        raise ValueError("dependency-groups must be an object")

    return refs
