"""Optional SkillSpector integration.

SkillSpector is consumed through its CLI/SARIF contract rather than imported as
a Python dependency. That keeps OpenACA's default install small and preserves
source attribution while classifying each scanner rule by claim type.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from tools.component_ref import ComponentRef, canonical_component_identity
from tools.observations.finding import (
    Confidence,
    ObservationFinding,
)
from tools.observations.finding import (
    Severity as ObservationSeverity,
)
from tools.posture.finding import PostureFinding, Standards
from tools.posture.finding import Severity as PostureSeverity

DEFAULT_COMMAND = "skillspector"
DEFAULT_TIMEOUT_SECONDS = 120.0

RunCommand = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]

_CATEGORY_MAP: dict[str, list[str]] = {
    # Prompt Injection (P1-P5). Source: NVIDIA/SkillSpector README.
    "P1": ["prompt-injection"],
    "P2": ["prompt-injection"],
    "P3": ["prompt-injection"],
    "P4": ["prompt-injection"],
    "P5": ["prompt-injection"],
    # System Prompt Leakage (P6-P8). Source: NVIDIA/SkillSpector README.
    "P6": ["data-exfiltration"],  # Direct Leakage
    "P7": ["data-exfiltration"],  # Indirect Extraction
    "P8": ["data-exfiltration"],  # Tool-Based Exfiltration
    # Data Exfiltration (E1-E4). Source: NVIDIA/SkillSpector README.
    "E1": ["data-exfiltration"],
    "E2": ["data-exfiltration"],
    "E3": ["data-exfiltration"],
    "E4": ["data-exfiltration"],
    # Privilege Escalation (PE1-PE3). Source: NVIDIA/SkillSpector README.
    "PE1": ["privilege-escalation"],
    "PE2": ["privilege-escalation"],
    "PE3": ["privilege-escalation"],
    # Supply Chain (SC1-SC6). Source: NVIDIA/SkillSpector README.
    "SC1": ["supply-chain"],
    "SC2": ["supply-chain"],
    "SC3": ["supply-chain"],
    "SC4": ["supply-chain"],
    "SC5": ["supply-chain"],
    "SC6": ["supply-chain"],
    # Excessive Agency (EA1-EA4). Source: NVIDIA/SkillSpector README.
    "EA1": ["excessive-agency"],  # Unrestricted Tool Access
    "EA2": ["excessive-agency"],  # Autonomous Decision Making
    "EA3": ["excessive-agency"],  # Scope Creep
    "EA4": ["excessive-agency"],  # Unbounded Resource Access
    # Output Handling (OH1-OH3). Source: NVIDIA/SkillSpector README.
    "OH1": ["prompt-injection"],  # Unvalidated Output Injection — injection via output
    "OH2": ["data-exfiltration"],  # Cross-Context Output — data leaking across contexts
    "OH3": ["data-exfiltration"],  # Unbounded Output — excessive data in output
    # Memory Poisoning (MP1-MP3). Source: NVIDIA/SkillSpector README.
    "MP1": ["prompt-injection"],  # Persistent Context Injection
    "MP2": ["prompt-injection"],  # Context Window Stuffing
    "MP3": ["prompt-injection"],  # Memory Manipulation
    # Tool Misuse (TM1-TM3). Source: NVIDIA/SkillSpector README.
    "TM1": ["unsafe-tool-use"],  # Tool Parameter Abuse
    "TM2": ["unsafe-tool-use"],  # Chaining Abuse
    "TM3": ["unsafe-tool-use"],  # Unsafe Defaults
    # Rogue Agent (RA1-RA2). Source: NVIDIA/SkillSpector README.
    "RA1": ["excessive-agency"],  # Self-Modification
    "RA2": ["excessive-agency"],  # Session Persistence
    # Trigger Abuse (TR1-TR3). Source: NVIDIA/SkillSpector README.
    "TR1": ["excessive-agency"],  # Overly Broad Trigger — triggers beyond intended scope
    "TR2": ["prompt-injection"],  # Shadow Command Trigger — hidden commands
    "TR3": ["prompt-injection"],  # Keyword Baiting Trigger — baiting to override behavior
    # Behavioral AST (AST1-AST8). Source: NVIDIA/SkillSpector README.
    "AST1": ["unsafe-tool-use"],  # exec() Call
    "AST2": ["unsafe-tool-use"],  # eval() Call
    "AST3": ["unsafe-tool-use"],  # Dynamic Import
    "AST4": ["unsafe-tool-use"],  # subprocess Call
    "AST5": ["unsafe-tool-use"],  # os.system / exec-family
    "AST6": ["unsafe-tool-use"],  # compile() Call
    "AST7": ["unsafe-tool-use"],  # Dynamic getattr()
    "AST8": ["unsafe-tool-use"],  # Dangerous Execution Chain
    # Taint Tracking (TT1-TT5). Source: NVIDIA/SkillSpector README.
    "TT1": ["data-exfiltration"],  # Direct Taint Flow
    "TT2": ["data-exfiltration"],  # Variable-Mediated Taint Flow
    "TT3": ["data-exfiltration"],  # Credential Exfiltration Chain
    "TT4": ["data-exfiltration"],  # File Read to Network Exfiltration
    "TT5": ["prompt-injection"],  # External Input to Code Execution
    # YARA Signatures (YR1-YR4). Source: NVIDIA/SkillSpector README.
    "YR1": ["supply-chain"],  # Malware Match
    "YR2": ["supply-chain"],  # Webshell Match
    "YR3": ["supply-chain"],  # Cryptominer Match
    "YR4": ["supply-chain"],  # Hack Tool / Exploit Match
    # MCP Least Privilege (LP1-LP4). Source: NVIDIA/SkillSpector README.
    "LP1": ["privilege-escalation"],  # Underdeclared Capability
    "LP2": ["privilege-escalation"],  # Wildcard Permission
    "LP3": ["privilege-escalation"],  # Missing Permission Declaration
    "LP4": ["privilege-escalation"],  # Overdeclared Permission
    # MCP Tool Poisoning (TP1-TP4). Source: NVIDIA/SkillSpector README.
    "TP1": ["prompt-injection"],  # Hidden Instructions
    "TP2": ["prompt-injection"],  # Unicode Deception
    "TP3": ["prompt-injection"],  # Parameter Description Injection
    "TP4": ["unsafe-tool-use"],  # Description-Behavior Mismatch
}

_LEVEL_TO_SEVERITY: dict[str, ObservationSeverity] = {
    "error": "high",
    "warning": "medium",
    "note": "low",
    "none": "low",
}

# Severity overrides for SkillSpector rules whose SARIF level="error" under-
# reports native CRITICAL severity. SkillSpector collapses HIGH and CRITICAL
# to level="error"; entries here inject openaca_severity so the adapter emits
# "critical" instead of "high".
# Source: NVIDIA/SkillSpector README vulnerability-pattern table.
_SEVERITY_MAP: dict[str, ObservationSeverity] = {
    "P5": "critical",
    "RA1": "critical",
    "AST1": "critical",
    "AST8": "critical",
    "TT3": "critical",
    "TT5": "critical",
    "YR1": "critical",
    "YR2": "critical",
}

# Posture = declarative configuration / permission / provenance hygiene claims; these
# route to PostureFinding under ADR-0035. The rest are content/behavior claims
# (observations). LP1-LP4: declared permission/capability hygiene. SC1/SC5/SC6: dependency
# provenance hygiene (unpinned, abandoned, typosquatting declarations). SC2/SC3 (remote
# code execution, obfuscated code) stay observations — they describe artifact behavior,
# not the declaration.
_POSTURE_RULE_IDS = frozenset({"LP1", "LP2", "LP3", "LP4", "SC1", "SC5", "SC6"})

# SC4 (known-vulnerable dependency) is a CVE/advisory match, not an observation.
# OpenACA's vulnerability pipeline owns dependency advisory matching for natively
# parsed dependency manifests and lockfiles. We do not ingest external scanner
# vulnerability findings yet, so skip SC4 rather than mixing a second advisory
# source into the posture/observation adapter.
_SKIP_RULE_IDS = frozenset({"SC4"})


@dataclass(frozen=True)
class SkillSpectorFindings:
    observations: list[ObservationFinding] = field(default_factory=list)
    posture_findings: list[PostureFinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def collect_skillspector_findings(
    refs: list[ComponentRef],
    *,
    command: str = DEFAULT_COMMAND,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    run_command: RunCommand | None = None,
) -> SkillSpectorFindings:
    observations: list[ObservationFinding] = []
    posture_findings: list[PostureFinding] = []
    warnings: list[str] = []
    runner = run_command or _run_command

    for ref in refs:
        scan_path = _skill_scan_path(ref)
        if scan_path is None:
            continue
        with tempfile.TemporaryDirectory(prefix="openaca-skillspector-") as tmp:
            sarif_path = Path(tmp) / "skillspector.sarif"
            args = [
                command,
                "scan",
                str(scan_path),
                "--no-llm",
                "--format",
                "sarif",
                "--output",
                str(sarif_path),
            ]
            try:
                result = runner(args, timeout_seconds)
            except FileNotFoundError:
                return SkillSpectorFindings(
                    observations=observations,
                    posture_findings=posture_findings,
                    warnings=[f"SkillSpector command not found: {command}"],
                )
            except subprocess.TimeoutExpired:
                warnings.append(f"SkillSpector timed out for {scan_path}")
                continue

            sarif = _read_sarif(sarif_path)
            if sarif is None:
                if result.returncode != 0:
                    warnings.append(
                        f"SkillSpector failed for {scan_path}: exit {result.returncode}"
                    )
                continue
            _apply_severity_overrides(sarif, _SEVERITY_MAP)
            ref_findings = _findings_from_sarif(ref, sarif, scan_path)
            observations.extend(ref_findings.observations)
            posture_findings.extend(ref_findings.posture_findings)

    return SkillSpectorFindings(
        observations=observations,
        posture_findings=posture_findings,
        warnings=warnings,
    )


def collect_skillspector_observations(
    refs: list[ComponentRef],
    *,
    command: str = DEFAULT_COMMAND,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    run_command: RunCommand | None = None,
) -> tuple[list[ObservationFinding], list[str]]:
    result = collect_skillspector_findings(
        refs,
        command=command,
        timeout_seconds=timeout_seconds,
        run_command=run_command,
    )
    return result.observations, result.warnings


def _run_command(args: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _skill_scan_path(ref: ComponentRef) -> Path | None:
    if (ref.extra or {}).get("component_type") != "skill":
        return None
    if not ref.source_manifest:
        return None
    source = Path(ref.source_manifest)
    scan_path = source.parent if source.name == "SKILL.md" else source
    return scan_path if scan_path.exists() else None


def _read_sarif(path: Path) -> dict[str, Any] | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _findings_from_sarif(
    ref: ComponentRef,
    sarif: dict[str, Any],
    scan_path: Path,
) -> SkillSpectorFindings:
    observations: list[ObservationFinding] = []
    posture_findings: list[PostureFinding] = []
    for run in _list_of_dicts(sarif.get("runs")):
        driver = _dict_at(run, "tool", "driver")
        source_version = (
            _str(driver.get("semanticVersion")) or _str(driver.get("version")) or "unknown"
        )
        for result in _list_of_dicts(run.get("results")):
            rule_id = _result_rule_id(result)
            if rule_id is None:
                continue
            if rule_id in _SKIP_RULE_IDS:
                continue
            if rule_id in _POSTURE_RULE_IDS:
                posture = _posture_from_result(ref, result, rule_id, scan_path, source_version)
                if posture is not None:
                    posture_findings.append(posture)
                continue
            observation = _observation_from_result(ref, result, rule_id, scan_path, source_version)
            if observation is not None:
                observations.append(observation)
    return SkillSpectorFindings(
        observations=observations,
        posture_findings=posture_findings,
    )


def _observation_from_result(
    ref: ComponentRef,
    result: dict[str, Any],
    rule_id: str,
    scan_path: Path,
    source_version: str,
) -> ObservationFinding | None:
    message = _message_text(result.get("message")) or rule_id
    location = _first_location(result, scan_path)
    declared_by = {"kind": "sarif", "path": location["uri"]} if "uri" in location else None
    evidence = _evidence_for_result(result, rule_id, message, location)
    identity = canonical_component_identity(ref) or ref.component_identity or ref.name or "skill"
    return ObservationFinding(
        source="skillspector",
        source_version=source_version,
        observation_id=rule_id,
        title=message,
        severity=_severity(result),
        confidence=_confidence(result),
        component={
            "identity": identity,
            "name": ref.name or identity,
            "type": str((ref.extra or {}).get("component_type") or "component"),
        },
        subject_coordinate=_subject_coordinate(ref),
        evidence=evidence,
        categories=list(_CATEGORY_MAP.get(rule_id, [])),
        declared_by=declared_by,
        component_path=_component_path(ref),
    )


def _posture_from_result(
    ref: ComponentRef,
    result: dict[str, Any],
    rule_id: str,
    scan_path: Path,
    source_version: str,
) -> PostureFinding | None:
    message = _message_text(result.get("message")) or rule_id
    location = _first_location(result, scan_path)
    declared_by = {"kind": "sarif", "path": location["uri"]} if "uri" in location else None
    evidence = _evidence_for_result(result, rule_id, message, location)
    categories = _CATEGORY_MAP.get(rule_id, [])
    if categories:
        evidence["categories"] = list(categories)
    identity = canonical_component_identity(ref) or ref.component_identity or ref.name or "skill"
    return PostureFinding(
        source="skillspector",
        source_version=source_version,
        rule_id=rule_id,
        title=message,
        severity=_posture_severity(result),
        confidence=_confidence(result),
        component={
            "identity": identity,
            "name": ref.name or identity,
            "type": str((ref.extra or {}).get("component_type") or "component"),
        },
        active_in=_active_in(ref),
        declared_by=declared_by,
        component_path=_component_path(ref),
        standards=Standards(),
        remediation=message,
        evidence=evidence,
    )


def _evidence_for_result(
    result: dict[str, Any],
    rule_id: str,
    message: str,
    location: dict[str, Any],
) -> dict[str, Any]:
    return {
        "sarif_rule_id": rule_id,
        **({"sarif_level": result.get("level")} if isinstance(result.get("level"), str) else {}),
        "sarif_message": message,
        **({"location_uri": location["uri"]} if "uri" in location else {}),
        **({"start_line": location["start_line"]} if "start_line" in location else {}),
    }


def _result_rule_id(result: dict[str, Any]) -> str | None:
    return _str(result.get("ruleId")) or _str(_dict_at(result, "rule").get("id"))


def _severity(result: dict[str, Any]) -> ObservationSeverity:
    explicit = _first_string_property(result, "openaca_severity", "severity")
    if explicit in {"info", "low", "medium", "high", "critical"}:
        return cast(ObservationSeverity, explicit)
    score = _first_string_property(result, "security-severity", "security_severity")
    if score is not None:
        try:
            parsed = float(score)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed >= 9.0:
                return "critical"
            if parsed >= 7.0:
                return "high"
            if parsed >= 4.0:
                return "medium"
            if parsed > 0:
                return "low"
            return "info"
    level = _str(result.get("level")) or "warning"
    return _LEVEL_TO_SEVERITY.get(level, "medium")


def _posture_severity(result: dict[str, Any]) -> PostureSeverity:
    severity = _severity(result)
    if severity == "critical":
        return "high"
    if severity == "info":
        return "low"
    return cast(PostureSeverity, severity)


def _confidence(result: dict[str, Any]) -> Confidence:
    explicit = _first_string_property(result, "openaca_confidence", "confidence", "precision")
    if explicit in {"low", "medium", "high"}:
        return cast(Confidence, explicit)
    if explicit == "very-high":
        return "high"
    return "medium"


def _first_string_property(result: dict[str, Any], *names: str) -> str | None:
    properties = _dict_at(result, "properties")
    for name in names:
        value = _str(properties.get(name))
        if value is not None:
            return value.lower()
    return None


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
    return [{"type": "skill", "name": identity}] if identity else []


def _first_location(result: dict[str, Any], scan_path: Path) -> dict[str, Any]:
    for location in _list_of_dicts(result.get("locations")):
        physical = _dict_at(location, "physicalLocation")
        artifact = _dict_at(physical, "artifactLocation")
        region = _dict_at(physical, "region")
        out: dict[str, Any] = {}
        uri = _str(artifact.get("uri"))
        if uri is not None:
            out["uri"] = uri if _is_absolute_uri(uri) else str(scan_path / uri)
        start_line = region.get("startLine")
        if isinstance(start_line, int):
            out["start_line"] = start_line
        return out
    return {}


def _is_absolute_uri(uri: str) -> bool:
    return uri.startswith(("/", "file://", "http://", "https://"))


def _active_in(ref: ComponentRef) -> list[str]:
    runtime_hosts = (ref.extra or {}).get("runtime_hosts")
    if isinstance(runtime_hosts, list):
        return [h for h in runtime_hosts if isinstance(h, str)]
    return []


def _apply_severity_overrides(
    sarif: dict[str, Any], severity_map: dict[str, ObservationSeverity]
) -> None:
    if not severity_map:
        return
    for run in _list_of_dicts(sarif.get("runs")):
        for result in _list_of_dicts(run.get("results")):
            rule_id = _str(result.get("ruleId")) or _str(_dict_at(result, "rule").get("id"))
            if rule_id is None:
                continue
            severity = severity_map.get(rule_id)
            if severity is None:
                continue
            props = result.setdefault("properties", {})
            if isinstance(props, dict):
                props["openaca_severity"] = severity


def _message_text(raw: object) -> str | None:
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, dict):
        text = raw.get("text")
        if isinstance(text, str) and text:
            return text
    return None


def _dict_at(raw: dict[str, Any], *path: str) -> dict[str, Any]:
    current: object = raw
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _list_of_dicts(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _str(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None
