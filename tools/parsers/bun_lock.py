"""Parse Bun's text lockfile (`bun.lock`).

Walks the top-level `packages` map and emits one ComponentRef per resolved
package. Each value is an array whose element [0] is the resolved
`name@version` (e.g. "@discordjs/builders@1.13.1"); the version is taken from
there so it is always the exact pinned version. The empty-string / workspace
key is the host package — skipped, since plugin self-identity is emitted by
claude_install from plugin.json. All emissions tag `extra["transitive"]=True`
so the lockfile-vs-manifest distinction propagates to SARIF
properties.coverage.

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
    refs: list[ComponentRef] = []
    for key, entry in packages.items():
        if not key:
            continue  # workspace / host-root entry
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
