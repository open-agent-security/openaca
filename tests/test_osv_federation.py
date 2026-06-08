"""Tests for the OSV.dev federation client.

Network is mocked at the urllib.request boundary; tests never hit the
real OSV.dev endpoint.
"""

from unittest.mock import patch

from tools.component_ref import ComponentRef
from tools.osv_federation import (
    OsvQuery,
    augment_corpus,
    collect_osv_queries,
    collect_target_purls,
    is_queryable,
    stamp_osv_query_provenance,
)


def _ref(eco: str, name: str, version: str) -> ComponentRef:
    return ComponentRef(ecosystem=eco, name=name, version=version)


def test_is_queryable_requires_supported_osv_query_shape():
    assert is_queryable(_ref("npm", "lodash", "4.17.20")) is True
    assert is_queryable(_ref("PyPI", "requests", "2.31.0")) is True
    assert (
        is_queryable(
            ComponentRef(
                ecosystem="github",
                name="oraios/serena",
                version="0123456789abcdef0123456789abcdef01234567",
            )
        )
        is True
    )
    assert (
        is_queryable(
            ComponentRef(
                ecosystem="github",
                name="oraios/serena",
                extra={"git_ref": "v1.0.0"},
            )
        )
        is True
    )
    # Docker PURLs are inventory/BOM identities in V0, not OSV query targets.
    assert is_queryable(_ref("docker", "ghcr.io/org/server", "1.0")) is False
    # Plain npm/PyPI refs without versions are unresolved dependency specs,
    # not unpinned MCP launches.
    assert is_queryable(ComponentRef(ecosystem="npm", name="lodash")) is False
    assert is_queryable(ComponentRef(ecosystem="PyPI", name="requests")) is False
    assert (
        is_queryable(
            ComponentRef(
                ecosystem="npm",
                name="lodash",
                extra={"component_type": "mcp_server", "install_source": "npx lodash"},
            )
        )
        is True
    )
    # GitHub without version and without git_ref → not queryable
    assert is_queryable(ComponentRef(ecosystem="github", name="oraios/serena")) is False
    assert (
        is_queryable(ComponentRef(ecosystem="docker", name="repo/image", version="1.0.0")) is False
    )
    # Source-less agent components → no PURL, not queryable
    assert (
        is_queryable(
            ComponentRef(name="supabase", version="0.1.6", extra={"component_type": "plugin"})
        )
        is False
    )
    assert (
        is_queryable(
            ComponentRef(name="bootstrap", version="1.0.0", extra={"component_type": "skill"})
        )
        is False
    )
    # Identity-only refs (no ecosystem) → not queryable
    assert is_queryable(ComponentRef(component_identity="claude-hook/command:abcd1234")) is False


def test_collect_target_purls_dedupes_and_preserves_order():
    refs = [
        _ref("npm", "lodash", "4.17.20"),
        _ref("PyPI", "requests", "2.31.0"),
        _ref("docker", "ghcr.io/org/server", "1.0"),  # skipped
        _ref("npm", "lodash", "4.17.20"),  # dup
        ComponentRef(
            name="supabase", version="0.1.6", extra={"component_type": "plugin"}
        ),  # skipped
        ComponentRef(
            ecosystem="npm",
            name="left-pad",
            extra={"component_type": "mcp_server", "install_source": "npx left-pad"},
        ),  # no version → package query, not PURL
    ]
    purls = collect_target_purls(refs)
    assert purls == ["pkg:npm/lodash@4.17.20", "pkg:pypi/requests@2.31.0"]


def test_collect_osv_queries_uses_supported_query_shapes():
    sha = "0123456789abcdef0123456789abcdef01234567"
    refs = [
        _ref("npm", "lodash", "4.17.20"),
        _ref("PyPI", "requests", "2.31.0"),
        ComponentRef(ecosystem="github", name="oraios/serena", version=sha),
        ComponentRef(ecosystem="github", name="oraios/serena", extra={"git_ref": "v1.0.0"}),
        ComponentRef(ecosystem="docker", name="hashicorp/terraform-mcp-server", version="0.4.0"),
    ]

    queries = collect_osv_queries(refs)

    assert [query.payload for query in queries] == [
        {"package": {"purl": "pkg:npm/lodash@4.17.20"}},
        {"package": {"purl": "pkg:pypi/requests@2.31.0"}},
        {"commit": sha},
        {
            "version": "v1.0.0",
            "package": {"ecosystem": "GIT", "name": "https://github.com/oraios/serena.git"},
        },
    ]
    assert [query.label for query in queries] == [
        "pkg:npm/lodash@4.17.20",
        "pkg:pypi/requests@2.31.0",
        f"github.com/oraios/serena@{sha}",
        "github.com/oraios/serena@v1.0.0",
    ]


def test_collect_osv_queries_uses_package_query_for_unpinned_mcp_refs():
    """Inferred unpinned MCP refs (ecosystem+name, no version) emit a name+ecosystem
    package query so all advisories for the package are fetched."""
    refs = [
        ComponentRef(
            ecosystem="npm",
            name="@scope/mcp-server",
            extra={"component_type": "mcp_server", "install_source": "npx @scope/mcp-server"},
        ),
        ComponentRef(
            ecosystem="PyPI",
            name="my-mcp-tool",
            extra={"component_type": "mcp_server", "install_source": "uvx my-mcp-tool"},
        ),
        ComponentRef(
            ecosystem="PyPI",
            name="weather-mcp",
            extra={"component_type": "mcp_server", "install_source": "uv tool run weather-mcp"},
        ),
    ]

    queries = collect_osv_queries(refs)

    assert len(queries) == 3
    assert queries[0].payload == {"package": {"name": "@scope/mcp-server", "ecosystem": "npm"}}
    assert queries[0].label == "npm:@scope/mcp-server (unpinned)"
    assert queries[1].payload == {"package": {"name": "my-mcp-tool", "ecosystem": "PyPI"}}
    assert queries[1].label == "PyPI:my-mcp-tool (unpinned)"
    assert queries[2].payload == {"package": {"name": "weather-mcp", "ecosystem": "PyPI"}}
    assert queries[2].label == "PyPI:weather-mcp (unpinned)"


def test_collect_osv_queries_uses_package_query_for_parser_emitted_unpinned_mcp_refs():
    refs = [
        ComponentRef(component_identity="mcp-stdio/npx-unpinned:@scope/mcp-server"),
        ComponentRef(component_identity="mcp-stdio/uvx-unpinned:my-mcp-tool"),
    ]

    queries = collect_osv_queries(refs)

    assert [query.payload for query in queries] == [
        {"package": {"name": "@scope/mcp-server", "ecosystem": "npm"}},
        {"package": {"name": "my-mcp-tool", "ecosystem": "PyPI"}},
    ]
    assert [query.label for query in queries] == [
        "npm:@scope/mcp-server (unpinned)",
        "PyPI:my-mcp-tool (unpinned)",
    ]


def test_collect_osv_queries_skips_plain_unversioned_package_refs():
    refs = [
        ComponentRef(ecosystem="npm", name="left-pad"),
        ComponentRef(ecosystem="PyPI", name="requests"),
    ]

    assert collect_osv_queries(refs) == []


def test_augment_returns_base_corpus_when_no_refs():
    base = [{"id": "CVE-2026-0001"}]
    augmented, warnings = augment_corpus(refs=[], base_corpus=base)
    assert augmented == base
    assert warnings == []


def test_augment_returns_base_corpus_when_no_versioned_refs():
    """Refs without ecosystem+name+version (e.g., identity-only hooks)
    can't be queried via OSV.dev — they're skipped, base corpus returned."""
    refs = [
        ComponentRef(component_identity="claude-hook/command:abcd1234"),
        ComponentRef(name="x", extra={"component_type": "skill"}),  # no source ecosystem
    ]
    base = [{"id": "CVE-2026-0001"}]
    augmented, warnings = augment_corpus(refs=refs, base_corpus=base)
    assert augmented == base


def test_augment_batches_purls_and_merges_results():
    """Versioned refs get batched into /v1/querybatch; full advisory records
    fetched via /v1/vulns/<id>; deduped against the base corpus by id."""
    refs = [_ref("npm", "lodash", "4.17.20"), _ref("PyPI", "requests", "2.31.0")]
    base = [{"id": "CVE-2026-0001"}]
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
    assert ids == {"CVE-2026-0001", "GHSA-1111", "GHSA-2222"}


def test_augment_batches_mixed_osv_queries_and_filters_git_repo():
    sha = "0123456789abcdef0123456789abcdef01234567"
    refs = [
        _ref("npm", "lodash", "4.17.20"),
        ComponentRef(ecosystem="github", name="oraios/serena", version=sha),
    ]
    querybatch_response = {
        "results": [
            {"vulns": [{"id": "GHSA-npm"}]},
            {"vulns": [{"id": "GHSA-git-match"}, {"id": "GHSA-git-other"}]},
        ]
    }
    vuln_records = {
        "GHSA-npm": {
            "id": "GHSA-npm",
            "affected": [{"package": {"ecosystem": "npm", "name": "lodash"}}],
        },
        "GHSA-git-match": {
            "id": "GHSA-git-match",
            "affected": [
                {
                    "ranges": [
                        {
                            "type": "GIT",
                            "repo": "https://github.com/oraios/serena.git",
                            "events": [{"introduced": "0"}],
                        }
                    ]
                }
            ],
        },
        "GHSA-git-other": {
            "id": "GHSA-git-other",
            "affected": [
                {
                    "ranges": [
                        {
                            "type": "GIT",
                            "repo": "https://github.com/other/repo.git",
                            "events": [{"introduced": "0"}],
                        }
                    ]
                }
            ],
        },
    }

    def fake_post(url, payload):
        assert payload["queries"] == [
            {"package": {"purl": "pkg:npm/lodash@4.17.20"}},
            {"commit": sha},
        ]
        return querybatch_response

    def fake_get(url):
        return vuln_records[url.rsplit("/", 1)[-1]]

    with (
        patch("tools.osv_federation._post_json", fake_post),
        patch("tools.osv_federation._get_json", fake_get),
    ):
        augmented, warnings = augment_corpus(refs=refs, base_corpus=[])

    assert warnings == []
    assert [record["id"] for record in augmented] == ["GHSA-npm", "GHSA-git-match"]
    assert augmented[1]["database_specific"]["openaca"]["osv_query_matches"] == [
        {
            "kind": "git_commit",
            "repo": "github.com/oraios/serena",
            "ref": sha,
        }
    ]


def test_augment_dedupes_returned_records_by_alias_graph():
    refs = [_ref("npm", "@cyanheads/git-mcp-server", "1.1.0")]
    querybatch_response = {
        "results": [
            {
                "vulns": [
                    {"id": "GHSA-3q26-f695-pp76"},
                    {"id": "CVE-2025-53107"},
                ]
            }
        ]
    }
    vuln_records = {
        "GHSA-3q26-f695-pp76": {
            "id": "GHSA-3q26-f695-pp76",
            "aliases": ["CVE-2025-53107"],
        },
        "CVE-2025-53107": {
            "id": "CVE-2025-53107",
            "aliases": ["GHSA-3q26-f695-pp76"],
        },
    }

    def fake_post(url, payload):
        return querybatch_response

    def fake_get(url):
        return vuln_records[url.rsplit("/", 1)[-1]]

    with (
        patch("tools.osv_federation._post_json", fake_post),
        patch("tools.osv_federation._get_json", fake_get),
    ):
        augmented, warnings = augment_corpus(refs=refs, base_corpus=[])

    assert warnings == []
    assert [record["id"] for record in augmented] == ["GHSA-3q26-f695-pp76"]


def test_augment_fails_soft_on_network_error():
    """If the batch query raises, return base corpus + a warning string."""
    refs = [_ref("npm", "lodash", "4.17.20")]
    base = [{"id": "CVE-2026-0001"}]

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


def test_augment_queries_unversioned_mcp_launches_as_package_queries():
    """Refs with ecosystem+name but no version (inferred unpinned MCP launches) are
    queried via a name+ecosystem package query (not a PURL query) so all advisories
    for the package are fetched; the matcher then emits unknown-confidence findings."""
    refs = [
        ComponentRef(
            ecosystem="PyPI",
            name="requests",
            extra={"component_type": "mcp_server", "install_source": "uvx requests"},
        ),
        ComponentRef(ecosystem="npm", name="lodash", version="4.17.20"),
    ]
    base = []
    queries_seen: list[list[dict]] = []

    def fake_post(url, payload):
        queries_seen.append(payload["queries"])
        return {"results": [{"vulns": []}, {"vulns": []}]}

    with patch("tools.osv_federation._post_json", fake_post):
        augment_corpus(refs=refs, base_corpus=base)
    assert len(queries_seen) == 1
    assert len(queries_seen[0]) == 2
    # Unversioned PyPI ref uses name+ecosystem form (no purl key).
    assert queries_seen[0][0] == {"package": {"name": "requests", "ecosystem": "PyPI"}}
    # Versioned npm ref uses the PURL form.
    assert queries_seen[0][1] == {"package": {"purl": "pkg:npm/lodash@4.17.20"}}


def test_augment_skips_plain_unversioned_package_refs():
    refs = [
        ComponentRef(ecosystem="PyPI", name="requests"),
        ComponentRef(ecosystem="npm", name="left-pad"),
    ]
    queries_seen: list[list[dict]] = []

    def fake_post(url, payload):
        queries_seen.append(payload["queries"])
        return {"results": []}

    with patch("tools.osv_federation._post_json", fake_post):
        augmented, warnings = augment_corpus(refs=refs, base_corpus=[])

    assert augmented == []
    assert warnings == []
    assert queries_seen == []


def test_augment_skips_purls_without_purl_form():
    """Refs whose ecosystem isn't in the PURL map (e.g., source-less skill) aren't
    queryable via OSV.dev — skip them, query the rest."""
    refs = [
        _ref("npm", "lodash", "4.17.20"),
        ComponentRef(name="demo", version="1.0.0", extra={"component_type": "skill"}),
    ]
    base = []

    def fake_post(url, payload):
        # Only the npm ref should have made it into the query batch.
        purls = [p["package"]["purl"] for p in payload["queries"]]
        assert purls == ["pkg:npm/lodash@4.17.20"]
        return {"results": [{"vulns": []}]}

    with patch("tools.osv_federation._post_json", fake_post):
        augment_corpus(refs=refs, base_corpus=base)


def test_augment_filters_git_repo_case_insensitive():
    """Mixed-case owner/repo in a GitHub ref must still match a lowercase OSV GIT range URL."""
    sha = "0123456789abcdef0123456789abcdef01234567"
    # User typed "OraIOS/Serena" (mixed case); OSV records use lowercase canonical URL.
    ref = ComponentRef(ecosystem="github", name="OraIOS/Serena", version=sha)
    querybatch_response = {"results": [{"vulns": [{"id": "GHSA-case-test"}]}]}
    vuln_record = {
        "id": "GHSA-case-test",
        "affected": [
            {
                "ranges": [
                    {
                        "type": "GIT",
                        "repo": "https://github.com/oraios/serena.git",
                        "events": [{"introduced": "0"}],
                    }
                ]
            }
        ],
    }

    def fake_post(url, payload):
        return querybatch_response

    def fake_get(url):
        return vuln_record

    with (
        patch("tools.osv_federation._post_json", fake_post),
        patch("tools.osv_federation._get_json", fake_get),
    ):
        augmented, warnings = augment_corpus(refs=[ref], base_corpus=[])

    assert warnings == []
    assert any(r["id"] == "GHSA-case-test" for r in augmented)


def _git_version_query(repo: str, ref: str) -> OsvQuery:
    return OsvQuery(
        key=f"git-version:{repo}:{ref}",
        label=f"{repo}@{ref}",
        payload={"version": ref, "package": {"ecosystem": "GIT", "name": f"https://{repo}.git"}},
        kind="git_version",
        git_repo=repo,
        git_ref=ref,
    )


def test_stamp_osv_query_provenance_stamps_matching_git_query():
    record = {
        "id": "GHSA-git",
        "affected": [
            {
                "ranges": [
                    {
                        "type": "GIT",
                        "repo": "https://github.com/oraios/serena.git",
                        "events": [{"introduced": "0"}],
                    }
                ]
            }
        ],
    }
    query = _git_version_query("github.com/oraios/serena", "v1.0.0")

    assert stamp_osv_query_provenance(record, [query]) is True
    assert record["database_specific"]["openaca"]["osv_query_matches"] == [
        {"kind": "git_version", "repo": "github.com/oraios/serena", "ref": "v1.0.0"}
    ]


def test_stamp_osv_query_provenance_skips_when_no_query_matches_record_repo():
    record = {
        "id": "GHSA-other",
        "affected": [
            {
                "ranges": [
                    {
                        "type": "GIT",
                        "repo": "https://github.com/other/repo.git",
                        "events": [{"introduced": "0"}],
                    }
                ]
            }
        ],
    }
    query = _git_version_query("github.com/oraios/serena", "v1.0.0")

    assert stamp_osv_query_provenance(record, [query]) is False
    assert "database_specific" not in record
