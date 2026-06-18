"""Normalize external scanner SARIF into OpenACA observations.

SARIF is the transport format, not the trust model. This adapter preserves the
scanner source and rule identity while attaching the observation to an OpenACA
component/skill coordinate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from tools.component_ref import ComponentRef, canonical_component_identity
from tools.observations.finding import Confidence, ObservationFinding, Severity

DEFAULT_SOURCE_VERSION = "unknown"

_SEVERITIES: set[str] = {"info", "low", "medium", "high", "critical"}
_CONFIDENCES: set[str] = {"low", "medium", "high"}


@dataclass(frozen=True)
class SarifObservationAdapter:
    """Adapter for source-attributed scanner SARIF.

    `category_map` is intentionally adapter-owned rather than inferred from
    SARIF alone. Different scanners reuse rule IDs and category labels, so
    OpenACA mappings must be explicit.
    """

    source: str | None = None
    source_version: str | None = None
    category_map: Mapping[str, list[str]] = field(default_factory=dict)

    def collect(self, ref: ComponentRef, sarif: Mapping[str, Any]) -> list[ObservationFinding]:
        observations: list[ObservationFinding] = []
        for run in _list_of_dicts(sarif.get("runs")):
            driver = _dict_at(run, "tool", "driver")
            source = self.source or _str(driver.get("name")) or "sarif-scanner"
            source_version = (
                self.source_version
                or _str(driver.get("semanticVersion"))
                or _str(driver.get("version"))
                or DEFAULT_SOURCE_VERSION
            )
            driver_rules_list = _list_of_dicts(driver.get("rules"))
            extension_rules_lists = [
                _list_of_dicts(ext.get("rules"))
                for ext in _list_of_dicts(_dict_at(run, "tool").get("extensions"))
            ]
            # Merge driver + extension rules for ID-based lookup (SARIF rule IDs are unique per run)
            rules = _rules_by_id(driver_rules_list)
            for ext_rules in extension_rules_lists:
                rules.update(_rules_by_id(ext_rules))
            for result in _list_of_dicts(run.get("results")):
                observation = self._observation_from_result(
                    ref=ref,
                    result=result,
                    rules=rules,
                    driver_rules_list=driver_rules_list,
                    extension_rules_lists=extension_rules_lists,
                    source=source,
                    source_version=source_version,
                )
                if observation is not None:
                    observations.append(observation)
        return observations

    def _observation_from_result(
        self,
        *,
        ref: ComponentRef,
        result: Mapping[str, Any],
        rules: Mapping[str, Mapping[str, Any]],
        driver_rules_list: list[Mapping[str, Any]],
        extension_rules_lists: list[list[Mapping[str, Any]]],
        source: str,
        source_version: str,
    ) -> ObservationFinding | None:
        rule_id = _str(result.get("ruleId")) or _str(_dict_at(result, "rule").get("id"))
        if rule_id is None:
            # SARIF 2.1.0: toolComponent.index selects which extension's rules to resolve by index;
            # absent toolComponent means driver rules
            tc_index = _dict_at(result, "rule", "toolComponent").get("index")
            if isinstance(tc_index, int) and 0 <= tc_index < len(extension_rules_lists):
                component_rules_list = extension_rules_lists[tc_index]
            else:
                component_rules_list = driver_rules_list
            rule_index = result.get("ruleIndex")
            if not isinstance(rule_index, int):
                rule_index = _dict_at(result, "rule").get("index")
            if isinstance(rule_index, int) and 0 <= rule_index < len(component_rules_list):
                rule_id = _str(component_rules_list[rule_index].get("id"))
        if rule_id is None:
            return None
        rule = rules.get(rule_id, {})
        message = _message_text(result.get("message"))
        title = _message_text(rule.get("shortDescription")) or message or rule_id
        location = _first_location(result)
        declared_by = {"kind": "sarif", "path": location["uri"]} if "uri" in location else None
        raw_tags = _raw_sarif_tags(result, rule)
        evidence = {
            "sarif_rule_id": rule_id,
            **(
                {"sarif_level": result.get("level")} if isinstance(result.get("level"), str) else {}
            ),
            **({"sarif_message": message} if message else {}),
            **({"location_uri": location["uri"]} if "uri" in location else {}),
            **({"start_line": location["start_line"]} if "start_line" in location else {}),
            **({"sarif_tags": raw_tags} if raw_tags else {}),
        }
        identity = (
            canonical_component_identity(ref) or ref.component_identity or ref.name or "skill"
        )
        return ObservationFinding(
            source=source,
            source_version=source_version,
            observation_id=rule_id,
            title=title,
            severity=_severity(result, rule),
            confidence=_confidence(result, rule),
            component={
                "identity": identity,
                "name": ref.name or identity,
                "type": str((ref.extra or {}).get("component_type") or "component"),
            },
            subject_coordinate=_subject_coordinate(ref),
            evidence=evidence,
            categories=_categories(rule_id, self.category_map),
            remediation=_message_text(rule.get("help")) or None,
            declared_by=declared_by,
            component_path=_component_path(ref),
        )


def _rules_by_id(rules_list: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    rules: dict[str, Mapping[str, Any]] = {}
    for rule in rules_list:
        rule_id = _str(rule.get("id"))
        if rule_id is not None:
            rules[rule_id] = rule
    return rules


def _severity(result: Mapping[str, Any], rule: Mapping[str, Any]) -> Severity:
    explicit = _first_string_property(result, rule, "openaca_severity", "severity")
    if explicit in _SEVERITIES:
        return cast(Severity, explicit)
    score = _security_severity(result, rule)
    if score is not None:
        if score >= 9.0:
            return "critical"
        if score >= 7.0:
            return "high"
        if score >= 4.0:
            return "medium"
        if score > 0:
            return "low"
        return "info"
    level = _str(result.get("level"))
    if level is None:
        # SARIF 2.1.0: absent level inherits rule defaultConfiguration.level, then "warning"
        level = _str(_dict_at(rule, "defaultConfiguration").get("level")) or "warning"
    if level == "error":
        return "high"
    if level == "warning":
        return "medium"
    if level in {"note", "none"}:
        return "low"
    return "medium"


def _confidence(result: Mapping[str, Any], rule: Mapping[str, Any]) -> Confidence:
    explicit = _first_string_property(result, rule, "openaca_confidence", "confidence", "precision")
    if explicit in _CONFIDENCES:
        return cast(Confidence, explicit)
    if explicit == "very-high":
        return "high"
    return "medium"


def _security_severity(result: Mapping[str, Any], rule: Mapping[str, Any]) -> float | None:
    raw = _first_string_property(result, rule, "security-severity", "security_severity")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _first_string_property(
    result: Mapping[str, Any], rule: Mapping[str, Any], *names: str
) -> str | None:
    for container in (_dict_at(result, "properties"), _dict_at(rule, "properties")):
        for name in names:
            value = _str(container.get(name))
            if value is not None:
                return value.lower()
    return None


def _categories(
    rule_id: str,
    category_map: Mapping[str, list[str]],
) -> list[str]:
    return list(dict.fromkeys(category_map[rule_id])) if rule_id in category_map else []


def _raw_sarif_tags(result: Mapping[str, Any], rule: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for container in (_dict_at(result, "properties"), _dict_at(rule, "properties")):
        for key in ("tags", "categories"):
            raw = container.get(key)
            if isinstance(raw, list):
                values.extend(item for item in raw if isinstance(item, str) and item)
    return list(dict.fromkeys(values))


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


def _first_location(result: Mapping[str, Any]) -> dict[str, Any]:
    for location in _list_of_dicts(result.get("locations")):
        physical = _dict_at(location, "physicalLocation")
        artifact = _dict_at(physical, "artifactLocation")
        region = _dict_at(physical, "region")
        out: dict[str, Any] = {}
        uri = _str(artifact.get("uri"))
        if uri is not None:
            out["uri"] = uri
        start_line = region.get("startLine")
        if isinstance(start_line, int):
            out["start_line"] = start_line
        return out
    return {}


def _message_text(raw: object) -> str | None:
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, dict):
        text = raw.get("text")
        if isinstance(text, str) and text:
            return text
    return None


def _dict_at(raw: Mapping[str, Any], *path: str) -> dict[str, Any]:
    current: object = raw
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _list_of_dicts(raw: object) -> list[Mapping[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _str(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None
