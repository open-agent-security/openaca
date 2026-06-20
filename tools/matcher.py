"""Match ComponentRefs against OpenACA advisories.

Three match strategies, dispatched per-ref:

- **Versioned match** (high confidence). Ref has ecosystem+name+version
  that parses cleanly via packaging.Version → check OSV ECOSYSTEM ranges
  and emit a finding when version is in the vulnerable interval
  [introduced, fixed). Anything at/after `fixed` is excluded.

- **Unparseable-version match** (low confidence). Ref has ecosystem+name
  but version is a range/spec (`^1.0.0`, `~2.1`, etc.) that
  packaging.Version can't reduce to a single point. We don't drop the
  ref — emit a finding so the consumer knows to pin and verify.

- **Unpinned-launch match** (unknown confidence). Ref carries no resolved
  version but has MCP launch provenance that resolves to a package coordinate.
  Match against advisory `affected[*].package`. Confidence is "unknown"
  because we can't see the runtime-resolved version.

- **External match-coordinate match** (high confidence). Ref carries a
  non-PURL/non-Git match coordinate, such as a future skills.sh audit handle,
  and the advisory targets `database_specific.openaca.match_coordinate`.

Graph occurrence identity (`openaca:identity`) is not a vulnerability matching
coordinate.

Out of V0 scope: the schema's `database_specific.openaca.detection_hints`
field — substring matching against synthesized command lines is fragile
and `affected[*].package` already carries the same information for any
advisory that has a published PURL. Detection hints stay in the corpus
as evidence/documentation but aren't the matching mechanism in V0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from packaging.version import InvalidVersion, Version

from tools.component_ref import ComponentRef
from tools.graph import Graph
from tools.identity import match_coordinates

# Source forge ecosystems use GIT ranges (commit SHAs), not ECOSYSTEM/SEMVER
# ranges. The current matcher only evaluates packaging.Version ranges, so refs
# with a commit SHA (or any non-PEP-440 ref) as version are non-queryable until
# GIT range support is added. Skip them rather than emitting a false low-
# confidence "range/spec" finding.
_FORGE_ECOSYSTEMS: frozenset[str] = frozenset({"github", "GitHub", "gitlab", "GitLab", "git"})


@dataclass(frozen=True)
class Finding:
    advisory_id: str
    component: ComponentRef
    confidence: str  # "high" | "low" | "unknown"
    reason: str = ""
    attributed_to: Optional[str] = None  # mirrored from component.attributed_to


def _parse_version(value: Optional[str]) -> Optional[Version]:
    if value is None:
        return None
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _in_range(version: Version, events: list[dict[str, Any]]) -> bool:
    """Return True if version falls in any vulnerable window encoded in events.

    OSV events alternate introduced/fixed/last_affected/limit within a single
    range object. A trailing introduced with no closing event means the range is
    open-ended (still unpatched). `last_affected` is an inclusive upper bound
    (version <= last_affected is vulnerable); `fixed` and `limit` are exclusive
    (version < fixed/limit is vulnerable).
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
        elif "limit" in ev and intro is not None:
            intro_v = Version("0") if intro == "0" else _parse_version(intro)
            limit_v = _parse_version(ev["limit"])
            if intro_v is not None and limit_v is not None:
                if version >= intro_v and version < limit_v:
                    return True
            intro = None
        elif "last_affected" in ev and intro is not None:
            intro_v = Version("0") if intro == "0" else _parse_version(intro)
            last_v = _parse_version(ev["last_affected"])
            if intro_v is not None and last_v is not None:
                if version >= intro_v and version <= last_v:
                    return True
            intro = None
    # Open-ended: introduced with no fixed/limit/last_affected → still unpatched
    if intro is not None:
        intro_v = Version("0") if intro == "0" else _parse_version(intro)
        if intro_v is not None and version >= intro_v:
            return True
    return False


def _match_one(ref: ComponentRef, advisories: list[dict[str, Any]]) -> list[Finding]:
    coordinates = match_coordinates(ref)
    if ref.ecosystem and ref.name:
        if ref.ecosystem in _FORGE_ECOSYSTEMS:
            return _match_git_ref(ref, advisories)
        pkg = _unpinned_package_coordinate(coordinates)
        if pkg is not None:
            return _match_unpinned(ref, pkg, advisories)
        if ref.version is None:
            return []
        return _match_versioned(ref, advisories)

    pkg = _unpinned_package_coordinate(coordinates)
    if pkg is not None:
        return _match_unpinned(ref, pkg, advisories)
    return _match_external_coordinate(ref, coordinates, advisories)


def _match_external_coordinate(
    ref: ComponentRef, coordinates: list[Any], advisories: list[dict[str, Any]]
) -> list[Finding]:
    targets = {
        coordinate.value
        for coordinate in coordinates
        if coordinate.kind == "external_audit" and coordinate.value
    }
    if not targets:
        return []

    findings: list[Finding] = []
    for advisory in advisories:
        ds = advisory.get("database_specific") or {}
        openaca_block = ds.get("openaca") or {}
        target = openaca_block.get("match_coordinate")
        if isinstance(target, str) and target in targets:
            findings.append(
                Finding(
                    advisory_id=advisory["id"],
                    component=ref,
                    confidence="high",
                    reason=f"{target} matches {advisory['id']} (match-coordinate)",
                    attributed_to=ref.attributed_to,
                )
            )
    return findings


def _unpinned_package_coordinate(coordinates: list[Any]) -> tuple[str, str] | None:
    for coordinate in coordinates:
        if coordinate.kind == "package" and coordinate.ecosystem and coordinate.name:
            return coordinate.ecosystem, coordinate.name
    return None


def _match_git_ref(ref: ComponentRef, advisories: list[dict[str, Any]]) -> list[Finding]:
    repo = _git_repo_name(ref)
    if repo is None:
        return []
    git_ref = ref.extra.get("git_ref") if isinstance(ref.extra, dict) else None
    findings: list[Finding] = []
    for advisory in advisories:
        matching_entries = [
            entry
            for entry in advisory.get("affected") or []
            if _affected_entry_has_git_repo(entry, repo)
        ]
        if not matching_entries:
            continue
        if ref.version and _advisory_has_osv_query_match(advisory, "git_commit", repo, ref.version):
            findings.append(
                Finding(
                    advisory_id=advisory["id"],
                    component=ref,
                    confidence="high",
                    reason=f"{repo}@{ref.version} matches {advisory['id']} (GIT commit)",
                    attributed_to=ref.attributed_to,
                )
            )
            continue
        if isinstance(git_ref, str) and (
            _advisory_has_osv_query_match(advisory, "git_version", repo, git_ref)
            or any(_affected_entry_has_git_version(entry, git_ref) for entry in matching_entries)
        ):
            findings.append(
                Finding(
                    advisory_id=advisory["id"],
                    component=ref,
                    confidence="high",
                    reason=f"{repo}@{git_ref} matches {advisory['id']} (GIT version)",
                    attributed_to=ref.attributed_to,
                )
            )
    return findings


def _advisory_has_osv_query_match(advisory: dict[str, Any], kind: str, repo: str, ref: str) -> bool:
    ds = advisory.get("database_specific") or {}
    if not isinstance(ds, dict):
        return False
    openaca_block = ds.get("openaca") or {}
    if not isinstance(openaca_block, dict):
        return False
    matches = openaca_block.get("osv_query_matches") or []
    for match_entry in matches:
        if not isinstance(match_entry, dict):
            continue
        if (
            match_entry.get("kind") == kind
            and match_entry.get("repo") == repo
            and match_entry.get("ref") == ref
        ):
            return True
    return False


def _git_repo_name(ref: ComponentRef) -> str | None:
    if not ref.name:
        return None
    if ref.ecosystem in {"github", "GitHub"}:
        return f"github.com/{ref.name.lower()}"
    return ref.name


def _affected_entry_has_git_repo(entry: dict[str, Any], repo: str) -> bool:
    for range_entry in entry.get("ranges") or []:
        if range_entry.get("type") != "GIT":
            continue
        candidate = range_entry.get("repo")
        if isinstance(candidate, str) and _normalize_git_repo(candidate) == repo:
            return True
    return False


def _affected_entry_has_git_version(entry: dict[str, Any], git_ref: str) -> bool:
    candidates = {_normalize_git_ref(git_ref)}
    for version in entry.get("versions") or []:
        if isinstance(version, str) and _normalize_git_ref(version) in candidates:
            return True
    return False


def _normalize_git_repo(repo: str) -> str:
    parsed = urlparse(repo)
    if parsed.netloc:
        normalized = f"{parsed.netloc}{parsed.path}"
    else:
        normalized = repo
    return normalized.rstrip("/").removesuffix(".git").lower()


def _normalize_git_ref(ref: str) -> str:
    return ref.removeprefix("refs/tags/")


def _match_versioned(ref: ComponentRef, advisories: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    parsed = _parse_version(ref.version)
    for advisory in advisories:
        for entry in advisory.get("affected") or []:
            pkg = entry.get("package") or {}
            if pkg.get("ecosystem") != ref.ecosystem or pkg.get("name") != ref.name:
                continue
            if parsed is None:
                if ref.ecosystem in _FORGE_ECOSYSTEMS:
                    break
                findings.append(
                    Finding(
                        advisory_id=advisory["id"],
                        component=ref,
                        confidence="low",
                        reason=(
                            f"{ref.name}@{ref.version!r} is a range/spec, not a "
                            f"concrete version — pin to verify against {advisory['id']}"
                        ),
                        attributed_to=ref.attributed_to,
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
                        attributed_to=ref.attributed_to,
                    )
                )
                break
    return findings


def _match_unpinned(
    ref: ComponentRef, package: tuple[str, str], advisories: list[dict[str, Any]]
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
                        attributed_to=ref.attributed_to,
                    )
                )
                break
    return findings


def match(
    refs: list[ComponentRef],
    advisories: list[dict[str, Any]],
    *,
    graph: Graph | None = None,
) -> list[Finding]:
    # `graph` is threaded by scan as the source of truth for attribution
    # (Stage 3); consumed by matcher/sarif in a later stage. Unused here.
    findings: list[Finding] = []
    for ref in refs:
        findings.extend(_match_one(ref, advisories))
    return findings
