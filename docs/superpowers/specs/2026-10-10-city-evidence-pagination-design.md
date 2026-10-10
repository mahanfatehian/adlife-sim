# Saved-Run Evidence Pagination Design

**Date:** 2026-10-10

**Status:** Approved for bounded implementation

## Problem

The saved-run FastAPI viewer already returns deterministic, immutable pages for opportunity,
attention, and response evidence. Each endpoint caps a page at 100 records and returns
`next_offset`, but the vanilla browser always requests offset zero. Busy minutes in the current
test fixture contain 150 opportunities, 222 attention events, and 144 response records, so an
analyst cannot inspect valid persisted evidence after the first 100 records.

The current truncation disclosure is truthful, but an evidence viewer that cannot reach all of
its source records is incomplete. This slice closes that browser gap without changing the core,
run artifacts, schemas, simulation behavior, or server-side ordering.

## Scope

Add bounded Previous and Next navigation to the current-minute opportunity, attention, and
response rails. Keep at most 100 cards from each rail in the DOM. Display an exact inclusive
range such as `Records 101-144 of 144`, preserve canonical server order, and allow keyboard
operation at narrow viewport widths.

The final response-state list is out of scope. It is filtered to one selected synthetic agent,
whose maximum 20 campaign states already fit within one page. Scenario creation, job control,
authentication, provider settings, server writes, and remote deployment remain separate roadmap
gates.

## Data and request contract

- The fixed browser page size is 100.
- Requests send explicit `minute`, `offset`, and `limit=100` query parameters.
- A committed page must echo the requested minute, offset, and limit.
- `total` and `next_offset` must describe the returned item count exactly.
- A nonterminal page must contain 100 items and advance to `offset + items.length`.
- A terminal page must return `next_offset: null`; an offset beyond the total is refused.
- The browser never sorts, merges, or fabricates records.
- Existing summary totals and selected-agent counts continue to describe the complete matching
  minute, not only the displayed page.

## Interaction model

Each rail owns an independent page-request generation. A deliberate page action stops playback,
fetches one immutable page, validates it, and commits it only if both the timeline generation and
that rail's generation are still current. Therefore a delayed page cannot overwrite a newer
minute, and two rails can be paged independently.

Changing the model minute resets all three rails to offset zero. Selecting an agent at the same
minute preserves offsets because the rails are all-agent evidence with selected-agent
highlighting; otherwise selecting a record from a later page would immediately hide it.

Previous and Next are native buttons with explicit accessible labels and disabled terminal
states. The page status is a small polite live region; the large record lists are not live
regions. Opportunity and attention copy states that map markers reflect the currently displayed
page.

## Failure behavior

HTTP failures, malformed envelopes, inconsistent offsets, and invalid item counts show the
existing concise error banner and retain the last committed page. They never clear the list into
a misleading zero state. Timeline scrubbing invalidates any outstanding page response before it
can commit.

## Security and architecture

All requests remain same-origin GETs against fixed API paths. Rendering continues to use DOM text
properties only; pagination adds no HTML injection, arbitrary path, external resource, browser
storage, credential, or network requirement. The FastAPI layer stays read-only and the adapter-
free core is unchanged.

## Verification

Browser tests must traverse every page and compare rendered canonical IDs to the source evidence
without omissions or duplicates. They also cover exact ranges, disabled states, keyboard use,
minute reset, delayed responses, malformed envelopes, 390-pixel containment, no external
requests, and source-object immutability. Existing API tests remain the server contract gate.

This is incremental D1/D2/D4 progress. It does not close the parent workbench gates.
