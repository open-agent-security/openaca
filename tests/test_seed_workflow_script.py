import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_seed_osv_workflow_downloads_npm_and_pypi_then_runs_incremental_seed(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_gcs = tmp_path / "gcs"
    cache = tmp_path / "cache"
    uv_log = tmp_path / "uv.log"
    fake_bin.mkdir()

    for ecosystem in ("npm", "PyPI"):
        root = fake_gcs / ecosystem
        root.mkdir(parents=True)
        (root / "modified_id.csv").write_text("2026-05-13T00:00:00Z,GHSA-test\n", encoding="utf-8")
        (root / "all.zip").write_bytes(b"zip")

    _write_executable(
        fake_bin / "gcloud",
        """#!/usr/bin/env bash
set -euo pipefail
test "$1" = "storage"
test "$2" = "cp"
src="$3"
dst="$4"
rel="${src#gs://osv-vulnerabilities/}"
mkdir -p "$(dirname "$dst")"
cp "$FAKE_GCS_ROOT/$rel" "$dst"
""",
    )
    _write_executable(
        fake_bin / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$UV_LOG"
""",
    )

    env = {
        **os.environ,
        "ASVE_OSV_CACHE_DIR": str(cache),
        "FAKE_GCS_ROOT": str(fake_gcs),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "UV_LOG": str(uv_log),
    }

    result = subprocess.run(
        ["bash", "scripts/seed-osv-overlays.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (cache / "npm" / "modified_id.csv").exists()
    assert (cache / "npm" / "all.zip").exists()
    assert (cache / "PyPI" / "modified_id.csv").exists()
    assert (cache / "PyPI" / "all.zip").exists()
    log = uv_log.read_text(encoding="utf-8")
    assert "--records-root " + str(cache / "npm") in log
    assert "--state .asve-seed-state-npm.json" in log
    assert "--records-root " + str(cache / "PyPI") in log
    assert "--state .asve-seed-state-pypi.json" in log


def test_seed_osv_workflow_passes_optional_llm_command(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_gcs = tmp_path / "gcs"
    cache = tmp_path / "cache"
    uv_log = tmp_path / "uv.log"
    fake_bin.mkdir()

    for ecosystem in ("npm", "PyPI"):
        root = fake_gcs / ecosystem
        root.mkdir(parents=True)
        (root / "modified_id.csv").write_text("2026-05-13T00:00:00Z,GHSA-test\n", encoding="utf-8")
        (root / "all.zip").write_bytes(b"zip")

    _write_executable(
        fake_bin / "gcloud",
        """#!/usr/bin/env bash
set -euo pipefail
rel="${3#gs://osv-vulnerabilities/}"
mkdir -p "$(dirname "$4")"
cp "$FAKE_GCS_ROOT/$rel" "$4"
""",
    )
    _write_executable(
        fake_bin / "uv",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$UV_LOG"
""",
    )

    env = {
        **os.environ,
        "ASVE_OSV_CACHE_DIR": str(cache),
        "ASVE_SEED_LLM_COMMAND": "python tools/seed_llm.py",
        "FAKE_GCS_ROOT": str(fake_gcs),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "UV_LOG": str(uv_log),
    }

    result = subprocess.run(
        ["bash", "scripts/seed-osv-overlays.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    log = uv_log.read_text(encoding="utf-8")
    assert log.count("--llm-command python tools/seed_llm.py") == 2
