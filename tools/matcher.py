"""Match ComponentRefs against ASVE advisories.

Three match strategies, dispatched per-ref:

- **Versioned match** (high confidence). Ref has ecosystem+name+version
  that parses cleanly via packaging.Version → check OSV ECOSYSTEM ranges
  and emit a finding when version is in the vulnerable interval
  [introduced, fixed). Anything at/after `fixed` is excluded.

- **Unparseable-version match** (low confidence). Ref has ecosystem+name
  but version is a range/spec (`^1.0.0`, `~2.1`, etc.) that
  packaging.Version can't reduce to a single point. We don't drop the
  ref — emit a finding so the consumer knows to pin and verify.

- **Unpinned-launch match** (unknown confidence). Ref carries no
  ecosystem/version but has a `component_identity` shaped like
  `mcp-stdio/(npx|uvx)-unpinned:<package>`. Extract the package name and
  match against advisory `affected[*].package`. Confidence is
  "unknown" because we can't see the runtime-resolved version.

Out of V0 scope: the schema's `database_specific.asve.detection_hints`
field — substring matching against synthesized command lines is fragile
and `affected[*].package` already carries the same information for any
advisory that has a published PURL. Detection hints stay in the corpus
as evidence/documentation but aren't the matching mechanism in V0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from packaging.version import InvalidVersion, Version

from tools.component_ref import ComponentRef

_UNPINNED_IDENTITY_PREFIXES: dict[str, str] = {
    "mcp-stdio/npx-unpinned:": "npm",
    "mcp-stdio/uvx-unpinned:": "PyPI",
}


@dataclass(frozen=True)
class Finding:
    advisory_id: str
    component: ComponentRef
    confidence: str  # "high" | "low" | "unknown"
    reason: str = ""


def _parse_version(value: Optional[str]) -> Optional[Version]:
    if value is None:
        return None
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _in_range(version: Version, events: list[dict]) -> bool:
    """Return True if version falls in any vulnerable window encoded in events.

    OSV events alternate introduced/fixed/last_affected within a single range
    object. A trailing introduced with no closing event means the range is
    open-ended (still unpatched). `last_affected` is an inclusive upper bound
    (version <= last_affected is vulnerable); `fixed` is exclusive (version <
    fixed is vulnerable).
    """
    intro: Optional[str] = None
    for ev in events:
        if "introduced" in ev:
            intro = ev["introduced"]
        elif "fixed" in ev and intro is not None:
            intro_v = Version("0") if intro == "0" else _parse_version(intro)
            fixed_v = _parse_version(ev["fixed"])
            if intro_v is not None and fixed_v is not None:
                if version >= intro_v and version < fixed_v:
                    return True
            intro = None
        elif "last_affected" in ev and intro is not None:
            intro_v = Version("0") if intro == "0" else _parse_version(intro)
            last_v = _parse_version(ev["last_affected"])
            if intro_v is not None and last_v is not None:
                if version >= intro_v and version <= last_v:
                    return True
            intro = None
    # Open-ended range: introduced with no following fixed or last_affected → still unpatched
    if intro is not None:
        intro_v = Version("0") if intro == "0" else _parse_version(intro)
        if intro_v is not None and version >= intro_v:
            return True
    return False


def _unpinned_identity_to_package(identity: str) -> Optional[tuple[str, str]]:
    for prefix, ecosystem in _UNPINNED_IDENTITY_PREFIXES.items():
        if identity.startswith(prefix):
            return ecosystem, identity[len(prefix) :]
    return None


def _match_one(ref: ComponentRef, advisories: list[dict]) -> list[Finding]:
    if ref.ecosystem and ref.name:
        return _match_versioned(ref, advisories)
    if ref.component_identity:
        pkg = _unpinned_identity_to_package(ref.component_identity)
        if pkg is not None:
            return _match_unpinned(ref, pkg, advisories)
    return []


def _match_versioned(ref: ComponentRef, advisories: list[dict]) -> list[Finding]:
    findings: list[Finding] = []
    parsed = _parse_version(ref.version)
    for advisory in advisories:
        for entry in advisory.get("affected") or []:
            pkg = entry.get("package") or {}
            if pkg.get("ecosystem") != ref.ecosystem or pkg.get("name") != ref.name:
                continue
            if parsed is None:
                findings.append(
                    Finding(
                        advisory_id=advisory["id"],
                        component=ref,
                        confidence="low",
                        reason=(
                            f"{ref.name}@{ref.version!r} is a range/spec, not a "
                            f"concrete version — pin to verify against {advisory['id']}"
                        ),
                    )
                )
                break
            if any(_in_range(parsed, r.get("events") or []) for r in entry.get("ranges") or []):
                findings.append(
                    Finding(
                        advisory_id=advisory["id"],
                        component=ref,
                        confidence="high",
                        reason=f"{ref.name}@{ref.version} matches {advisory['id']}",
                    )
                )
                break
    return findings


def _match_unpinned(
    ref: ComponentRef, package: tuple[str, str], advisories: list[dict]
) -> list[Finding]:
    ecosystem, name = package
    findings: list[Finding] = []
    for advisory in advisories:
        for entry in advisory.get("affected") or []:
            pkg = entry.get("package") or {}
            if pkg.get("ecosystem") == ecosystem and pkg.get("name") == name:
                findings.append(
                    Finding(
                        advisory_id=advisory["id"],
                        component=ref,
                        confidence="unknown",
                        reason=(
                            f"{ecosystem}:{name} launched unpinned via "
                            f"{ref.source_locator} matches {advisory['id']} — "
                            "pin the version to verify"
                        ),
                    )
                )
                break
    return findings


def match(refs: list[ComponentRef], advisories: list[dict]) -> list[Finding]:
    findings: list[Finding] = []
    for ref in refs:
        findings.extend(_match_one(ref, advisories))
    return findings
