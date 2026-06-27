"""Parse mcp.json / .mcp.json files: extract MCP server installations.

There is no official `mcp.json` schema (see modelcontextprotocol#2219); each
host (Claude Code, Claude Desktop, Cursor, VS Code, Codex) defines its own
file conventions on top of the wire-protocol spec. We handle the dominant
JSON shapes here:

- `mcpServers` root (Claude Code, Claude Desktop, Cursor, plugin manifests).
- `servers` root (VS Code's `.vscode/mcp.json`).
- Flat root (no wrapper) — observed in real Claude Code plugin `.mcp.json`
  files (e.g. the `claude-plugins-official/playwright` plugin ships
  `{"playwright": {"command": "npx", "args": [...]}}` at top level).
  Detected when every value is a dict with `command` or `url`.

V0 detection scope is package-pinned stdio servers (npx/uvx + binary
fallback) so OpenACA can alias upstream CVE/GHSA records via PURL. URL/HTTP
transports, secret-surface fields (`env`/`headers`/`oauth`), and TOML
configs are out of V0 scope.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tools.component_ref import ComponentRef

NPM_PINNED_RE = re.compile(r"^(?P<name>(?:@[^/]+/)?[^@]+)@(?P<version>[^@\s]+)$")
PYPI_PINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+-]+)$")
PYPI_AT_VERSION_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)@(?P<version>[^@\s]+)$")
PYPI_UNPINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)$")
GITHUB_URL_RE = re.compile(
    r"^git\+https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/@\s#]+)"
    r"(?:@(?P<ref>[^#\s]+))?(?:#(?P<fragment>[^\s]*))?$"
)
LOCAL_MCP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
# Git object IDs are hex and case-insensitive, so an uppercase pin like
# `@ABCDEF...` is still an immutable commit. Match both cases; the value is
# lowercased before use so the PURL @version stays canonical.
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
INTERPOLATION_RE = re.compile(r"\$\{[^}]+\}")
_DOCKER_VALUE_FLAGS = frozenset(
    {
        "--add-host",
        "--annotation",
        "--attach",
        "-a",
        "--blkio-weight",
        "--blkio-weight-device",
        "--cap-add",
        "--cap-drop",
        "--cgroup-parent",
        "--cgroupns",
        "--cidfile",
        "--cpu-count",
        "--cpu-percent",
        "--cpu-period",
        "--cpu-quota",
        "--cpu-rt-period",
        "--cpu-rt-runtime",
        "--cpu-shares",
        "-c",
        "--cpus",
        "--cpuset-cpus",
        "--cpuset-mems",
        "--detach-keys",
        "--device",
        "--device-cgroup-rule",
        "--device-read-bps",
        "--device-read-iops",
        "--device-write-bps",
        "--device-write-iops",
        "--dns",
        "--dns-opt",  # hidden alias of --dns-option (docker/cli registers it MarkHidden)
        "--dns-option",
        "--dns-search",
        "--domainname",
        "--entrypoint",
        "--env",
        "-e",
        "--env-file",
        "--expose",
        "--gpus",
        "--group-add",
        "--health-cmd",
        "--health-interval",
        "--health-retries",
        "--health-start-interval",
        "--health-start-period",
        "--health-timeout",
        "--hostname",
        "-h",
        "--io-maxbandwidth",
        "--io-maxiops",
        "--ip",
        "--ip6",
        "--ipc",
        "--isolation",
        "--kernel-memory",
        "--label",
        "--label-file",
        "-l",
        "--link",
        "--link-local-ip",
        "--log-driver",
        "--log-opt",
        "--mac-address",
        "--memory",
        "-m",
        "--memory-reservation",
        "--memory-swap",
        "--memory-swappiness",
        "--mount",
        "--name",
        "--net",  # documented alias of --network; takes a value
        "--net-alias",  # hidden alias of --network-alias (docker/cli MarkHidden)
        "--network",
        "--network-alias",
        "--oom-score-adj",
        "--pid",
        "--pids-limit",
        "--platform",
        "--publish",
        "-p",
        "--pull",
        "--restart",
        "--runtime",
        "--security-opt",
        "--shm-size",
        "--stop-signal",
        "--stop-timeout",
        "--storage-opt",
        "--sysctl",
        "--tmpfs",
        "--ulimit",
        "--user",
        "-u",
        "--userns",
        "--uts",
        "--volume",
        "-v",
        "--volume-driver",
        "--volumes-from",
        "--workdir",
        "-w",
    }
)
_DOCKER_GLOBAL_VALUE_FLAGS = frozenset(
    {
        "--config",
        "--context",
        "-c",
        "--host",
        "-H",
        "--log-level",
        "--tlscacert",
        "--tlscert",
        "--tlskey",
    }
)


def _has_interpolation(spec: str) -> bool:
    return bool(INTERPOLATION_RE.search(spec))


def _extract_flag_value(
    args: list[str], flag_long: str, flag_short: str | None = None
) -> str | None:
    """Return the value of `--<flag_long>=<v>`, `--<flag_long> <v>`, or
    `-<flag_short>=<v>` / `-<flag_short> <v>` if present.

    Stops at `--` (option terminator)."""
    long_eq = f"--{flag_long}="
    long_bare = f"--{flag_long}"
    short_eq = f"-{flag_short}=" if flag_short else None
    short_bare = f"-{flag_short}" if flag_short else None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        if a.startswith(long_eq):
            return a[len(long_eq) :]
        if a == long_bare and i + 1 < len(args):
            return args[i + 1]
        if short_eq and a.startswith(short_eq):
            return a[len(short_eq) :]
        if short_bare and a == short_bare and i + 1 < len(args):
            return args[i + 1]
        i += 1
    return None


_FLAGS_WITH_VALUE = (
    "--package",
    "-p",
    "--from",
    "--with",
    "--python",
    # npx -c/--call takes a shell snippet; it's not a package spec and
    # the value must be excluded from positional analysis.
    "--call",
    "-c",
)


def _positional_args(args: list[str]) -> list[str]:
    """Return positional args, treating `--` as option terminator."""
    out: list[str] = []
    after_terminator = False
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if after_terminator:
            out.append(a)
            continue
        if a == "--":
            after_terminator = True
            continue
        if a.startswith("-"):
            # Flags consuming a separate value (handled in _extract_flag_value)
            # would otherwise leave that value here as a "positional" — skip it.
            if a in _FLAGS_WITH_VALUE:
                skip_next = True
            continue
        out.append(a)
    return out


def _classify_npm_spec(spec: str) -> tuple[str | None, str | None]:
    """Match a single npm spec like `@scope/name@1.2.3` or `bare-name`."""
    if _has_interpolation(spec):
        return None, None
    m = NPM_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version")
    return spec, None


def _classify_pypi_spec(spec: str) -> tuple[str | None, str | None, bool]:
    """Match a single PyPI spec: `name==1.2.3`, `name@1.2.3` (uvx form), or bare `name`."""
    if _has_interpolation(spec):
        return None, None, False
    m = PYPI_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version"), True
    m = PYPI_AT_VERSION_RE.match(spec)
    if m:
        return m.group("name"), m.group("version"), True
    m = PYPI_UNPINNED_RE.match(spec)
    if m:
        return m.group("name"), None, False
    return None, None, False


def _parse_npx_args(args: list[str]) -> tuple[str | None, str | None]:
    inline = _extract_flag_value(args, "package", "p")
    if inline is not None:
        return _classify_npm_spec(inline)
    positional = _positional_args(args)
    if not positional:
        return None, None
    return _classify_npm_spec(positional[0])


def _parse_uvx_args(args: list[str]) -> tuple[str | None, str | None, bool]:
    inline = _extract_flag_value(args, "from")
    if inline is not None:
        return _classify_pypi_spec(inline)
    positional = _positional_args(args)
    if not positional:
        return None, None, False
    return _classify_pypi_spec(positional[0])


def _parse_uvx_github_from(
    args: list[str],
) -> tuple[str | None, str | None, str | None, str | None]:
    inline = _extract_flag_value(args, "from")
    if inline is None or _has_interpolation(inline):
        return None, None, None, None
    match = GITHUB_URL_RE.match(inline)
    if not match:
        return None, None, None, None
    repo = match.group("repo").removesuffix(".git")
    if not repo:
        return None, None, None, None
    ref = match.group("ref")
    # Only immutable commit SHAs may be encoded as PURL versions (ADR-0016).
    # Mutable refs (branches, tags) stay out of PURL versions but remain as
    # metadata: the subdirectory for monorepo identity, and git_ref so OSV
    # federation can send the GIT package/version query. OSV's GIT version
    # query matches tagged releases; branch names (e.g. "main") are preserved
    # here but will return no results from OSV (see osv_federation.py).
    version = ref.lower() if (ref is not None and _COMMIT_SHA_RE.match(ref)) else None
    git_ref = ref if (ref is not None and version is None) else None
    name = f"{match.group('owner')}/{repo}"
    subdirectory = _github_subdirectory(match.group("fragment"))
    return name, version, subdirectory, git_ref


def _github_subdirectory(fragment: str | None) -> str | None:
    if not fragment:
        return None
    values = parse_qs(fragment).get("subdirectory")
    if not values:
        return None
    subdirectory = values[0].strip("/")
    parts = subdirectory.split("/")
    if not subdirectory or any(part in {"", ".", ".."} for part in parts):
        return None
    return subdirectory


def _parse_docker_run_image(args: list[str]) -> tuple[str | None, str | None]:
    if not args:
        return None, None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "run":
            i += 1
            break
        if i + 1 < len(args) and arg == "container" and args[i + 1] == "run":
            i += 2
            break
        if arg.startswith("-"):
            if "=" not in arg and arg in _DOCKER_GLOBAL_VALUE_FLAGS:
                i += 2
            else:
                i += 1
            continue
        return None, None
    else:
        return None, None
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if arg.startswith("-"):
            if "=" not in arg and arg in _DOCKER_VALUE_FLAGS:
                i += 2
            else:
                i += 1
            continue
        break
    if i >= len(args):
        return None, None
    return _classify_docker_image(args[i])


def _classify_docker_image(image: str) -> tuple[str | None, str | None]:
    if _has_interpolation(image) or "://" in image:
        return None, None
    image_without_digest, sep, digest = image.partition("@")
    last_slash = image_without_digest.rfind("/")
    last_colon = image_without_digest.rfind(":")
    if last_colon > last_slash:
        name = image_without_digest[:last_colon]
        version = image_without_digest[last_colon + 1 :]
    else:
        name = image_without_digest
        version = None
    if not name:
        return None, None
    if sep and digest:
        version = digest
    return name, version


def _local_mcp_identity(command: str, args: list[str], server_name: object) -> str | None:
    if not isinstance(server_name, str) or not LOCAL_MCP_ID_RE.match(server_name):
        return None
    if command == "bun" and args[:1] == ["run"]:
        return f"mcp-stdio/local:{server_name}"
    if command == "php" and len(args) >= 2 and args[0] == "artisan" and args[1].endswith(":mcp"):
        return f"mcp-stdio/local:{server_name}"
    return None


# uv global options (`uv [OPTIONS] <COMMAND>`) that consume a separate
# value. Used by `_command_dispatch` to walk past option/value pairs and
# locate the actual subcommand. Inline `--flag=value` doesn't appear here
# (the value is embedded in the same token).
#
# This list is best-effort against the uv CLI as of mid-2026. Missing
# entries cause `uv <unknown-flag> <value> tool run X` to fall to binary
# fallback instead of dispatching as uvx — false negative, not false
# positive, so safe to ship and extend incrementally as new flags land.
_UV_VALUE_FLAGS = frozenset(
    {
        "--directory",
        "--project",
        "--config-file",
        "--cache-dir",
        "--python",
        "--python-preference",
        "--color",
        "--default-index",
        "--index",
        "--index-url",
        "--extra-index-url",
        "--find-links",
        "--keyring-provider",
        "--allow-insecure-host",
        "--trusted-host",
    }
)


def _command_dispatch(command: str | None, args: list[str]) -> tuple[str, list[str]]:
    """Normalize `uv tool run <pkg>` into the equivalent `uvx <pkg>` form.

    Walks past leading uv global options to find the actual subcommand,
    so `uv --offline tool run weather-mcp==0.5.0` dispatches but
    `uv --directory tool run python` (directory literally named `tool`,
    then `run python`) does NOT. Returns (effective_command, effective_args).
    """
    if not command or _classify_command(command) != "uv":
        return command or "", args
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if "=" in args[i]:
            i += 1
        elif args[i] in _UV_VALUE_FLAGS:
            i += 2
        else:
            i += 1
    if i + 1 < len(args) and args[i] == "tool" and args[i + 1] == "run":
        return "uvx", args[i + 2 :]
    return command, args


def _classify_command(command: str) -> str:
    """Return the canonical command name for classification.

    Strips directory prefix and extension so `npx`, `/usr/local/bin/npx`,
    and `npx.cmd` all classify as `"npx"`. Case-sensitive (POSIX-correct):
    `/opt/NPX` stays `NPX` and falls to binary fallback.

    V0 is POSIX-only. Windows-specific edge cases (backslash paths,
    PATHEXT-resolved bare uppercase tokens) are deferred to V1 — they
    require disambiguating Windows-vs-POSIX intent from the manifest,
    which we can't do reliably without an OS hint.
    """
    return Path(command).stem


def parse_mcp_servers(
    servers: dict,
    source_manifest: str,
    locator_prefix: str = "$.mcpServers",
    runtime_hosts: list[str] | None = None,
) -> list[ComponentRef]:
    """Convert an `mcpServers`/`servers` dict into ComponentRefs."""
    if not isinstance(servers, dict):
        return []
    if runtime_hosts is None:
        runtime_hosts = ["claude-code"]
    refs: list[ComponentRef] = []
    for server_name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("disabled") is True:
            continue
        locator = f"{locator_prefix}.{server_name}"
        # ADR-0020: URL-bearing entries are remote MCPs and take precedence
        # over any stdio fields in the same entry. The MCP spec treats
        # transport as mutually exclusive; favoring URL matches Claude
        # Code's runtime resolution.
        raw_url = entry.get("url")
        if isinstance(raw_url, str) and raw_url:
            remote_ref = _make_remote_mcp_ref(
                url=raw_url,
                entry=entry,
                server_name=server_name,
                source_manifest=source_manifest,
                locator=locator,
                runtime_hosts=runtime_hosts,
            )
            if remote_ref is not None:
                refs.append(remote_ref)
            continue
        raw_command = entry.get("command")
        raw_args = entry.get("args") or []
        if not isinstance(raw_args, list):
            raw_args = []
        # Drop any non-string elements: `_extract_flag_value` and
        # `_positional_args` call `.startswith()` on each arg, so a stray
        # `null`, integer, or nested object would AttributeError mid-scan.
        raw_args = [a for a in raw_args if isinstance(a, str)]
        command, args = _command_dispatch(
            raw_command if isinstance(raw_command, str) else None,
            raw_args,
        )
        # Posture rules (plan 014) need the raw install reference to decide
        # whether it's mutable. Reconstruct from raw_command + raw_args so
        # we capture exactly what the user wrote (before _command_dispatch
        # rewrites `uv run python` etc.).
        install_source = _format_install_source(raw_command, raw_args)
        cmd_class = _classify_command(command) if command else ""
        if cmd_class == "npx":
            name, version = _parse_npx_args(args)
            if name and version:
                refs.append(
                    ComponentRef(
                        ecosystem="npm",
                        name=name,
                        version=version,
                        source_manifest=source_manifest,
                        source_locator=locator,
                        extra=_mcp_ref_extra(
                            source_manifest, install_source, server_name, runtime_hosts
                        ),
                    )
                )
            elif name:
                refs.append(
                    ComponentRef(
                        ecosystem="npm",
                        name=name,
                        source_manifest=source_manifest,
                        source_locator=locator,
                        extra=_mcp_ref_extra(
                            source_manifest, install_source, server_name, runtime_hosts
                        ),
                    )
                )
        elif cmd_class == "uvx":
            github_name, github_version, github_subdirectory, github_ref = _parse_uvx_github_from(
                args
            )
            if github_name:
                extra = _mcp_ref_extra(source_manifest, install_source, server_name, runtime_hosts)
                if github_subdirectory is not None:
                    extra["source_subdirectory"] = github_subdirectory
                if github_ref is not None:
                    extra["git_ref"] = github_ref
                refs.append(
                    ComponentRef(
                        ecosystem="github",
                        name=github_name,
                        version=github_version,
                        source_manifest=source_manifest,
                        source_locator=locator,
                        extra=extra,
                    )
                )
            else:
                name, version, pinned = _parse_uvx_args(args)
                if name and pinned:
                    refs.append(
                        ComponentRef(
                            ecosystem="PyPI",
                            name=name,
                            version=version,
                            source_manifest=source_manifest,
                            source_locator=locator,
                            extra=_mcp_ref_extra(
                                source_manifest, install_source, server_name, runtime_hosts
                            ),
                        )
                    )
                elif name:
                    refs.append(
                        ComponentRef(
                            ecosystem="PyPI",
                            name=name,
                            source_manifest=source_manifest,
                            source_locator=locator,
                            extra=_mcp_ref_extra(
                                source_manifest, install_source, server_name, runtime_hosts
                            ),
                        )
                    )
        elif cmd_class == "bunx":
            name, version = _parse_npx_args(args)
            if name and version:
                refs.append(
                    ComponentRef(
                        ecosystem="npm",
                        name=name,
                        version=version,
                        source_manifest=source_manifest,
                        source_locator=locator,
                        extra=_mcp_ref_extra(
                            source_manifest, install_source, server_name, runtime_hosts
                        ),
                    )
                )
            elif name:
                refs.append(
                    ComponentRef(
                        ecosystem="npm",
                        name=name,
                        source_manifest=source_manifest,
                        source_locator=locator,
                        extra=_mcp_ref_extra(
                            source_manifest, install_source, server_name, runtime_hosts
                        ),
                    )
                )
        elif cmd_class == "docker":
            name, version = _parse_docker_run_image(args)
            if name:
                refs.append(
                    ComponentRef(
                        ecosystem="docker",
                        name=name,
                        version=version,
                        source_manifest=source_manifest,
                        source_locator=locator,
                        extra=_mcp_ref_extra(
                            source_manifest, install_source, server_name, runtime_hosts
                        ),
                    )
                )
            else:
                refs.append(
                    ComponentRef(
                        component_identity=f"mcp-stdio/binary:{command}",
                        source_manifest=source_manifest,
                        source_locator=locator,
                        extra=_mcp_ref_extra(
                            source_manifest, install_source, server_name, runtime_hosts
                        ),
                    )
                )
        elif command:
            local_identity = _local_mcp_identity(cmd_class, args, server_name)
            if local_identity:
                refs.append(
                    ComponentRef(
                        component_identity=local_identity,
                        source_manifest=source_manifest,
                        source_locator=locator,
                        extra=_mcp_ref_extra(
                            source_manifest, install_source, server_name, runtime_hosts
                        ),
                    )
                )
                continue
            refs.append(
                ComponentRef(
                    component_identity=f"mcp-stdio/binary:{command}",
                    source_manifest=source_manifest,
                    source_locator=locator,
                    extra=_mcp_ref_extra(
                        source_manifest, install_source, server_name, runtime_hosts
                    ),
                )
            )
    return refs


def _mcp_ref_extra(
    source_manifest: str,
    install_source: str,
    server_name: str,
    runtime_hosts: list[str],
) -> dict:
    return {
        "component_type": "mcp_server",
        "runtime_hosts": list(runtime_hosts),
        "declared_by": {"kind": "manifest", "path": source_manifest},
        "component_path": [{"type": "mcp_server", "name": server_name}],
        "install_source": install_source,
    }


def _make_remote_mcp_ref(
    url: str,
    entry: dict,
    server_name: str,
    source_manifest: str,
    locator: str,
    runtime_hosts: list[str],
) -> ComponentRef | None:
    """Build a ComponentRef for a URL-bearing (remote) MCP entry per ADR-0020.

    Returns None if the URL is malformed, empty after parsing, or contains
    interpolation we can't resolve at scan time.
    """
    identity = _normalize_remote_identity(url)
    if identity is None:
        return None
    transport = _classify_remote_transport(entry)
    extra = {
        "component_type": "mcp_server",
        "transport": transport,
        "url": url,
        "runtime_hosts": list(runtime_hosts),
        "declared_by": {"kind": "manifest", "path": source_manifest},
        "component_path": [{"type": "mcp_server", "name": server_name}],
        # Posture rules (plan 014) consume install_source. For remote MCPs
        # the install reference is the URL itself; the mutable-install rule
        # is stdio-focused and the insecure-transport rule reads url
        # directly from the manifest, but we keep the field populated for
        # consistency with stdio refs.
        "install_source": url,
    }
    return ComponentRef(
        component_identity=identity,
        source_manifest=source_manifest,
        source_locator=locator,
        extra=extra,
    )


def _normalize_remote_identity(url: str) -> str | None:
    """Normalize a URL into the `mcp-remote/<host>/<path>` identity form.

    See ADR-0020 for the normalization rules. Returns None when the URL
    has no parseable host, contains `${...}` interpolation, or otherwise
    can't be normalized into a stable identity.
    """
    if _has_interpolation(url):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    # urlparse keeps userinfo on .netloc but exposes .hostname stripped and
    # lowercased — exactly what we want for the identity.
    host = parsed.hostname
    if not host:
        return None
    try:
        port = parsed.port
    except ValueError:
        # Malformed port like "https://x.com:notaport/" raises on access.
        return None
    if port is not None and port not in (80, 443):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    return f"mcp-remote/{host}{path}"


def _classify_remote_transport(entry: dict) -> str:
    """Return the transport label for a URL-bearing entry.

    Defaults to "http" when no explicit `type` is provided — matches the
    common shorthand for plain-URL entries in Claude Code configs.
    """
    raw_type = entry.get("type")
    if isinstance(raw_type, str) and raw_type:
        return raw_type
    return "http"


def _format_install_source(raw_command: object, raw_args: list[str]) -> str:
    """Reconstruct the user-facing install reference for posture-rule input.

    For `command: "uvx", args: ["mcp-bar"]` returns `"uvx mcp-bar"`.
    Uses shlex.join so args containing spaces are shell-quoted, allowing
    resolve_mcp_launch_dir to recover the original tokens via shlex.split.
    For non-string commands or empty args, falls back gracefully.
    """
    if not isinstance(raw_command, str):
        return ""
    if not raw_args:
        return raw_command
    return shlex.join([raw_command, *raw_args])


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return []
    # `mcpServers` (Claude Code/Desktop/Cursor) and `servers` (VS Code) are
    # two names for the same shape. Prefer mcpServers when both exist —
    # only relevant if a config file straddles host conventions.
    if isinstance(data.get("mcpServers"), dict):
        return parse_mcp_servers(
            data["mcpServers"],
            source_manifest=str(path),
            locator_prefix="$.mcpServers",
            runtime_hosts=["claude-code"],
        )
    if isinstance(data.get("servers"), dict):
        # `servers` is the VS Code convention; host cannot be inferred here.
        return parse_mcp_servers(
            data["servers"],
            source_manifest=str(path),
            locator_prefix="$.servers",
            runtime_hosts=[],
        )
    # Flat shape (no wrapper). Some real Claude Code plugins ship `.mcp.json`
    # as a bare `{server_name: {command, args}}` map without the conventional
    # `mcpServers` envelope. Detect by requiring every value to be a dict
    # with a server-shaped key (`command` for stdio, `url` for HTTP). Strict
    # all-or-nothing avoids false positives on top-level configs that happen
    # to contain a server-shaped sub-object alongside unrelated keys.
    if _looks_like_flat_server_map(data):
        return parse_mcp_servers(
            data,
            source_manifest=str(path),
            locator_prefix="$",
            runtime_hosts=[],
        )
    return []


def _looks_like_flat_server_map(data: dict) -> bool:
    if not data:
        return False
    return all(isinstance(v, dict) and ("command" in v or "url" in v) for v in data.values())
