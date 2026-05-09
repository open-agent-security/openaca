from tools.component_ref import ComponentRef
from tools.matcher import match


def make_advisory(asve_id: str, ecosystem: str, name: str, fixed: str) -> dict:
    return {
        "id": asve_id,
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-06T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": ecosystem, "name": name},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": fixed}],
                    }
                ],
            }
        ],
    }


def test_match_npm_in_range():
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.1.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].advisory_id == "ASVE-2026-0001"
    assert findings[0].component is ref
    assert findings[0].confidence == "high"


def test_match_npm_at_fixed_version_excluded():
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.2.3",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_match_npm_above_fixed_version_excluded():
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.5.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_unparseable_version_emits_low_confidence():
    """`^1.0.0`-style spec can't resolve to a single version — flag it but don't drop it."""
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="^1.0.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].confidence == "low"


def test_match_pypi_pinned():
    advisories = [make_advisory("ASVE-2026-0004", "PyPI", "aws-mcp-server", "0.3.2")]
    ref = ComponentRef(
        ecosystem="PyPI",
        name="aws-mcp-server",
        version="0.3.0",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.aws",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].confidence == "high"


def test_no_match_when_package_name_differs():
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="some-other-package",
        version="1.1.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_no_match_when_ecosystem_differs():
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="PyPI",
        name="@cyanheads/git-mcp-server",
        version="1.1.0",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.x",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_unpinned_npx_matches_affected_package_with_unknown_confidence():
    """The headline cross-layer behavior: an unpinned mcp.json npx launch
    of a known-vulnerable package emits an 'unknown' finding so the consumer
    knows to pin the version."""
    advisories = [
        make_advisory("ASVE-2026-0003", "npm", "@akoskm/create-mcp-server-stdio", "1.0.4")
    ]
    ref = ComponentRef(
        component_identity="mcp-stdio/npx-unpinned:@akoskm/create-mcp-server-stdio",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.x",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].advisory_id == "ASVE-2026-0003"
    assert findings[0].confidence == "unknown"


def test_unpinned_uvx_matches_pypi_advisory():
    advisories = [make_advisory("ASVE-2026-0004", "PyPI", "aws-mcp-server", "0.3.2")]
    ref = ComponentRef(
        component_identity="mcp-stdio/uvx-unpinned:aws-mcp-server",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.aws",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].advisory_id == "ASVE-2026-0004"
    assert findings[0].confidence == "unknown"


def test_binary_component_identity_does_not_match():
    """An mcp-stdio/binary:<path> identity has no package info; must not falsely match."""
    advisories = [make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        component_identity="mcp-stdio/binary:/opt/local/bin/custom",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.custom",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_in_range_multiple_event_windows():
    """A single OSV range can encode disjoint vulnerable intervals via alternating
    introduced/fixed events. Versions in each window must be detected; versions
    between windows must not be."""
    advisory = {
        "id": "ASVE-2026-TEST",
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-06T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "multi-window-pkg"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [
                            {"introduced": "1.0.0"},
                            {"fixed": "1.2.0"},
                            {"introduced": "2.0.0"},
                            {"fixed": "2.1.0"},
                        ],
                    }
                ],
            }
        ],
    }

    def ref(v: str) -> ComponentRef:
        return ComponentRef(
            ecosystem="npm",
            name="multi-window-pkg",
            version=v,
            source_manifest="package.json",
            source_locator="dependencies",
        )

    assert len(match([ref("1.1.0")], [advisory])) == 1  # in first window
    assert len(match([ref("2.0.5")], [advisory])) == 1  # in second window
    assert len(match([ref("1.5.0")], [advisory])) == 0  # between windows


def test_in_range_open_ended_no_fixed():
    """An advisory with no fixed event is still-unpatched — all versions at or
    after introduced are vulnerable."""
    advisory = {
        "id": "ASVE-2026-TEST",
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-06T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "unpatched-pkg"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "1.0.0"}],
                    }
                ],
            }
        ],
    }

    def ref(v: str) -> ComponentRef:
        return ComponentRef(
            ecosystem="npm",
            name="unpatched-pkg",
            version=v,
            source_manifest="package.json",
            source_locator="dependencies",
        )

    assert len(match([ref("1.0.0")], [advisory])) == 1  # at introduced boundary
    assert len(match([ref("9.9.9")], [advisory])) == 1  # well above introduced
    assert len(match([ref("0.9.9")], [advisory])) == 0  # before introduced


def test_in_range_last_affected_inclusive_bound():
    """last_affected closes a window inclusively: versions at and below the bound
    are vulnerable; versions above it must not be flagged as false positives."""
    advisory = {
        "id": "ASVE-2026-TEST",
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-06T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "last-affected-pkg"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"last_affected": "2.1.214"}],
                    }
                ],
            }
        ],
    }

    def ref(v: str) -> ComponentRef:
        return ComponentRef(
            ecosystem="npm",
            name="last-affected-pkg",
            version=v,
            source_manifest="package.json",
            source_locator="dependencies",
        )

    assert len(match([ref("2.1.214")], [advisory])) == 1  # at last_affected boundary (inclusive)
    assert len(match([ref("1.0.0")], [advisory])) == 1  # within range
    assert len(match([ref("2.1.215")], [advisory])) == 0  # above last_affected — not vulnerable
    assert len(match([ref("3.0.0")], [advisory])) == 0  # well above — must not false-positive


def test_no_duplicate_findings_when_advisory_has_multiple_ranges():
    """An advisory may list multiple ranges per affected entry (e.g.,
    discrete events). Same component+advisory pair should produce one
    finding, not one per range."""
    advisory = make_advisory("ASVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")
    advisory["affected"][0]["ranges"].append(
        {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.0.0"}]}
    )
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.1.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    findings = match(refs=[ref], advisories=[advisory])
    assert len(findings) == 1
