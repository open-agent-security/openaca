"""`openaca bom` commands for emitting Agent BOMs."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import click

from tools.agent_kinds import (
    DiscoveryContext,
    build_agent_graph,
    discover_agents,
    kind_for,
    output_basenames,
    resolve_coverage,
)
from tools.bom import build_agent_bom
from tools.bom_diff import BomDiffComponent, BomDiffResult, ChangedBomDiffComponent, diff_boms
from tools.bom_lint import main as lint_cmd
from tools.parsers import parse_repo_grouped
from tools.scan import _filter_agent_scope_refs, _is_plugin_ref, _refs_from_graph


@click.group()
def main() -> None:
    """Generate OpenACA Agent BOMs."""


main.add_command(lint_cmd, name="lint")


_output_option = click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the CycloneDX Agent BOM JSON to this file instead of stdout.",
)


_output_dir_option = click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write one CycloneDX Agent BOM per agent into this directory.",
)


_BOM_MANIFEST_NAME = ".openaca-bom-manifest.json"


def _is_safe_manifest_name(name: str, output_dir: Path) -> bool:
    """A basename this tool could plausibly have written itself: the exact
    `<basename>.cdx.json` shape this emitter produces, no path separators, no
    `.`/`..`, and the resolved path stays a direct child of `output_dir`.
    Filename shape alone doesn't authenticate that this tool actually wrote
    the entry — a planted manifest can still name a real `*.cdx.json` this
    tool never emitted — but it does rule out a manifest entry directing
    cleanup at a file this emitter could never have produced in the first
    place (`notes.txt`, `.env`, the manifest itself, `../important.cdx.json`,
    an absolute path)."""
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return False
    if name == _BOM_MANIFEST_NAME or not name.endswith(".cdx.json"):
        return False
    if Path(name).name != name:
        return False
    return (output_dir / name).parent == output_dir


def _read_bom_manifest(manifest_path: Path, output_dir: Path) -> set[str]:
    """Basenames this tool wrote into the directory on its last run into this
    path, or empty if there is no manifest (first run, or a directory never
    written by this tool) — in which case nothing is treated as stale."""
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    try:
        names = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    if not isinstance(names, list):
        return set()
    return {
        name for name in names if isinstance(name, str) and _is_safe_manifest_name(name, output_dir)
    }


def _write_new_temp_file(directory: Path, content: str) -> Path:
    """Write `content` to a fresh file in `directory` and return its path.

    A predictable `.tmp` name plus `write_text` still follows a symlink an
    attacker pre-planted at that exact name — `write_text` opens (and follows)
    whatever is already there before this function's own `Path.replace` ever
    runs, so the atomic-replace step arrives too late to help.
    `tempfile.mkstemp` opens with `O_CREAT | O_EXCL` on an unpredictable
    name, so it fails on any existing path entry (including a symlink)
    instead of opening through it."""
    fd, name = tempfile.mkstemp(dir=directory, suffix=".tmp")
    temp_path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _write_bom_manifest(manifest_path: Path, names: list[str]) -> None:
    """Write the ownership manifest via write-temp-then-replace, the same
    pattern `emit_bom_documents` uses for the `*.cdx.json` documents
    themselves. `Path.replace` renames onto the destination instead of
    opening through it, so a planted symlink is swapped out for a real file
    rather than dereferenced; `_write_new_temp_file` keeps the same guarantee
    for the temp file itself."""
    temp_path = _write_new_temp_file(manifest_path.parent, json.dumps(names))
    temp_path.replace(manifest_path)


def emit_bom_documents(
    documents: list[tuple[str, dict]],
    *,
    output_path: Path | None,
    output_dir: Path | None,
) -> None:
    """One document per agent. stdout is NDJSON so a consumer never needs to know
    the agent count in advance; `--output-dir` is one file per agent.

    `--output` is deprecated rather than removed: it keeps working for a single
    agent and errors only when one path genuinely cannot hold the result.

    `--output-dir` owns only the files it wrote on a prior run, not the whole
    `*.cdx.json` namespace: a rerun that resolves fewer agents than the previous
    one (an agent removed, a kind's discovery narrowed) must not leave that
    agent's stale file behind for a consumer to misread as still current, but a
    `.cdx.json` file the tool never wrote (hand-authored, from another tool, from
    a previous scan pointed at this directory by a different invocation) is left
    alone — including when its name collides with a basename this run would
    generate. `_BOM_MANIFEST_NAME` records the exact basenames this tool wrote
    last time, and only those are candidates for removal or overwrite.

    Every new document is written to a temp file first and only moved into place
    once the whole set has serialized successfully. If a later step (publishing
    a final file or removing a stale one) fails partway through, the command
    tries to rewrite the manifest to describe exactly what ended up on disk
    before raising; if that manifest rewrite itself fails, the command still
    raises and reports it, since the manifest may now be stale — this is
    best-effort recovery, not a guarantee the manifest always matches reality.
    """
    if output_dir is not None and output_path is not None:
        raise click.ClickException("--output and --output-dir are mutually exclusive")
    if output_dir is not None:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise click.ClickException(f"failed to prepare {output_dir}: {exc}") from exc
        manifest_path = output_dir / _BOM_MANIFEST_NAME
        previously_owned = _read_bom_manifest(manifest_path, output_dir)
        current_names = [f"{basename}.cdx.json" for basename, _ in documents]
        current_name_set = set(current_names)

        unowned_collisions = sorted(
            name
            for name in current_name_set
            if name not in previously_owned and (output_dir / name).exists()
        )
        if unowned_collisions:
            raise click.ClickException(
                f"refusing to overwrite file(s) in {output_dir} not written by a "
                f"previous run of this tool: {', '.join(unowned_collisions)}"
            )

        staged: list[tuple[Path, Path]] = []
        try:
            for name, (_, document) in zip(current_names, documents, strict=True):
                final_path = output_dir / name
                temp_path = _write_new_temp_file(output_dir, f"{json.dumps(document, indent=2)}\n")
                staged.append((temp_path, final_path))
        except OSError as exc:
            for temp_path, _ in staged:
                temp_path.unlink(missing_ok=True)
            raise click.ClickException(f"failed to write BOM to {output_dir}: {exc}") from exc

        published: set[str] = set()
        try:
            for temp_path, final_path in staged:
                temp_path.replace(final_path)
                published.add(final_path.name)
        except OSError as exc:
            # Files already published are real; a name not yet published keeps
            # its old (still-current) content, and its `.tmp` sibling is not a
            # real output — clean it up rather than leaving it as litter.
            for temp_path, final_path in staged:
                if final_path.name not in published:
                    temp_path.unlink(missing_ok=True)
            # Record exactly what is on disk now so a later run's stale
            # accounting reflects reality instead of a manifest that lies in
            # either direction.
            try:
                _write_bom_manifest(
                    manifest_path, sorted(published | (previously_owned - current_name_set))
                )
            except OSError as manifest_exc:
                raise click.ClickException(
                    f"failed to publish BOM to {output_dir}: {exc}; "
                    f"{len(published)} of {len(staged)} document(s) written; "
                    f"the ownership manifest could not be updated to match and may "
                    f"now be stale: {manifest_exc}"
                ) from manifest_exc
            raise click.ClickException(
                f"failed to publish BOM to {output_dir}: {exc}; "
                f"{len(published)} of {len(staged)} document(s) written"
            ) from exc

        removed: set[str] = set()
        try:
            for stale_name in previously_owned - current_name_set:
                (output_dir / stale_name).unlink(missing_ok=True)
                removed.add(stale_name)
        except OSError as exc:
            still_stale = previously_owned - current_name_set - removed
            try:
                _write_bom_manifest(manifest_path, sorted(current_name_set | still_stale))
            except OSError as manifest_exc:
                raise click.ClickException(
                    f"failed to remove stale BOM(s) from {output_dir}: {exc}; "
                    f"the ownership manifest could not be updated to match and may "
                    f"now be stale: {manifest_exc}"
                ) from manifest_exc
            raise click.ClickException(
                f"failed to remove stale BOM(s) from {output_dir}: {exc}"
            ) from exc

        try:
            _write_bom_manifest(manifest_path, current_names)
        except OSError as exc:
            raise click.ClickException(
                f"wrote {len(current_names)} BOM(s) to {output_dir} but failed to "
                f"update the ownership manifest: {exc}"
            ) from exc
        return
    if output_path is not None:
        if len(documents) > 1:
            raise click.ClickException(
                f"{len(documents)} agents resolved; --output holds one document. "
                "Use --output-dir instead."
            )
        if not documents:
            return
        try:
            output_path.write_text(f"{json.dumps(documents[0][1], indent=2)}\n", encoding="utf-8")
        except OSError as exc:
            raise click.ClickException(f"failed to write BOM to {output_path}: {exc}") from exc
        return
    for _, document in documents:
        click.echo(json.dumps(document, separators=(",", ":")))


@main.command()
@click.option(
    "--target",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to inspect.",
)
@click.option(
    "--include-gitignored",
    is_flag=True,
    default=False,
    help="Walk paths matched by <target>/.gitignore.",
)
@_output_option
@_output_dir_option
def repo(
    target: Path,
    include_gitignored: bool,
    output_path: Path | None,
    output_dir: Path | None,
) -> None:
    """Generate one Agent BOM per agent this repository declares."""
    agents = discover_agents(
        DiscoveryContext(source="declared", scan_root=target, include_gitignored=include_gitignored)
    )
    if not agents:
        click.echo(f"{target} declares no agent", err=True)
        emit_bom_documents([], output_path=output_path, output_dir=output_dir)
        return
    basenames = output_basenames(agents)
    documents: list[tuple[str, dict]] = []
    # A many-per-place kind can return several declared agents over one root, so
    # memoize the walk per (root, kind surface) rather than re-walking per agent.
    walks: dict[tuple[Path, tuple], tuple[int, int]] = {}
    for agent in agents:
        assert agent.scan_root is not None
        kind = kind_for(agent.kind_id)
        warnings: list[str] = []
        graph = build_agent_graph(agent, include_gitignored=include_gitignored, warnings=warnings)
        for w in warnings:
            click.echo(f"warning: {w}", err=True)
        # Manifest-visited count and parse-failure reporting are properties of
        # the filesystem walk, not the graph; source them from the walk against
        # this kind's own patterns so a repo declaring two kinds counts each
        # kind's own manifests. Composition comes from the graph.
        walk_key = (agent.scan_root, kind.manifest_patterns)
        if walk_key not in walks:
            parse_groups, n_found = parse_repo_grouped(
                agent.scan_root,
                include_gitignored=include_gitignored,
                registry=kind.manifest_patterns,
            )
            walks[walk_key] = (n_found, n_found - len(parse_groups))
        n_found, n_failed = walks[walk_key]
        if n_failed:
            click.echo(
                f"warning: {n_failed} of {n_found} matched manifest(s) failed to parse"
                " and were skipped",
                err=True,
            )
        bom = build_agent_bom(
            _filter_agent_scope_refs(_refs_from_graph(graph)),
            target=str(agent.scan_root),
            source_unit_count=n_found,
            source_unit_label="manifest",
            graph=graph,
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
            agent_name=agent.display_name,
            composition_source=agent.source,
            composition_coverage=resolve_coverage(
                agent.coverage_baseline, evidence_gaps=len(warnings) + n_failed
            ),
        )
        documents.append((basenames[agent.bom_ref], bom.to_cyclonedx()))
    emit_bom_documents(documents, output_path=output_path, output_dir=output_dir)


@main.command()
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Agent host config directory. Defaults to $CLAUDE_CONFIG_DIR, else ~/.claude.",
)
@click.option(
    "--project",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root whose .claude settings/skills/MCPs are layered into endpoint resolution.",
)
@_output_option
@_output_dir_option
def endpoint(
    config_dir: Path | None,
    project: Path | None,
    output_path: Path | None,
    output_dir: Path | None,
) -> None:
    """Generate one Agent BOM per installed agent."""
    agents = discover_agents(
        DiscoveryContext(source="installed", config_dir=config_dir, project_root=project)
    )
    if not agents:
        click.echo("no installed agent found", err=True)
        emit_bom_documents([], output_path=output_path, output_dir=output_dir)
        return
    basenames = output_basenames(agents)
    documents: list[tuple[str, dict]] = []
    for agent in agents:
        warnings: list[str] = []
        graph = build_agent_graph(agent, warnings=warnings)
        for w in warnings:
            click.echo(f"warning: {w}", err=True)
        refs = _refs_from_graph(graph)
        bom = build_agent_bom(
            _filter_agent_scope_refs(refs),
            target=str(agent.config_root),
            source_unit_count=sum(1 for r in refs if _is_plugin_ref(r)),
            source_unit_label="active plugin",
            graph=graph,
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
            agent_name=agent.display_name,
            composition_source=agent.source,
            composition_coverage=resolve_coverage(
                agent.coverage_baseline, evidence_gaps=len(warnings)
            ),
        )
        documents.append((basenames[agent.bom_ref], bom.to_cyclonedx()))
    emit_bom_documents(documents, output_path=output_path, output_dir=output_dir)


@main.command(name="diff")
@click.option(
    "--before",
    "before_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Earlier CycloneDX Agent BOM JSON file.",
)
@click.option(
    "--after",
    "after_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Later CycloneDX Agent BOM JSON file.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
def diff_command(before_path: Path, after_path: Path, output_format: str) -> None:
    """Compare two Agent BOMs by component occurrence and composition edge."""
    try:
        result = diff_boms(_read_json_bom(before_path), _read_json_bom(after_path))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if output_format == "json":
        click.echo(json.dumps(result.to_json(), indent=2))
        return
    click.echo(_render_diff_text(result))


def _read_json_bom(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc.msg}") from exc
    except OSError as exc:
        raise ValueError(f"failed to read BOM from {path}: {exc}") from exc


def _render_diff_text(result: BomDiffResult) -> str:
    lines = [
        "BOM diff: "
        f"{len(result.added_components)} added, "
        f"{len(result.removed_components)} removed, "
        f"{len(result.changed_components)} changed, "
        f"{len(result.added_edges)} added edge(s), "
        f"{len(result.removed_edges)} removed edge(s)"
    ]
    if result.added_components:
        lines.append("Added components:")
        lines.extend(f"  + {_format_component(c)}" for c in result.added_components)
    if result.removed_components:
        lines.append("Removed components:")
        lines.extend(f"  - {_format_component(c)}" for c in result.removed_components)
    if result.changed_components:
        lines.append("Changed components:")
        for item in result.changed_components:
            lines.extend(_format_changed_component(item))
    if result.added_edges:
        lines.append("Added edges:")
        lines.extend(f"  + {parent} -> {child}" for parent, child in result.added_edges)
    if result.removed_edges:
        lines.append("Removed edges:")
        lines.extend(f"  - {parent} -> {child}" for parent, child in result.removed_edges)
    return "\n".join(lines)


def _format_component(component: BomDiffComponent) -> str:
    label = component.identity or component.name or component.purl or component.bom_ref
    parts = [label]
    if component.component_type:
        parts.append(f"({component.component_type})")
    if component.version:
        parts.append(f"version {component.version}")
    parts.append(f"[{component.bom_ref}]")
    return " ".join(parts)


def _format_changed_component(component: ChangedBomDiffComponent) -> list[str]:
    lines = [f"  ~ {_format_component(component.after)}"]
    if component.before.version != component.after.version:
        lines.append(f"    version: {component.before.version} -> {component.after.version}")
    if component.before.purl != component.after.purl:
        lines.append(f"    purl: {component.before.purl} -> {component.after.purl}")
    if component.before.git_commit_sha != component.after.git_commit_sha:
        lines.append(
            f"    git_commit_sha: {component.before.git_commit_sha}"
            f" -> {component.after.git_commit_sha}"
        )
    if component.before.artifact_coordinates != component.after.artifact_coordinates:
        before_hash = _extract_skill_hash(component.before.artifact_coordinates)
        after_hash = _extract_skill_hash(component.after.artifact_coordinates)
        lines.append(f"    artifact_coordinates: {before_hash} -> {after_hash}")
    if component.before.url != component.after.url:
        lines.append(f"    url: {component.before.url} -> {component.after.url}")
    if component.before.install_source != component.after.install_source:
        lines.append(
            f"    install_source: {component.before.install_source}"
            f" -> {component.after.install_source}"
        )
    if component.before.git_ref != component.after.git_ref:
        lines.append(f"    git_ref: {component.before.git_ref} -> {component.after.git_ref}")
    if component.before.transport != component.after.transport:
        lines.append(f"    transport: {component.before.transport} -> {component.after.transport}")
    if component.before.source_provenance != component.after.source_provenance:
        before_prov = _extract_provenance_label(component.before.source_provenance)
        after_prov = _extract_provenance_label(component.after.source_provenance)
        lines.append(f"    source_provenance: {before_prov} -> {after_prov}")
    if component.before.match_coordinate != component.after.match_coordinate:
        lines.append(
            f"    match_coordinate: {component.before.match_coordinate}"
            f" -> {component.after.match_coordinate}"
        )
    if component.before.scope != component.after.scope:
        lines.append(f"    scope: {component.before.scope} -> {component.after.scope}")
    if component.before.capabilities != component.after.capabilities:
        lines.append(
            f"    capabilities: {component.before.capabilities} -> {component.after.capabilities}"
        )
    if component.before.capability_coverage != component.after.capability_coverage:
        lines.append(
            f"    capability_coverage: {component.before.capability_coverage}"
            f" -> {component.after.capability_coverage}"
        )
    return lines


def _extract_skill_hash(coords_json: str | None) -> str | None:
    if coords_json is None:
        return None
    try:
        coords = json.loads(coords_json)
    except (json.JSONDecodeError, TypeError):
        return coords_json
    if isinstance(coords, list):
        for coord in coords:
            if isinstance(coord, dict) and coord.get("kind") == "skill-content-hash":
                return coord.get("value")
    return coords_json


def _extract_provenance_label(provenance_json: str | None) -> str | None:
    if provenance_json is None:
        return None
    try:
        prov = json.loads(provenance_json)
    except (json.JSONDecodeError, TypeError):
        return provenance_json
    if not isinstance(prov, dict):
        return provenance_json
    source = prov.get("source")
    ref = prov.get("ref")
    if source and ref:
        return f"{source}@{ref}"
    if source:
        return source
    status = prov.get("status")
    return status or provenance_json
