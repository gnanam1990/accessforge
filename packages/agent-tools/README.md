# `packages/agent-tools`

`navigation/` owns the strict model projection, sealed action schema and final in-process check
before a proposal reaches the desktop supervisor. It exposes no generic computer or network tool.

`TYPE_TEXT` carries only a sealed fixture-value reference; the gateway resolves the value after
model validation. Dynamic lease epoch, cancellation, action-in-flight, origin, action budget and
wall-time state are re-read for every dispatch. The desktop supervisor remains responsible for an
atomic durable intent write and its own final authority check immediately before the OS effect.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
