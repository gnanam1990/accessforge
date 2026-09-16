# Independent observer startup authority

The baseline bootstrap now checks the current reader-startup operation signal
before and after its approval callback, before starting the independent effect
collector, and after collector readiness. Previously a late approval could start
the collector under the longer run lifetime even after the startup operation was
cancelled.

Cancellation during collector startup aborts that collector. The temporary
operation listener is removed when authorization returns: closing a successfully
completed startup scope must not kill the long-lived observer. Its existing run
lifetime, failure fencing, explicit closure and no-replay rules still apply.
Repeated fresh authorization reuses the original readiness promise, not a new
worker.

TypeScript compilation and 17 focused checks passed: four synthetic authority
cases plus the existing synthetic child-process protocol cases. They exercise
late approval, cancellation during readiness, successful scope closure,
repeated approval, refusal and run cancellation. No reader, model, live database
or deployment was used. This fixes the baseline composition but does not supply
the missing concrete private native provisioner or establish actual-reader
qualification. VoiceOver runtime testing remains paused by the owner.
