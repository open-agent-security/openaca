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
from tools.component_ref import ComponentRef
from tools.fleet.client import BomUploadResult, FleetClient, FleetServerError
from tools.fleet.config import FleetConfig, get_config_path, load_fleet_config, save_fleet_config
from tools.fleet.redaction import RedactionError, validate_fleet_upload_payload
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
    bom = _relativize_bom_paths(
        build_agent_bom(
            refs,
            target_type="endpoint",
            target=TARGET_LOCATOR_ENDPOINT,
            source_unit_count=sum(1 for ref in refs if _is_plugin_ref(ref)),
            source_unit_label="active plugin",
        ).to_cyclonedx(),
        config_dir,
    )
    mcp_manifests = collect_endpoint_mcp_manifests(config_dir, project, refs)
    settings_manifests = collect_endpoint_settings_manifests(config_dir, project)
    posture_findings = [
        _posture_finding_to_payload(finding)
        for finding in run_posture_rules(refs, mcp_manifests, settings_manifests)
    ]
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
        _replay_pending_uploads(client)

    collection = build_endpoint_collection(config_dir=config_dir, project=project)
    asset_id = config.asset_id
    if asset_id is None:
        registered = client.register_asset(_asset_registration_payload())
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
    validate_fleet_upload_payload(payload)
    try:
        return client.upload_bom(payload)
    except (FleetServerError, httpx.TransportError) as exc:
        path = _write_pending_payload(payload)
        exit_code = 0 if quiet or allow_offline_cache else 2
        raise CollectError(
            f"saved to {path}; upload failed (network)", exit_code=exit_code
        ) from exc


def upload_bom_file(path: Path) -> BomUploadResult:
    config = load_fleet_config(get_config_path())
    if config.token is None:
        raise CollectError("Fleet is not configured; run openaca fleet configure --token <TOKEN>")
    if config.asset_id is None:
        raise CollectError("No asset configured yet. Run openaca fleet collect endpoint first.")
    try:
        bom = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectError(f"failed to read BOM from {path}") from exc
    if not isinstance(bom, dict):
        raise CollectError("BOM file must contain a JSON object")
    bom = _relativize_bom_paths(bom, _default_config_dir())
    payload = _upload_payload(
        asset_id=config.asset_id,
        source="manual",
        target_locator="manual",
        bom=bom,
        posture_findings=[],
    )
    try:
        validate_fleet_upload_payload(payload)
    except RedactionError as exc:
        raise CollectError(f"BOM contains redaction-blocked content: {exc}") from exc
    client = FleetClient(api_url=config.api_url, token=config.token)
    return client.upload_bom(payload)


def clear_pending_uploads() -> None:
    """Remove all pending offline-cache files (call when credentials change)."""
    for path in get_pending_dir().glob("pending-bom-*.json"):
        path.unlink(missing_ok=True)


def _replay_pending_uploads(client: FleetClient) -> None:
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
        try:
            validate_fleet_upload_payload(payload)
            client.upload_bom(payload)
        except RedactionError:
            path.unlink(missing_ok=True)
            continue
        except (FleetServerError, httpx.TransportError):
            break
        path.unlink()


def _write_pending_payload(payload: JsonObject) -> Path:
    validate_fleet_upload_payload(payload)
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


def _default_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"


def _relativize_bom_paths(bom: JsonObject, config_dir: Path) -> JsonObject:
    """Strip home-directory prefixes from openaca:source_manifest and
    openaca:declared_by BOM properties before upload validation."""
    components = bom.get("components")
    if not isinstance(components, list):
        return bom
    sanitized_components = []
    for component in components:
        if not isinstance(component, dict):
            sanitized_components.append(component)
            continue
        props = component.get("properties")
        if not isinstance(props, list):
            sanitized_components.append(component)
            continue
        new_props = []
        for prop in props:
            if not isinstance(prop, dict):
                new_props.append(prop)
                continue
            name = prop.get("name")
            value = prop.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                new_props.append(prop)
                continue
            if name == "openaca:source_manifest":
                new_props.append({"name": name, "value": _relativize_path(value, config_dir)})
            elif name == "openaca:declared_by":
                new_props.append(
                    {"name": name, "value": _relativize_declared_by(value, config_dir)}
                )
            elif name == "openaca:source_provenance":
                new_props.append(
                    {"name": name, "value": _relativize_source_provenance(value, config_dir)}
                )
            else:
                new_props.append(prop)
        sanitized_components.append({**component, "properties": new_props})
    return {**bom, "components": sanitized_components}


def _relativize_path(path: str, config_dir: Path) -> str:
    if not path or not Path(path).is_absolute():
        return path
    try:
        return str(Path(path).relative_to(config_dir))
    except ValueError:
        return Path(path).name


def _relativize_declared_by(json_value: str, config_dir: Path) -> str:
    try:
        obj = json.loads(json_value)
    except (ValueError, TypeError):
        return json_value
    if not isinstance(obj, dict):
        return json_value
    raw_path = obj.get("path")
    if not isinstance(raw_path, str):
        return json_value
    return json.dumps({**obj, "path": _relativize_path(raw_path, config_dir)}, sort_keys=True)


def _relativize_source_provenance(json_value: str, config_dir: Path) -> str:
    try:
        obj = json.loads(json_value)
    except (ValueError, TypeError):
        return json_value
    if not isinstance(obj, dict):
        return json_value
    updated = dict(obj)
    for field in ("lockfile_path", "resolved_path"):
        raw = updated.get(field)
        if isinstance(raw, str) and raw:
            updated[field] = _relativize_path(raw, config_dir)
    return json.dumps(updated, sort_keys=True)
