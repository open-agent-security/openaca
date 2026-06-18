"""Normalize external scanner SARIF into OpenACA observations.

SARIF is the transport format, not the trust model. This adapter preserves the
scanner source and rule identity while attaching the observation to an OpenACA
component/skill coordinate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from tools.component_ref import ComponentRef, canonical_component_identity
from tools.observations.finding import Confidence, ObservationFinding, Severity

DEFAULT_SOURCE_VERSION = "unknown"

_SEVERITIES: set[str] = {"info", "low", "medium", "high", "critical"}
_CONFIDENCES: set[str] = {"low", "medium", "high"}
# SARIF 2.1.0 result.kind values that are not security findings (§3.27.9)
_NON_FINDING_KINDS: frozenset[str] = frozenset({"pass", "notApplicable", "informational"})


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
            extensions = _list_of_dicts(_dict_at(run, "tool").get("extensions"))
            extension_rules_lists = [_list_of_dicts(ext.get("rules")) for ext in extensions]
            # SARIF 2.1.0: run.artifacts resolves artifactLocation.index references
            artifacts = _list_of_dicts(run.get("artifacts"))
            # SARIF 2.1.0 §3.20.5: invocation ruleConfigurationOverrides re-level rules at runtime
            invocations = _list_of_dicts(run.get("invocations"))
            # SARIF 2.1.0 §3.52.3: an absent toolComponent references tool.driver, so the
            # id-based fallback is driver-scoped — same-id extension rules must not shadow it.
            # Extension rules are resolved only when a result explicitly references them.
            rules = _rules_by_id(driver_rules_list)
            # guids are globally unique across components, so a merged guid map is unambiguous.
            rules_by_guid = _rules_by_guid(driver_rules_list)
            for ext_rules in extension_rules_lists:
                rules_by_guid.update(_rules_by_guid(ext_rules))
            for result in _list_of_dicts(run.get("results")):
                # SARIF 2.1.0: kind defaults to "fail"; skip non-finding evaluation states
                if result.get("kind") in _NON_FINDING_KINDS:
                    continue
                # Skip results not active in the current run: an "absent" baseline state
                # (fixed since baseline) or an accepted suppression would otherwise resurface
                # as a current observation.
                if _is_inactive_result(result):
                    continue
                observation = self._observation_from_result(
                    ref=ref,
                    result=result,
                    rules=rules,
                    rules_by_guid=rules_by_guid,
                    driver_rules_list=driver_rules_list,
                    extension_rules_lists=extension_rules_lists,
                    extensions=extensions,
                    artifacts=artifacts,
                    invocations=invocations,
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
        rules_by_guid: Mapping[str, Mapping[str, Any]],
        driver_rules_list: list[Mapping[str, Any]],
        extension_rules_lists: list[list[Mapping[str, Any]]],
        extensions: list[Mapping[str, Any]],
        artifacts: list[Mapping[str, Any]],
        invocations: list[Mapping[str, Any]],
        source: str,
        source_version: str,
    ) -> ObservationFinding | None:
        rule_id, rule = _resolve_rule(
            result, rules, rules_by_guid, driver_rules_list, extension_rules_lists, extensions
        )
        if rule_id is None:
            return None
        message = _result_message_text(result.get("message"), rule)
        title = _message_text(rule.get("shortDescription")) or message or rule_id
        location = _first_location(result, artifacts)
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
            severity=_severity(result, rule, invocations, extensions),
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


def _resolve_rule(
    result: Mapping[str, Any],
    rules: Mapping[str, Mapping[str, Any]],
    rules_by_guid: Mapping[str, Mapping[str, Any]],
    driver_rules_list: list[Mapping[str, Any]],
    extension_rules_lists: list[list[Mapping[str, Any]]],
    extensions: list[Mapping[str, Any]],
) -> tuple[str | None, Mapping[str, Any]]:
    # SARIF 2.1.0 §3.27.6: when ruleIndex is present use it directly — it is
    # authoritative for disambiguation when duplicate rule ids exist in the array.
    rule_index = result.get("ruleIndex")
    if not isinstance(rule_index, int):
        rule_index = _dict_at(result, "rule").get("index")
    if isinstance(rule_index, int):
        tc_ref = _dict_at(result, "rule", "toolComponent")
        component_rules_list = (
            _referenced_extension_rules(tc_ref, extension_rules_lists, extensions)
            or driver_rules_list
        )
        if 0 <= rule_index < len(component_rules_list):
            indexed_rule = component_rules_list[rule_index]
            # SARIF 2.1.0 §3.52.4: the reference id MAY extend the descriptor id with
            # additional hierarchical components, so the result-level id can be more
            # specific than the indexed descriptor's id. Prefer the scanner-emitted
            # reference id for the observation identity (and category_map key); keep the
            # indexed descriptor for metadata (shortDescription, help, defaultConfiguration).
            rule_id = (
                _str(result.get("ruleId"))
                or _str(_dict_at(result, "rule").get("id"))
                or _str(indexed_rule.get("id"))
            )
            return rule_id, indexed_rule
    # Fall back to id-based lookup. SARIF 2.1.0 §3.52.3: an absent toolComponent references
    # tool.driver, so resolve against an explicitly referenced extension if present, otherwise
    # the driver-scoped `rules` map — a same-id extension rule must not shadow the driver rule.
    rule_id = _str(result.get("ruleId")) or _str(_dict_at(result, "rule").get("id"))
    if rule_id is not None:
        tc_ref = _dict_at(result, "rule", "toolComponent")
        if tc_ref:
            ext_rules_list = _referenced_extension_rules(tc_ref, extension_rules_lists, extensions)
            if ext_rules_list is not None:
                return rule_id, _rule_for_hierarchical_id(rule_id, _rules_by_id(ext_rules_list))
        return rule_id, _rule_for_hierarchical_id(rule_id, rules)
    # SARIF 2.1.0 §3.52.3: a reference may locate the rule by guid instead of id/index.
    # Resolve the descriptor by guid and use its (required) id as the observation identity.
    rule_guid = _str(_dict_at(result, "rule").get("guid"))
    if rule_guid is not None:
        descriptor = rules_by_guid.get(rule_guid)
        if descriptor is not None:
            descriptor_id = _str(descriptor.get("id"))
            if descriptor_id is not None:
                return descriptor_id, descriptor
    return None, {}


def _is_inactive_result(result: Mapping[str, Any]) -> bool:
    # SARIF 2.1.0: baselineState "absent" means the result was present in the baseline but
    # not the current run (i.e. fixed); an accepted suppression means it was intentionally
    # muted. Either way it is not a current active observation. suppression.status defaults
    # to "accepted" when omitted.
    if result.get("baselineState") == "absent":
        return True
    for suppression in _list_of_dicts(result.get("suppressions")):
        status = suppression.get("status")
        if status is None or status == "accepted":
            return True
    return False


def _referenced_extension_rules(
    tc_ref: Mapping[str, Any],
    extension_rules_lists: list[list[Mapping[str, Any]]],
    extensions: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]] | None:
    # Resolve the extension a reference points at by toolComponent index, then name/guid.
    # Returns None when no extension is referenced (caller falls back to the driver).
    tc_index = tc_ref.get("index")
    if isinstance(tc_index, int) and 0 <= tc_index < len(extension_rules_lists):
        return extension_rules_lists[tc_index]
    return _extension_rules_by_ref(tc_ref, extensions)


def _extension_rules_by_ref(
    tc_ref: Mapping[str, Any],
    extensions: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]] | None:
    tc_name = _str(tc_ref.get("name"))
    tc_guid = _str(tc_ref.get("guid"))
    if not tc_name and not tc_guid:
        return None
    for ext in extensions:
        if tc_name and _str(ext.get("name")) == tc_name:
            return _list_of_dicts(ext.get("rules"))
        if tc_guid and _str(ext.get("guid")) == tc_guid:
            return _list_of_dicts(ext.get("rules"))
    return None


def _rules_by_id(rules_list: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    rules: dict[str, Mapping[str, Any]] = {}
    for rule in rules_list:
        rule_id = _str(rule.get("id"))
        if rule_id is not None:
            rules[rule_id] = rule
    return rules


def _rules_by_guid(rules_list: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    rules: dict[str, Mapping[str, Any]] = {}
    for rule in rules_list:
        guid = _str(rule.get("guid"))
        if guid is not None:
            rules[guid] = rule
    return rules


def _rule_for_hierarchical_id(
    rule_id: str, rules: Mapping[str, Mapping[str, Any]]
) -> Mapping[str, Any]:
    # SARIF 2.1.0 §3.52.4: a hierarchical reference id (e.g. "P1/sub") extends a base
    # descriptor id. If the full id is not registered, fall back to the longest
    # registered prefix so descriptor metadata (title, help, defaultConfiguration) is
    # preserved instead of dropped.
    if rule_id in rules:
        return rules[rule_id]
    parts = rule_id.split("/")
    for end in range(len(parts) - 1, 0, -1):
        prefix = "/".join(parts[:end])
        if prefix in rules:
            return rules[prefix]
    return {}


def _severity(
    result: Mapping[str, Any],
    rule: Mapping[str, Any],
    invocations: list[Mapping[str, Any]],
    extensions: list[Mapping[str, Any]],
) -> Severity:
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
        # SARIF 2.1.0: when result.level is absent, an invocation
        # ruleConfigurationOverride (§3.20.5) re-levels the rule for that run and takes
        # precedence over the rule's defaultConfiguration; fall back to "warning".
        level = (
            _invocation_override_level(result, invocations, extensions)
            or _str(_dict_at(rule, "defaultConfiguration").get("level"))
            or "warning"
        )
    if level == "error":
        return "high"
    if level == "warning":
        return "medium"
    if level in {"note", "none"}:
        return "low"
    return "medium"


def _invocation_override_level(
    result: Mapping[str, Any],
    invocations: list[Mapping[str, Any]],
    extensions: list[Mapping[str, Any]],
) -> str | None:
    # SARIF 2.1.0: a result selects its invocation via provenance.invocationIndex; that
    # invocation's ruleConfigurationOverrides may carry a configuration.level for the
    # result's rule (§3.20.5). Match the override descriptor to the result's rule by id
    # or index, mirroring _resolve_rule's reference handling.
    inv_index = _dict_at(result, "provenance").get("invocationIndex")
    if not isinstance(inv_index, int):
        # SARIF 2.1.0: invocationIndex defaults to -1 (no associated invocation). But when
        # the run has exactly one invocation, a result without explicit provenance was
        # produced by it, so its ruleConfigurationOverrides apply.
        if len(invocations) == 1:
            inv_index = 0
        else:
            return None
    if not (0 <= inv_index < len(invocations)):
        return None
    for override in _list_of_dicts(invocations[inv_index].get("ruleConfigurationOverrides")):
        if _override_matches_rule(_dict_at(override, "descriptor"), result, extensions):
            level = _str(_dict_at(override, "configuration").get("level"))
            if level is not None:
                return level
    return None


def _override_matches_rule(
    descriptor: Mapping[str, Any],
    result: Mapping[str, Any],
    extensions: list[Mapping[str, Any]],
) -> bool:
    # SARIF 2.1.0 §3.52.3: the tool components must agree (absent → driver). An
    # extension-scoped override must not apply to a driver-scoped result with the same id.
    if _component_key(_dict_at(descriptor, "toolComponent"), extensions) != _component_key(
        _dict_at(result, "rule", "toolComponent"), extensions
    ):
        return False
    desc_id = _str(descriptor.get("id"))
    result_rule_id = _str(result.get("ruleId")) or _str(_dict_at(result, "rule").get("id"))
    if desc_id is not None and desc_id == result_rule_id:
        return True
    desc_index = descriptor.get("index")
    result_index = result.get("ruleIndex")
    if not isinstance(result_index, int):
        result_index = _dict_at(result, "rule").get("index")
    return isinstance(desc_index, int) and desc_index == result_index


def _component_key(tc_ref: Mapping[str, Any], extensions: list[Mapping[str, Any]]) -> str:
    # Canonical key for the referenced tool component so references made by index vs.
    # name/guid compare equal; an absent or unresolved reference is the driver.
    tc_index = tc_ref.get("index")
    if isinstance(tc_index, int) and 0 <= tc_index < len(extensions):
        return f"ext:{tc_index}"
    name = _str(tc_ref.get("name"))
    guid = _str(tc_ref.get("guid"))
    for index, ext in enumerate(extensions):
        if name and _str(ext.get("name")) == name:
            return f"ext:{index}"
        if guid and _str(ext.get("guid")) == guid:
            return f"ext:{index}"
    return "driver"


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


def _first_location(
    result: Mapping[str, Any],
    artifacts: list[Mapping[str, Any]],
) -> dict[str, Any]:
    for location in _list_of_dicts(result.get("locations")):
        physical = _dict_at(location, "physicalLocation")
        artifact = _dict_at(physical, "artifactLocation")
        region = _dict_at(physical, "region")
        out: dict[str, Any] = {}
        uri = _str(artifact.get("uri"))
        if uri is None:
            # SARIF 2.1.0: artifactLocation.index resolves into run.artifacts[index].location.uri
            art_index = artifact.get("index")
            if isinstance(art_index, int) and 0 <= art_index < len(artifacts):
                uri = _str(_dict_at(artifacts[art_index], "location").get("uri"))
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


def _result_message_text(message: object, rule: Mapping[str, Any]) -> str | None:
    # A literal text message is used directly; otherwise SARIF 2.1.0 §3.11.6 allows
    # message.id (+ arguments) resolved against the rule's messageStrings, so id-based or
    # localized scanner messages still produce evidence instead of collapsing to the title.
    literal = _message_text(message)
    if literal is not None:
        return literal
    if not isinstance(message, Mapping):
        return None
    msg_id = _str(message.get("id"))
    if msg_id is None:
        return None
    template = _str(_dict_at(rule, "messageStrings", msg_id).get("text"))
    if template is None:
        return None
    arguments = message.get("arguments")
    return _substitute_arguments(template, arguments) if isinstance(arguments, list) else template


def _substitute_arguments(template: str, arguments: list[object]) -> str:
    # SARIF 2.1.0 §3.11.5: "{n}" -> arguments[n]; "{{" and "}}" are literal braces.
    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        index = int(match.group(1))
        return str(arguments[index]) if 0 <= index < len(arguments) else token

    return re.sub(r"\{\{|\}\}|\{(\d+)\}", _replace, template)


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
