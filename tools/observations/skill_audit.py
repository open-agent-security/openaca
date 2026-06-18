"""Conservative local observations for skill artifacts.

Native skill observations are reserved for content or behavior claims about the
artifact. Configuration, permission, provenance, and admission claims belong in
posture rules, even when they read SKILL.md frontmatter.
"""

from __future__ import annotations

from tools.component_ref import ComponentRef
from tools.observations.finding import ObservationFinding


def collect_skill_observations(_refs: list[ComponentRef]) -> list[ObservationFinding]:
    return []
