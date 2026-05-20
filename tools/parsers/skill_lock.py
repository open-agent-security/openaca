"""Parse skills CLI lockfiles for direct-skill source provenance.

The skills CLI writes two compatible lockfile shapes:

- global installs: `~/.agents/.skill-lock.json` (currently version 3)
- project installs: `skills-lock.json` (currently version 1)

OpenACA treats these files as observation evidence only. They do not make
`skills.sh` a source ecosystem; entries point at the underlying source such as
GitHub, node_modules, or a local path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class SkillLockEntry:
    name: str
    source: str
    source_type: str
    source_url: Optional[str]
    ref: Optional[str]
    skill_path: Optional[str]
    hash: Optional[str]
    hash_type: Optional[str]
    plugin_name: Optional[str]
    lockfile_path: str

    def to_provenance(self, *, resolved_path: Optional[Path] = None) -> dict[str, str]:
        provenance = {
            "status": "known",
            "source": self.source,
            "source_type": self.source_type,
            "lockfile_path": self.lockfile_path,
        }
        optional = {
            "source_url": self.source_url,
            "ref": self.ref,
            "skill_path": self.skill_path,
            "hash": self.hash,
            "hash_type": self.hash_type,
            "plugin_name": self.plugin_name,
        }
        for key, value in optional.items():
            if value:
                provenance[key] = value
        if resolved_path is not None:
            provenance["resolved_path"] = str(resolved_path)
        return provenance


def parse(path: Path) -> dict[str, SkillLockEntry]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    skills = raw.get("skills")
    if not isinstance(skills, dict):
        return {}

    entries: dict[str, SkillLockEntry] = {}
    for name, entry in skills.items():
        parsed = _parse_entry(name, entry, path)
        if parsed is not None:
            entries[name] = parsed
    return entries


def provenance_for_skill(
    skill_md_path: Path, skill_name: str, *, project_root: Optional[Path] = None
) -> dict[str, str] | None:
    try:
        resolved_path = skill_md_path.resolve()
    except (OSError, RuntimeError):
        resolved_path = None

    for lock_path in _candidate_lock_paths(resolved_path or skill_md_path, project_root):
        entry = parse(lock_path).get(skill_name)
        if entry is not None:
            return entry.to_provenance(resolved_path=resolved_path)

    if resolved_path is not None and resolved_path != skill_md_path.absolute():
        return {"status": "symlink-target", "resolved_path": str(resolved_path)}
    return None


def _parse_entry(name: object, entry: object, path: Path) -> SkillLockEntry | None:
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(entry, dict):
        return None
    source = entry.get("source")
    source_type = entry.get("sourceType")
    if not isinstance(source, str) or not source:
        return None
    if not isinstance(source_type, str) or not source_type:
        return None

    hash_value: Optional[str] = None
    hash_type: Optional[str] = None
    for candidate in ("skillFolderHash", "computedHash"):
        value = entry.get(candidate)
        if isinstance(value, str) and value:
            hash_value = value
            hash_type = candidate
            break

    return SkillLockEntry(
        name=name,
        source=source,
        source_type=source_type,
        source_url=_optional_str(entry.get("sourceUrl")),
        ref=_optional_str(entry.get("ref")),
        skill_path=_optional_str(entry.get("skillPath")),
        hash=hash_value,
        hash_type=hash_type,
        plugin_name=_optional_str(entry.get("pluginName")),
        lockfile_path=str(path),
    )


def _optional_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _candidate_lock_paths(skill_md_path: Path, project_root: Optional[Path]) -> list[Path]:
    paths: list[Path] = []

    if project_root is not None:
        try:
            skill_md_path.relative_to(project_root.resolve())
        except (ValueError, OSError, RuntimeError):
            pass
        else:
            paths.append(project_root / "skills-lock.json")

    skill_dir = skill_md_path.parent
    skills_root = skill_dir.parent
    paths.extend(
        [
            skills_root.parent / ".skill-lock.json",
            skills_root / ".skill-lock.json",
        ]
    )

    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            out.append(path)
    return out
