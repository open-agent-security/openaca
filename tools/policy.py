"""Policy document validation and endpoint-independent admission evaluation."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from tools.component_ref import ComponentRef
from tools.graph import Graph, ref_occurrence_key
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
        return ("marketplace", self.marketplace)


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
class Decision:
    ref: ComponentRef
    category: DecisionCategory
    blocked: bool
    reasons: tuple[str, ...]


def load(path: Path) -> Policy:
    """Load and validate a YAML or JSON policy document."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyValidationError(str(exc)) from exc
    return parse(document)


def parse(document: object) -> Policy:
    """Validate a decoded policy document and return its typed form."""
    root = _mapping(document, "policy")
    _require_exact_keys(root, {"version", "admission", "risk_gates"}, "policy")
    if root.get("version") != 1:
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
    return [_admission_decision(policy, component.ref, component.graph) for component in components]


def apply_risk_gates(
    policy: Policy,
    components: list[EndpointComponent],
    *,
    advisories: list[dict[str, Any]],
    advisory_matches: list[tuple[ComponentRef, str]],
    posture_matches: list[tuple[ComponentRef, str]],
) -> list[Decision]:
    """Evaluate admission and add blocks for matching fresh risk evidence.

    ``advisory_matches`` and ``posture_matches`` are deliberately occurrence
    based. A plugin child resolves to its owning plugin before the restriction
    is added, preserving the plugin trust boundary.
    """
    decisions = {id(c.ref): _admission_decision(policy, c.ref, c.graph) for c in components}
    component_by_ref = {id(c.ref): c for c in components}
    # `_restriction_target` resolves a finding to a graph node's own `ref`,
    # which is a distinct object from the `dataclasses.replace` copy scan
    # projects into `components` (see `_refs_from_graph`). This index maps
    # that occurrence back to the copy actually keyed in `decisions`.
    component_by_occurrence = {ref_occurrence_key(c.ref): c for c in components}
    advisory_by_id = {
        record.get("id"): record for record in advisories if isinstance(record.get("id"), str)
    }

    for ref, advisory_id in advisory_matches:
        advisory = advisory_by_id.get(advisory_id)
        if advisory is None or not _matches_vulnerability_gate(
            policy.risk_gates.vulnerabilities, advisory
        ):
            continue
        target = _restriction_target(ref, component_by_ref.get(id(ref)), component_by_occurrence)
        _block_decision(decisions, target, f"vulnerability {advisory_id}")

    for ref, rule_id in posture_matches:
        if rule_id not in policy.risk_gates.posture_rule_ids:
            continue
        target = _restriction_target(ref, component_by_ref.get(id(ref)), component_by_occurrence)
        _block_decision(decisions, target, f"posture {rule_id}")

    return [decisions[id(c.ref)] for c in components]


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


def _admission_decision(policy: Policy, ref: ComponentRef, graph: Graph | None = None) -> Decision:
    category = _category(ref)
    if category is None:
        return Decision(ref=ref, category="other", blocked=False, reasons=("outside policy scope",))
    # Spec: "A plugin remains the trust boundary for its bundled MCP servers,
    # skills, and other contents." A component contained by a plugin inherits
    # the plugin's own admission decision outright rather than being evaluated
    # independently against `mcps`/`skills` targets or defaults.
    if category != "plugins":
        plugin_ref = _owning_plugin_ref(ref, graph)
        if plugin_ref is not None:
            plugin_decision = _admission_decision(policy, plugin_ref, graph)
            return Decision(
                ref=ref,
                category=category,
                blocked=plugin_decision.blocked,
                reasons=tuple(f"owning plugin: {reason}" for reason in plugin_decision.reasons),
            )
    if category == "skills":
        blocked = policy.skills_default == "blocked"
        return Decision(
            ref=ref,
            category=category,
            blocked=blocked,
            reasons=(f"skills default: {policy.skills_default}",),
        )
    rule = policy.mcps if category == "mcps" else policy.plugins
    matches = _matching_targets(ref, rule)
    blocked = any(state == "blocked" for state in matches) or (
        not matches and rule.default == "blocked"
    )
    reasons = tuple(f"admission {state}" for state in matches) or (
        f"{category} default: {rule.default}",
    )
    return Decision(ref=ref, category=category, blocked=blocked, reasons=reasons)


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


def _owning_plugin_ref(ref: ComponentRef, graph: Graph | None) -> ComponentRef | None:
    if graph is None:
        return None
    node = graph.node_for_ref(ref)
    if node is None:
        return None
    plugin = graph.nearest_plugin_ancestor(node)
    return plugin.ref if plugin is not None else None


def _restriction_target(
    ref: ComponentRef,
    component: EndpointComponent | None,
    component_by_occurrence: dict[tuple[str, ...], EndpointComponent],
) -> ComponentRef:
    graph = component.graph if component is not None else None
    plugin_ref = _owning_plugin_ref(ref, graph)
    if plugin_ref is None:
        return ref
    owner = component_by_occurrence.get(ref_occurrence_key(plugin_ref))
    return owner.ref if owner is not None else ref


def _block_decision(decisions: dict[int, Decision], ref: ComponentRef, reason: str) -> None:
    current = decisions.get(id(ref))
    if current is None:
        return
    decisions[id(ref)] = Decision(
        ref=current.ref,
        category=current.category,
        blocked=True,
        reasons=(*current.reasons, reason),
    )
