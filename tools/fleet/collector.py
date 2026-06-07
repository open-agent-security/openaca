from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx

from tools.bom import build_agent_bom
from tools.component_ref import ComponentRef, safe_pinned_mcp_install_source
from tools.fleet.client import BomUploadResult, FleetClient, FleetClientError, FleetServerError
from tools.fleet.config import FleetConfig, get_config_path, load_fleet_config, save_fleet_config
from tools.fleet.upload_contract import (
    FleetUploadContractError,
    enforce_fleet_upload_contract,
)
from tools.parsers.claude_install import parse_install
from tools.posture import (
    PostureFinding,
    collect_endpoint_mcp_manifests,
    collect_endpoint_settings_manifests,
    run_posture_rules,
)

JsonObject = dict[str, Any]
TARGET_LOCATOR_ENDPOINT = "endpoint:user-scope"


@dataclass(frozen=True)
class EndpointCollection:
    bom: JsonObject
    posture_findings: list[JsonObject]
    component_count: int


class CollectError(Exception):
    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def get_pending_dir() -> Path:
    return Path.home() / ".local" / "state" / "openaca"


def build_endpoint_collection(config_dir: Path, project: Path | None) -> EndpointCollection:
    refs, _warnings = parse_install(
        install_root=config_dir,
        project_root=project,
        mode="endpoint",
        include_transitive=True,
    )
    bom = _prepare_fleet_bom(
        build_agent_bom(
            refs,
            target_type="endpoint",
            target=TARGET_LOCATOR_ENDPOINT,
            source_unit_count=sum(1 for ref in refs if _is_plugin_ref(ref)),
            source_unit_label="active plugin",
        ).to_cyclonedx()
    )
    mcp_manifests = collect_endpoint_mcp_manifests(config_dir, project, refs)
    settings_manifests = collect_endpoint_settings_manifests(config_dir, project)
    findings = list(run_posture_rules(refs, mcp_manifests, settings_manifests))
    posture_findings = [_posture_finding_to_payload(f) for f in findings]
    # Posture rules emit `component_identity` as the leaf name (e.g. `github`)
    # because that's what their `component_path` carries. The backend joins
    # findings to BomComponent by the full `openaca:identity` (e.g.
    # `claude-plugin/claude-plugins-official/github`), so we rewrite each
    # finding's identity to match the BOM's view before upload. Without this,
    # the backend's ingest rejects with "posture finding component_identity
    # did not match BOM component" for any plugin-rooted finding.
    _align_posture_identities_to_bom(posture_findings, findings, bom)
    return EndpointCollection(
        bom=bom,
        posture_findings=posture_findings,
        component_count=len(bom.get("components") or []),
    )


def collect_endpoint(
    *,
    config_dir: Path,
    project: Path | None,
    quiet: bool = False,
    allow_offline_cache: bool = False,
) -> BomUploadResult:
    config_path = get_config_path()
    config = load_fleet_config(config_path)
    if config.token is None:
        raise CollectError("Fleet is not configured; run openaca fleet configure --token <TOKEN>")

    client = FleetClient(api_url=config.api_url, token=config.token)
    if config.asset_id is not None:
        _replay_pending_uploads(client, config.asset_id)

    collection = build_endpoint_collection(config_dir=config_dir, project=project)
    asset_id = config.asset_id
    if asset_id is None:
        try:
            registered = client.register_asset(_asset_registration_payload())
        except (FleetServerError, httpx.TransportError) as exc:
            exit_code = 0 if quiet or allow_offline_cache else 2
            raise CollectError("asset registration failed (network)", exit_code=exit_code) from exc
        except FleetClientError as exc:
            raise CollectError(str(exc)) from exc
        asset_id = registered.asset_id
        config = FleetConfig(api_url=config.api_url, token=config.token, asset_id=asset_id)
        save_fleet_config(config, config_path)

    payload = _upload_payload(
        asset_id=asset_id,
        source="endpoint",
        target_locator=TARGET_LOCATOR_ENDPOINT,
        bom=collection.bom,
        posture_findings=collection.posture_findings,
    )
    # ADR 0003 (Fleet redaction contract): the OSS BOM can carry absolute
    # filesystem paths because the OSS CLI runs on the user's own machine;
    # those paths are useful for offline analysis. Fleet uploads cross a
    # SaaS network boundary into a multi-tenant store, so we redact at the
    # upload boundary. Relativize against config_dir / project first to
    # preserve component provenance (each skill still identifies as a
    # distinct relative path), and fall back to basename only when no known
    # root applies.
    _redact_payload_for_fleet(payload, config_dir=config_dir, project=project)
    payload["content_hash"] = _content_hash(payload["bom"])
    enforce_fleet_upload_contract(payload)
    try:
        return client.upload_bom(payload)
    except (FleetServerError, httpx.TransportError) as exc:
        path = _write_pending_payload(payload)
        exit_code = 0 if quiet or allow_offline_cache else 2
        raise CollectError(
            f"saved to {path}; upload failed (network)", exit_code=exit_code
        ) from exc
    except FleetClientError as exc:
        raise CollectError(str(exc)) from exc


def _is_absolute_path(value: str) -> bool:
    if value.startswith("/"):
        return True
    if value.startswith("\\\\"):
        return True
    return len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")


def _redact_url_for_fleet(value: str) -> str:
    """Strip path + query from an http(s) URL so only `scheme://host` remains.

    The backend's redaction rule rejects URLs with paths or queries in
    `openaca:*` property values (see `redaction._validate_string`). Bare
    `https://api.example.com` is fine; `https://api.example.com/mcp/` is
    not. Non-URL strings pass through unchanged.
    """
    if not (value.startswith("http://") or value.startswith("https://")):
        return value
    scheme, rest = value.split("://", 1)
    # Drop everything after the first '/', '?', or '#' — keeping only the host.
    for delim in ("/", "?", "#"):
        idx = rest.find(delim)
        if idx >= 0:
            rest = rest[:idx]
    return f"{scheme}://{rest}" if rest else value


def _relativize_path_for_fleet(
    value: str,
    *,
    config_dir: Path,
    project: Path | None,
) -> str:
    """Convert an absolute filesystem path into a redacted form safe for
    Fleet upload.

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


def _redact_json_structure(
    value: Any,
    *,
    config_dir: Path,
    project: Path | None,
) -> Any:
    """Recursively redact absolute paths and URL paths inside a deserialized
    JSON structure. Used to scrub embedded paths in JSON-valued ``openaca:*``
    BOM properties such as ``openaca:declared_by``."""
    if isinstance(value, str):
        if _is_absolute_path(value):
            return _relativize_path_for_fleet(value, config_dir=config_dir, project=project)
        if value.startswith("http://") or value.startswith("https://"):
            return _redact_url_for_fleet(value)
        return value
    if isinstance(value, dict):
        return {
            k: _redact_json_structure(v, config_dir=config_dir, project=project)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_json_structure(v, config_dir=config_dir, project=project) for v in value]
    return value


def _redact_payload_for_fleet(
    payload: JsonObject,
    *,
    config_dir: Path,
    project: Path | None,
) -> None:
    """In-place redaction of absolute filesystem paths inside a Fleet
    upload payload. Scope mirrors the backend's `validate_upload_privacy`
    rule (`backend/src/openaca_fleet/redaction.py`): scan only the
    CLI-synthesized property/evidence values that the backend will scan,
    not arbitrary pass-through CycloneDX content.
    """
    bom = payload.get("bom")
    if isinstance(bom, dict):
        for component in bom.get("components", []) or []:
            if not isinstance(component, dict):
                continue
            for prop in component.get("properties", []) or []:
                if not isinstance(prop, dict):
                    continue
                name = prop.get("name")
                if not isinstance(name, str) or not name.startswith("openaca:"):
                    continue
                value = prop.get("value")
                if not isinstance(value, str):
                    continue
                if _is_absolute_path(value):
                    prop["value"] = _relativize_path_for_fleet(
                        value, config_dir=config_dir, project=project
                    )
                elif value.startswith("http://") or value.startswith("https://"):
                    prop["value"] = _redact_url_for_fleet(value)
                elif value.startswith(("{", "[")):
                    try:
                        parsed = json.loads(value)
                    except json.JSONDecodeError:
                        pass
                    else:
                        redacted = _redact_json_structure(
                            parsed, config_dir=config_dir, project=project
                        )
                        if redacted != parsed:
                            prop["value"] = json.dumps(redacted, sort_keys=True)

    for finding in payload.get("posture_findings", []) or []:
        if not isinstance(finding, dict):
            continue
        evidence = finding.get("evidence")
        if not isinstance(evidence, dict):
            continue
        for key, value in list(evidence.items()):
            if not isinstance(value, str):
                continue
            if _is_absolute_path(value):
                evidence[key] = _relativize_path_for_fleet(
                    value, config_dir=config_dir, project=project
                )
            elif value.startswith("http://") or value.startswith("https://"):
                evidence[key] = _redact_url_for_fleet(value)


def clear_pending_uploads() -> None:
    """Remove all pending offline-cache files (call when credentials change)."""
    for path in get_pending_dir().glob("pending-bom-*.json"):
        path.unlink(missing_ok=True)


def _replay_pending_uploads(client: FleetClient, current_asset_id: str) -> None:
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
            enforce_fleet_upload_contract(payload)
            client.upload_bom(payload)
        except FleetUploadContractError:
            path.unlink(missing_ok=True)
            continue
        except (FleetServerError, httpx.TransportError):
            break
        except FleetClientError as exc:
            raise CollectError(str(exc)) from exc
        path.unlink()


def _write_pending_payload(payload: JsonObject) -> Path:
    enforce_fleet_upload_contract(payload)
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
) -> JsonObject:
    return {
        "asset_id": asset_id,
        "source": source,
        "openaca_version": _openaca_version(),
        "target_locator": target_locator,
        "content_hash": _content_hash(bom),
        "bom": bom,
        "posture_findings": posture_findings,
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


def _align_posture_identities_to_bom(
    posture_payloads: list[JsonObject],
    findings: list[PostureFinding],
    bom: JsonObject,
) -> None:
    """Rewrite each posture payload's `component_identity` from the leaf
    name the rule emitted (e.g. `github`, `code-review`) to the full BOM
    `openaca:identity` (e.g. `claude-plugin/claude-plugins-official/github`),
    so the backend's ingest can join on `BomComponent.identity`.

    Some leaf names appear under multiple component types in the BOM
    (`code-review` is both a plugin and a command, for instance). We
    disambiguate using the source PostureFinding's `component["type"]`
    when available. If lookup fails — truly novel finding shape, or BOM
    drift — we leave the original value so the backend's existing error
    message still surfaces the problem.
    """
    by_key: dict[tuple[str, str], str] = {}
    for comp in bom.get("components", []) or []:
        if not isinstance(comp, dict):
            continue
        name = comp.get("name")
        ctype: str | None = None
        identity: str | None = None
        for prop in comp.get("properties", []) or []:
            if not isinstance(prop, dict):
                continue
            pname = prop.get("name")
            pvalue = prop.get("value")
            if pname == "openaca:component_type" and isinstance(pvalue, str):
                ctype = pvalue
            elif pname == "openaca:identity" and isinstance(pvalue, str):
                identity = pvalue
        if isinstance(name, str) and ctype is not None and identity is not None:
            key = (ctype, name)
            if key not in by_key:
                by_key[key] = identity
            elif by_key[key] != identity:
                by_key[key] = ""  # ambiguous: same (type, name), different identities

    for payload, finding in zip(posture_payloads, findings, strict=True):
        current = payload.get("component_identity")
        if not isinstance(current, str):
            continue
        ctype = finding.component.get("type")
        if not isinstance(ctype, str):
            continue
        full_identity = by_key.get((ctype, current))
        if full_identity:
            payload["component_identity"] = full_identity


def _posture_finding_to_payload(finding: PostureFinding) -> JsonObject:
    return {
        "rule_id": finding.rule_id,
        "rule_version": "1",
        "severity": finding.severity.upper(),
        "scope": _posture_scope(finding),
        "component_identity": _posture_component_identity(finding),
        "summary": finding.title,
        "fix": finding.remediation,
        "evidence": _posture_evidence(finding),
    }


def _posture_scope(finding: PostureFinding) -> str:
    if finding.rule_id == "openaca-posture-api-endpoint-override":
        return "asset"
    return "component"


def _posture_component_identity(finding: PostureFinding) -> str | None:
    if _posture_scope(finding) != "component":
        return None
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
    if finding.rule_id == "openaca-posture-insecure-transport":
        return {"transport": "http", "manifest_path": manifest_path}
    if finding.rule_id == "openaca-posture-mutable-install-reference":
        return {"install_ref": _install_ref(finding), "manifest_path": manifest_path}
    if finding.rule_id == "openaca-posture-api-endpoint-override":
        return {"override_present": True, "manifest_path": manifest_path}
    if finding.rule_id == "openaca-posture-mcp-auto-approve":
        return {"auto_approve": True, "manifest_path": manifest_path}
    return {"manifest_path": manifest_path}


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


def _prepare_fleet_bom(bom: JsonObject) -> JsonObject:
    components = bom.get("components")
    if not isinstance(components, list):
        return bom
    prepared_components = [
        _prepare_fleet_component(component) if isinstance(component, dict) else component
        for component in components
    ]
    return {**bom, "components": prepared_components}


def _prepare_fleet_component(component: JsonObject) -> JsonObject:
    properties = component.get("properties")
    if not isinstance(properties, list):
        return component
    props_by_name = {
        prop.get("name"): prop.get("value") for prop in properties if isinstance(prop, dict)
    }
    if _is_binary_mcp_component(props_by_name):
        prepared_props = [
            _trim_binary_install_source(prop) if isinstance(prop, dict) else prop
            for prop in properties
        ]
        return {**component, "properties": prepared_props}
    if _is_package_mcp_component(props_by_name):
        prepared_props = [
            _trim_package_install_source(prop, props_by_name) if isinstance(prop, dict) else prop
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


def _is_binary_mcp_component(props_by_name: dict[Any, Any]) -> bool:
    identity = props_by_name.get("openaca:identity")
    return isinstance(identity, str) and identity.startswith(
        ("mcp-stdio/binary:", "mcp-stdio/local:")
    )


def _is_package_mcp_component(props_by_name: dict[Any, Any]) -> bool:
    identity = props_by_name.get("openaca:identity")
    return isinstance(identity, str) and (
        identity.startswith("mcp-stdio/npx-unpinned:")
        or identity.startswith("mcp-stdio/uvx-unpinned:")
    )


def _trim_binary_install_source(prop: JsonObject) -> JsonObject:
    if prop.get("name") != "openaca:install_source":
        return prop
    value = prop.get("value")
    if not isinstance(value, str):
        return prop
    command = value.split(maxsplit=1)[0] if value.strip() else value
    return {**prop, "value": command}


def _trim_package_install_source(prop: JsonObject, props_by_name: dict[Any, Any]) -> JsonObject:
    if prop.get("name") != "openaca:install_source":
        return prop
    identity = props_by_name.get("openaca:identity")
    if not isinstance(identity, str):
        return prop
    # Reconstruct from identity rather than splitting argv, so flags like
    # `-y` that precede the package name don't interfere.
    if identity.startswith("mcp-stdio/npx-unpinned:"):
        package = identity[len("mcp-stdio/npx-unpinned:") :]
        return {**prop, "value": f"npx {package}"}
    if identity.startswith("mcp-stdio/uvx-unpinned:"):
        package = identity[len("mcp-stdio/uvx-unpinned:") :]
        return {**prop, "value": f"uvx {package}"}
    return prop


def _is_pinned_mcp_component(props_by_name: dict[Any, Any]) -> bool:
    return (
        props_by_name.get("openaca:component_type") == "mcp_server"
        and "openaca:identity" not in props_by_name
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
