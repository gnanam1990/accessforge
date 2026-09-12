# `apps/orchestrator`

Module 12 implements the bounded Strands planning worker in
`accessforge_orchestrator.navigator`. Each invocation creates a fresh agent with one explicit tool,
no directory discovery, session memory, background work, generic shell/browser/HTTP/repository
tools, or outcome-verification capability. Module 13 diagnosis remains separate.

The process receives a sealed navigator projection and a control-plane supplied durable checkpoint
sink. The Bedrock credential chain belongs to this orchestrator process; desktop-runner, target,
repository, publisher and observer principals are separate and never become navigator inputs.

Importing or constructing the agent makes no model call. Real invocation requires configured AWS
credentials, access to the pinned Bedrock model and explicit approval for billable use.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
