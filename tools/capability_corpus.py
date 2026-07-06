"""Curated-tier capability corpus loader, identity-/coordinate-keyed."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from tools.capability import Capability

__all__ = ["CapabilityCorpus", "load_capability_corpus", "default_capabilities_dir"]


def default_capabilities_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "capabilities"


def _openaca_version() -> str:
    try:
        return version("openaca")
    except PackageNotFoundError:
        return "unknown"


@dataclass(frozen=True)
class CapabilityCorpus:
    by_coordinate: dict[str, list[Capability]]
    by_identity: dict[str, list[Capability]]

    def lookup(self, identity: str, match_coordinate: str | None = None) -> list[Capability]:
        if match_coordinate is not None:
            return list(self.by_coordinate.get(match_coordinate, []))
        return list(self.by_identity.get(identity, []))


def load_capability_corpus(root: Path | None = None) -> CapabilityCorpus:
    if root is None:
        root = default_capabilities_dir()
    by_coordinate: dict[str, list[Capability]] = {}
    by_identity: dict[str, list[Capability]] = {}
    source_version = _openaca_version()
    for path in sorted(root.rglob("*.yaml")):
        record = yaml.safe_load(path.read_text())
        caps = _record_capabilities(record, source_version)
        coordinate = record.get("match_coordinate")
        if coordinate:
            by_coordinate[coordinate] = caps
        else:
            by_identity[record["identity"]] = caps
    return CapabilityCorpus(by_coordinate=by_coordinate, by_identity=by_identity)


def _record_capabilities(record: dict[str, Any], source_version: str) -> list[Capability]:
    review = {
        "kind": "curated_review",
        "reviewed_version": record.get("reviewed_version"),
        "last_reviewed": record.get("last_reviewed"),
    }
    caps: list[Capability] = []
    for entry in record.get("capabilities", []):
        caps.append(
            Capability(
                name=entry["name"],
                execution_locus=entry["execution_locus"],
                method="curated",
                source="openaca",
                source_version=source_version,
                confidence=entry["confidence"],
                evidence=(*entry.get("evidence", []), review),
            )
        )
    return caps
