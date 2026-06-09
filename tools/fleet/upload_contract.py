from __future__ import annotations

import re
from typing import Any

from tools.identity import is_mcp_package_launch_install_source, safe_unpinned_mcp_install_source


class FleetUploadContractError(Exception):
    pass


_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(ghp_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]+|"
    r"sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{20,}|ot_[A-Za-z0-9_]{20,})\b"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:token|api[_-]?key|apikey|secret|password|authorization|auth[_-]?token)="
)
_FORBIDDEN_NAME_RE = re.compile(
    r"(?i)(?:^|[:_.-])("
    r"env|environment|token|api[_-]?key|apikey|secret|password|authorization|"
    r"auth[_-]?token|command[_-]?args|argv|shell[_-]?argv|"
    r"raw[_-]?config|config[_-]?body|settings[_-]?json|mcp[_-]?json|plugin[_-]?json"
    r")(?:$|[:_.-])"
)


def enforce_fleet_upload_contract(payload: dict[str, Any]) -> None:
    """Enforce the narrow Fleet upload hygiene contract.

    Fleet uploads are endpoint inventory. They may include paths, component
    identities, install references, and posture evidence, but must not include
    raw config bodies, env values, detected secrets, or full shell argv.

    Also mirrors the backend's `validate_upload_privacy` rule
    (`backend/src/openaca_fleet/redaction.py`) so absolute paths in
    CLI-synthesized `openaca:*` property values or posture evidence fail
    locally with a clear contract error instead of round-tripping to the
    server. The collector applies `_redact_payload_for_fleet` before
    calling this enforcer, so this check is defense-in-depth.
    """
    _validate_value(payload, "$")
    _validate_no_absolute_paths(payload)


def _validate_value(value: Any, path: str) -> None:
    if isinstance(value, dict):
        _validate_mapping(value, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_value(item, f"{path}[{index}]")
    elif isinstance(value, str):
        _validate_string(value, path)


def _validate_mapping(value: dict[Any, Any], path: str) -> None:
    _validate_component_properties(value, path)
    property_name = value.get("name")
    if isinstance(property_name, str) and "value" in value and _is_forbidden_name(property_name):
        raise FleetUploadContractError(f"{path}.value is forbidden by Fleet upload contract")

    for key, item in value.items():
        item_path = f"{path}.{key}" if isinstance(key, str) else f"{path}[{key!r}]"
        if isinstance(key, str) and _is_forbidden_name(key):
            raise FleetUploadContractError(f"{item_path} is forbidden by Fleet upload contract")
        _validate_value(item, item_path)


def _validate_string(value: str, path: str) -> None:
    if _SECRET_VALUE_RE.search(value) or _SECRET_ASSIGNMENT_RE.search(value):
        raise FleetUploadContractError(f"{path} contains a blocked value")


def _is_absolute_path(value: str) -> bool:
    if value.startswith("/"):
        return True
    if value.startswith("\\\\"):
        return True
    return len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")


def _validate_no_absolute_paths(payload: dict[str, Any]) -> None:
    """Mirror the backend's path scope so we fail locally with the same
    message instead of round-tripping to the server.

    Scope: CLI-synthesized `openaca:*` component property values and any
    string under `posture_findings[*].evidence`. Pass-through CycloneDX
    content is not scanned (consistent with backend behavior).
    """
    bom = payload.get("bom")
    if isinstance(bom, dict):
        for c_idx, component in enumerate(bom.get("components", []) or []):
            if not isinstance(component, dict):
                continue
            for p_idx, prop in enumerate(component.get("properties", []) or []):
                if not isinstance(prop, dict):
                    continue
                name = prop.get("name")
                if not isinstance(name, str) or not name.startswith("openaca:"):
                    continue
                value = prop.get("value")
                if not isinstance(value, str):
                    continue
                location = f"$.bom.components[{c_idx}].properties[{p_idx}].value"
                if _is_absolute_path(value):
                    raise FleetUploadContractError(f"{location} is an absolute path ({name!r})")
                if _is_url_with_path_or_query(value):
                    raise FleetUploadContractError(
                        f"{location} is a URL with a path or query ({name!r})"
                    )

    for f_idx, finding in enumerate(payload.get("posture_findings", []) or []):
        if not isinstance(finding, dict):
            continue
        evidence = finding.get("evidence")
        if not isinstance(evidence, dict):
            continue
        for key, value in evidence.items():
            if not isinstance(value, str):
                continue
            location = f"$.posture_findings[{f_idx}].evidence.{key}"
            if _is_absolute_path(value):
                raise FleetUploadContractError(f"{location} is an absolute path")
            if _is_url_with_path_or_query(value):
                raise FleetUploadContractError(f"{location} is a URL with a path or query")


def _is_url_with_path_or_query(value: str) -> bool:
    """Mirror the backend rule: an http(s) URL with anything after the host
    (path, query, fragment) leaks endpoint specifics.
    """
    if not value.lower().startswith(("http://", "https://")):
        return False
    _, rest = value.split("://", 1)
    return any(d in rest for d in ("/", "?", "#"))


def _is_forbidden_name(value: str) -> bool:
    return bool(_FORBIDDEN_NAME_RE.search(value))


def _validate_component_properties(value: dict[Any, Any], path: str) -> None:
    properties = value.get("properties")
    if not isinstance(properties, list):
        return
    props_by_name: dict[str, tuple[Any, int]] = {}
    for index, prop in enumerate(properties):
        if not isinstance(prop, dict):
            continue
        name = prop.get("name")
        if isinstance(name, str):
            props_by_name[name] = (prop.get("value"), index)
    identity = props_by_name.get("openaca:identity", (None, -1))[0]
    component_type = props_by_name.get("openaca:component_type", (None, -1))[0]
    install_source, install_source_index = props_by_name.get("openaca:install_source", (None, -1))
    component_purl = value.get("purl")
    graph_mcp_without_purl = (
        component_type == "mcp_server"
        and isinstance(identity, str)
        and identity.startswith("mcp-server/")
        and not component_purl
        and "openaca:transport" not in props_by_name
        and isinstance(install_source, str)
        and install_source.strip()
    )
    is_package = safe_unpinned_mcp_install_source(install_source=install_source) is not None
    is_binary = isinstance(identity, str) and identity.startswith(
        ("mcp-stdio/binary:", "mcp-stdio/local:")
    )
    if not is_binary and graph_mcp_without_purl and not is_package:
        is_binary = not is_mcp_package_launch_install_source(install_source)
    is_pinned_mcp = component_type == "mcp_server" and (
        identity is None
        or (
            isinstance(identity, str)
            and identity.startswith("mcp-server/")
            and bool(component_purl)
            and "openaca:transport" not in props_by_name
        )
    )
    if not is_binary and not is_package and not is_pinned_mcp:
        return
    if not isinstance(install_source, str) or not install_source.strip():
        return
    max_tokens = 1 if is_binary else 2
    if len(install_source.split(maxsplit=max_tokens)) > max_tokens:
        raise FleetUploadContractError(
            f"{path}.properties[{install_source_index}].value is forbidden by Fleet upload contract"
        )
