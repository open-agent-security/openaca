"""ASVE overlay loading and merge helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml


def load_overlays(overlays_root: Path) -> list[dict]:
    """Load every YAML overlay under `overlays_root` in stable path order."""
    return [yaml.safe_load(p.read_text()) for p in sorted(overlays_root.rglob("*.yaml"))]


def build_alias_to_overlay_id_map(overlays: list[dict]) -> dict[str, str]:
    """Map every id/alias in each overlay to that overlay's canonical id.

    Needed for SARIF helpUri: OSV may return a record under a CVE alias while
    the overlay file is named for the GHSA id. Without this map, the helpUri
    points to a URL that has no corresponding overlay page.
    """
    result: dict[str, str] = {}
    for overlay in overlays:
        overlay_id = overlay.get("id")
        if not isinstance(overlay_id, str):
            continue
        result[overlay_id] = overlay_id
        for alias in overlay.get("aliases") or []:
            if isinstance(alias, str):
                result[alias] = overlay_id
    return result


def id_set(record: dict) -> set[str]:
    """Return the vulnerability identity set for an OSV-shaped record."""
    ids: set[str] = set()
    record_id = record.get("id")
    if isinstance(record_id, str):
        ids.add(record_id)
    for alias in record.get("aliases") or []:
        if isinstance(alias, str):
            ids.add(alias)
    return ids


def apply_overlays(records: list[dict], overlays: list[dict]) -> list[dict]:
    """Return OSV records with matching ASVE overlay metadata merged in.

    Matching is by alias-set intersection, not exact `id`: OSV can expose
    both `GHSA-*` and `CVE-*` records for the same underlying issue. The
    upstream record keeps authority over package ranges, severity, summaries,
    and references; ASVE contributes only `database_specific.asve` metadata.
    """
    out: list[dict] = []
    for record in records:
        merged = deepcopy(record)
        record_ids = id_set(record)
        for overlay in overlays:
            if record_ids.isdisjoint(id_set(overlay)):
                continue
            _merge_overlay(merged, overlay)
        out.append(merged)
    return out


def _merge_overlay(record: dict, overlay: dict) -> None:
    overlay_asve = (overlay.get("database_specific") or {}).get("asve") or {}
    if not isinstance(overlay_asve, dict):
        return

    ds = record.setdefault("database_specific", {})
    if not isinstance(ds, dict):
        record["database_specific"] = ds = {}
    asve = ds.setdefault("asve", {})
    if not isinstance(asve, dict):
        ds["asve"] = asve = {}

    for key, value in overlay_asve.items():
        if key == "source":
            continue
        asve[key] = deepcopy(value)
    asve.setdefault("overlay_source", "asve.dev")
