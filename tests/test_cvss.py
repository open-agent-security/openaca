import pytest

from tools.cvss import is_valid_cvss, is_valid_cvss_v3, is_valid_cvss_v4


@pytest.mark.parametrize(
    "vector",
    [
        "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
        "CVSS:4.0/AV:L/AC:H/AT:P/PR:L/UI:A/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N",
    ],
)
def test_valid_v4_vectors(vector):
    assert is_valid_cvss_v4(vector) is True


@pytest.mark.parametrize(
    "vector",
    [
        "",
        "CVSS:3.1/AV:N",
        "CVSS:4.0/AV:Z/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
        "AV:N/AC:L",
        "CVSS:4.0",
        # duplicate base metric
        "CVSS:4.0/AV:N/AV:L/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
    ],
)
def test_invalid_v4_vectors(vector):
    assert is_valid_cvss_v4(vector) is False


@pytest.mark.parametrize(
    "vector",
    [
        # The Splunk CVE-2026-20205 vector — the concrete case that drove
        # v3 acceptance. Locks in interop with upstream.
        "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H",
        # v3.0 prefix is wire-compatible with v3.1 base metrics
        "CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        # All UI:R (Required) is also valid
        "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:C/C:L/I:L/A:L",
    ],
)
def test_valid_v3_vectors(vector):
    assert is_valid_cvss_v3(vector) is True


@pytest.mark.parametrize(
    "vector",
    [
        "",
        # v4 prefix
        "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
        # v3 prefix but missing required metric (S)
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
        # Bad enum value for AV
        "CVSS:3.1/AV:Z/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        # Bad enum for UI (v4 uses N/P/A; v3 uses N/R — P is illegal in v3)
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:P/S:U/C:H/I:H/A:H",
        # No prefix
        "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        # Truncated
        "CVSS:3.1/AV:N",
        # Duplicate metric
        "CVSS:3.1/AV:N/AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    ],
)
def test_invalid_v3_vectors(vector):
    assert is_valid_cvss_v3(vector) is False


def test_dispatcher_routes_by_type():
    v3 = "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H"
    v4 = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
    assert is_valid_cvss("CVSS_V3", v3) is True
    assert is_valid_cvss("CVSS_V4", v4) is True


def test_dispatcher_rejects_cross_version_vector():
    """A declared v3 type with a v4 vector body must fail — the score is
    invalid for its declared version even though it'd parse standalone."""
    v3 = "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H"
    v4 = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
    assert is_valid_cvss("CVSS_V3", v4) is False
    assert is_valid_cvss("CVSS_V4", v3) is False


def test_dispatcher_rejects_unknown_type():
    assert is_valid_cvss("CVSS_V2", "AV:N/AC:L/Au:N/C:P/I:P/A:P") is False
    assert is_valid_cvss("OWASP-XYZ", "anything") is False
