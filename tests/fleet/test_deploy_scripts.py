from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_fleet_deploy_scripts_default_to_latest_openaca():
    for path in _fleet_deploy_scripts():
        text = path.read_text(encoding="utf-8")
        assert 'OPENACA_VERSION="${OPENACA_VERSION:-latest}"' in text
        assert 'OPENACA_PACKAGE="openaca"' in text
        assert 'OPENACA_PACKAGE="openaca==$OPENACA_VERSION"' in text
        assert '"$UV_BIN" tool install "$OPENACA_PACKAGE" --force' in text
        assert 'tool install "openaca==' not in text


def _fleet_deploy_scripts() -> list[Path]:
    return [
        REPO_ROOT / "deploy" / "fleet" / "jamf.sh",
        REPO_ROOT / "deploy" / "fleet" / "kandji.sh",
        REPO_ROOT / "deploy" / "fleet" / "intune-macos.sh",
    ]
