# Retained reader input for navigator turns

`navigator.projection.load_retained_turn` reads an exact live manual attempt under the same
runner/run/lease lock order as action admission. It validates the current execution approval,
accepted non-revoked supervisor session, immutable policy digest, per-run safe fixture values,
wall/action budgets and caller-expected completed action sequence. Initial input can be empty only
before any action; later boundaries require contiguous resolved non-STOP actions and their original
open reader stream. Unknown, unresolved, stale, foreign, expired and post-STOP boundaries cannot be
used to manufacture fresh planning authority.

Reader records are joined through their original producer source identity to canonical event IDs.
Workspace/run/attempt, epoch, manifest, action, producer sequence, canonical position and content
digests must agree. The loader does not read observer payloads or private supervisor credentials.
Only the complete validated navigator projection may enter the model; retained IDs/digests and
sealed model configuration remain separate coordination metadata. Fixture-redacted announcements
remain redacted. Genuine silence stays empty speech; CAPTURE_UNKNOWN stays unknown. Oversized
announcements refuse the turn rather than silently truncating or substituting a summary.

This is a production read boundary, not the completed execution loop. The next coordinator must
durably reserve each billable invocation, establish explicit provider consent and independently
recheck current authority before calling the configured model. Loading a projection performs no
model invocation, OS action, verdict, historical backfill or approval mutation. The native
supervisor remains the final input gate, and no actual reader/provider capability is claimed.

Validation: scoped Ruff/mypy and diff checks locally. Existing CI receives a real DB/HTTP retention
case with labelled synthetic speech, plus small unknown/silence, identity, extra-field and size
cases. No local full suite, real model call, reader startup or deployment was performed.
