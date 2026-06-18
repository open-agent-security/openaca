"""Conservative local observations for skill artifacts.

This is intentionally not an LLM or behavioral scanner. It emits only
deterministic, source-attributed audit observations that are useful as
first-run evidence without claiming a vulnerability verdict.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from tools.component_ref import ComponentRef, canonical_component_identity
from tools.observations.finding import ObservationFinding

SOURCE = "openaca-skill-audit"
EXECUTABLE_TOOLS = {"bash", "shell"}


def collect_skill_observations(refs: list[ComponentRef]) -> list[ObservationFinding]:
    observations: list[ObservationFinding] = []
    for ref in refs:
        if (ref.extra or {}).get("component_type") != "skill":
            continue
        observation = _allowed_executable_tool_observation(ref)
        if observation is not None:
            observations.append(observation)
    return observations


def _executable_tool_base(tool: str) -> str:
    """Strip command-filter suffix, e.g. ``Bash(git:*)`` → ``Bash``."""
    return tool.split("(", 1)[0].strip()


def _allowed_executable_tool_observation(ref: ComponentRef) -> ObservationFinding | None:
    frontmatter = _read_frontmatter(Path(ref.source_manifest))
    allowed_tools = _allowed_tools(frontmatter)
    executable = sorted(
        tool for tool in allowed_tools if _executable_tool_base(tool).lower() in EXECUTABLE_TOOLS
    )
    if not executable:
        return None
    identity = canonical_component_identity(ref) or ref.component_identity or ref.name or "skill"
    subject_coordinate = _subject_coordinate(ref)
    return ObservationFinding(
        source=SOURCE,
        source_version=_openaca_version(),
        observation_id="skill.allowed-executable-tool",
        title="Skill declares executable tool access",
        severity="low",
        confidence="high",
        component={
            "identity": identity,
            "name": ref.name or identity,
            "type": "skill",
        },
        subject_coordinate=subject_coordinate,
        evidence={
            "allowed_tools": executable,
            "source_manifest": ref.source_manifest,
            "subject_coordinate": subject_coordinate,
        },
        categories=["skill-capability"],
        remediation=(
            "Review whether this skill needs executable tool access and keep it under "
            "normal code-review/change-control."
        ),
        declared_by=(ref.extra or {}).get("declared_by")
        if isinstance((ref.extra or {}).get("declared_by"), dict)
        else {"kind": "manifest", "path": ref.source_manifest},
        component_path=_component_path(ref),
    )


def _read_frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        loaded = yaml.safe_load(text[3:end].strip())
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _allowed_tools(frontmatter: dict[str, Any]) -> set[str]:
    raw = frontmatter.get("allowed-tools")
    if isinstance(raw, str):
        return {part.strip() for part in raw.split(",") if part.strip()}
    if isinstance(raw, list):
        return {item for item in raw if isinstance(item, str) and item}
    return set()


def _subject_coordinate(ref: ComponentRef) -> str:
    coordinates = (ref.extra or {}).get("artifact_coordinates")
    if isinstance(coordinates, list):
        for coordinate in coordinates:
            if not isinstance(coordinate, dict):
                continue
            value = coordinate.get("value")
            if isinstance(value, str) and value:
                return value
    return canonical_component_identity(ref) or ref.component_identity or ref.source_manifest


def _component_path(ref: ComponentRef) -> list[dict[str, str]]:
    raw = (ref.extra or {}).get("component_path")
    if isinstance(raw, list):
        return [
            {"type": str(item.get("type")), "name": str(item.get("name"))}
            for item in raw
            if isinstance(item, dict)
            and item.get("type") is not None
            and item.get("name") is not None
        ]
    identity = canonical_component_identity(ref) or ref.component_identity
    if identity:
        return [{"type": "skill", "name": identity}]
    return []


def _openaca_version() -> str:
    try:
        return version("openaca")
    except PackageNotFoundError:
        return "unknown"
