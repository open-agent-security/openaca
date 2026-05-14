"""Seed reviewable ASVE overlay candidates from OSV bulk dumps."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Iterable

import click
import yaml

from tools.seed import llm
from tools.seed.validator import validate_candidate

REPO_ROOT = Path(__file__).resolve().parents[2]

_NAME_PATTERN = re.compile(r"(?:^|[/_\-@])mcp(?:[/_\-]|$)", re.IGNORECASE)
_SUMMARY_HINTS = ("model context protocol", "mcp server", "mcp client", " mcp ")
_AGENT_STACK_PACKAGE_PATTERN = re.compile(
    r"(^|[/@_\-])("
    r"fastmcp|"
    r"langchain|langgraph|langsmith|langfuse|"
    r"llama[-_]?index|"
    r"litellm|vllm|open[-_]?webui|"
    r"semantic[-_]?kernel|pydantic[-_]?ai|dspy|agno|"
    r"praisonai(?:agents)?|openclaw|openclaude|"
    r"flowise(?:[-_]?components)?|langflow|dify|"
    r"aider(?:[-_]?chat)?|cline|"
    r"anthropic|claude[-_]?code|copilot|"
    r"openai[/_\-]codex|"
    r"qdrant[-_]?client|pymilvus|milvus|hnswlib"
    r")($|[/@_\-])",
    re.IGNORECASE,
)
_AGENT_AI_FEATURE_HINTS = (
    "prompt injection",
    "indirect prompt",
    "system prompt injection",
    "ai assistant",
    "large language model",
    "gemini connector",
    "claude sdk",
    "memory tool",
    "ai agent",
    "agent tool",
    "csv agent",
    "read_skill_file",
    "skill file",
    "rag poisoning",
    "knowledge base",
)
_AGENT_TOOL_HINTS = ("tool call", "tool invocation", "function calling")
_AGENT_TOOL_CONTEXT_HINTS = ("ai", "agent", "llm", "gemini", "claude", "openai", "assistant")

_CLASS_RULES: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (
        (
            "command injection",
            "command execution",
            "os command",
            "shell injection",
            "remote code execution",
            "rce",
            "arbitrary code",
            "code injection",
            "code execution",
            "execute arbitrary",
        ),
        {
            "taxonomies": {"owasp_agentic_top10": {"asi05"}},
        },
    ),
    (
        ("path traversal", "directory traversal", "arbitrary file read", "file disclosure"),
        {
            "taxonomies": {"owasp_agentic_top10": {"asi08"}},
        },
    ),
    (
        ("ssrf", "server-side request forgery"),
        {
            "taxonomies": {"owasp_agentic_top10": {"asi02"}},
        },
    ),
    (
        ("authentication bypass", "auth bypass", "missing authentication", "unauthenticated"),
        {
            "taxonomies": {"owasp_agentic_top10": {"asi02"}},
        },
    ),
    (
        ("prompt injection", "indirect prompt"),
        {
            "taxonomies": {"owasp_agentic_top10": {"asi01"}},
        },
    ),
]


def _text(record: dict[str, Any]) -> str:
    return " ".join([str(record.get("summary") or ""), str(record.get("details") or "")]).lower()


def _package_names(record: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for affected in record.get("affected") or []:
        package = affected.get("package") or {}
        name = package.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def discovery_reasons(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    package_names = _package_names(record)
    if any(_NAME_PATTERN.search(name) for name in package_names):
        reasons.append("package_name_mcp")
    if any(_AGENT_STACK_PACKAGE_PATTERN.search(name) for name in package_names):
        reasons.append("package_name_agent_stack")
    text = _text(record)
    if any(hint in text for hint in _SUMMARY_HINTS):
        reasons.append("summary_mentions_mcp")
    if _mentions_agent_ai_feature(text):
        reasons.append("topic_agent_ai_feature")
    return reasons


def _mentions_agent_ai_feature(text: str) -> bool:
    if any(hint in text for hint in _AGENT_AI_FEATURE_HINTS):
        return True
    return any(hint in text for hint in _AGENT_TOOL_HINTS) and any(
        context in text for context in _AGENT_TOOL_CONTEXT_HINTS
    )


def iter_records(source: Path) -> Iterable[dict[str, Any]]:
    if source.is_file() and source.suffix == ".zip":
        with zipfile.ZipFile(source) as zf:
            for name in sorted(zf.namelist()):
                if not name.endswith(".json"):
                    continue
                try:
                    data = json.loads(zf.read(name))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(data, dict):
                    yield data
        return

    paths = [source] if source.is_file() else sorted(source.rglob("*.json"))
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            yield data


def _load_state(state: Path | None) -> tuple[str | None, set[str]]:
    if state is None or not state.exists():
        return None, set()
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, set()
    last_modified = data.get("last_modified")
    ts = last_modified if isinstance(last_modified, str) else None
    raw_ids = data.get("last_modified_ids")
    ids: set[str] = set(raw_ids) if isinstance(raw_ids, list) else set()
    return ts, ids


def _record_path(records_root: Path, row_id: str) -> Path:
    path = records_root / f"{row_id}.json"
    resolved_root = records_root.resolve()
    resolved_path = path.resolve()
    if resolved_path.parent != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError(f"unsafe modified_id.csv record id: {row_id!r}")
    return path


def _modified_record_zip_paths(records_root: Path, row_id: str) -> list[Path]:
    paths: list[Path] = []
    ecosystem, sep, _record_id = row_id.partition("/")
    if sep:
        paths.append(records_root / ecosystem / "all.zip")
    paths.append(records_root / "all.zip")
    return paths


def _load_modified_record(
    records_root: Path,
    row_id: str,
    zip_cache: dict[Path, zipfile.ZipFile],
) -> dict[str, Any] | None:
    path = _record_path(records_root, row_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = None
    if isinstance(data, dict):
        return data

    member = f"{Path(row_id).name}.json"
    for zip_path in _modified_record_zip_paths(records_root, row_id):
        if not zip_path.exists():
            continue
        try:
            if zip_path not in zip_cache:
                zip_cache[zip_path] = zipfile.ZipFile(zip_path)
            zf = zip_cache[zip_path]
            data = json.loads(zf.read(member))
        except (KeyError, json.JSONDecodeError, OSError, zipfile.BadZipFile):
            continue
        if isinstance(data, dict):
            return data
    return None


def iter_modified_records(
    modified_index: Path,
    records_root: Path,
    last_modified: str | None,
    last_modified_ids: set[str],
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    zip_cache: dict[Path, zipfile.ZipFile] = {}
    try:
        for raw_line in modified_index.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            modified, sep, row_id = raw_line.partition(",")
            if not sep or not modified or not row_id:
                continue
            if last_modified is not None:
                if modified < last_modified:
                    break
                if modified == last_modified and row_id in last_modified_ids:
                    continue
            data = _load_modified_record(records_root, row_id, zip_cache)
            if data is None:
                continue
            yield modified, row_id, data
    finally:
        for zf in zip_cache.values():
            zf.close()


def _write_state(state: Path | None, newest_modified: str | None, newest_ids: set[str]) -> None:
    if state is None or newest_modified is None:
        return
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {"last_modified": newest_modified, "last_modified_ids": sorted(newest_ids)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _has_malicious_package_id(record: dict[str, Any]) -> bool:
    ids = [record.get("id"), *(record.get("aliases") or [])]
    return any(isinstance(value, str) and value.startswith("MAL-") for value in ids)


def _apply_deterministic_asve_fields(
    record: dict[str, Any], asve: dict[str, Any]
) -> dict[str, Any]:
    annotation = copy.deepcopy(asve)
    annotation.pop("threat_kind", None)
    if _has_malicious_package_id(record):
        annotation["threat_kind"] = "malicious_package"
    return annotation


def _classify(record: dict[str, Any]) -> dict[str, Any]:
    taxonomies: dict[str, set[str]] = {"owasp_agentic_top10": set()}
    text = _text(record)

    for keywords, payload in _CLASS_RULES:
        if not any(keyword in text for keyword in keywords):
            continue
        for family, values in (payload.get("taxonomies") or {}).items():
            taxonomies.setdefault(family, set()).update(values)

    taxonomy_lists = {key: sorted(value) for key, value in taxonomies.items() if value}
    if not taxonomy_lists:
        taxonomy_lists = {"owasp_agentic_top10": ["asi05"]}

    asve: dict[str, Any] = {
        "taxonomies": taxonomy_lists,
        "evidence_level": "likely",
    }
    return _apply_deterministic_asve_fields(record, asve)


def build_candidate(
    record: dict[str, Any],
    matched_by: list[str],
    asve_annotation: dict[str, Any] | None = None,
    evidence: list[dict[str, str]] | None = None,
    annotation_source: str = "deterministic",
    framework_documents: list[str] | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any]:
    rec_id = record.get("id")
    if not isinstance(rec_id, str) or not rec_id:
        raise ValueError("OSV record is missing id")

    aliases = [a for a in record.get("aliases") or [] if isinstance(a, str) and a != rec_id]
    candidate_metadata: dict[str, Any] = {
        "review_status": "needs_review",
        "matched_by": matched_by,
        "package_names": _package_names(record),
        "annotation_source": annotation_source,
    }
    if framework_documents:
        candidate_metadata["framework_documents"] = framework_documents
    if llm_provider:
        candidate_metadata["llm_provider"] = llm_provider
    if llm_model:
        candidate_metadata["llm_model"] = llm_model
    asve = asve_annotation if asve_annotation is not None else _classify(record)
    candidate: dict[str, Any] = {
        "schema_version": record.get("schema_version") or "1.7.5",
        "id": rec_id,
        "modified": record.get("modified") or record.get("published") or "1970-01-01T00:00:00Z",
        "_candidate": candidate_metadata,
        "_evidence": evidence
        if evidence is not None
        else [{"field": "summary", "quote": record.get("summary") or ""}],
        "database_specific": {"asve": _apply_deterministic_asve_fields(record, asve)},
    }
    if aliases:
        candidate["aliases"] = aliases
    for key in ("summary", "details", "references", "affected"):
        if key in record:
            candidate[key] = record[key]
    return candidate


def build_rejected_candidate(
    record: dict[str, Any],
    matched_by: list[str],
    reject_reason: str,
    evidence: list[dict[str, str]] | None,
    framework_documents: list[str],
    llm_provider: str,
    llm_model: str,
    llm_error: str | None = None,
) -> dict[str, Any]:
    rec_id = record.get("id")
    if not isinstance(rec_id, str) or not rec_id:
        raise ValueError("OSV record is missing id")
    candidate_metadata: dict[str, Any] = {
        "review_status": "rejected",
        "matched_by": matched_by,
        "package_names": _package_names(record),
        "annotation_source": "llm",
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "framework_documents": framework_documents,
        "reject_reason": reject_reason,
    }
    if llm_error:
        candidate_metadata["llm_error"] = llm_error
    return {
        "schema_version": record.get("schema_version") or "1.7.5",
        "id": rec_id,
        "modified": record.get("modified") or record.get("published") or "1970-01-01T00:00:00Z",
        "_candidate": candidate_metadata,
        "_evidence": evidence
        if evidence is not None
        else [{"field": "summary", "quote": record.get("summary") or ""}],
        "summary": record.get("summary") or "",
    }


def _identity(record: dict[str, Any]) -> set[str]:
    ids = {record.get("id")}
    ids.update(a for a in record.get("aliases") or [] if isinstance(a, str))
    return {i for i in ids if isinstance(i, str)}


def _curated_keys(existing_overlays: Path) -> set[str]:
    keys: set[str] = set()
    if not existing_overlays.exists():
        return keys
    for path in existing_overlays.rglob("*.yaml"):
        try:
            overlay = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(overlay, dict):
            continue
        keys.update(_identity(overlay))
    return keys


def _resolve_llm_config(
    provider: str | None,
    model: str | None,
    api_key: str | None,
) -> tuple[str | None, str | None, str | None]:
    if not any((provider, model)):
        return None, None, None
    if not provider:
        raise click.UsageError("--llm-provider is required when LLM annotation is enabled")
    if not model:
        raise click.UsageError("--llm-model is required when LLM annotation is enabled")
    try:
        normalized = llm.normalize_provider(provider)
    except llm.LLMAnnotationError as exc:
        raise click.UsageError(str(exc)) from exc
    resolved_key = api_key or os.environ.get("ASVE_LLM_API_KEY")
    if not resolved_key:
        raise click.UsageError("LLM API key is required via --llm-api-key or ASVE_LLM_API_KEY")
    return normalized, model, resolved_key


@click.command()
@click.argument("source", required=False, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--modified-index",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="OSV modified_id.csv file. Reads only records newer than --state.",
)
@click.option(
    "--records-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Root directory containing JSON records referenced by --modified-index.",
)
@click.option(
    "--state",
    type=click.Path(dir_okay=False, path_type=Path),
    help="JSON state file containing last_modified for incremental seeding.",
)
@click.option(
    "--out",
    "out_dir",
    default=Path("candidates"),
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for reviewable candidate YAML files.",
)
@click.option(
    "--existing",
    "existing_overlays",
    default=REPO_ROOT / "overlays",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Existing overlay corpus used for alias deduplication.",
)
@click.option("--dry-run", is_flag=True, help="Print candidates without writing files.")
@click.option(
    "--llm-provider",
    type=click.Choice(["openai", "anthropic"], case_sensitive=False),
    envvar="ASVE_LLM_PROVIDER",
    help="LLM provider for framework-grounded annotations.",
)
@click.option(
    "--llm-model",
    envvar="ASVE_LLM_MODEL",
    help="LLM model name for framework-grounded annotations.",
)
@click.option(
    "--llm-api-key",
    envvar="ASVE_LLM_API_KEY",
    help="LLM API key. Prefer ASVE_LLM_API_KEY.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    envvar="ASVE_SEED_LIMIT",
    help="Stop after writing this many candidates. Does not advance --state.",
)
def main(
    source: Path | None,
    modified_index: Path | None,
    records_root: Path | None,
    state: Path | None,
    out_dir: Path,
    existing_overlays: Path,
    dry_run: bool,
    llm_provider: str | None,
    llm_model: str | None,
    llm_api_key: str | None,
    limit: int | None,
) -> None:
    """Generate deterministic review candidates from an OSV JSON directory or zip."""
    if modified_index is not None and records_root is None:
        raise click.UsageError("--records-root is required with --modified-index")
    if modified_index is None and source is None:
        raise click.UsageError("SOURCE is required unless --modified-index is provided")

    existing_keys = _curated_keys(existing_overlays)
    seen_keys = set(existing_keys)
    scanned = matched = already_in_overlays = duplicate_aliases = written = 0
    limit_hit = False
    newest_modified: str | None = None
    newest_modified_ids: set[str] = set()

    normalized_llm_provider, resolved_llm_model, resolved_llm_api_key = _resolve_llm_config(
        llm_provider, llm_model, llm_api_key
    )
    framework_documents = llm.load_framework_documents() if normalized_llm_provider else None

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    out_dir_resolved = out_dir.resolve()

    last_modified, last_modified_ids = _load_state(state)

    if modified_index is not None:
        assert records_root is not None
        records: Iterable[tuple[str | None, str | None, dict[str, Any]]] = iter_modified_records(
            modified_index, records_root, last_modified, last_modified_ids
        )
    else:
        assert source is not None
        records = ((None, None, record) for record in iter_records(source))

    for modified, row_id, record in records:
        scanned += 1
        if modified is not None:
            if newest_modified is None:
                newest_modified = modified
            if modified == newest_modified and row_id is not None:
                newest_modified_ids.add(row_id)
        matched_by = discovery_reasons(record)
        if not matched_by:
            continue
        matched += 1
        identity = _identity(record)
        if identity & seen_keys:
            if identity & existing_keys:
                already_in_overlays += 1
            else:
                duplicate_aliases += 1
            continue

        if normalized_llm_provider:
            assert framework_documents is not None
            assert resolved_llm_model is not None
            assert resolved_llm_api_key is not None
            request = llm.build_request(record, matched_by, framework_documents)
            click.echo(
                f"llm: annotating {record.get('id')} "
                f"with {normalized_llm_provider}/{resolved_llm_model}"
            )
            try:
                annotation = llm.annotate_with_provider(
                    normalized_llm_provider,
                    resolved_llm_model,
                    resolved_llm_api_key,
                    request,
                )
            except llm.LLMProviderError as exc:
                raise click.ClickException(str(exc)) from exc
            except llm.LLMAnnotationError as exc:
                candidate = build_rejected_candidate(
                    record,
                    matched_by,
                    "unsupported_record",
                    None,
                    sorted(framework_documents),
                    normalized_llm_provider,
                    resolved_llm_model,
                    llm_error=str(exc),
                )
                rejected_dir_resolved = (out_dir / "rejected").resolve()
                target = out_dir / "rejected" / f"{candidate['id']}.yaml"
                if target.resolve().parent != rejected_dir_resolved:
                    click.echo(f"{candidate['id']!r}: unsafe candidate ID, skipping", err=True)
                    continue
                click.echo(f"llm: rejected {candidate['id']} after annotation error")
                if dry_run:
                    click.echo(f"would reject {target}: unsupported_record")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
                written += 1
                seen_keys.update(identity)
                if limit is not None and written >= limit:
                    limit_hit = True
                    click.echo(f"limit {limit} reached; state not advanced")
                    break
                continue
            if annotation.decision == "reject":
                assert annotation.reject_reason is not None
                candidate = build_rejected_candidate(
                    record,
                    matched_by,
                    annotation.reject_reason,
                    annotation.evidence,
                    sorted(framework_documents),
                    normalized_llm_provider,
                    resolved_llm_model,
                )
                rejected_dir_resolved = (out_dir / "rejected").resolve()
                target = out_dir / "rejected" / f"{candidate['id']}.yaml"
                if target.resolve().parent != rejected_dir_resolved:
                    click.echo(f"{candidate['id']!r}: unsafe candidate ID, skipping", err=True)
                    continue
                if dry_run:
                    click.echo(f"would reject {target}: {annotation.reject_reason}")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
                written += 1
                seen_keys.update(identity)
                if limit is not None and written >= limit:
                    limit_hit = True
                    click.echo(f"limit {limit} reached; state not advanced")
                    break
                continue
            if annotation.asve is None:
                raise click.ClickException("LLM annotation must include database_specific.asve")
            candidate = build_candidate(
                record,
                matched_by,
                asve_annotation=annotation.asve,
                evidence=annotation.evidence,
                annotation_source="llm",
                framework_documents=sorted(framework_documents),
                llm_provider=normalized_llm_provider,
                llm_model=resolved_llm_model,
            )
        else:
            candidate = build_candidate(record, matched_by)
        errors = validate_candidate(candidate)
        if errors:
            click.echo(f"{candidate.get('id')}: candidate validation failed", err=True)
            for error in errors:
                click.echo(f"  {error}", err=True)
            sys.exit(1)

        target = out_dir / f"{candidate['id']}.yaml"
        if target.resolve().parent != out_dir_resolved:
            click.echo(f"{candidate['id']!r}: unsafe candidate ID, skipping", err=True)
            continue
        if dry_run:
            click.echo(f"would write {target}: {candidate.get('summary', '')[:80]}")
        else:
            target.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
        written += 1
        seen_keys.update(identity)
        if limit is not None and written >= limit:
            limit_hit = True
            click.echo(f"limit {limit} reached; state not advanced")
            break

    if newest_modified is not None and newest_modified == last_modified:
        newest_modified_ids |= last_modified_ids

    if not dry_run and not limit_hit:
        _write_state(state, newest_modified, newest_modified_ids)

    click.echo(
        f"scanned {scanned} records, {matched} matched, "
        f"{already_in_overlays} already in overlays, "
        f"{duplicate_aliases} duplicate alias{'es' if duplicate_aliases != 1 else ''}, "
        f"{written} candidate{'s' if written != 1 else ''} "
        f"{'(dry-run)' if dry_run else 'written'}"
    )


if __name__ == "__main__":
    main()
