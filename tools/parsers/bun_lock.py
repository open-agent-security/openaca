"""Parse Bun's text lockfile (`bun.lock`).

Walks the top-level `packages` map and emits one ComponentRef per resolved
package. Each value is an array whose element [0] is the resolved
`name@version` (e.g. "@discordjs/builders@1.13.1"); the version is taken from
there so it is always the exact pinned version. The empty-string / workspace
key is the host package — skipped, since plugin self-identity is emitted by
claude_install from plugin.json. All emissions tag `extra["transitive"]=True`
so the lockfile-vs-manifest distinction propagates to SARIF
properties.coverage.

Dev-dep filtering: when `workspaces` is present, a BFS from each workspace's
`dependencies` and `optionalDependencies` (through each package's own dep map
at `packages[name][2]`) collects runtime-reachable keys. Entries not in that
set are `devDependencies`-only and are skipped. When `workspaces` is absent or
yields no runtime seeds, all entries are emitted (safe degradation).

Bun lockfiles observed in the wild are strict JSON with trailing commas — no
comments, single quotes, or unquoted keys. We therefore strip trailing commas
with a small string-aware preprocessor and hand the result to the stdlib JSON
parser, rather than taking a JSON5 dependency. If Bun ever emits broader
JSONC/JSON5 syntax, `json.loads` raises and this parser fails closed (returns
[]), matching the other lockfile parsers.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef


def parse(path: Path) -> list[ComponentRef]:
    try:
        raw = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        data = json.loads(_strip_trailing_commas(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    packages = data.get("packages")
    if not isinstance(packages, dict):
        return []
    workspaces = data.get("workspaces")
    runtime_keys: set[str] | None = None
    if isinstance(workspaces, dict):
        runtime_keys = _collect_runtime_keys(packages, workspaces)
    refs: list[ComponentRef] = []
    for key, entry in packages.items():
        if not key:
            continue  # workspace / host-root entry
        if runtime_keys is not None and key not in runtime_keys:
            continue  # devDependencies-only; not shipped at plugin runtime
        if not isinstance(entry, list) or not entry:
            continue
        spec = entry[0]
        if not isinstance(spec, str):
            continue
        # "@scope/name@1.2.3" -> ("@scope/name", "@", "1.2.3");
        # "name@1.2.3" -> ("name", "@", "1.2.3").
        name, _, version = spec.rpartition("@")
        if not name or not version:
            continue
        refs.append(
            ComponentRef(
                ecosystem="npm",
                name=name,
                version=version,
                source_manifest=str(path),
                source_locator=f"$.packages[{key!r}]",
                extra={"transitive": True},
            )
        )
    return refs


def _collect_runtime_keys(packages: dict, workspaces: dict) -> set[str] | None:
    """BFS from workspace runtime deps through each package's dep map.

    Returns None when no runtime seeds are found (workspaces present but all
    deps are devDependencies or empty) so the caller can fall back to emitting
    everything rather than silently returning nothing.
    """
    seeds: set[str] = set()
    for ws in workspaces.values():
        if not isinstance(ws, dict):
            continue
        for dep_name in ws.get("dependencies", {}):
            seeds.add(dep_name)
        for dep_name in ws.get("optionalDependencies", {}):
            seeds.add(dep_name)
    if not seeds:
        return None
    reachable: set[str] = set()
    queue = list(seeds)
    while queue:
        name = queue.pop()
        if name in reachable:
            continue
        reachable.add(name)
        entry = packages.get(name)
        if isinstance(entry, list) and len(entry) >= 3 and isinstance(entry[2], dict):
            for dep_name in entry[2]:
                if dep_name not in reachable:
                    queue.append(dep_name)
    return reachable


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas (a comma whose next non-whitespace char is `}` or
    `]`) that appear OUTSIDE string literals. Tracks string state and escapes so
    a literal comma inside a string value is never touched."""
    out: list[str] = []
    n = len(text)
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                continue  # drop the trailing comma
        out.append(ch)
    return "".join(out)
