from __future__ import annotations

import os
import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ScriptRun:
    result: subprocess.CompletedProcess[str]
    home: Path
    log_dir: Path


def test_remote_deploy_scripts_default_to_latest_openaca():
    for path in _remote_deploy_scripts():
        text = path.read_text(encoding="utf-8")
        assert 'OPENACA_VERSION="${OPENACA_VERSION:-latest}"' in text
        # latest must upgrade in place AND allow pre-releases (openaca ships
        # only betas, so without --prerelease an old build is never advanced).
        assert '"$UV_BIN" tool install --upgrade --prerelease allow openaca' in text
        # a pinned version installs exactly that build, still upgrading in place.
        assert '"$UV_BIN" tool install --upgrade "openaca==$OPENACA_VERSION"' in text
        # the old non-upgrading form must be gone.
        assert "--force" not in text
        assert "OPENACA_PACKAGE" not in text


def test_remote_deploy_scripts_are_valid_bash():
    for path in _remote_deploy_scripts():
        result = subprocess.run(
            ["bash", "-n", str(path)],
            check=False,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr


def test_remote_deploy_scripts_require_token(tmp_path: Path):
    for path in _remote_deploy_scripts():
        run = _run_script(path, tmp_path / path.stem)
        assert run.result.returncode == 2
        assert "OPENACA_REMOTE_TOKEN is required" in run.result.stderr


def test_remote_deploy_scripts_reject_missing_console_user(tmp_path: Path):
    for path in _remote_deploy_scripts():
        run = _run_script(
            path,
            tmp_path / path.stem,
            env={"OPENACA_REMOTE_TOKEN": "ot_TEST", "OPENACA_CONSOLE_USER": "root"},
        )
        assert run.result.returncode == 3
        assert "No logged-in console user found" in run.result.stderr


def test_jamf_script_accepts_standard_parameters(tmp_path: Path):
    run = _run_script(
        REPO_ROOT / "deploy" / "remote" / "jamf.sh",
        tmp_path,
        args=["unused1", "unused2", "unused3", "ot_JAMF", "https://remote.example", "0.1.0b6"],
    )

    _assert_successful_install(
        run,
        token="ot_JAMF",
        api_url="https://remote.example",
        package="openaca==0.1.0b6",
    )


def test_kandji_script_configures_launchagent_from_environment(tmp_path: Path):
    run = _run_script(
        REPO_ROOT / "deploy" / "remote" / "kandji.sh",
        tmp_path,
        env={
            "OPENACA_REMOTE_TOKEN": "ot_KANDJI",
            "OPENACA_REMOTE_API_URL": "https://remote.example",
            "OPENACA_VERSION": "0.1.0b6",
        },
    )

    _assert_successful_install(
        run,
        token="ot_KANDJI",
        api_url="https://remote.example",
        package="openaca==0.1.0b6",
    )


def test_intune_script_configures_launchagent_from_environment(tmp_path: Path):
    run = _run_script(
        REPO_ROOT / "deploy" / "remote" / "intune-macos.sh",
        tmp_path,
        env={
            "OPENACA_REMOTE_TOKEN": "ot_INTUNE",
            "OPENACA_REMOTE_API_URL": "https://remote.example",
            "OPENACA_VERSION": "0.1.0b6",
        },
    )

    _assert_successful_install(
        run,
        token="ot_INTUNE",
        api_url="https://remote.example",
        package="openaca==0.1.0b6",
    )


def test_remote_deploy_scripts_upgrade_to_latest_prerelease(tmp_path: Path):
    """With no pinned version (the default), each script must upgrade in place
    to the latest pre-release. The earlier `--force` form reinstalled but did
    not advance an already-installed build, so a managed endpoint kept its old
    version across re-runs — the bug this guards against.
    """
    for path in _remote_deploy_scripts():
        run = _run_script(
            path,
            tmp_path / path.stem,
            env={
                "OPENACA_REMOTE_TOKEN": "ot_TEST",
                "OPENACA_REMOTE_API_URL": "https://remote.example",
            },
        )
        assert run.result.returncode == 0, run.result.stderr
        assert (run.log_dir / "uv.log").read_text(encoding="utf-8").splitlines() == [
            "self update",
            "tool install --upgrade --prerelease allow openaca",
        ]


def _run_script(
    path: Path,
    root: Path,
    *,
    env: dict[str, str] | None = None,
    args: list[str] | None = None,
) -> ScriptRun:
    home = root / "home" / "alice"
    log_dir = root / "logs"
    fakebin = root / "fakebin"
    openaca_bin = home / ".local" / "bin" / "openaca"
    uv_bin = home / ".local" / "bin" / "uv"
    home.mkdir(parents=True)
    fakebin.mkdir(parents=True)
    openaca_bin.parent.mkdir(parents=True)
    log_dir.mkdir(parents=True)

    _write_executable(fakebin / "dscl", f'printf "NFSHomeDirectory: {home}\\n"\n')
    _write_executable(fakebin / "id", 'printf "501\\n"\n')
    _write_executable(fakebin / "chown", "exit 0\n")
    _write_executable(
        fakebin / "launchctl",
        f'printf "%s\\n" "$*" >> "{log_dir / "launchctl.log"}"\n',
    )
    _write_executable(
        fakebin / "sudo",
        f"""
if [ "$1" = "-u" ]; then
  shift 2
fi
if [ "$1" = "env" ]; then
  shift
  while [ "$#" -gt 0 ]; do
    case "$1" in
      HOME=*) export HOME="${{1#HOME=}}"; shift ;;
      PATH=*) export PATH="{fakebin}:$PATH:${{1#PATH=}}"; shift ;;
      *) break ;;
    esac
  done
fi
exec "$@"
""",
    )
    _write_executable(
        uv_bin,
        f'printf "%s\\n" "$*" >> "{log_dir / "uv.log"}"\n',
    )
    _write_executable(
        openaca_bin,
        f"""
if [ ! -t 0 ]; then
  token="$(cat)"
else
  token=""
fi
printf "args=%s\\n" "$*" >> "{log_dir / "openaca.log"}"
printf "stdin=%s\\n" "$token" >> "{log_dir / "openaca.log"}"
""",
    )

    full_env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("OPENACA_")},
        "PATH": f"{fakebin}:{os.environ['PATH']}",
        "OPENACA_CONSOLE_USER": "alice",
    }
    if env:
        full_env.update(env)

    result = subprocess.run(
        ["bash", str(path), *(args or [])],
        check=False,
        text=True,
        capture_output=True,
        env=full_env,
    )
    return ScriptRun(result=result, home=home, log_dir=log_dir)


def _assert_successful_install(
    run: ScriptRun,
    *,
    token: str,
    api_url: str,
    package: str,
) -> None:
    assert run.result.returncode == 0, run.result.stderr
    assert "OpenACA remote LaunchAgent installed for alice" in run.result.stdout

    assert (run.log_dir / "uv.log").read_text(encoding="utf-8").splitlines() == [
        "self update",
        f"tool install --upgrade {package}",
    ]
    assert (run.log_dir / "openaca.log").read_text(encoding="utf-8").splitlines() == [
        f"args=remote configure --api-url {api_url} --token {token}",
        "stdin=",
    ]

    plist_path = run.home / "Library" / "LaunchAgents" / "com.openaca.remote.plist"
    plist = plistlib.loads(plist_path.read_bytes())
    assert plist["Label"] == "com.openaca.remote"
    assert plist["ProgramArguments"] == [
        str(run.home / ".local" / "bin" / "openaca"),
        "remote",
        "sync",
        "endpoint",
        "--quiet",
    ]
    assert plist["StartInterval"] == 21600
    assert plist["RunAtLoad"] is True
    assert plist["StandardOutPath"] == str(
        run.home / "Library" / "Logs" / "OpenACA" / "remote.out.log"
    )
    assert plist["StandardErrorPath"] == str(
        run.home / "Library" / "Logs" / "OpenACA" / "remote.err.log"
    )

    assert (run.log_dir / "launchctl.log").read_text(encoding="utf-8").splitlines() == [
        f"bootout gui/501 {plist_path}",
        f"bootstrap gui/501 {plist_path}",
        "kickstart -k gui/501/com.openaca.remote",
    ]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _remote_deploy_scripts() -> list[Path]:
    return [
        REPO_ROOT / "deploy" / "remote" / "jamf.sh",
        REPO_ROOT / "deploy" / "remote" / "kandji.sh",
        REPO_ROOT / "deploy" / "remote" / "intune-macos.sh",
    ]
