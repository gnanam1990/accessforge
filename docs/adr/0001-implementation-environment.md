# ADR 0001 — Implementation environment and repository selection

- **Status:** Accepted
- **Date:** 2026-09-09
- **Module:** 00 (repository and runtime capability gate)
- **Supersedes:** none

## Context

AccessForge was supplied as a specification-only package. Building it required choosing an
implementation location and establishing which real boundaries the host machine can actually prove,
before any module makes a claim that depends on one.

The specification pack warns repeatedly against two failure modes: turning the specification folder
into an application, and letting a convenient substitute (virtual screen reader, mocked model,
in-memory store) stand in for a real boundary while still being reported as proof.

## Decision

**1. A new dedicated repository, not an existing project.**
Implementation lives at `<implementation-checkout>`, published as the
public repository `github.com/gnanam1990/accessforge`. The session's original working directory belonged to
an unrelated project and was left untouched.

**2. The specification pack is vendored, not referenced.**
The complete pack is committed at `specs/accessforge/` (43 files) exactly as supplied, preserving
its internal relative links. Implementation-owned records live under `docs/` and never inside
`specs/`.

**3. A minimal bootstrap root commit was made directly on `main`.**
A pull request cannot target a base branch that does not exist. Commit `7576c05` contains only
`README.md`, `.gitignore`, and the specification pack — no application code. This is the one-time
bootstrap exception described in MASTER-BUILD-AND-MERGE §2. Every subsequent change, including
module 00's own deliverables, goes through a pull request.

**4. Module 00 is delivered under the documented pre-CI exception.**
CI does not exist yet; module 01 owns introducing it. This module's delivery is documentation-only
and its CI status is recorded as NOT YET CONFIGURED — never passed. This exception covers no
application code and expires the moment module 01 lands.

**5. PostgreSQL is the local authoritative store; no in-memory fallback.**
A real PostgreSQL 17.10 server is running locally and will back integration proof. Business-state
proof will not be satisfied by an in-memory substitute.

**6. Machine state was not modified to improve the gate's result.**
VoiceOver was not launched, the AppleScript control setting was not toggled, Accessibility
permissions were not altered, and the Docker daemon was not started. Where a capability could not
be determined read-only, it is recorded as unknown rather than guessed in either direction.

## Consequences

**Accepted now.** Module 01 may begin immediately; it depends only on module 00 and none of the
blocked capabilities. Foundation work through module 07 is largely platform-independent and can
proceed while the actual-AT path stays blocked.

**Accepted as a hard limit.** Full R1 cannot be delivered from this host: module 09 requires
Windows/NVDA and there is no Windows machine or virtualization host available. This is a capability
gap, not a scheduling problem, and no amount of implementation work closes it.

**Accepted as a near-term limit.** E0 cannot be completed until three owner actions occur: VoiceOver
AppleScript control plus Accessibility permission are enabled on a dedicated desktop session; AWS
credentials with Bedrock model access are configured (creating billable invocations, which needs
separate approval); and an authorized target application with permitted effects is named.

**Rejected alternative — build into an existing repository.** Would have risked an unrelated
product and contradicted the pack's explicit instruction.

**Rejected alternative — proceed with the virtual screen reader to keep the pipeline green.**
`@guidepup/virtual-screen-reader` is registry-available and was deliberately not installed or run,
so no claim is made about its output. It simulates a reader rather than driving one, so reporting
its results as E0 proof would violate INV-02 whatever they said — precisely the failure this product
exists to prevent.

**Rejected alternative — defer all CI to module 27.** MASTER-BUILD-AND-MERGE §5 requires meaningful
CI from module 01 onward; module 27 hardens a pipeline rather than inventing the first trustworthy one.
