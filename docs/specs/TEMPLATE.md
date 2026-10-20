# Short noun phrase for the design

**Status:** draft | accepted | implemented
**Date:** YYYY-MM-DD

A spec holds the design. Copy this file to the path that the project
CLAUDE.md gives for specs. For a living component spec, update this file
in place when the component changes. Delete the guidance text when you
write the spec.

## Context

What exists today, and what is wrong or missing. Name the files, the
failing case or the incident. A reader must be able to see why this
work is necessary now.

## Goals

The results that tell us the work is done. Write each goal so that a
test or a demonstration can show it.

## Non-goals

What this spec does not do. Name the ADR or the later plan that owns
each item, if one exists. This list stops scope creep in review.

## Decisions

| ADR | Decision |
|---|---|
| [NNNN](../adrs/NNNN-title.md) | One line that states the choice |

The ADRs that you pulled out of this spec. Write the spec first. Then
write an ADR for each decision whose rejected alternative is plausible.
Each ADR links back to the section of this spec that has the detail.
If no decision meets the ADR bar, write "None."

## Design

How the system works. Someone must be able to implement from this
section alone. Use one subsection for each area, for example:

### Components and boundaries

What each part owns, and what crosses each boundary.

### Data model

Tables, files or records, with their fields and their owners.

### Interfaces

The API routes, CLI commands, flags, file formats and events. Use the
exact names.

### Flows

The steps of each main path, in order.

## Failure behavior

What happens when a dependency is missing, slow or incompatible, and
when input is malformed. Say what the user sees and what continues to
work.

## Verification

The tests that show each goal. The invariants that the change must
keep, and how a test checks each one. The behavior that stays
unverified, and why.

## Open questions

Each question that is not resolved, with the condition or the person
that resolves it. Write "None." when there are none.
