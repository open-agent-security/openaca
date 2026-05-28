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
