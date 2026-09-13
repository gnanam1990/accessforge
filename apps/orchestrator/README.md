# `apps/orchestrator`

Module 12 implements the bounded Strands planning worker in
`accessforge_orchestrator.navigator`. Each invocation creates a fresh agent with one explicit tool,
no directory discovery, session memory, background work, generic shell/browser/HTTP/repository
tools, or outcome-verification capability.

Module 13's first diagnosis slice is separate under `accessforge_orchestrator.diagnosis`: a
zero-tool Strands worker consumes a closed privileged projection, validates every evidence/source
reference after structured output, and reads only digest-pinned UTF-8 excerpts from the exact frozen
commit. The retained-input preparation and diagnosis delivery path now connects the original
evaluation to immutable finding occurrences, linked follow-up analysis and the findings read API.
Request authorization and source/evidence identity are rechecked around the model call. UI delivery,
durable pre-call model reservation/budget accounting and the real failed-reader acceptance run remain
pending; the internal delivery function is not started automatically or exposed as an unrestricted API.

The process receives a sealed navigator projection and a control-plane supplied durable checkpoint
sink. The Bedrock credential chain belongs to this orchestrator process; desktop-runner, target,
repository, publisher and observer principals are separate and never become navigator inputs.

Importing or constructing the agent makes no model call. Real invocation requires configured AWS
credentials, access to the pinned Bedrock model and explicit approval for billable use.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
