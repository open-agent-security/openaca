"""OSV.dev federation: batched live query against /v1/querybatch.

ASVE's default scan uses only the local advisories/ corpus. This module
provides opt-in federation via --federate-osv: given a list of emitted
ComponentRefs, fetch matching vulnerability records from OSV.dev and
merge them into the corpus for the matcher to consume.

Behavior:
- Only refs with a derivable PURL (ecosystem in PURL_ECOSYSTEM_MAP +
  name + version) are queried. Identity-only refs (claude-hook,
  claude-command, claude-agent) and ASVE-native ecosystems
  (claude-skill, claude-plugin) are skipped — OSV.dev wouldn't have
  records for them anyway.
- PURLs are deduplicated within a scan (same PURL queried once).
- /v1/querybatch caps at 1000 packages per request; chunked into
  multiple requests if needed.
- Network errors fail-soft: return the base corpus unchanged with a
  warning string. The scan continues with local-corpus-only matching.
- Returned vuln IDs are dereferenced to full records via /v1/vulns/<id>
  and merged into the corpus, deduped against base by `id` (base wins
  on conflict — local advisories override upstream).

Module API:
    augment_corpus(refs, base_corpus) -> (augmented_corpus, warnings)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from tools.component_ref import ComponentRef

_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"
_VULN_URL = "https://api.osv.dev/v1/vulns/{id}"
_BATCH_SIZE = 1000
_TIMEOUT_SECONDS = 30


def is_queryable(ref: ComponentRef) -> bool:
    """A ref is sent to OSV.dev iff it has a version AND a PURL we can derive.

    Identity-only refs (claude-hook, claude-command, claude-agent) and
    ASVE-native ecosystems (claude-skill, claude-plugin) have `purl=None`
    so they're skipped here — OSV.dev wouldn't have records for them.
    Same rule for any ecosystem-tagged ref missing a version.
    """
    return bool(ref.version) and ref.purl is not None


def augment_corpus(
    refs: list[ComponentRef], base_corpus: list[dict]
) -> tuple[list[dict], list[str]]:
    """Return `(merged_corpus, warnings)`. Fail-soft on any network issue.

    Dedup is alias-aware: an OSV record is skipped when its `id` OR any
    of its `aliases` overlaps with the same set on any base advisory.
    The base corpus wins — ASVE records carry the agent overlay, so we
    keep our record rather than the upstream duplicate. Without this,
    `ASVE-2026-0001` (aliasing `GHSA-3q26-f695-pp76`) and OSV's
    `GHSA-3q26-f695-pp76` would both fire on the same component as if
    they were independent vulnerabilities.
    """
    purls = collect_target_purls(refs)
    if not purls:
        return list(base_corpus), []
    try:
        vuln_ids = _query_batch(purls)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return list(base_corpus), [f"osv.dev federation failed: {exc}"]
    if not vuln_ids:
        return list(base_corpus), []
    new_records: list[dict] = []
    fetch_warnings: list[str] = []
    for vid in vuln_ids:
        try:
            record = _get_vuln(vid)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            fetch_warnings.append(f"osv.dev fetch failed for {vid}: {exc}")
            continue
        if isinstance(record, dict) and record.get("id"):
            new_records.append(record)
    blocklist = _collect_alias_graph(base_corpus)
    merged = list(base_corpus)
    for r in new_records:
        ids = _record_alias_keys(r)
        if ids & blocklist:
            continue
        merged.append(r)
        blocklist |= ids
    return merged, fetch_warnings


def _record_alias_keys(record: dict) -> set[str]:
    """Identity set for a record: its `id` plus every entry in `aliases`."""
    keys: set[str] = set()
    rec_id = record.get("id")
    if isinstance(rec_id, str) and rec_id:
        keys.add(rec_id)
    for a in record.get("aliases") or []:
        if isinstance(a, str) and a:
            keys.add(a)
    return keys


def _collect_alias_graph(corpus: list[dict]) -> set[str]:
    """Union of every record's identity set — what an incoming record
    would have to not overlap to be considered new."""
    out: set[str] = set()
    for r in corpus:
        if isinstance(r, dict):
            out |= _record_alias_keys(r)
    return out


def collect_target_purls(refs: list[ComponentRef]) -> list[str]:
    """Deduplicated, query-ready PURLs (versioned, PURL-mappable ecosystem).

    Public so the CLI verbose path can surface what was sent to OSV.dev
    without re-implementing the filter. Order is the order refs first
    contributed a unique PURL — stable for reproducible verbose output.
    """
    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        if not is_queryable(r):
            continue
        purl = r.purl
        assert purl is not None  # narrowed by is_queryable
        if purl in seen:
            continue
        seen.add(purl)
        out.append(purl)
    return out


def _query_batch(purls: list[str]) -> list[str]:
    """POST /v1/querybatch in chunks of <=1000; collect returned vuln IDs."""
    ids: list[str] = []
    seen: set[str] = set()
    for i in range(0, len(purls), _BATCH_SIZE):
        chunk = purls[i : i + _BATCH_SIZE]
        payload = {"queries": [{"package": {"purl": p}} for p in chunk]}
        response = _post_json(_QUERYBATCH_URL, payload)
        for entry in response.get("results", []) or []:
            for vuln in entry.get("vulns") or []:
                vid = vuln.get("id")
                if isinstance(vid, str) and vid not in seen:
                    seen.add(vid)
                    ids.append(vid)
    return ids


def _get_vuln(vuln_id: str) -> dict:
    """GET /v1/vulns/<id> → full advisory record."""
    return _get_json(_VULN_URL.format(id=vuln_id))


def _post_json(url: str, payload: dict) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))
