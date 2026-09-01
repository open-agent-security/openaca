"""CLI for validating and compiling endpoint policy artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from tools.agent_kinds import (
    AgentInstance,
    DiscoveryContext,
    build_agent_graph,
    discover_agents,
    kind_for,
)
from tools.bom_cli import _write_new_temp_file
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
    load,
)
from tools.policy_claude import compile_policy
from tools.posture import run_posture_rules
from tools.scan import (
    _agent_scan_prep,
    _filter_agent_scope_refs,
    _load_osv_with_overlays,
    _refs_from_graph,
)

_OPENACA_FILENAME = "50-openaca-policy.json"


@click.group()
def main() -> None:
    """Validate and compile restrictive endpoint policies."""


@main.command()
@click.argument("policy_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def validate(policy_path: Path) -> None:
    """Validate a policy document without scanning an endpoint."""
    try:
        load(policy_path)
    except PolicyValidationError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.argument("policy_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--target", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--project", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--host", type=click.Choice(["claude"]), required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--managed-settings-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True)
@click.option("--format", "output_format", type=click.Choice(["text", "json"]), default="text")
def compile(
    policy_path: Path,
    target: Path,
    project: Path | None,
    host: str,
    output: Path | None,
    managed_settings_dir: Path | None,
    dry_run: bool,
    output_format: str,
) -> None:
    """Scan one endpoint and render its host-managed policy artifact."""
    if output is None and not dry_run:
        raise click.UsageError("--output is required unless --dry-run is set")
    try:
        policy = load(policy_path)
        compilation = compile_endpoint_policy(
            policy,
            target=target,
            project=project,
            output=output,
            managed_settings_dir=managed_settings_dir,
            dry_run=dry_run,
        )
    except (PolicyValidationError, PolicyEvaluationError) as exc:
        raise click.ClickException(str(exc)) from exc

    emit_policy_report(compilation, output_format)
    if project is None:
        click.echo(
            "note: project-local configuration was not scanned; pass --project to include it",
            err=True,
        )


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
        raise click.UsageError("--output is required unless --dry-run is set")
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
    rendered = compile_policy(policy, decisions)
    directory = managed_settings_dir or settings_layers.default_managed_dir()
    collisions = _managed_key_collisions(directory, set(rendered.settings))
    if collisions:
        labels = ", ".join(f"{key} in {path}" for key, path in collisions)
        raise click.ClickException(f"managed settings key collision: {labels}")

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
        raise click.ClickException(f"no installed agent found at {target}")

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
        raise click.ClickException("; ".join(graph_warnings))

    advisories: list[dict[str, Any]] = []
    if policy.risk_gates.vulnerabilities is not None:
        nonqueryable = [
            ref for _agent, _graph, refs in refs_by_agent for ref in refs if not is_queryable(ref)
        ]
        if nonqueryable:
            labels = ", ".join(_component_label(ref) for ref in nonqueryable[:3])
            suffix = "" if len(nonqueryable) <= 3 else ", ..."
            raise click.ClickException(
                f"vulnerability gates cannot evaluate non-queryable component(s): {labels}{suffix}"
            )
        all_refs = [ref for _agent, _graph, refs in refs_by_agent for ref in refs]
        advisories, warnings, _overlay_count, _aliases = _load_osv_with_overlays(all_refs)
        if warnings:
            raise click.ClickException("; ".join(warnings))
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
        raise click.ClickException(
            f"cannot read managed settings directory {directory}: {exc}"
        ) from exc
    if not directory.is_dir():
        raise click.ClickException(f"managed settings path is not a directory: {directory}")
    files = [directory / "managed-settings.json"]
    dropins = directory / "managed-settings.d"
    try:
        dropins.stat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise click.ClickException(
            f"cannot read managed settings drop-in path {dropins}: {exc}"
        ) from exc
    else:
        if not dropins.is_dir():
            raise click.ClickException(
                f"managed settings drop-in path is not a directory: {dropins}"
            )
        try:
            files.extend(
                sorted(path for path in dropins.glob("*.json") if path.name != _OPENACA_FILENAME)
            )
        except OSError as exc:
            raise click.ClickException(
                f"cannot read managed settings drop-in path {dropins}: {exc}"
            ) from exc
    collisions: list[tuple[str, Path]] = []
    for path in files:
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise click.ClickException(f"cannot read managed settings file {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise click.ClickException(f"managed settings file {path} must contain a JSON object")
        collisions.extend((key, path) for key in generated_keys & set(value))
    return sorted(collisions, key=lambda item: (str(item[1]), item[0]))


def _write_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _write_new_temp_file(path.parent, content)
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


def emit_policy_report(report: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        click.echo(json.dumps(report, sort_keys=True))
        return
    click.echo("Expected Claude policy:")
    click.echo(json.dumps(report["expected_policy"], indent=2, sort_keys=True))
    click.echo(f"\nComponents: {len(report['decisions'])}")
    for decision in report["decisions"]:
        click.echo(
            f"  {decision['result']}: {decision['component']} ({'; '.join(decision['reasons'])})"
        )
    for limitation in report["limitations"]:
        click.echo(f"  not enforceable: {limitation}")


def _component_label(ref: ComponentRef) -> str:
    return ref.component_identity or ref.purl or ref.name or "<unidentified>"
