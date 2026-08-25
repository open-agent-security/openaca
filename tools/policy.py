"""Policy document validation and endpoint-independent admission evaluation."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from tools.component_ref import ComponentRef
from tools.graph import Graph
from tools.lint import UPSTREAM_ID_RE
from tools.overlays import id_set
from tools.posture import KNOWN_RULE_IDS
from tools.severity import derive_severity_label

AdmissionDefault = Literal["allowed", "blocked"]
ComponentCategory = Literal["mcps", "plugins", "skills"]
DecisionCategory = ComponentCategory | Literal["other"]

_SEVERITY_ORDER = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


class PolicyValidationError(ValueError):
    """A policy document does not match the V1 policy shape."""


class PolicyEvaluationError(ValueError):
    """A configured risk gate could not be evaluated against available evidence."""


class _PolicyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_mapping(
    loader: _PolicyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key ({key!r})",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_PolicyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class McpTarget:
    command: tuple[str, ...] | None = None
    url: str | None = None

    def key(self) -> tuple[str, tuple[str, ...] | str]:
        if self.command is not None:
            return ("command", self.command)
        assert self.url is not None
        return ("url", self.url)


@dataclass(frozen=True)
class PluginTarget:
    plugin: str | None = None
    marketplace: str | None = None

    def key(self) -> tuple[str, str]:
        if self.plugin is not None:
            return ("plugin", self.plugin)
        assert self.marketplace is not None
        return ("marketplace", _normalize_marketplace_url(self.marketplace))


@dataclass(frozen=True)
class AdmissionRule:
    default: AdmissionDefault
    allowed: tuple[McpTarget | PluginTarget, ...] = ()
    blocked: tuple[McpTarget | PluginTarget, ...] = ()


@dataclass(frozen=True)
class VulnerabilityGate:
    severity_at_least: str | None = None
    ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RiskGates:
    vulnerabilities: VulnerabilityGate | None = None
    posture_rule_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Policy:
    mcps: AdmissionRule
    plugins: AdmissionRule
    skills_default: AdmissionDefault
    risk_gates: RiskGates


@dataclass(frozen=True)
class EndpointComponent:
    ref: ComponentRef
    graph: Graph | None = None


@dataclass(frozen=True)
class PolicySubject:
    """A component evaluated once for policy purposes."""

    ref: ComponentRef
    category: DecisionCategory


@dataclass(frozen=True)
class Decision:
    ref: ComponentRef
    category: DecisionCategory
    subject: PolicySubject
    controlled_by_plugin: bool
    blocked: bool
    reasons: tuple[str, ...]
    risk_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ResolvedComponent:
    component: EndpointComponent
    category: DecisionCategory
    subject_index: int
    controlled_by_plugin: bool


def load(path: Path) -> Policy:
    """Load and validate a YAML or JSON policy document."""
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=_PolicyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyValidationError(str(exc)) from exc
    return parse(document)


def parse(document: object) -> Policy:
    """Validate a decoded policy document and return its typed form."""
    root = _mapping(document, "policy")
    _require_exact_keys(root, {"version", "admission", "risk_gates"}, "policy")
    if type(root.get("version")) is not int or root["version"] != 1:
        raise PolicyValidationError("policy.version must be 1")

    admission = _mapping(root.get("admission"), "policy.admission")
    _require_exact_keys(admission, {"mcps", "plugins", "skills"}, "policy.admission")
    return Policy(
        mcps=_parse_admission_rule(admission.get("mcps"), "policy.admission.mcps", McpTarget),
        plugins=_parse_admission_rule(
            admission.get("plugins"), "policy.admission.plugins", PluginTarget
        ),
        skills_default=_parse_skills(admission.get("skills")),
        risk_gates=_parse_risk_gates(root.get("risk_gates")),
    )


def canonical_json(policy: Policy) -> str:
    """Return a deterministic JSON representation suitable for hashing/reporting."""
    return json.dumps(to_document(policy), sort_keys=True, separators=(",", ":"))


def to_document(policy: Policy) -> dict[str, Any]:
    """Return the public policy document representation."""
    document: dict[str, Any] = {
        "version": 1,
        "admission": {
            "mcps": _admission_to_document(policy.mcps),
            "plugins": _admission_to_document(policy.plugins),
            "skills": {"default": policy.skills_default},
        },
    }
    risk: dict[str, Any] = {}
    if policy.risk_gates.vulnerabilities is not None:
        vulnerabilities: dict[str, Any] = {}
        gate = policy.risk_gates.vulnerabilities
        if gate.severity_at_least is not None:
            vulnerabilities["severity_at_least"] = gate.severity_at_least.lower()
        if gate.ids:
            vulnerabilities["ids"] = sorted(gate.ids)
        risk["vulnerabilities"] = vulnerabilities
    if policy.risk_gates.posture_rule_ids:
        risk["posture"] = {"rules": sorted(policy.risk_gates.posture_rule_ids)}
    if risk:
        document["risk_gates"] = risk
    return document


def evaluate_admission(policy: Policy, components: list[EndpointComponent]) -> list[Decision]:
    """Evaluate admission only; risk findings are applied by ``apply_risk_gates``."""
    subjects, resolved = _resolve_policy_subjects(components)
    decisions = [_admission_decision(policy, subject) for subject in subjects]
    return _component_decisions(resolved, decisions)


def apply_risk_gates(
    policy: Policy,
    components: list[EndpointComponent],
    *,
    advisories: list[dict[str, Any]],
    advisory_matches: list[tuple[ComponentRef, str]],
    posture_matches: list[tuple[ComponentRef, str]],
) -> list[Decision]:
    """Evaluate admission and add blocks for matching fresh risk evidence.

    Every observation resolves to one host-controllable policy subject before
    admission and risk evaluation. Plugin contents share their owning plugin's
    subject, so a risk block has one target and one decision.
    """
    subjects, resolved = _resolve_policy_subjects(components)
    decisions = [_admission_decision(policy, subject) for subject in subjects]
    advisory_by_id = {
        record.get("id"): record for record in advisories if isinstance(record.get("id"), str)
    }

    for ref, advisory_id in advisory_matches:
        advisory = advisory_by_id.get(advisory_id)
        if advisory is None or not _matches_vulnerability_gate(
            policy.risk_gates.vulnerabilities, advisory
        ):
            continue
        subject_index = _subject_index_for_ref(ref, resolved)
        if subject_index is not None:
            _block_subject(decisions, subject_index, f"vulnerability {advisory_id}")

    for ref, rule_id in posture_matches:
        if rule_id not in policy.risk_gates.posture_rule_ids:
            continue
        subject_index = _subject_index_for_ref(ref, resolved)
        if subject_index is not None:
            _block_subject(decisions, subject_index, f"posture {rule_id}")

    return _component_decisions(resolved, decisions)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyValidationError(f"{label} must be an object")
    return value


def _require_exact_keys(value: dict[str, Any], permitted: set[str], label: str) -> None:
    unknown = sorted(set(value) - permitted)
    if unknown:
        raise PolicyValidationError(f"{label} has unsupported field(s): {', '.join(unknown)}")


def _parse_admission_rule(
    value: object,
    label: str,
    target_type: type[McpTarget] | type[PluginTarget],
) -> AdmissionRule:
    rule = _mapping(value, label)
    _require_exact_keys(rule, {"default", "allowed", "blocked"}, label)
    default = rule.get("default")
    if default not in {"allowed", "blocked"}:
        raise PolicyValidationError(f"{label}.default must be allowed or blocked")
    allowed = _parse_targets(rule.get("allowed", []), f"{label}.allowed", target_type)
    blocked = _parse_targets(rule.get("blocked", []), f"{label}.blocked", target_type)
    overlap = {target.key() for target in allowed} & {target.key() for target in blocked}
    if overlap:
        raise PolicyValidationError(f"{label} contains a target in both allowed and blocked")
    return AdmissionRule(default=default, allowed=tuple(allowed), blocked=tuple(blocked))


def _parse_targets(
    value: object, label: str, target_type: type[McpTarget] | type[PluginTarget]
) -> list[McpTarget | PluginTarget]:
    if not isinstance(value, list):
        raise PolicyValidationError(f"{label} must be a list")
    targets: list[McpTarget | PluginTarget] = []
    for index, raw_target in enumerate(value):
        target_label = f"{label}[{index}]"
        target = _mapping(raw_target, target_label)
        if target_type is McpTarget:
            _require_exact_keys(target, {"command", "url"}, target_label)
            command, url = target.get("command"), target.get("url")
            if (command is None) == (url is None):
                raise PolicyValidationError(
                    f"{target_label} must contain exactly one of command or url"
                )
            if command is not None:
                if (
                    not isinstance(command, list)
                    or not command
                    or not all(isinstance(part, str) and part for part in command)
                ):
                    raise PolicyValidationError(
                        f"{target_label}.command must be a non-empty string array"
                    )
                targets.append(McpTarget(command=tuple(command)))
            elif not isinstance(url, str) or not url:
                raise PolicyValidationError(f"{target_label}.url must be a non-empty string")
            else:
                targets.append(McpTarget(url=url))
        else:
            _require_exact_keys(target, {"plugin", "marketplace"}, target_label)
            plugin, marketplace = target.get("plugin"), target.get("marketplace")
            if (plugin is None) == (marketplace is None):
                raise PolicyValidationError(
                    f"{target_label} must contain exactly one of plugin or marketplace"
                )
            if plugin is not None:
                if not isinstance(plugin, str) or not plugin:
                    raise PolicyValidationError(
                        f"{target_label}.plugin must be a non-empty plugin@marketplace string"
                    )
                # Matches the `<plugin>@<marketplace>` key format
                # `installed_plugins.json` itself uses (see
                # `tools.parsers.claude_install._split_plugin_key`): split on
                # the last "@" so a scoped plugin name (`@scope/plugin`) stays
                # intact while `foo@`, `@marketplace`, and other malformed
                # inputs are rejected instead of silently admitted.
                name, _, marketplace_part = plugin.rpartition("@")
                if not name or not marketplace_part:
                    raise PolicyValidationError(
                        f"{target_label}.plugin must be a non-empty plugin@marketplace string"
                    )
                targets.append(PluginTarget(plugin=plugin))
            elif not isinstance(marketplace, str) or not marketplace:
                raise PolicyValidationError(
                    f"{target_label}.marketplace must be a non-empty string"
                )
            else:
                targets.append(PluginTarget(marketplace=marketplace))
    if len({target.key() for target in targets}) != len(targets):
        raise PolicyValidationError(f"{label} contains a duplicate target")
    return targets


def _parse_skills(value: object) -> AdmissionDefault:
    rule = _mapping(value, "policy.admission.skills")
    _require_exact_keys(rule, {"default"}, "policy.admission.skills")
    default = rule.get("default")
    if default not in {"allowed", "blocked"}:
        raise PolicyValidationError("policy.admission.skills.default must be allowed or blocked")
    return default


def _parse_risk_gates(value: object) -> RiskGates:
    if value is None:
        return RiskGates()
    risk = _mapping(value, "policy.risk_gates")
    _require_exact_keys(risk, {"vulnerabilities", "posture"}, "policy.risk_gates")
    vulnerabilities = None
    if "vulnerabilities" in risk:
        raw_vulnerabilities = _mapping(risk["vulnerabilities"], "policy.risk_gates.vulnerabilities")
        _require_exact_keys(
            raw_vulnerabilities, {"severity_at_least", "ids"}, "policy.risk_gates.vulnerabilities"
        )
        severity = raw_vulnerabilities.get("severity_at_least")
        if severity is not None:
            if not isinstance(severity, str) or severity.upper() not in _SEVERITY_ORDER:
                raise PolicyValidationError(
                    "policy.risk_gates.vulnerabilities.severity_at_least must be a severity"
                )
            severity = severity.upper()
        raw_ids = raw_vulnerabilities.get("ids", [])
        if not isinstance(raw_ids, list) or not all(
            isinstance(item, str) and item for item in raw_ids
        ):
            raise PolicyValidationError(
                "policy.risk_gates.vulnerabilities.ids must be a string list"
            )
        malformed_ids = [item for item in raw_ids if not UPSTREAM_ID_RE.match(item)]
        if malformed_ids:
            raise PolicyValidationError(
                f"policy.risk_gates.vulnerabilities.ids contains malformed id(s): "
                f"{', '.join(malformed_ids)} (expected GHSA-*, CVE-*, OSV-*, PYSEC-*, or MAL-*)"
            )
        if severity is None and not raw_ids:
            raise PolicyValidationError(
                "policy.risk_gates.vulnerabilities must configure a condition"
            )
        vulnerabilities = VulnerabilityGate(severity_at_least=severity, ids=frozenset(raw_ids))

    posture_rule_ids: frozenset[str] = frozenset()
    if "posture" in risk:
        posture = _mapping(risk["posture"], "policy.risk_gates.posture")
        _require_exact_keys(posture, {"rules"}, "policy.risk_gates.posture")
        rules = posture.get("rules")
        if (
            not isinstance(rules, list)
            or not rules
            or not all(isinstance(rule, str) and rule for rule in rules)
        ):
            raise PolicyValidationError(
                "policy.risk_gates.posture.rules must be a non-empty string list"
            )
        unknown = set(rules) - KNOWN_RULE_IDS
        if unknown:
            raise PolicyValidationError(
                f"policy.risk_gates.posture.rules contains unknown rule id(s): "
                f"{', '.join(sorted(unknown))}"
            )
        posture_rule_ids = frozenset(rules)
    return RiskGates(vulnerabilities=vulnerabilities, posture_rule_ids=posture_rule_ids)


def _admission_to_document(rule: AdmissionRule) -> dict[str, Any]:
    result: dict[str, Any] = {"default": rule.default}
    if rule.allowed:
        result["allowed"] = [_target_to_document(target) for target in rule.allowed]
    if rule.blocked:
        result["blocked"] = [_target_to_document(target) for target in rule.blocked]
    return result


def _target_to_document(target: McpTarget | PluginTarget) -> dict[str, Any]:
    if isinstance(target, McpTarget):
        return {"command": list(target.command)} if target.command else {"url": target.url}
    return {"plugin": target.plugin} if target.plugin else {"marketplace": target.marketplace}


def _category(ref: ComponentRef) -> ComponentCategory | None:
    component_type = ref.extra.get("component_type") if isinstance(ref.extra, dict) else None
    if component_type == "mcp_server":
        return "mcps"
    if component_type == "plugin":
        return "plugins"
    if component_type == "skill":
        return "skills"
    return None


def _admission_decision(policy: Policy, subject: PolicySubject) -> Decision:
    category = subject.category
    if category == "other":
        return Decision(
            ref=subject.ref,
            category=category,
            subject=subject,
            controlled_by_plugin=False,
            blocked=False,
            reasons=("outside policy scope",),
        )
    if category == "skills":
        blocked = policy.skills_default == "blocked"
        return Decision(
            ref=subject.ref,
            category=category,
            subject=subject,
            controlled_by_plugin=False,
            blocked=blocked,
            reasons=(f"skills default: {policy.skills_default}",),
        )
    rule = policy.mcps if category == "mcps" else policy.plugins
    matches = _matching_targets(subject.ref, rule)
    blocked = any(state == "blocked" for state in matches) or (
        not matches and rule.default == "blocked"
    )
    reasons = tuple(f"admission {state}" for state in matches) or (
        f"{category} default: {rule.default}",
    )
    return Decision(
        ref=subject.ref,
        category=category,
        subject=subject,
        controlled_by_plugin=False,
        blocked=blocked,
        reasons=reasons,
    )


def _matching_targets(ref: ComponentRef, rule: AdmissionRule) -> list[AdmissionDefault]:
    result: list[AdmissionDefault] = []
    if any(_target_matches(ref, target) for target in rule.allowed):
        result.append("allowed")
    if any(_target_matches(ref, target) for target in rule.blocked):
        result.append("blocked")
    return result


def _target_matches(ref: ComponentRef, target: McpTarget | PluginTarget) -> bool:
    extra = ref.extra if isinstance(ref.extra, dict) else {}
    if isinstance(target, McpTarget):
        if target.command is not None:
            command = extra.get("mcp_command")
            if command is None:
                install_source = extra.get("install_source")
                if isinstance(install_source, str):
                    try:
                        command = shlex.split(install_source)
                    except ValueError:
                        command = None
            return isinstance(command, list) and tuple(command) == target.command
        return extra.get("url") == target.url
    if target.plugin is not None:
        marketplace = extra.get("marketplace")
        return (
            isinstance(ref.name, str)
            and isinstance(marketplace, str)
            and f"{ref.name}@{marketplace}" == target.plugin
        )
    source = extra.get("marketplace_source")
    return (
        isinstance(source, str)
        and isinstance(target.marketplace, str)
        and _normalize_marketplace_url(source) == _normalize_marketplace_url(target.marketplace)
    )


def _normalize_marketplace_url(value: str) -> str:
    """Strip a trailing ``.git`` so a policy target matches a discovered source.

    ``_marketplace_source`` (``tools.parsers.claude_install``) always appends
    ``.git`` for a GitHub-sourced marketplace, but a policy author writing the
    target by hand has no reason to include it.
    """
    return value[:-4] if value.endswith(".git") else value


def _matches_vulnerability_gate(gate: VulnerabilityGate | None, advisory: dict[str, Any]) -> bool:
    if gate is None:
        return False
    if gate.ids & id_set(advisory):
        return True
    if gate.severity_at_least is None:
        return False
    severity = derive_severity_label(advisory)
    if severity == "UNKNOWN":
        # Neither an upstream `database_specific.severity` label nor a
        # parseable CVSS vector is available. Treating this as "below
        # threshold" would let a component pass unblocked on missing data,
        # not evidence of low risk (mirrors the non-queryable-component
        # fail-closed rule: "not evidence that it is clean").
        raise PolicyEvaluationError(
            f"cannot evaluate severity_at_least gate: {advisory.get('id')} has no upstream "
            "severity label or parseable CVSS vector"
        )
    return _SEVERITY_ORDER[severity] >= _SEVERITY_ORDER[gate.severity_at_least]


def _resolve_policy_subjects(
    components: list[EndpointComponent],
) -> tuple[list[PolicySubject], list[_ResolvedComponent]]:
    subjects: list[PolicySubject] = []
    node_subjects: dict[tuple[int, str], int] = {}
    contexts: list[
        tuple[EndpointComponent, DecisionCategory, int | None, str | None, str | None]
    ] = []
    graphs: list[Graph] = []

    for component in components:
        category = _category(component.ref) or "other"
        graph_index: int | None = None
        node_key: str | None = None
        owner_key: str | None = None
        if component.graph is not None:
            for index, graph in enumerate(graphs):
                if graph is component.graph:
                    graph_index = index
                    break
            else:
                graph_index = len(graphs)
                graphs.append(component.graph)
            node = component.graph.node_for_ref(component.ref)
            if node is not None:
                node_key = node.key
                owner = component.graph.nearest_plugin_ancestor(node)
                owner_key = owner.key if owner is not None else None
        contexts.append((component, category, graph_index, node_key, owner_key))

    for component, category, graph_index, node_key, owner_key in contexts:
        if category != "plugins" and owner_key is not None:
            continue
        subject_index = len(subjects)
        subjects.append(PolicySubject(ref=component.ref, category=category))
        if graph_index is not None and node_key is not None:
            node_subjects[(graph_index, node_key)] = subject_index

    resolved: list[_ResolvedComponent] = []
    for component, category, graph_index, node_key, owner_key in contexts:
        owner_index = (
            node_subjects.get((graph_index, owner_key))
            if category != "plugins" and graph_index is not None and owner_key is not None
            else None
        )
        if owner_index is not None:
            resolved.append(
                _ResolvedComponent(component, category, owner_index, controlled_by_plugin=True)
            )
            continue
        if graph_index is not None and node_key is not None:
            subject_index = node_subjects.get((graph_index, node_key))
            if subject_index is None:
                subject_index = len(subjects)
                subjects.append(PolicySubject(ref=component.ref, category=category))
                node_subjects[(graph_index, node_key)] = subject_index
        else:
            subject_index = next(
                index for index, subject in enumerate(subjects) if subject.ref is component.ref
            )
        resolved.append(_ResolvedComponent(component, category, subject_index, False))
    return subjects, resolved


def _subject_index_for_ref(ref: ComponentRef, resolved: list[_ResolvedComponent]) -> int | None:
    bom_ref = ref.extra.get("bom_ref")
    for component in resolved:
        candidate = component.component.ref
        if candidate is ref:
            return component.subject_index
        if isinstance(bom_ref, str) and bom_ref and candidate.extra.get("bom_ref") == bom_ref:
            return component.subject_index
    return None


def _component_decisions(
    resolved: list[_ResolvedComponent], subject_decisions: list[Decision]
) -> list[Decision]:
    result: list[Decision] = []
    for component in resolved:
        subject_decision = subject_decisions[component.subject_index]
        reasons = subject_decision.reasons
        if component.controlled_by_plugin:
            reasons = tuple(f"owning plugin: {reason}" for reason in reasons)
        result.append(
            Decision(
                ref=component.component.ref,
                category=component.category,
                subject=subject_decision.subject,
                controlled_by_plugin=component.controlled_by_plugin,
                blocked=subject_decision.blocked,
                reasons=reasons,
                risk_reasons=subject_decision.risk_reasons,
            )
        )
    return result


def _block_subject(decisions: list[Decision], subject_index: int, reason: str) -> None:
    current = decisions[subject_index]
    decisions[subject_index] = Decision(
        ref=current.ref,
        category=current.category,
        subject=current.subject,
        controlled_by_plugin=False,
        blocked=True,
        reasons=(*current.reasons, reason),
        risk_reasons=(*current.risk_reasons, reason),
    )
