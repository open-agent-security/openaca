"""Parse requirements.txt (one PEP 508 requirement spec per line)."""

from __future__ import annotations

import re
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from tools.component_ref import ComponentRef


def _canonical_name(name: str) -> str:
    """PEP 503 canonical form: lowercase, collapse [-_.] runs to a single hyphen."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pinned_version(req: Requirement) -> str | None:
    """Return the pinned version if the spec is a single exact `==` clause, else None."""
    specs = list(req.specifier)
    if len(specs) == 1 and specs[0].operator == "==" and not specs[0].version.endswith(".*"):
        return specs[0].version
    return None


def _strip_pip_annotations(line: str) -> str:
    """Remove pip-specific extensions that packaging.Requirement does not accept.

    pip allows trailing inline comments (``# via botocore``) and
    per-requirement options (``--hash=sha256:...``) after the PEP 508 specifier.
    Neither is valid PEP 508, so both must be stripped before parsing.
    """
    line = re.sub(r"\s+#.*$", "", line)
    line = re.sub(r"\s+--\S+", "", line)
    return line.strip()


def parse(path: Path) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    source = str(path)
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = _strip_pip_annotations(line)
        if not line:
            continue
        try:
            req = Requirement(line)
        except InvalidRequirement:
            continue
        if req.url:
            continue
        refs.append(
            ComponentRef(
                ecosystem="PyPI",
                name=_canonical_name(req.name),
                version=_pinned_version(req),
                source_manifest=source,
                source_locator=f"line:{i}",
            )
        )
    return refs
