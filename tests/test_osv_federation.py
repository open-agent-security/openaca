"""Tests for the OSV.dev federation client.

Network is mocked at the urllib.request boundary; tests never hit the
real OSV.dev endpoint.
"""

from unittest.mock import patch

from tools.component_ref import ComponentRef
from tools.osv_federation import augment_corpus


def _ref(eco: str, name: str, version: str) -> ComponentRef:
    return ComponentRef(ecosystem=eco, name=name, version=version)


def test_augment_returns_base_corpus_when_no_refs():
    base = [{"id": "ASVE-2026-0001"}]
    augmented, warnings = augment_corpus(refs=[], base_corpus=base)
    assert augmented == base
    assert warnings == []


def test_augment_returns_base_corpus_when_no_versioned_refs():
    """Refs without ecosystem+name+version (e.g., identity-only hooks)
    can't be queried via OSV.dev — they're skipped, base corpus returned."""
    refs = [
        ComponentRef(component_identity="claude-hook/p/PreToolUse/0"),
        ComponentRef(ecosystem="claude-skill", name="x"),  # no version
    ]
    base = [{"id": "ASVE-2026-0001"}]
    augmented, warnings = augment_corpus(refs=refs, base_corpus=base)
    assert augmented == base


def test_augment_batches_purls_and_merges_results():
    """Versioned refs get batched into /v1/querybatch; full advisory records
    fetched via /v1/vulns/<id>; deduped against the base corpus by id."""
    refs = [_ref("npm", "lodash", "4.17.20"), _ref("PyPI", "requests", "2.31.0")]
    base = [{"id": "ASVE-2026-0001"}]
    querybatch_response = {
        "results": [
            {"vulns": [{"id": "GHSA-1111"}]},
            {"vulns": [{"id": "GHSA-2222"}]},
        ]
    }
    vuln_records = {
        "GHSA-1111": {
            "id": "GHSA-1111",
            "affected": [{"package": {"ecosystem": "npm", "name": "lodash"}}],
        },
        "GHSA-2222": {
            "id": "GHSA-2222",
            "affected": [{"package": {"ecosystem": "PyPI", "name": "requests"}}],
        },
    }

    def fake_post(url, payload):
        assert "querybatch" in url
        purls = [p["package"]["purl"] for p in payload["queries"]]
        assert "pkg:npm/lodash@4.17.20" in purls
        assert any("requests" in p for p in purls)
        return querybatch_response

    def fake_get(url):
        vuln_id = url.rsplit("/", 1)[-1]
        return vuln_records[vuln_id]

    with (
        patch("tools.osv_federation._post_json", fake_post),
        patch("tools.osv_federation._get_json", fake_get),
    ):
        augmented, warnings = augment_corpus(refs=refs, base_corpus=base)
    assert warnings == []
    ids = {a["id"] for a in augmented}
    assert ids == {"ASVE-2026-0001", "GHSA-1111", "GHSA-2222"}


def test_augment_fails_soft_on_network_error():
    """If the batch query raises, return base corpus + a warning string."""
    refs = [_ref("npm", "lodash", "4.17.20")]
    base = [{"id": "ASVE-2026-0001"}]

    def fake_post(url, payload):
        raise OSError("connection refused")

    with patch("tools.osv_federation._post_json", fake_post):
        augmented, warnings = augment_corpus(refs=refs, base_corpus=base)
    assert augmented == base
    assert any("osv.dev" in w.lower() for w in warnings)


def test_augment_dedupes_purls_within_a_scan():
    """The same PURL appearing on multiple refs should be queried once."""
    refs = [_ref("npm", "lodash", "4.17.20"), _ref("npm", "lodash", "4.17.20")]
    base = []
    calls = []

    def fake_post(url, payload):
        calls.append(payload)
        return {"results": [{"vulns": []}]}

    with patch("tools.osv_federation._post_json", fake_post):
        augment_corpus(refs=refs, base_corpus=base)
    assert len(calls) == 1
    assert len(calls[0]["queries"]) == 1  # deduped


def test_augment_chunks_large_batches():
    """OSV.dev /v1/querybatch caps at 1000 packages; chunk into multiple calls."""
    refs = [_ref("npm", f"pkg-{i}", "1.0.0") for i in range(1500)]
    base = []
    calls = []

    def fake_post(url, payload):
        calls.append(payload)
        return {"results": [{"vulns": []} for _ in payload["queries"]]}

    with patch("tools.osv_federation._post_json", fake_post):
        augment_corpus(refs=refs, base_corpus=base)
    assert len(calls) == 2
    assert len(calls[0]["queries"]) == 1000
    assert len(calls[1]["queries"]) == 500


def test_augment_skips_unversioned_refs():
    """Refs with a purl-capable ecosystem but no version (e.g., manifest fallback
    with an unpinned dep) must NOT be queried — OSV would return advisories for
    all versions, creating noisy/incorrect findings."""
    refs = [
        ComponentRef(ecosystem="PyPI", name="requests"),  # version=None
        ComponentRef(ecosystem="npm", name="lodash", version="4.17.20"),
    ]
    base = []
    queries_seen: list[list[str]] = []

    def fake_post(url, payload):
        queries_seen.append([q["package"]["purl"] for q in payload["queries"]])
        return {"results": [{"vulns": []}]}

    with patch("tools.osv_federation._post_json", fake_post):
        augment_corpus(refs=refs, base_corpus=base)
    assert len(queries_seen) == 1
    purls = queries_seen[0]
    assert purls == ["pkg:npm/lodash@4.17.20"]  # unversioned PyPI ref excluded


def test_augment_skips_purls_without_purl_form():
    """Refs whose ecosystem isn't in the PURL map (e.g., claude-skill) aren't
    queryable via OSV.dev — skip them, query the rest."""
    refs = [
        _ref("npm", "lodash", "4.17.20"),
        _ref("claude-skill", "demo", "1.0.0"),
    ]
    base = []

    def fake_post(url, payload):
        # Only the npm ref should have made it into the query batch.
        purls = [p["package"]["purl"] for p in payload["queries"]]
        assert purls == ["pkg:npm/lodash@4.17.20"]
        return {"results": [{"vulns": []}]}

    with patch("tools.osv_federation._post_json", fake_post):
        augment_corpus(refs=refs, base_corpus=base)
