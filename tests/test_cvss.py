import pytest

from tools.cvss import is_valid_cvss_v4


@pytest.mark.parametrize(
    "vector",
    [
        "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
        "CVSS:4.0/AV:L/AC:H/AT:P/PR:L/UI:A/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N",
    ],
)
def test_valid_vectors(vector):
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
def test_invalid_vectors(vector):
    assert is_valid_cvss_v4(vector) is False
