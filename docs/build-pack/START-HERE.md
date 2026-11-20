# Build AccessForge Tester Desk

**Starting status:** SPECIFICATION_ONLY. Product implementation, product tests and external integration are NOT_RUN for this new scope. The source baseline for an upstream repository is listed in [SOURCES.md](SOURCES.md).

## First session

Supply this ZIP and the intended project checkout, or a new dedicated repository for this product. Extract this pack inside the target repo under `docs/build-pack/` when that path is free. If it already contains a pack, keep the supplied version alongside it and point the prompt to that directory. Keep the source pack immutable and track implementation progress in a separate project directory.

Paste this message into Devin, Claude Code, Codex or Gemini:

> Build AccessForge Tester Desk in the supplied target repository. Read START-HERE.md and MASTER-PROMPT.md in this pack. Inspect the checkout and any existing build-progress/accessforge-tester-desk/HANDOFF.md first. Start at the first unverified dependency, implement the project through its normal entry points, run the tests, and maintain the handoff after each module. Continue across available modules. Record the exact dependency and evidence if external integration is blocked.

## Resume in another agent

> Continue AccessForge Tester Desk at the current repository HEAD. Read build-progress/accessforge-tester-desk/HANDOFF.md, STATUS.json and this pack’s MASTER-PROMPT.md. Verify the recorded commit exists and reproduce its last relevant check. Inspect uncommitted work. Resume the first incomplete module using its recorded next action. Preserve earlier work and report exact commands and outstanding dependencies.

## Module mode

> Implement one ready module from MODULES.md in AccessForge Tester Desk. Read its prompt and CORE-CONTRACT.md, run its positive and failure tests, and record the resulting revision and next action in the project handoff.

## Reading order

1. [Product requirements](PRD.md) and [source map](SOURCES.md)
2. [Technical design](TDD.md), [provider gates](KITE-AND-PROVIDERS.md) and [security](SECURITY.md)
3. [Module prompts](MODULES.md), [UI](UI-SPEC.md) and [tests](TEST-PLAN.md)
4. [Continuity and delivery](DELIVERY.md) and [master instructions](MASTER-PROMPT.md)

For a structural self-check, use Node 20+ from this pack directory: `node tools/check-build-pack.mjs --root .`. Keep runtime artifacts and progress outside this directory so the supplied inventory can still validate it.
