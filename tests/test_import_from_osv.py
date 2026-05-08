import json

import pytest
import yaml
from click.testing import CliRunner

from tools.import_from_osv import main, osv_to_asve_skeleton


@pytest.fixture
def osv_record(fixtures_dir):
    return json.loads((fixtures_dir / "osv" / "ghsa-3q26-f695-pp76.json").read_text())


def test_skeleton_aliases_upstream_ids(osv_record):
    skeleton = osv_to_asve_skeleton(osv_record, asve_id="ASVE-2026-0001")
    assert skeleton["id"] == "ASVE-2026-0001"
    assert "GHSA-3q26-f695-pp76" in skeleton["aliases"]
    assert "CVE-2025-53107" in skeleton["aliases"]


def test_skeleton_carries_affected(osv_record):
    skeleton = osv_to_asve_skeleton(osv_record, asve_id="ASVE-2026-0001")
    assert skeleton["affected"][0]["package"]["ecosystem"] == "npm"
    assert skeleton["affected"][0]["package"]["name"] == "@cyanheads/git-mcp-server"


def test_skeleton_includes_asve_extension_placeholder(osv_record):
    skeleton = osv_to_asve_skeleton(osv_record, asve_id="ASVE-2026-0001")
    asve = skeleton["database_specific"]["asve"]
    assert "component_type" in asve
    assert asve["component_type"] == "TODO"


def test_cli_writes_yaml(tmp_path, fixtures_dir):
    src = fixtures_dir / "osv" / "ghsa-3q26-f695-pp76.json"
    dst = tmp_path / "ASVE-2026-0001.yaml"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--osv-file", str(src), "--asve-id", "ASVE-2026-0001", "--out", str(dst)],
    )
    assert result.exit_code == 0, result.output
    advisory = yaml.safe_load(dst.read_text())
    assert advisory["id"] == "ASVE-2026-0001"
