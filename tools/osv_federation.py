"""OSV.dev matching: batched live query against /v1/querybatch.

Given a list of emitted ComponentRefs, fetch matching vulnerability
records from OSV.dev for the matcher to consume. OpenACA overlays are
applied by `tools.overlays` after these records are fetched.

Behavior:
- npm/PyPI refs use OSV package PURL queries.
- GitHub commit refs use OSV commit queries.
- GitHub mutable refs use OSV's GIT package/version query shape. OSV's
  documented GIT version query is for tagged releases; branch names (e.g.
  "main") are sent as the version field but will return no advisory results
  because OSV does not index branch pointers. Resolving branches to commit
  SHAs for a commit query requires a GitHub API call and is deferred to V1.
- Generic Docker refs are inventory-only in V0 because OSV does not support
  `pkg:docker/...` queries for ordinary container images.
- Query targets are deduplicated within a scan.
- /v1/querybatch caps at 1000 packages per request; chunked into
  multiple requests if needed.
- Network errors fail-soft: return the base corpus (always empty in V0
  because the caller passes `base_corpus=[]`) with a warning string.
  There is no local advisory corpus to fall back to under the overlay model.
- Returned vuln IDs are dereferenced to full records via /v1/vulns/<id>
  and merged into the corpus, deduped by alias graph.

Module API:
    augment_corpus(refs, base_corpus) -> (augmented_corpus, warnings)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from tools.component_ref import ComponentRef
from tools.overlays import id_set

_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"
_VULN_URL = "https://api.osv.dev/v1/vulns/{id}"
_BATCH_SIZE = 1000
_TIMEOUT_SECONDS = 30
_PURL_QUERY_ECOSYSTEMS = frozenset({"npm", "PyPI", "pypi"})
_GITHUB_ECOSYSTEMS = frozenset({"github", "GitHub"})


@dataclass(frozen=True)
class OsvQuery:
    """A query payload plus metadata needed to interpret OSV results."""

    key: str
    label: str
    payload: dict[str, Any]
    kind: str
    git_repo: str | None = None
    git_ref: str | None = None


def is_queryable(ref: ComponentRef) -> bool:
    """Return true when the ref has a supported OSV.dev query shape."""
    return _query_for_ref(ref) is not None


def augment_corpus(
    refs: list[ComponentRef], base_corpus: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return `(merged_corpus, warnings)`. Fail-soft on any network issue."""
    queries = collect_osv_queries(refs)
    if not queries:
        return list(base_corpus), []
    try:
        matches_by_id = _query_batch(queries)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return list(base_corpus), [f"osv.dev federation failed: {exc}"]
    if not matches_by_id:
        return list(base_corpus), []
    new_records: list[dict[str, Any]] = []
    fetch_warnings: list[str] = []
    for vid, matching_queries in matches_by_id.items():
        try:
            record = _get_vuln(vid)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            fetch_warnings.append(f"osv.dev fetch failed for {vid}: {exc}")
            continue
        if isinstance(record, dict) and record.get("id"):
            if stamp_osv_query_provenance(record, matching_queries):
                new_records.append(record)
    covered_ids: set[str] = set()
    for advisory in base_corpus:
        if isinstance(advisory, dict):
            covered_ids.update(id_set(advisory))
    merged = list(base_corpus)
    for r in new_records:
        ids = id_set(r)
        if ids.isdisjoint(covered_ids):
            merged.append(r)
            covered_ids.update(ids)
    return merged, fetch_warnings


def collect_osv_queries(refs: list[ComponentRef]) -> list[OsvQuery]:
    """Deduplicated OSV.dev queries in first-seen ref order."""
    seen: set[str] = set()
    out: list[OsvQuery] = []
    for ref in refs:
        query = _query_for_ref(ref)
        if query is None or query.key in seen:
            continue
        seen.add(query.key)
        out.append(query)
    return out


def collect_target_purls(refs: list[ComponentRef]) -> list[str]:
    """Deduplicated package PURLs still queried through OSV's PURL path.

    Kept for callers/tests that only need the package-query subset. Git and
    Docker source identities may still have internal PURLs, but OSV does not
    accept those generic PURL forms.
    """
    out: list[str] = []
    for query in collect_osv_queries(refs):
        package = query.payload.get("package")
        if isinstance(package, dict):
            purl = package.get("purl")
            if isinstance(purl, str):
                out.append(purl)
    return out


def collect_osv_query_labels(refs: list[ComponentRef]) -> list[str]:
    """Readable labels for verbose output, in OSV query order."""
    return [query.label for query in collect_osv_queries(refs)]


def _query_for_ref(ref: ComponentRef) -> OsvQuery | None:
    if ref.ecosystem in _PURL_QUERY_ECOSYSTEMS and ref.version and ref.purl is not None:
        return OsvQuery(
            key=f"purl:{ref.purl}",
            label=ref.purl,
            payload={"package": {"purl": ref.purl}},
            kind="purl",
        )
    # Unpinned launch: ecosystem+name without version (inferred from install_source).
    # Query all advisories for the package so the matcher can emit unknown-confidence findings.
    if ref.ecosystem in _PURL_QUERY_ECOSYSTEMS and ref.name and not ref.version:
        return OsvQuery(
            key=f"package:{ref.ecosystem}:{ref.name}",
            label=f"{ref.ecosystem}:{ref.name} (unpinned)",
            payload={"package": {"name": ref.name, "ecosystem": ref.ecosystem}},
            kind="purl",
        )
    if ref.ecosystem in _GITHUB_ECOSYSTEMS and ref.name:
        repo = f"github.com/{ref.name.lower()}"
        if ref.version:
            return OsvQuery(
                key=f"git-commit:{repo}:{ref.version}",
                label=f"{repo}@{ref.version}",
                payload={"commit": ref.version},
                kind="git_commit",
                git_repo=repo,
                git_ref=ref.version,
            )
        git_ref = ref.extra.get("git_ref") if isinstance(ref.extra, dict) else None
        if isinstance(git_ref, str) and git_ref:
            # OSV's GIT version query expects the full repo URL in package.name
            # and a git tag in the version field (per the v1 query docs:
            # `{"ecosystem": "GIT", "name": "https://github.com/owner/repo.git",
            # "version": "8.5.0"}`). Branch names (e.g. "main") are passed
            # through unchanged but will return no results from OSV because OSV
            # does not index branch pointers — only tagged releases. The bare
            # `repo` form stays as git_repo for stamping/record matching, which
            # normalizes scheme and `.git` away on the response side.
            return OsvQuery(
                key=f"git-version:{repo}:{git_ref}",
                label=f"{repo}@{git_ref}",
                payload={
                    "version": git_ref,
                    "package": {"ecosystem": "GIT", "name": f"https://{repo}.git"},
                },
                kind="git_version",
                git_repo=repo,
                git_ref=git_ref,
            )
    return None


def _query_batch(queries: list[OsvQuery]) -> dict[str, list[OsvQuery]]:
    """POST /v1/querybatch in chunks of <=1000; collect returned vuln IDs."""
    matches: dict[str, list[OsvQuery]] = {}
    seen_pairs: set[tuple[str, str]] = set()
    for i in range(0, len(queries), _BATCH_SIZE):
        chunk = queries[i : i + _BATCH_SIZE]
        payload = {"queries": [query.payload for query in chunk]}
        response = _post_json(_QUERYBATCH_URL, payload)
        for query, entry in zip(chunk, response.get("results", []) or []):
            for vuln in entry.get("vulns") or []:
                vid = vuln.get("id")
                if not isinstance(vid, str):
                    continue
                pair = (vid, query.key)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                matches.setdefault(vid, []).append(query)
    return matches


def _record_matching_queries(record: dict[str, Any], queries: list[OsvQuery]) -> list[OsvQuery]:
    matches: list[OsvQuery] = []
    for query in queries:
        if query.git_repo is None:
            matches.append(query)
        elif _record_has_git_repo(record, query.git_repo):
            matches.append(query)
    return matches


def _stamp_query_matches(record: dict[str, Any], queries: list[OsvQuery]) -> None:
    git_matches = [
        {"kind": query.kind, "repo": query.git_repo, "ref": query.git_ref}
        for query in queries
        if query.git_repo is not None and query.git_ref is not None
    ]
    if not git_matches:
        return
    ds = record.setdefault("database_specific", {})
    if not isinstance(ds, dict):
        return
    openaca_block = ds.setdefault("openaca", {})
    if not isinstance(openaca_block, dict):
        return
    existing = openaca_block.setdefault("osv_query_matches", [])
    if isinstance(existing, list):
        existing.extend(git_matches)


def stamp_osv_query_provenance(record: dict[str, Any], queries: list[OsvQuery]) -> bool:
    """Stamp a fetched OSV record with the queries that returned it.

    `match()` trusts `database_specific.openaca.osv_query_matches` for
    git_commit / git_version findings (ADR-0027), so a consumer that fetches
    advisories with its own client must stamp records the same way
    `augment_corpus` does. Returns True when a query matched this record (by git
    repo); a consumer fetching itself should drop the record when False.

    `queries` MUST be only the queries that returned THIS record (e.g. the batch
    result for a single vuln id), never all scan queries — non-git PURL queries
    match unconditionally, so passing every query would stamp unrelated git
    provenance onto the record.
    """
    matches = _record_matching_queries(record, queries)
    if not matches:
        return False
    _stamp_query_matches(record, matches)
    return True


def _record_has_git_repo(record: dict[str, Any], repo: str) -> bool:
    for affected in record.get("affected") or []:
        for range_entry in affected.get("ranges") or []:
            if range_entry.get("type") != "GIT":
                continue
            candidate = range_entry.get("repo")
            if isinstance(candidate, str) and _normalize_git_repo(candidate) == repo:
                return True
    return False


def _normalize_git_repo(repo: str) -> str:
    parsed = urlparse(repo)
    if parsed.netloc:
        normalized = f"{parsed.netloc}{parsed.path}"
    else:
        normalized = repo
    return normalized.rstrip("/").removesuffix(".git").lower()


def _get_vuln(vuln_id: str) -> dict[str, Any]:
    """GET /v1/vulns/<id> → full advisory record."""
    return _get_json(_VULN_URL.format(id=vuln_id))


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
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
