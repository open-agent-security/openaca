"""Pi's persisted project-trust fallback.

Invocation overrides and extension ``project_trust`` events run before this
fallback in Pi and cannot be inferred from static configuration.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ProjectTrust = Literal["trusted", "untrusted", "unresolved"]
TrustStoreStatus = Literal["missing", "valid", "invalid"]
TrustValue = bool | None


@dataclass(frozen=True)
class TrustStoreRead:
    status: TrustStoreStatus
    data: dict[str, TrustValue] | None
    error: str | None = None


def read_trust_store(path: Path) -> TrustStoreRead:
    """Read one trust file while preserving missing and invalid as distinct states."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return TrustStoreRead("missing", {})
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return TrustStoreRead("invalid", None, str(exc))

    error = _validation_error(parsed)
    if error is not None:
        return TrustStoreRead("invalid", None, error)
    return TrustStoreRead("valid", parsed)


def project_trust(
    trust_json: dict,
    default_project_trust: str | None,
    project_root: Path,
) -> ProjectTrust:
    """Classify Pi's persisted/default fallback without inferring dynamic overrides."""
    if _validation_error(trust_json) is not None:
        return "unresolved"

    current = _normalize_project_root(project_root)
    while True:
        decision = trust_json.get(str(current))
        if isinstance(decision, bool):
            return "trusted" if decision else "untrusted"
        parent = current.parent
        if parent == current:
            break
        current = parent

    if default_project_trust == "always":
        return "trusted"
    if default_project_trust == "never":
        return "untrusted"
    return "unresolved"


def _normalize_project_root(project_root: Path) -> Path:
    resolved = Path(os.path.abspath(project_root.expanduser()))
    try:
        return resolved.resolve(strict=True)
    except (OSError, RuntimeError):
        return resolved


def _validation_error(value: object) -> str | None:
    if not isinstance(value, dict):
        return "expected an object"
    for key, decision in value.items():
        if not isinstance(key, str):
            return "expected string path keys"
        if decision is not None and not isinstance(decision, bool):
            return f"value for {key!r} must be true, false, or null"
    return None
