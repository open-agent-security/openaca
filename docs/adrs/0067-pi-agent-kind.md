---
id: 0067
title: Compose Pi from selected package and resource declarations
status: accepted
date: 2026-09-14
supersedes: null
superseded-by: null
---

## Context

Pi 0.85.1 composes packages, extensions, skills, prompt templates, and themes
through settings, conventional directories, and shared `.agents/skills` roots.
Its package filters and project overrides affect individual files; a package
container alone cannot describe that selection. Its resource traversal differs
from the Claude-shaped traversal parameterized by ADR-0053.

## Decision

Register singleton kind `pi`, rooted at `root/pi`. A dedicated composer reuses
public graph node, occurrence-key, normalization, and identity primitives while
calling Pi's source and resource parsers. It never descends Claude surfaces in
Pi bundles and never executes packages or extensions. Pi has no native MCP
surface in this composition.

Packages are `plugin` containers; resources are `extension`, `skill`, `command`
(prompt template), and `theme`. Source-stable npm plugin and plugin-private
resource identities are constructed centrally. Raw configured sources remain
`install_source`; observed package versions remain `version`. A missing install
is retained with `installed=false` and a coverage gap. Delta overrides reuse the
base container, with per-file declaration provenance identifying the override.

Declared composition stays within the scan root and respects gitignore. It reads
neither home configuration nor trust. A Pi-bearing `package.json` declares a
package source repository; ordinary npm manifests do not. Managed package trees
and fixtures within bundles do not declare additional project surfaces.

Installed composition honors `PI_CODING_AGENT_DIR`, defaulting to `~/.pi/agent`.
Shared global skills remain under `~/.agents/skills`; project shared roots walk
up to the repository root. Consequently Pi refuses `--config-dir` under
ADR-0054: relocating only the agent root cannot specify the whole target.
Persisted/default project trust gates project resources; unresolved and denied
projects produce an exclusion gap. Invocation and extension trust overrides
cannot be inferred statically.

Coverage is partial for both sources. Extension code can register additional
resources; static selection is not proof of successful runtime loading. Invalid
skill contents remain visible with an explicit gap and disabled selection.
Name collisions follow resource precedence and first-name selection. Mutable
install references and the global project-trust default use the existing two
posture rules, without native-MCP or raw-settings posture traversal.

## Alternatives considered

- Extend Claude descent with Pi path names: would impose unrelated bundle and
  MCP semantics and cannot express file-level package deltas.
- Use the configured version as the installed version: a tag or range is an
  input to resolution, not an observation of the installed package.
- Accept `--config-dir` and quietly keep home skills: would combine independently
  addressed roots while presenting them as a complete foreign target.

## Consequences

Consumers must recognize `extension` and `theme` as agent component types.
The scanner inventories declared files without claiming runtime registration,
CLI trust outcomes, or extension execution. Foreign-home installed scanning
requires a separately designed coherent home override.
