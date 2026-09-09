# ADR 0003 — Canonicalization restrictions and generated bindings

- **Status:** Accepted
- **Date:** 2026-09-09
- **Module:** 02 (shared schemas and deterministic reducers)
- **Builds on:** [ADR 0002](0002-configuration-and-workspace-boundaries.md)

## Context

Every digest-bound guarantee in AccessForge — sealed manifests, approval binding, the evidence
hash chain — depends on Python and TypeScript producing byte-identical canonical JSON. A silent
divergence between the two would not surface as an error; it would surface as approvals that stop
matching and chains that stop verifying, long after the cause.

## Decision 1 — reject non-integral floats rather than canonicalize them

RFC8785 defers number formatting to ES6 `Number::toString`, which is shortest-round-trip. Matching
that exactly in Python for all doubles is possible but fiddly, and a mismatch would be silent.

CONTRACTS section 4 already says no floating-point value may grant authority or decide an outcome.
So rather than implement a delicate agreement nobody should be relying on, both implementations
**refuse** non-integral numbers with a clear error. Integral floats normalize to integers, which
also renders `-0.0` as `0` exactly as ES6 does.

The consequence is deliberate: there is nowhere in a canonicalized document for a confidence score
to live. `outcome-record.schema.json` has no such field either.

Integers outside the JSON safe range are rejected for the same reason, per the same section's
overflow rule.

## Decision 2 — object keys sort by UTF-16 code unit, and it is tested above the BMP

RFC8785 orders properties by UTF-16 code unit. JavaScript's `Array.prototype.sort` does this
natively; Python's string comparison does **not** — it compares code points, and the two differ
above the BMP. U+FF00 sorts before U+1F600 by code point, and after it by code unit, because
U+1F600 is the surrogate pair 0xD83D 0xDE00.

Python therefore sorts on `key.encode("utf-16-be")`. A test uses exactly that pair, and a mutation
check confirms the test fails when the sort is changed to code-point order. Without a test above
the BMP this bug would pass every ASCII fixture.

## Decision 3 — schemas are authoritative; bindings are generated and committed

`packages/contracts/schemas/*.json` is the single definition of every wire contract. The Python
and TypeScript constant files are produced by `scripts/generate_contract_bindings.py` and
committed **only so that drift is detectable**: CI regenerates them and fails if anything changes.

Hand-editing a generated file is self-defeating — the next check overwrites the intent and fails.

A separate test asserts that the hand-written Python enums equal the schema-derived constants, so
the domain code cannot quietly diverge from the contract it claims to implement.

## Decision 4 — shared vectors owned by neither language

`packages/contracts/vectors/canonicalization.json` is read by both test suites. A differential
test additionally executes both implementations over payloads that are not in the vectors —
astral-plane characters, every C0 control, combining sequences, safe-integer boundaries — because
the vectors only cover cases someone thought of.

That differential test **fails rather than skips** when the TypeScript build is absent. An unbuilt
implementation cannot be reported as agreeing with anything.

## Consequences

Callers that genuinely need a fractional value must carry it outside the canonicalized document,
or represent it as a scaled integer with its scale recorded. This is a real constraint and is
accepted deliberately.

Adding a state to the vocabulary now requires editing a schema, regenerating bindings, and
updating the domain enum — three places that CI cross-checks. That friction is the point: adding
a run status is a contract change, not an implementation detail.

## Rejected alternatives

- **A third-party JCS library in each language.** Would move the agreement question to whether two
  independent libraries agree, which is the same problem with less visibility.
- **Full ES6 float formatting.** Deferred rather than half-implemented. It can be added later with
  its own shared vectors if a real need appears; guessing now would create the exact silent
  divergence this module exists to prevent.
- **Generating full typed models from schemas.** Larger and not yet needed. Module 18 owns
  generated API clients; this module generates only what both languages must agree on today.
