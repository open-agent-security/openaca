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
            _trim_pinned_install_source(prop, component) if isinstance(prop, dict) else prop
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


def _trim_pinned_install_source(prop: JsonObject, component: JsonObject) -> JsonObject:
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
    if isinstance(purl, str) and isinstance(name, str) and isinstance(version, str):
        if purl.startswith("pkg:npm/"):
            return {**prop, "value": f"{launcher} {name}@{version}"}
        if purl.startswith("pkg:pypi/"):
            return {**prop, "value": f"{launcher} {name}=={version}"}
        if purl.startswith("pkg:docker/"):
            return {**prop, "value": f"{launcher} {name}:{version}"}
    # Fallback: keep first two raw tokens when no PURL metadata is available.
    parts = value.split(maxsplit=2)
    if len(parts) <= 2:
        return prop
    return {**prop, "value": " ".join(parts[:2])}
