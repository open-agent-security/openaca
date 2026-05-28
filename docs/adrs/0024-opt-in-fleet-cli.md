---
id: 0024
title: Keep Fleet collection as an opt-in CLI surface
status: superseded
date: 2026-05-28
supersedes: null
superseded-by: 0025
---

## Context

OpenACA is local by default: scans, matching, posture checks, and Agent BOM
generation run without network upload unless the user explicitly chooses a
networked workflow. Fleet collection needs a repeatable endpoint-upload path
for organizations that want centralized asset history, but adding remote upload
behavior to the core CLI can blur the local-default trust boundary if it is
implicit, ambient, or hard-coded to one deployment.

The same endpoint collection also needs deployment through existing macOS
management tools. That can be satisfied either by the CLI plus launchd recipes,
or by a separate long-running agent.

## Decision

OpenACA ships Fleet collection as an explicit `openaca fleet ...` CLI surface
inside the public CLI. The command group is opt-in: users must configure a token
and API URL before any upload occurs. Local scan and BOM commands remain fully
usable without Fleet configuration. Endpoint collection is scheduled by
external launchd/MDM recipes that invoke the CLI; OpenACA does not ship a
separate daemon for this path.

Fleet upload payloads pass through local redaction validation before HTTPS
upload and before writing pending offline cache files. The backend URL remains
configurable so development, self-hosted, or private deployments can use the
same CLI protocol.

## Alternatives considered

- **Separate wrapper CLI**: rejected because it creates two command surfaces for
  the same scanner behavior and risks drift between local BOM generation and
  upload packaging.
- **Separate long-running agent daemon**: rejected for now because scheduling
  can be delegated to launchd/MDM, while daemon lifecycle, updates, logs, and
  permissions would add operational surface before it is required.
- **Implicit upload from existing scan/BOM commands**: rejected because it would
  weaken the local-default guarantee. Uploads should be visible in command
  history and require explicit Fleet configuration.
- **Hard-code the hosted API URL**: rejected because the Agent BOM and collector
  protocol should remain deployable against non-default API endpoints.

## Consequences

The public CLI contains a small amount of upload protocol code, but users can
audit exactly what is sent and when. The upload path reuses the same parser,
Agent BOM, and posture engine as local scanner workflows, reducing duplicate
implementation risk.

The design does not provide daemon-level controls such as an always-running
health monitor, centralized agent updates, or background retry outside launchd.
Pending upload replay is best-effort and runs when the scheduled CLI command
runs again.

## When to revisit

Revisit this decision if endpoint collection needs capabilities that launchd
plus a CLI cannot provide, such as continuous runtime monitoring, privileged
system inventory, mandatory update control, or reliable background retries while
no user session is active. Revisit the command boundary if Fleet upload becomes
implicit in local scan workflows.
