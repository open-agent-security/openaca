from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PureWindowsPath
from typing import Any

import httpx

from tools.bom import build_agent_bom
from tools.component_ref import ComponentRef, safe_pinned_mcp_install_source
from tools.graph import Graph
from tools.graph_build import build_graph
from tools.identity import is_mcp_package_launch_install_source, safe_unpinned_mcp_install_source
from tools.observations import (
    ObservationFinding,
    SkillSpectorCommandNotFound,
    collect_skill_observations,
    collect_skillspector_findings,
)
from tools.posture import (
    PostureFinding,
    collect_endpoint_mcp_manifests,
    collect_endpoint_settings_manifests,
    run_posture_rules,
)
from tools.remote.client import BomUploadResult, RemoteClient, RemoteClientError, RemoteServerError
from tools.remote.config import (
    RemoteConfig,
    get_config_path,
    load_remote_config,
    save_remote_config,
)
from tools.remote.upload_contract import (
    RemoteUploadContractError,
    enforce_remote_upload_contract,
)

JsonObject = dict[str, Any]
TARGET_LOCATOR_ENDPOINT = "endpoint:user-scope"
_AGENT_SCOPES = frozenset({"agent-component", "agent-dependency"})


@dataclass(frozen=True)
class EndpointCollection:
    bom: JsonObject
    posture_findings: list[JsonObject]
    observations: list[JsonObject]
    component_count: int


class CollectError(Exception):
    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def get_pending_dir() -> Path:
    return Path.home() / ".local" / "state" / "openaca"


def _collect_endpoint_components(
    config_dir: Path, project: Path | None
) -> tuple[Graph, list[ComponentRef]]:
    """Build the endpoint composition graph and return agent-scope refs.

    Isolated as a helper so tests can monkeypatch this single boundary
    rather than every graph-build internal.
    """
    graph = build_graph(config_dir, mode="endpoint", project_root=project)
    all_refs = [
        replace(node.ref, scope=graph.scope_of(node))
        for node in graph.nodes.values()
        if node.ref is not None
    ]
    return graph, [r for r in all_refs if r.scope in _AGENT_SCOPES]


def build_endpoint_collection(
    config_dir: Path,
    project: Path | None,
    *,
    external_scanners: tuple[str, ...] = (),
) -> EndpointCollection:
    graph, refs = _collect_endpoint_components(config_dir, project)
    bom = _prepare_remote_bom(
        build_agent_bom(
            refs,
            target_type="endpoint",
            target=TARGET_LOCATOR_ENDPOINT,
            source_unit_count=sum(1 for ref in refs if _is_plugin_ref(ref)),
            source_unit_label="active plugin",
            graph=graph,
        ).to_cyclonedx()
    )
    mcp_manifests = collect_endpoint_mcp_manifests(config_dir, project, refs)
    settings_manifests = collect_endpoint_settings_manifests(config_dir, project)
    posture_findings = [
        _posture_finding_to_payload(finding)
        for finding in run_posture_rules(refs, mcp_manifests, settings_manifests)
    ]
    observations, scanner_posture_findings = _collect_scanner_findings(
        refs, external_scanners=external_scanners
    )
    posture_findings.extend(
        _posture_finding_to_payload(finding) for finding in scanner_posture_findings
    )
    return EndpointCollection(
        bom=bom,
        posture_findings=posture_findings,
        observations=[_observation_to_payload(finding) for finding in observations],
        component_count=len(bom.get("components") or []),
    )


def _collect_scanner_findings(
    refs: list[ComponentRef],
    *,
    external_scanners: tuple[str, ...],
) -> tuple[list[ObservationFinding], list[PostureFinding]]:
    observations = collect_skill_observations(refs)
    posture_findings: list[PostureFinding] = []
    if "nvidia-skillspector" in external_scanners:
        try:
            skillspector_findings = collect_skillspector_findings(refs)
        except SkillSpectorCommandNotFound as exc:
            raise CollectError(str(exc)) from exc
        observations.extend(skillspector_findings.observations)
        posture_findings.extend(skillspector_findings.posture_findings)
    return observations, posture_findings


def collect_endpoint(
    *,
    config_dir: Path,
    project: Path | None,
    quiet: bool = False,
    allow_offline_cache: bool = False,
    external_scanners: tuple[str, ...] = (),
) -> BomUploadResult:
    config_path = get_config_path()
    config = load_remote_config(config_path)
    if config.token is None:
        raise CollectError("Remote is not configured; run openaca remote configure --token <TOKEN>")

    client = RemoteClient(api_url=config.api_url, token=config.token)
    if config.asset_id is not None:
        _replay_pending_uploads(client, config.asset_id)

    if external_scanners:
        collection = build_endpoint_collection(
            config_dir=config_dir,
            project=project,
            external_scanners=external_scanners,
        )
    else:
        collection = build_endpoint_collection(config_dir=config_dir, project=project)
    asset_id = config.asset_id
    if asset_id is None:
        try:
            registered = client.register_asset(_asset_registration_payload())
        except (RemoteServerError, httpx.TransportError) as exc:
            exit_code = 0 if quiet or allow_offline_cache else 2
            raise CollectError("asset registration failed (network)", exit_code=exit_code) from exc
        except RemoteClientError as exc:
            raise CollectError(str(exc)) from exc
        asset_id = registered.asset_id
        config = RemoteConfig(api_url=config.api_url, token=config.token, asset_id=asset_id)
        save_remote_config(config, config_path)

    payload = _upload_payload(
        asset_id=asset_id,
        source="endpoint",
        target_locator=TARGET_LOCATOR_ENDPOINT,
        bom=collection.bom,
        posture_findings=collection.posture_findings,
        observations=collection.observations,
    )
    # ADR 0003 (remote redaction contract): the OSS BOM can carry absolute
    # filesystem paths because the OSS CLI runs on the user's own machine;
    # those paths are useful for offline analysis. remote uploads cross a
    # SaaS network boundary into a multi-tenant store, so we redact at the
    # upload boundary. Relativize against config_dir / project first to
    # preserve component provenance (each skill still identifies as a
    # distinct relative path), and fall back to basename only when no known
    # root applies.
    _redact_payload_for_remote(payload, config_dir=config_dir, project=project)
    # Recompute content_hash AFTER redaction so the stored hash matches the
    # stored raw_bom. The remote contract defines content_hash as
    # sha256(raw_bom); without this, the wire payload carries a hash of the
    # pre-redacted BOM while the backend stores the post-redacted BOM under
    # that hash — a row-level invariant violation that any downstream
    # integrity check would surface. Idempotency still works on subsequent
    # uploads because the redaction is deterministic.
    payload["content_hash"] = _content_hash(payload["bom"])
    enforce_remote_upload_contract(payload)
    try:
        return client.upload_bom(payload)
    except (RemoteServerError, httpx.TransportError) as exc:
        path = _write_pending_payload(payload)
        exit_code = 0 if quiet or allow_offline_cache else 2
        raise CollectError(
            f"saved to {path}; upload failed (network)", exit_code=exit_code
        ) from exc
    except RemoteClientError as exc:
        raise CollectError(str(exc)) from exc


# Unix absolute path segment embedded within a larger string (e.g. inside Bash filter syntax).
# Matches a `/` preceded by a character that is not itself `/`, a word character, or `:`.
# Excluding `:` prevents URL schemes like `https://` from triggering the pattern;
# `@scope/pkg` and `mcp-server/name` are still not flagged.
_EMBEDDED_UNIX_PATH_RE = re.compile(r"(?<=[^/\w:])(/[^\s,)]*)")

# http(s) URL embedded within a larger string (e.g. `Bash(curl https://host/path *)`).
# Used to redact URL paths before the Unix-path regex runs, so URL path components like
# `host/path` are not mistaken for embedded Unix absolute paths.
_EMBEDDED_URL_RE = re.compile(r"https?://[^\s,)]*", re.IGNORECASE)


def _is_absolute_path(value: str) -> bool:
    if value.startswith("/"):
        return True
    if value.startswith("\\\\"):
        return True
    return len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")


def _redact_url_for_remote(value: str) -> str:
    """Strip path, query, fragment, and userinfo from an http(s) URL so only
    `scheme://host` remains.

    The backend's redaction rule rejects URLs with paths or queries in
    `openaca:*` property values. Bare `https://api.example.com` is fine;
    `https://api.example.com/mcp/` is not. We also strip userinfo
    (e.g. `https://user:pass@host/path` → `https://host`) because keeping
    credentials while removing the path would turn a backend rejection into
    a silent credential leak. Non-URL strings pass through unchanged.
    """
    if not value.lower().startswith(("http://", "https://")):
        return value
    scheme, rest = value.split("://", 1)
    # Drop everything after the first '/', '?', or '#' — keeping only the host.
    for delim in ("/", "?", "#"):
        idx = rest.find(delim)
        if idx >= 0:
            rest = rest[:idx]
    # Drop userinfo (user:pass@host → host) — credentials must not be uploaded.
    at_idx = rest.find("@")
    if at_idx >= 0:
        rest = rest[at_idx + 1 :]
    return f"{scheme}://{rest}" if rest else value


def _relativize_path_for_remote(
    value: str,
    *,
    config_dir: Path,
    project: Path | None,
) -> str:
    """Convert an absolute filesystem path into a redacted form safe for
    remote upload.

    Order matters. Try to preserve provenance:
      1. If the path is under `config_dir` (e.g. ~/.claude), return the
         path relative to it (e.g. `skills/clerk-billing/SKILL.md`).
      2. If the path is under `project` (when set), return `project/<rel>`.
      3. Otherwise, fall back to the basename so we never ship absolute
         paths over the wire.

    Non-absolute strings are returned unchanged.
    """
    if not _is_absolute_path(value):
        return value
    # Windows-shaped absolute paths (UNC `\\host\share\...` or drive-letter
    # `C:\...`) can reach this function from cross-platform property values
    # even though V0 parsers themselves are POSIX-only (ADR-0005). On a POSIX
    # runner, `Path("C:\\Users\\foo").name` returns the full string
    # unchanged — `\` is not a separator. Use PureWindowsPath to strip to
    # the basename consistently with how the helper redacts every other
    # unknown-root absolute path. Windows roots never match POSIX
    # config_dir/project, so going straight to the basename fallback is
    # correct.
    if value.startswith("\\\\") or (
        len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")
    ):
        return PureWindowsPath(value).name
    try:
        candidate = Path(value)
    except (TypeError, ValueError):
        return Path(value).name
    try:
        return candidate.relative_to(config_dir).as_posix()
    except ValueError:
        pass
    if project is not None:
        try:
            relative = candidate.relative_to(project).as_posix()
            return f"project/{relative}"
        except ValueError:
            pass
    return candidate.name


def _redact_json_node_for_remote(
    node: object,
    *,
    config_dir: Path,
    project: Path | None,
) -> object:
    """Recursively walk a parsed JSON structure, redacting any string leaf
    that is an absolute path or a URL-with-path.

    `openaca:declared_by`, `openaca:source_provenance`, and similar
    JSON-valued properties can embed absolute filesystem paths (e.g.
    `{"kind": "manifest", "path": "/Users/alex/.claude/mcp.json"}`).
    Those strings do not start with `/` at the property-value level, so
    the top-level `_is_absolute_path` check would miss them.
    """
    if isinstance(node, dict):
        return {
            k: _redact_property_value_for_remote(v, config_dir=config_dir, project=project)
            if isinstance(v, str)
            else _redact_json_node_for_remote(v, config_dir=config_dir, project=project)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [
            _redact_property_value_for_remote(item, config_dir=config_dir, project=project)
            if isinstance(item, str)
            else _redact_json_node_for_remote(item, config_dir=config_dir, project=project)
            for item in node
        ]
    return node


def _redact_embedded_unix_paths(
    value: str,
    *,
    config_dir: Path,
    project: Path | None,
) -> str:
    """Redact Unix absolute paths embedded within a larger string.

    Handles values like `Bash(/Users/alice/.claude/skills/deploy/run.sh *)` by
    replacing each embedded path segment with its relativized or basename-only form,
    preserving the surrounding structure (e.g. `Bash(run.sh *)`).
    """

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        return _relativize_path_for_remote(m.group(0), config_dir=config_dir, project=project)

    return _EMBEDDED_UNIX_PATH_RE.sub(_replace, value)


def _redact_embedded_urls_in_string(value: str) -> str:
    """Redact http(s) URLs embedded within a larger string.

    Strips path, query, and fragment from each embedded URL so only
    `scheme://host` remains — e.g. `Bash(curl https://api.example.com/mcp *)`
    becomes `Bash(curl https://api.example.com *)`.  This must run before the
    Unix-path regex so URL path components are not misidentified as Unix paths.
    """
    return _EMBEDDED_URL_RE.sub(lambda m: _redact_url_for_remote(m.group(0)), value)


def _redact_property_value_for_remote(
    value: str,
    *,
    config_dir: Path,
    project: Path | None,
) -> str:
    """Redact a single openaca:* property value or posture-evidence string.

    Handles:
    - Plain absolute paths → relativized form.
    - Plain http(s) URLs with path/query/fragment or userinfo → bare scheme://host.
    - JSON-encoded dicts/lists → recursively redact string leaves that are
      absolute paths or URLs with paths.
    - Strings containing embedded Unix absolute paths (e.g. Bash filter syntax) →
      relativize the embedded path segment, preserve surrounding structure.
    """
    if _is_absolute_path(value):
        return _relativize_path_for_remote(value, config_dir=config_dir, project=project)
    if value.lower().startswith(("http://", "https://")):
        return _redact_url_for_remote(value)
    if value.startswith("{") or value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        redacted = _redact_json_node_for_remote(parsed, config_dir=config_dir, project=project)
        return json.dumps(redacted, sort_keys=True)
    # Redact embedded URLs before checking for Unix paths: URL path components
    # like `//host/path` would otherwise trigger `_EMBEDDED_UNIX_PATH_RE`.
    if _EMBEDDED_URL_RE.search(value):
        value = _redact_embedded_urls_in_string(value)
    if _EMBEDDED_UNIX_PATH_RE.search(value):
        return _redact_embedded_unix_paths(value, config_dir=config_dir, project=project)
    return value


def _is_in_known_root(path: str, *, config_dir: Path, project: Path | None) -> bool:
    """Return True when path falls under config_dir or project.

    Mirrors the root-detection logic in _relativize_path_for_remote so callers
    can detect whether the basename fallback was taken without duplicating the
    try/except chain.
    """
    if not _is_absolute_path(path):
        return True
    if path.startswith("\\\\") or (len(path) >= 3 and path[1] == ":" and path[2] in ("\\", "/")):
        return False
    try:
        candidate = Path(path)
    except (TypeError, ValueError):
        return False
    try:
        candidate.relative_to(config_dir)
        return True
    except ValueError:
        pass
    if project is not None:
        try:
            candidate.relative_to(project)
            return True
        except ValueError:
            pass
    return False


def _redact_bom_ref_path(bom_ref: str, *, config_dir: Path, project: Path | None) -> str:
    """Relativize the source-manifest path portion of an occurrence-key bom-ref.

    Graph occurrence keys have the form `{source_manifest}#{locator}#{what}`.
    When the path normalizer falls back to an absolute path (e.g. a plugin
    installed outside config_dir/project), the bom-ref embeds that absolute path
    and must be relativized before remote upload. Non-absolute bom-refs pass through
    unchanged.

    For out-of-root paths (basename fallback), a stable 8-char SHA-256 digest of
    the original absolute path is appended to keep distinct install locations
    distinct: two plugins at `/rootA/package.json` and `/rootB/package.json` would
    otherwise both reduce to `package.json`, corrupting the dependency graph.
    """
    parts = bom_ref.split("#", 1)
    path_part = parts[0]
    if not _is_absolute_path(path_part):
        return bom_ref
    redacted = _redact_source_path(path_part, config_dir=config_dir, project=project)
    return f"{redacted}#{parts[1]}" if len(parts) > 1 else redacted


def _redact_source_path(path: str, *, config_dir: Path, project: Path | None) -> str:
    """Relativize an absolute source-manifest path, appending a stable 8-char
    digest of the original path for out-of-root locations (basename fallback).

    Shared by the bom-ref path-portion redaction and the `openaca:source_manifest`
    property redaction so they stay byte-identical: graph consumers map findings
    back to nodes by `ref_occurrence_key` (source_manifest + locator + identity),
    NOT the bom-ref, so a redacted `source_manifest` that collapsed two out-of-root
    same-basename manifests to `package.json` would make their occurrence keys
    collide and misattribute findings. Non-absolute paths pass through unchanged."""
    if not _is_absolute_path(path):
        return path
    redacted = _relativize_path_for_remote(path, config_dir=config_dir, project=project)
    if not _is_in_known_root(path, config_dir=config_dir, project=project):
        digest = hashlib.sha256(path.encode()).hexdigest()[:8]
        redacted = f"{redacted}.{digest}"
    return redacted


def _redact_bom_refs_in_bom(bom: JsonObject, *, config_dir: Path, project: Path | None) -> None:
    """In-place redaction of absolute-path bom-refs across the CycloneDX BOM.

    Graph-backed BOMs use node occurrence keys as bom-refs; when the path
    normalizer falls back to an absolute path for an out-of-root plugin
    installPath, that absolute path appears in components[*].bom-ref,
    metadata.component.bom-ref, dependencies[*].ref, and dependencies[*].dependsOn.
    Build a consistent rewrite map and apply it everywhere to preserve graph integrity.
    """
    ref_map: dict[str, str] = {}

    metadata = bom.get("metadata")
    if isinstance(metadata, dict):
        mc = metadata.get("component")
        if isinstance(mc, dict):
            old = mc.get("bom-ref")
            if isinstance(old, str):
                new = _redact_bom_ref_path(old, config_dir=config_dir, project=project)
                if new != old:
                    ref_map[old] = new

    for component in bom.get("components", []) or []:
        if not isinstance(component, dict):
            continue
        old = component.get("bom-ref")
        if isinstance(old, str):
            new = _redact_bom_ref_path(old, config_dir=config_dir, project=project)
            if new != old:
                ref_map[old] = new

    if not ref_map:
        return

    if isinstance(metadata, dict):
        mc = metadata.get("component")
        if isinstance(mc, dict):
            old = mc.get("bom-ref")
            if isinstance(old, str) and old in ref_map:
                mc["bom-ref"] = ref_map[old]

    for component in bom.get("components", []) or []:
        if not isinstance(component, dict):
            continue
        old = component.get("bom-ref")
        if isinstance(old, str) and old in ref_map:
            component["bom-ref"] = ref_map[old]

    for dependency in bom.get("dependencies", []) or []:
        if not isinstance(dependency, dict):
            continue
        old_ref = dependency.get("ref")
        if isinstance(old_ref, str) and old_ref in ref_map:
            dependency["ref"] = ref_map[old_ref]
        depends_on = dependency.get("dependsOn")
        if isinstance(depends_on, list):
            dependency["dependsOn"] = [
                ref_map.get(item, item) if isinstance(item, str) else item for item in depends_on
            ]


def _redact_payload_for_remote(
    payload: JsonObject,
    *,
    config_dir: Path,
    project: Path | None,
) -> None:
    """In-place redaction of absolute filesystem paths inside a Remote
    upload payload. Scope mirrors the backend's `validate_upload_privacy`
    rule (`backend/src/openaca_remote/redaction.py`): scan only the
    CLI-synthesized property/evidence values that the backend will scan,
    not arbitrary pass-through CycloneDX content.

    Handles both plain-string values (e.g. `openaca:source_manifest`) and
    JSON-encoded values (e.g. `openaca:declared_by`, `openaca:source_provenance`)
    that may embed absolute paths inside their fields.

    Also redacts bom-refs: graph-backed BOMs use occurrence keys (which embed
    the source_manifest path) as bom-refs. The path normalizer falls back to
    the absolute path for plugin installPaths outside config_dir/project, so
    those absolute paths appear in bom-ref, dependencies[].ref, and dependsOn[].
    """
    bom = payload.get("bom")
    if isinstance(bom, dict):
        _redact_bom_refs_in_bom(bom, config_dir=config_dir, project=project)
        for component in bom.get("components", []) or []:
            if not isinstance(component, dict):
                continue
            for prop in component.get("properties", []) or []:
                if not isinstance(prop, dict):
                    continue
                prop_name = prop.get("name")
                if not isinstance(prop_name, str) or not prop_name.startswith("openaca:"):
                    continue
                value = prop.get("value")
                if not isinstance(value, str):
                    continue
                # openaca:source_manifest feeds the graph occurrence key, so redact
                # it identically to the bom-ref path portion (relativize + out-of-root
                # digest) — keeping two out-of-root same-basename manifests distinct.
                if prop_name == "openaca:source_manifest":
                    prop["value"] = _redact_source_path(
                        value, config_dir=config_dir, project=project
                    )
                else:
                    prop["value"] = _redact_property_value_for_remote(
                        value, config_dir=config_dir, project=project
                    )

    for finding in payload.get("posture_findings", []) or []:
        if not isinstance(finding, dict):
            continue
        evidence = finding.get("evidence")
        if not isinstance(evidence, dict):
            continue
        for key, value in list(evidence.items()):
            if isinstance(value, str):
                evidence[key] = _redact_property_value_for_remote(
                    value, config_dir=config_dir, project=project
                )
            elif isinstance(value, list):
                evidence[key] = [
                    _redact_property_value_for_remote(item, config_dir=config_dir, project=project)
                    if isinstance(item, str)
                    else item
                    for item in value
                ]

    for finding in payload.get("observations", []) or []:
        if not isinstance(finding, dict):
            continue
        for key in ("evidence", "declared_by"):
            value = finding.get(key)
            if not isinstance(value, dict):
                continue
            for evidence_key, evidence_value in list(value.items()):
                if isinstance(evidence_value, str):
                    value[evidence_key] = _redact_property_value_for_remote(
                        evidence_value, config_dir=config_dir, project=project
                    )
                elif isinstance(evidence_value, list):
                    value[evidence_key] = [
                        _redact_property_value_for_remote(
                            item, config_dir=config_dir, project=project
                        )
                        if isinstance(item, str)
                        else item
                        for item in evidence_value
                    ]


def clear_pending_uploads() -> None:
    """Remove all pending offline-cache files (call when credentials change)."""
    for path in get_pending_dir().glob("pending-bom-*.json"):
        path.unlink(missing_ok=True)


def _replay_pending_uploads(client: RemoteClient, current_asset_id: str) -> None:
    pending_dir = get_pending_dir()
    for path in sorted(pending_dir.glob("pending-bom-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            continue
        if not isinstance(payload, dict):
            path.unlink(missing_ok=True)
            continue
        if payload.get("asset_id") != current_asset_id:
            path.unlink(missing_ok=True)
            continue
        try:
            enforce_remote_upload_contract(payload)
            client.upload_bom(payload)
        except RemoteUploadContractError:
            path.unlink(missing_ok=True)
            continue
        except (RemoteServerError, httpx.TransportError):
            break
        except RemoteClientError as exc:
            raise CollectError(str(exc)) from exc
        path.unlink()


def _write_pending_payload(payload: JsonObject) -> Path:
    enforce_remote_upload_contract(payload)
    pending_dir = get_pending_dir()
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = pending_dir / f"pending-bom-{time.time_ns()}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    os.chmod(path, 0o600)
    return path


def _upload_payload(
    *,
    asset_id: str,
    source: str,
    target_locator: str,
    bom: JsonObject,
    posture_findings: list[JsonObject],
    observations: list[JsonObject],
) -> JsonObject:
    return {
        "asset_id": asset_id,
        "source": source,
        "openaca_version": _openaca_version(),
        "target_locator": target_locator,
        "content_hash": _content_hash(bom),
        "bom": bom,
        "posture_findings": posture_findings,
        "observations": observations,
    }


def _asset_registration_payload() -> JsonObject:
    hostname = socket.gethostname()
    return {
        "asset_type": "endpoint",
        "external_id": hostname,
        "display_name": hostname,
        "metadata": {
            "openaca_version": _openaca_version(),
        },
    }


def _posture_finding_to_payload(finding: PostureFinding) -> JsonObject:
    return {
        "source": finding.source,
        "source_version": finding.source_version,
        "rule_id": finding.rule_id,
        "rule_version": "1",
        "severity": finding.severity.upper(),
        "confidence": finding.confidence,
        "scope": _posture_scope(finding),
        "component_identity": _posture_component_identity(finding),
        "summary": finding.title,
        "fix": finding.remediation,
        "evidence": _posture_evidence(finding),
    }


def _observation_to_payload(finding: ObservationFinding) -> JsonObject:
    return {
        "source": finding.source,
        "source_version": finding.source_version,
        "observation_id": finding.observation_id,
        "severity": finding.severity.upper(),
        "confidence": finding.confidence,
        "component_identity": _observation_component_identity(finding),
        "subject_coordinate": finding.subject_coordinate,
        "summary": finding.title,
        "fix": finding.remediation,
        "evidence": finding.evidence,
        "categories": finding.categories,
        "declared_by": finding.declared_by or {},
    }


def _observation_component_identity(finding: ObservationFinding) -> str:
    identity = finding.component.get("identity")
    return identity if isinstance(identity, str) and identity else finding.subject_coordinate


def _posture_scope(finding: PostureFinding) -> str:
    if finding.rule_id == "openaca-posture-api-endpoint-override":
        return "asset"
    return "component"


def _posture_component_identity(finding: PostureFinding) -> str | None:
    if _posture_scope(finding) != "component":
        return None
    identity = finding.component.get("identity")
    if isinstance(identity, str) and identity:
        return identity
    for item in reversed(finding.component_path):
        name = item.get("name")
        if isinstance(name, str) and name:
            return name.split(" @ ", maxsplit=1)[0]
    name = finding.component.get("name")
    if isinstance(name, str) and name:
        return name.split(" @ ", maxsplit=1)[0]
    return None


def _posture_evidence(finding: PostureFinding) -> JsonObject:
    manifest_path = _manifest_path(finding)
    if finding.rule_id == "openaca-posture-skill-executable-tool":
        return {
            "allowed_tools": finding.evidence.get("allowed_tools", []),
            "manifest_path": manifest_path,
        }
    if finding.rule_id == "openaca-posture-insecure-transport":
        return {"transport": "http", "manifest_path": manifest_path}
    if finding.rule_id == "openaca-posture-mutable-install-reference":
        return {"install_ref": _install_ref(finding), "manifest_path": manifest_path}
    if finding.rule_id == "openaca-posture-api-endpoint-override":
        return {"override_present": True, "manifest_path": manifest_path}
    if finding.rule_id == "openaca-posture-mcp-auto-approve":
        return {"auto_approve": True, "manifest_path": manifest_path}
    return {**finding.evidence, "manifest_path": manifest_path}


def _manifest_path(finding: PostureFinding) -> str:
    if finding.declared_by is None:
        return ""
    path = finding.declared_by.get("path")
    if not isinstance(path, str) or not path:
        return ""
    if path.startswith(("/", "~")) or ":\\" in path:
        return Path(path).name
    return path


def _install_ref(finding: PostureFinding) -> str:
    name = finding.component.get("name")
    if isinstance(name, str) and "(" in name and name.endswith(")"):
        return name.rsplit("(", maxsplit=1)[1][:-1]
    return _posture_component_identity(finding) or ""


def _content_hash(bom: JsonObject) -> str:
    payload = json.dumps(bom, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _is_plugin_ref(ref: ComponentRef) -> bool:
    return (ref.extra or {}).get("component_type") == "plugin"


def _openaca_version() -> str:
    try:
        return version("openaca")
    except PackageNotFoundError:
        return "unknown"


def _prepare_remote_bom(bom: JsonObject) -> JsonObject:
    components = bom.get("components")
    if not isinstance(components, list):
        return bom
    prepared_components = [
        _prepare_remote_component(component) if isinstance(component, dict) else component
        for component in components
    ]
    return {**bom, "components": prepared_components}


def _prepare_remote_component(component: JsonObject) -> JsonObject:
    properties = component.get("properties")
    if not isinstance(properties, list):
        return component
    props_by_name = {
        prop.get("name"): prop.get("value") for prop in properties if isinstance(prop, dict)
    }
    component_name = component.get("name")
    component_purl = component.get("purl")
    if _is_binary_mcp_component(props_by_name, component_name, component_purl):
        prepared_props = [
            _trim_binary_install_source(prop) if isinstance(prop, dict) else prop
            for prop in properties
        ]
        return {**component, "properties": prepared_props}
    if _is_package_mcp_component(props_by_name):
        prepared_props = [
            _trim_package_install_source(prop) if isinstance(prop, dict) else prop
            for prop in properties
        ]
        return {**component, "properties": prepared_props}
    if _is_pinned_mcp_component(props_by_name):
        prepared_props = [
            _trim_pinned_install_source(prop, component, props_by_name)
            if isinstance(prop, dict)
            else prop
            for prop in properties
        ]
        return {**component, "properties": prepared_props}
    return component


def _is_binary_mcp_component(
    props_by_name: dict[Any, Any],
    component_name: object,
    component_purl: object = None,
) -> bool:
    identity = props_by_name.get("openaca:identity")
    legacy_name = component_name if isinstance(component_name, str) else ""
    if (
        isinstance(identity, str) and identity.startswith(("mcp-stdio/binary:", "mcp-stdio/local:"))
    ) or legacy_name.startswith(("mcp-stdio/binary:", "mcp-stdio/local:")):
        return True
    # ADR-0029: binary/local MCPs now carry mcp-server/<name> identity.
    # Distinguish from package-backed MCPs by absence of PURL and from
    # package-manager-launched MCPs (npx/uvx) by the install_source first token.
    if not (
        props_by_name.get("openaca:component_type") == "mcp_server"
        and isinstance(identity, str)
        and identity.startswith("mcp-server/")
        and not component_purl
        and "openaca:transport" not in props_by_name
        and "openaca:install_source" in props_by_name
    ):
        return False
    install_source = props_by_name.get("openaca:install_source", "")
    return not is_mcp_package_launch_install_source(install_source)


def _is_package_mcp_component(props_by_name: dict[Any, Any]) -> bool:
    identity = props_by_name.get("openaca:identity")
    # Package-backed MCPs carry mcp-server/<name> graph identity. Distinguish
    # them from binary MCPs by npx/uvx first token in install_source.
    if not (
        props_by_name.get("openaca:component_type") == "mcp_server"
        and isinstance(identity, str)
        and identity.startswith("mcp-server/")
        and "openaca:transport" not in props_by_name
        and "openaca:install_source" in props_by_name
    ):
        return False
    install_source = props_by_name.get("openaca:install_source", "")
    return safe_unpinned_mcp_install_source(install_source=install_source) is not None


def _trim_binary_install_source(prop: JsonObject) -> JsonObject:
    if prop.get("name") != "openaca:install_source":
        return prop
    value = prop.get("value")
    if not isinstance(value, str):
        return prop
    command = value.split(maxsplit=1)[0] if value.strip() else value
    return {**prop, "value": command}


def _trim_package_install_source(prop: JsonObject) -> JsonObject:
    if prop.get("name") != "openaca:install_source":
        return prop
    value = prop.get("value")
    safe_source = safe_unpinned_mcp_install_source(install_source=value)
    if safe_source is None:
        return prop
    return {**prop, "value": safe_source}


def _is_pinned_mcp_component(props_by_name: dict[Any, Any]) -> bool:
    return (
        props_by_name.get("openaca:component_type") == "mcp_server"
        and "openaca:install_source" in props_by_name
    )


def _trim_pinned_install_source(
    prop: JsonObject, component: JsonObject, props_by_name: dict[Any, Any]
) -> JsonObject:
    if prop.get("name") != "openaca:install_source":
        return prop
    value = prop.get("value")
    if not isinstance(value, str):
        return prop
    # First token is always the launcher (npx/uvx), never a flag.
    launcher = value.split(maxsplit=1)[0] if value.strip() else ""
    if not launcher:
        return prop
    purl = component.get("purl")
    name = component.get("name")
    version = component.get("version")
    safe_source = safe_pinned_mcp_install_source(
        launcher=launcher,
        purl=purl,
        name=name,
        version=version,
        install_source=value,
        source_subdirectory=props_by_name.get("openaca:source_subdirectory"),
    )
    if safe_source is not None:
        return {**prop, "value": safe_source}
    # Fallback: keep first two raw tokens when no PURL metadata is available.
    parts = value.split(maxsplit=2)
    if len(parts) <= 2:
        return prop
    return {**prop, "value": " ".join(parts[:2])}
