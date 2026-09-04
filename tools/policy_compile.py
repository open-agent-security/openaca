"""Compile a policy document into one endpoint's host-managed settings artifact.

Below the command layer, so `openaca.core` can publish `compile_endpoint_policy`
and `render_policy_report` without the facade importing `tools/policy_cli.py`
(and transitively `tools/bom_cli.py`). `openaca policy compile` imports the same
two names from here and behaves identically.

This module raises no `click` exception. Every input and evaluation failure it
raises deliberately is a `PolicyValidationError` or a `PolicyEvaluationError`;
the command translates both into a `click.ClickException`, which exits 1 and
prints `Error: <message>`, exactly as it did when these bodies lived there.

The one failure those two do not cover is the artifact write: `_write_artifact`
translates none of `mkdir` / the temp write / `Path.replace`'s `OSError`s, so an
unwritable `--output` directory escapes uncaught. That is deliberate — wrapping
it in a domain error the command catches would turn today's traceback into
`Error: ...` with exit code 1, a command-line behaviour change.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.agent_kinds import (
    AgentInstance,
    DiscoveryContext,
    build_agent_graph,
    discover_agents,
    kind_for,
)
from tools.atomic_write import write_new_temp_file
from tools.component_ref import ComponentRef
from tools.graph import Graph
from tools.matcher import match
from tools.osv_federation import is_queryable
from tools.parsers import settings_layers
from tools.policy import (
    Decision,
    EndpointComponent,
    Policy,
    PolicyEvaluationError,
    PolicyValidationError,
    apply_risk_gates,
    canonical_json,
)
from tools.policy_claude import compile_policy
from tools.posture import run_posture_rules
from tools.scan import (
    _agent_scan_prep,
    _filter_agent_scope_refs,
    _load_osv_with_overlays,
    _refs_from_graph,
)

__all__ = ["compile_endpoint_policy", "render_policy_report"]

_OPENACA_FILENAME = "50-openaca-policy.json"


def compile_endpoint_policy(
    policy: Policy,
    *,
    target: Path,
    project: Path | None,
    output: Path | None,
    managed_settings_dir: Path | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Evaluate one endpoint and atomically write its policy artifact when requested."""
    if output is None and not dry_run:
        # A `PolicyValidationError` rather than a `click.UsageError`: the
        # argument combination is invalid before anything is evaluated, and a
        # module below the command layer must not raise the command layer's
        # exception type. The command keeps its own identical check ahead of
        # the call, so a CLI user still gets the usage error and exit code 2.
        raise PolicyValidationError("--output is required unless --dry-run is set")
    components, advisory_matches, advisories, posture_matches, unmapped_posture = (
        _evaluate_endpoint(policy, target, project)
    )
    decisions = apply_risk_gates(
        policy,
        components,
        advisories=advisories,
        advisory_matches=advisory_matches,
        posture_matches=posture_matches,
    )
    # `--host` is a `Choice(["claude"])` on the command — a gate on the input
    # rather than a key that selects anything, since the Claude compiler is
    # called unconditionally here. That is why this function takes no `host`
    # argument. The day a second host compiler lands, `--host` becomes a
    # dispatch key and this function needs the argument: without it a
    # programmatic caller silently gets Claude's format whatever it asked
    # for. Whoever adds the compiler owns that change.
    rendered = compile_policy(policy, decisions)
    directory = managed_settings_dir or settings_layers.default_managed_dir()
    collisions = _managed_key_collisions(directory, set(rendered.settings))
    if collisions:
        labels = ", ".join(f"{key} in {path}" for key, path in collisions)
        raise PolicyEvaluationError(f"managed settings key collision: {labels}")

    # Endpoint-level posture findings (no discovered component to attribute the
    # restriction to, e.g. `openaca-posture-api-endpoint-override`) cannot be
    # mapped to a `Decision` at all; per spec ("Map the result to a host-native
    # target. If no exact target exists, preserve the finding and report it as
    # not enforceable") they must still surface, not silently disappear.
    limitations = (*rendered.limitations, *unmapped_posture)
    artifact_json = json.dumps(rendered.settings, indent=2, sort_keys=True) + "\n"
    artifact_digest = hashlib.sha256(artifact_json.encode()).hexdigest()
    report = _report(
        policy,
        decisions,
        rendered.settings,
        artifact_digest,
        limitations,
        output,
        directory / "managed-settings.d" / _OPENACA_FILENAME,
        dry_run,
    )
    if not dry_run:
        assert output is not None
        _write_artifact(output, artifact_json)
    return report


def _evaluate_endpoint(
    policy: Policy, target: Path, project: Path | None
) -> tuple[
    list[EndpointComponent],
    list[tuple[ComponentRef, str]],
    list[dict[str, Any]],
    list[tuple[ComponentRef, str]],
    list[str],
]:
    # `kind_id` is pinned, not left open: the policy compiler targets a named
    # root via `--target` and compiles Claude-managed settings (`--host claude`
    # is a required Choice). Open discovery would additionally return every
    # other registered kind resolved at ITS own root — Cursor ignores
    # `config_dir` entirely (ADR-0054) — so a compile aimed at one directory
    # would silently pull in components from the invoking user's home.
    agents = discover_agents(
        DiscoveryContext(
            source="installed",
            config_dir=target,
            project_root=project,
            kind_id="claude-code",
        )
    )
    if not agents:
        raise PolicyEvaluationError(f"no installed agent found at {target}")

    components: list[EndpointComponent] = []
    findings: list[tuple[ComponentRef, str]] = []
    posture: list[tuple[ComponentRef, str]] = []
    unmapped_posture: list[str] = []
    refs_by_agent: list[tuple[AgentInstance, Graph, list[ComponentRef]]] = []
    graph_warnings: list[str] = []
    for agent in agents:
        graph = build_agent_graph(agent, warnings=graph_warnings)
        refs = _filter_agent_scope_refs(_refs_from_graph(graph))
        components.extend(EndpointComponent(ref, graph) for ref in refs)
        refs_by_agent.append((agent, graph, refs))
    if graph_warnings:
        # A dropped or malformed inventory entry means `components` is an
        # incomplete endpoint inventory: admission and risk gates would be
        # evaluated as if the missing component didn't exist, silently
        # implying a complete policy artifact (spec: compilation fails and
        # does not replace a previous artifact when evaluation is incomplete).
        raise PolicyEvaluationError("; ".join(graph_warnings))

    advisories: list[dict[str, Any]] = []
    if policy.risk_gates.vulnerabilities is not None:
        nonqueryable = [
            ref for _agent, _graph, refs in refs_by_agent for ref in refs if not is_queryable(ref)
        ]
        if nonqueryable:
            labels = ", ".join(_component_label(ref) for ref in nonqueryable[:3])
            suffix = "" if len(nonqueryable) <= 3 else ", ..."
            raise PolicyEvaluationError(
                f"vulnerability gates cannot evaluate non-queryable component(s): {labels}{suffix}"
            )
        all_refs = [ref for _agent, _graph, refs in refs_by_agent for ref in refs]
        advisories, warnings, _overlay_count, _aliases = _load_osv_with_overlays(all_refs)
        if warnings:
            raise PolicyEvaluationError("; ".join(warnings))
        for agent, graph, refs in refs_by_agent:
            for finding in match(
                refs, advisories, graph=graph, agent_kind=agent.kind_id, agent_id=agent.agent_id
            ):
                findings.append((finding.component, finding.advisory_id))

    if policy.risk_gates.posture_rule_ids:
        for agent, _graph, refs in refs_by_agent:
            prep = _agent_scan_prep(
                agent,
                kind_for(agent.kind_id),
                refs,
                repo_parse_cache={},
            )
            findings_by_ref = {
                str(ref.extra["bom_ref"]): ref
                for ref in refs
                if isinstance(ref.extra.get("bom_ref"), str)
            }
            for finding in run_posture_rules(
                refs,
                prep.manifests,
                prep.settings_manifests,
                allowed_rules=kind_for(agent.kind_id).posture_rules,
                agent_kind=agent.kind_id,
                agent_id=agent.agent_id,
            ):
                if finding.rule_id not in policy.risk_gates.posture_rule_ids:
                    continue
                if finding.bom_ref and finding.bom_ref in findings_by_ref:
                    posture.append((findings_by_ref[finding.bom_ref], finding.rule_id))
                else:
                    unmapped_posture.append(
                        f"{finding.component_label}: posture {finding.rule_id} is not "
                        "enforceable (no discovered component target)"
                    )
    return components, findings, advisories, posture, unmapped_posture


def _managed_key_collisions(directory: Path, generated_keys: set[str]) -> list[tuple[str, Path]]:
    try:
        directory.stat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise PolicyEvaluationError(
            f"cannot read managed settings directory {directory}: {exc}"
        ) from exc
    if not directory.is_dir():
        raise PolicyEvaluationError(f"managed settings path is not a directory: {directory}")
    files = [directory / "managed-settings.json"]
    dropins = directory / "managed-settings.d"
    try:
        dropins.stat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise PolicyEvaluationError(
            f"cannot read managed settings drop-in path {dropins}: {exc}"
        ) from exc
    else:
        if not dropins.is_dir():
            raise PolicyEvaluationError(
                f"managed settings drop-in path is not a directory: {dropins}"
            )
        try:
            files.extend(
                sorted(path for path in dropins.glob("*.json") if path.name != _OPENACA_FILENAME)
            )
        except OSError as exc:
            raise PolicyEvaluationError(
                f"cannot read managed settings drop-in path {dropins}: {exc}"
            ) from exc
    collisions: list[tuple[str, Path]] = []
    for path in files:
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PolicyEvaluationError(f"cannot read managed settings file {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise PolicyEvaluationError(f"managed settings file {path} must contain a JSON object")
        collisions.extend((key, path) for key in generated_keys & set(value))
    return sorted(collisions, key=lambda item: (str(item[1]), item[0]))


def _write_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = write_new_temp_file(path.parent, content)
    temp_path.replace(path)


def _report(
    policy: Policy,
    decisions: list[Decision],
    settings: dict,
    artifact_digest: str,
    limitations: tuple[str, ...],
    output: Path | None,
    intended_path: Path,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "host": "claude",
        "policy_digest": hashlib.sha256(canonical_json(policy).encode()).hexdigest(),
        "scan_time": datetime.now(UTC).isoformat(),
        "artifact": {
            "digest": artifact_digest,
            "intended_path": str(intended_path),
            "output": str(output) if output is not None else None,
            "written": not dry_run,
        },
        "expected_policy": settings,
        "observed_status": "not_verified",
        "decisions": [
            {
                "category": decision.category,
                "component": _component_label(decision.ref),
                "source_manifest": decision.ref.source_manifest,
                "source_locator": decision.ref.source_locator,
                "result": (
                    "blocked"
                    if decision.blocked
                    else "not_applicable"
                    if decision.category == "other"
                    else "allowed"
                ),
                "reasons": list(decision.reasons),
            }
            for decision in decisions
        ],
        "limitations": list(limitations),
    }


def render_policy_report(report: dict[str, Any], output_format: str) -> str:
    """The report as `openaca policy compile` prints it, newline for newline.

    Pure, where `emit_policy_report` prints: a library that writes to stdout is
    a library a caller cannot compose. The `click.echo` of this result stays in
    the command.
    """
    if output_format == "json":
        return json.dumps(report, sort_keys=True)
    lines = [
        "Expected Claude policy:",
        json.dumps(report["expected_policy"], indent=2, sort_keys=True),
        f"\nComponents: {len(report['decisions'])}",
    ]
    lines.extend(
        f"  {decision['result']}: {decision['component']} ({'; '.join(decision['reasons'])})"
        for decision in report["decisions"]
    )
    lines.extend(f"  not enforceable: {limitation}" for limitation in report["limitations"])
    return "\n".join(lines)


def _component_label(ref: ComponentRef) -> str:
    return ref.component_identity or ref.purl or ref.name or "<unidentified>"
