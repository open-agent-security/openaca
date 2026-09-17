"""Read-only Pi package declarations and resource selection."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from tools.parsers.pi_source import PiSource, parse_pi_source

RESOURCE_TYPES = ("extensions", "skills", "prompts", "themes")
Scope = Literal["global", "project"]


@dataclass
class PiResource:
    source: PiSource
    raw: str
    scope: Scope
    filters: dict[str, list[str]] | None
    autoload: bool
    resource_type: str = "packages"
    declaration_path: Path | None = None
    index: int = 0
    install_root: Path | None = None
    installed_version: str | None = None
    delta_base: PiResource | None = None
    gaps: tuple[str, ...] = ()


def permitted(path: Path, allowed_root: Path | None) -> bool:
    return allowed_root is None or path.resolve().is_relative_to(allowed_root.resolve())


def read_manifest(path: Path, allowed_root: Path | None = None) -> dict[str, Any]:
    if not permitted(path, allowed_root):
        return {}
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_resources(
    global_settings: dict,
    project_settings: dict | None = None,
    *,
    agent_root: Path | None = None,
    project_root: Path | None = None,
    install_root: Path | None = None,
    allowed_root: Path | None = None,
) -> list[PiResource]:
    rows: dict[tuple[str, str], PiResource] = {}
    for scope, settings, base in [
        ("global", global_settings, agent_root),
        ("project", project_settings or {}, project_root / ".pi" if project_root else None),
    ]:
        for kind in ("packages", *RESOURCE_TYPES):
            entries = settings.get(kind, [])
            if not isinstance(entries, list):
                continue
            for index, entry in enumerate(entries):
                raw = (
                    entry.get("source") if isinstance(entry, dict) and kind == "packages" else entry
                )
                if not isinstance(raw, str):
                    continue
                if kind != "packages" and (
                    raw.startswith(("!", "+", "-")) or "*" in raw or "?" in raw
                ):
                    continue
                if kind == "packages":
                    source = parse_pi_source(raw, base_dir=base)
                else:
                    path = Path(raw).expanduser()
                    path = path if path.is_absolute() else (base or Path.cwd()) / path
                    source = PiSource("local", f"local:{os.path.abspath(path)}", None, None, False)
                if source is None:
                    continue
                filters = (
                    {
                        k: v
                        for k, v in entry.items()
                        if k in RESOURCE_TYPES
                        and isinstance(v, list)
                        and all(isinstance(p, str) for p in v)
                    }
                    if isinstance(entry, dict)
                    else None
                )
                root = None
                if source.kind == "local":
                    root = Path(source.identity.removeprefix("local:"))
                elif source.kind == "npm" and (base or install_root):
                    root = (
                        install_root
                        if install_root is not None
                        else (base or Path.cwd()) / "npm/node_modules"
                    ) / source.identity.removeprefix("npm:")
                elif source.kind == "git" and base:
                    root = base / "git" / source.identity.removeprefix("git:")
                row = PiResource(
                    source,
                    raw,
                    cast(Scope, scope),
                    filters,
                    not isinstance(entry, dict) or entry.get("autoload") is not False,
                    kind,
                    base / "settings.json" if base else None,
                    index,
                    root,
                )
                if root is not None:
                    if not permitted(root, allowed_root):
                        row.gaps = ("outside allowed root",)
                    elif not root.exists():
                        row.gaps = ("installation or local resource missing",)
                    elif kind == "packages":
                        manifest = read_manifest(root / "package.json", allowed_root)
                        version = manifest.get("version")
                        if source.kind == "npm" and (
                            manifest.get("name") != source.identity.removeprefix("npm:")
                            or not isinstance(version, str)
                        ):
                            row.gaps = ("installed npm package identity or version unavailable",)
                        elif isinstance(version, str):
                            row.installed_version = version
                key = kind, source.identity
                if not row.autoload and key in rows:
                    row.delta_base = rows[key]
                    row.install_root = rows[key].install_root
                    row.installed_version = rows[key].installed_version
                    row.gaps = rows[key].gaps
                rows[key] = row
    return sorted(rows.values(), key=lambda r: r.scope != "project")


from tools.parsers.pi_resources import PiResourceFile, expand_resources  # noqa: E402,F401
