from __future__ import annotations

import re
from typing import Any


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
    """
    _validate_value(payload, "$")


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
    is_binary = isinstance(identity, str) and identity.startswith(
        ("mcp-stdio/binary:", "mcp-stdio/local:")
    )
    # ADR-0029: binary/local MCPs carry mcp-server/<name> identity with no PURL,
    # no transport, and an install_source that does not start with npx/uvx.
    if not is_binary and (
        component_type == "mcp_server"
        and isinstance(identity, str)
        and identity.startswith("mcp-server/")
        and not component_purl
        and "openaca:transport" not in props_by_name
        and isinstance(install_source, str)
        and install_source.strip()
    ):
        first = install_source.split(maxsplit=1)[0]
        is_binary = first not in ("npx", "uvx")
    is_package = isinstance(identity, str) and identity.startswith(
        ("mcp-stdio/npx-unpinned:", "mcp-stdio/uvx-unpinned:")
    )
    # ADR-0029: unpinned package MCPs carry mcp-server/<name> identity with no PURL.
    if not is_package and (
        component_type == "mcp_server"
        and isinstance(identity, str)
        and identity.startswith("mcp-server/")
        and not component_purl
        and "openaca:transport" not in props_by_name
        and isinstance(install_source, str)
        and install_source.strip()
    ):
        first = install_source.split(maxsplit=1)[0]
        is_package = first in ("npx", "uvx")
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
