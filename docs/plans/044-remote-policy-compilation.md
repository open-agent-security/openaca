# Plan 044 — Remote policy compilation

**Goal:** An endpoint with existing remote configuration can fetch its current
policy and compile the same endpoint-specific Claude artifact as the local-file
policy command.

**Architecture:** Keep one evaluator and one host compiler. The remote command
retrieves a logical policy document, validates it with `tools.policy.parse`,
and passes the resulting `Policy` to the public compilation helper used by
`openaca policy compile`. The command writes only its explicit output path;
deployment into host-managed settings stays outside the CLI.

**ADRs:** 0048, 0049, 0061.

## Constraints

- [x] Reuse the existing remote URL and endpoint credential; do not add a
  second configuration file or token.
- [x] Retrieve `GET /api/v1/policy` and validate its object-or-null document.
- [x] Run a fresh local scan and every configured risk gate before writing.
- [x] Treat an absent, invalid, or unreachable remote policy as a failed
  compilation and preserve any existing output artifact.
- [x] Do not install into a protected host settings location or cache policy
  documents.

## Tasks

- [x] Add a typed remote-client result and request test for the policy endpoint.
- [x] Extract the local compilation path into a public helper used by both CLI
  command groups.
- [x] Add `openaca remote policy compile` with the local compiler's endpoint,
  output, dry-run, and report options.
- [x] Test a successful remote fetch and a missing-policy failure that leaves
  the previous artifact unchanged.
- [x] Document local and remote policy compilation, including the separate
  deployment step.
- [x] Run formatting, type checking, the full test suite, and a final diff
  review before opening the PR.
