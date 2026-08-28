"""`openaca bom` commands for emitting Agent BOMs."""

from __future__ import annotations

import json
import os
import stat
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
from tools.bom import agent_info_from_cyclonedx, build_agent_bom
from tools.bom_diff import BomDiffComponent, BomDiffResult, ChangedBomDiffComponent, diff_boms
from tools.bom_lint import main as lint_cmd
from tools.cli_kind import kind_option, require_kind_for_config_dir
from tools.graph import WarningLog
from tools.parsers import parse_repo_registry_counts
from tools.scan import (
    _component_gap_count,
    _count_active_plugins,
    _filter_agent_scope_refs,
    _refs_from_graph,
)


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


def _is_shared_sticky_dir(output_dir: Path) -> bool:
    """Is this a sticky directory other users can write to (`/tmp` and friends)?

    Planting an ownership manifest normally grants an attacker nothing: writing
    it requires write access to the directory, and on POSIX that is exactly the
    permission needed to unlink or replace the files it names, so they could
    destroy them directly. The manifest is not a new capability.

    A sticky directory shared with other users is the one configuration where
    that reasoning fails: a non-owner may create their own files but may *not*
    unlink or rename anyone else's. There a planted manifest does escalate —
    it gets the owner's own tool to destroy the owner's file. So in that
    configuration the manifest is not treated as ownership proof.
    """
    try:
        mode = output_dir.stat().st_mode
    except OSError:
        return False
    return bool(mode & stat.S_ISVTX) and bool(mode & (stat.S_IWGRP | stat.S_IWOTH))


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
        if _is_shared_sticky_dir(output_dir):
            # Distrust the manifest entirely here: with no owned set, nothing is
            # deleted as stale and any colliding name is refused rather than
            # overwritten. The run still writes its own new documents.
            previously_owned: set[str] = set()
            click.echo(
                f"warning: {output_dir} is a sticky directory writable by other users; "
                "ignoring the ownership manifest, so stale files are left in place",
                err=True,
            )
        else:
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
                # A name that was previously owned and part of this run's target
                # set but did not get republished (its `Path.replace` never ran,
                # or ran after the one that failed) still holds its old,
                # previously-owned content on disk — dropping it here would make
                # the next run see it as an unowned collision and refuse to
                # overwrite it forever. Keep every previously-owned name plus
                # everything freshly published.
                _write_bom_manifest(manifest_path, sorted(published | previously_owned))
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
        # Written with a plain `write_text`, deliberately unlike `--output-dir`
        # above. The hardening there exists because this tool *chooses*
        # predictable names inside a directory it also scans, so a pre-planted
        # symlink at a name we are about to write is a path we picked, not one
        # the caller did. A single `--output` path is named by the caller, who
        # may legitimately point it at a symlink into an artifacts directory;
        # replacing that symlink with a regular file would be the surprise.
        if len(documents) > 1:
            raise click.ClickException(
                f"{len(documents)} agents resolved; --output holds one document. "
                "Use --output-dir instead."
            )
        if not documents:
            try:
                output_path.unlink(missing_ok=True)
            except OSError as exc:
                raise click.ClickException(
                    f"failed to remove stale BOM at {output_path}: {exc}"
                ) from exc
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
    # `target` is the only scan_root `discover_agents(source="declared", ...)`
    # ever returns here, so one walk over the union of every present kind's
    # patterns covers every agent — walking once per kind (or per agent, for a
    # many-per-place kind sharing a root) would re-walk the filesystem once
    # per kind for the five host-agnostic dependency manifests both kinds
    # declare. Each kind's own (n_found, n_failed) still comes out of that one
    # walk unmixed with the other kind's — a Cursor parse failure must not
    # degrade the Claude agent's coverage.
    registries: dict[str, tuple] = {
        agent.kind_id: kind_for(agent.kind_id).manifest_patterns for agent in agents
    }
    surfaces: dict[str, object] = {
        agent.kind_id: kind_for(agent.kind_id).repo_surface for agent in agents
    }
    per_kind_counts, _union_counts = parse_repo_registry_counts(
        target,
        registries,
        include_gitignored=include_gitignored,
        surfaces=surfaces,  # type: ignore[arg-type]
    )
    walks: dict[tuple[Path, tuple], tuple[int, int]] = {
        (target, patterns): per_kind_counts[kind_id] for kind_id, patterns in registries.items()
    }
    for agent in agents:
        assert agent.scan_root is not None
        kind = kind_for(agent.kind_id)
        warnings: WarningLog = WarningLog()
        graph = build_agent_graph(agent, include_gitignored=include_gitignored, warnings=warnings)
        for w in warnings:
            click.echo(f"warning: {w}", err=True)
        # Manifest-visited count and parse-failure reporting are properties of
        # the filesystem walk, not the graph; source them from the walk against
        # this kind's own patterns so a repo declaring two kinds counts each
        # kind's own manifests. Composition comes from the graph.
        n_found, n_failed = walks[(agent.scan_root, kind.manifest_patterns)]
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
                agent.coverage_baseline, evidence_gaps=_component_gap_count(warnings) + n_failed
            ),
        )
        documents.append((basenames[agent.bom_ref], bom.to_cyclonedx()))
    emit_bom_documents(documents, output_path=output_path, output_dir=output_dir)


@main.command()
@kind_option
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Agent host config directory for the kind selected with --kind. "
        "Requires --kind. Each kind resolves its own default root when "
        "omitted (Claude Code: $CLAUDE_CONFIG_DIR, else ~/.claude; Cursor: "
        "~/.cursor)."
    ),
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
    kind: str | None,
    config_dir: Path | None,
    project: Path | None,
    output_path: Path | None,
    output_dir: Path | None,
) -> None:
    """Generate one Agent BOM per installed agent."""
    require_kind_for_config_dir(kind, config_dir)
    agents = discover_agents(
        DiscoveryContext(
            source="installed", config_dir=config_dir, project_root=project, kind_id=kind
        )
    )
    if not agents:
        click.echo("no installed agent found", err=True)
        emit_bom_documents([], output_path=output_path, output_dir=output_dir)
        return
    basenames = output_basenames(agents)
    documents: list[tuple[str, dict]] = []
    for agent in agents:
        warnings: WarningLog = WarningLog()
        graph = build_agent_graph(agent, warnings=warnings)
        for w in warnings:
            click.echo(f"warning: {w}", err=True)
        refs = _refs_from_graph(graph)
        bom = build_agent_bom(
            _filter_agent_scope_refs(refs),
            target=str(agent.config_root),
            source_unit_count=_count_active_plugins(refs),
            source_unit_label="active plugin",
            graph=graph,
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
            agent_name=agent.display_name,
            composition_source=agent.source,
            composition_coverage=resolve_coverage(
                agent.coverage_baseline, evidence_gaps=_component_gap_count(warnings)
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
    """Compare two Agent BOMs by component occurrence and composition edge.

    Accepts either shape `bom endpoint`/`bom repo` emit: one JSON object, or
    NDJSON with one document per agent. With many documents the **caller pairs
    and the diff primitive stays singular** — pair on (kind, agent id) from
    metadata, diff each pair with the same function a single pair uses, and
    report an unpaired document as an added or removed agent.
    """
    try:
        before_docs = _read_bom_documents(before_path)
        after_docs = _read_bom_documents(after_path)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    # A single document each side keeps today's exact output — no agent
    # headings — but only when the two documents are the same agent (or both
    # legacy, pre-agent-metadata documents): a single-document diff across two
    # different agents (e.g. `synthetic/a` replaced by `synthetic/b`) must go
    # through the pairing logic below so it reports an added and a removed
    # agent instead of component churn between unrelated agents.
    if len(before_docs) == 1 and len(after_docs) == 1:
        before_info = agent_info_from_cyclonedx(before_docs[0])
        after_info = agent_info_from_cyclonedx(after_docs[0])
        before_key = (before_info.kind, before_info.agent_id or "") if before_info else None
        after_key = (after_info.kind, after_info.agent_id or "") if after_info else None
        if before_key == after_key:
            try:
                result = diff_boms(before_docs[0], after_docs[0])
            except ValueError as exc:
                raise click.ClickException(str(exc)) from exc
            if output_format == "json":
                click.echo(json.dumps(result.to_json(), indent=2))
                return
            click.echo(_render_diff_text(result))
            return

    before_by_agent = _documents_by_agent_key(before_docs, before_path)
    after_by_agent = _documents_by_agent_key(after_docs, after_path)

    paired = sorted(set(before_by_agent) & set(after_by_agent))
    removed = sorted(set(before_by_agent) - set(after_by_agent))
    added = sorted(set(after_by_agent) - set(before_by_agent))

    if output_format == "json":
        agent_entries: list[dict[str, object]] = []
        for key in paired:
            try:
                result = diff_boms(before_by_agent[key], after_by_agent[key])
            except ValueError as exc:
                raise click.ClickException(str(exc)) from exc
            agent_entries.append({"agent": _agent_key_label(key), "diff": result.to_json()})
        click.echo(
            json.dumps(
                {
                    "agents": agent_entries,
                    "added_agents": [_agent_key_label(k) for k in added],
                    "removed_agents": [_agent_key_label(k) for k in removed],
                },
                indent=2,
            )
        )
        return

    lines: list[str] = []
    for key in paired:
        try:
            result = diff_boms(before_by_agent[key], after_by_agent[key])
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        lines.append(f"agent {_agent_key_label(key)}")
        lines.append(_render_diff_text(result))
        lines.append("")
    for key in added:
        lines.append(f"added agent {_agent_key_label(key)}")
    for key in removed:
        lines.append(f"removed agent {_agent_key_label(key)}")
    click.echo("\n".join(lines).rstrip())


def _agent_key_label(key: tuple[str, str]) -> str:
    kind, agent_id = key
    return kind if not agent_id else f"{kind}/{agent_id}"


def _documents_by_agent_key(documents: list[dict], path: Path) -> dict[tuple[str, str], dict]:
    """Index documents by the (kind, agent id) half of the instance key.

    The asset — the third part of the key (ADR-0045) — is not in a document by
    design, so a caller diffing two files is asserting they came from the same
    asset. Two documents in one file sharing a key means the producer emitted
    the same agent twice, which is a malformed input rather than something to
    silently pick a winner from.
    """
    indexed: dict[tuple[str, str], dict] = {}
    for index, doc in enumerate(documents):
        info = agent_info_from_cyclonedx(doc)
        if info is None:
            raise ValueError(
                f"{path}: document {index + 1} carries no agent metadata, so it cannot be "
                "paired by agent. Diff single-document files individually."
            )
        key = (info.kind, info.agent_id or "")
        if key in indexed:
            raise ValueError(
                f"{path}: two documents describe the same agent "
                f"{_agent_key_label(key)!r}; cannot pair."
            )
        indexed[key] = doc
    return indexed


def _read_bom_documents(path: Path) -> list[dict]:
    """One JSON object, or NDJSON with one document per line.

    An empty or whitespace-only file is an empty document list, not an error:
    it's the exact shape `bom endpoint`/`bom repo` write to stdout when they
    resolve zero agents, and the diff command needs to accept that snapshot
    rather than reject the documented emitter-to-diff workflow.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not valid UTF-8") from exc
    except OSError as exc:
        raise ValueError(f"failed to read BOM from {path}: {exc}") from exc
    if not raw.strip():
        return []
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        documents: list[dict] = []
        for number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON — {exc.msg}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"{path}:{number}: BOM must be a JSON object, got {type(parsed).__name__}"
                )
            documents.append(parsed)
        if not documents:
            raise ValueError(f"{path}: no BOM documents found")
        return documents
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: BOM must be a JSON object, got {type(doc).__name__}")
    return [doc]


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
