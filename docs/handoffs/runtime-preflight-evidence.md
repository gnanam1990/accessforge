# Action-bound runtime preflight evidence

The authenticated desktop runner now retains its second, live physical preflight
through the machine session before entering the reader adapter. The first preflight
still rejects unsafe execution before creating an action intent. Neither step starts
the reader, changes permissions or relaxes the empty verified-profile matrix.

`POST /v1/workspaces/{workspace_id}/supervisor-sessions/{session_id}/actions/{action_id}/preflight`
requires the private machine-session bearer credential and fresh exact run/attempt/
lease authority. The action must already have a dispatch commitment and remain
unresolved. The source contains only action ID/sequence, UTC capture time and the
fourteen closed TRUE/FALSE/UNKNOWN check conditions. Probe detail strings, host paths,
account names, URLs, fixture values and purported observed identity digests are not
accepted. Capture time is bounded against server dispatch/receipt time; it grants no
extra lease lifetime.

The original lifecycle stream retains one immutable source identity per action.
An identical duplicate returns the original event; changed content refuses. Session
serialization assigns a contiguous sequence without a new table or historical
backfill. Normal STOP closes the actual lifecycle tail, including these reports.
Existing artifact retention therefore includes runtime reports alongside distinctly
labelled admission receipts. Missing old reports are not manufactured.

The native client validates the exact acknowledgement and sends no second report
for that action. A lost/mismatched receipt fences further input; UNKNOWN/FALSE checks
may be retained but still refuse adapter entry. Cancellation or transport abortion
does not prove that a committed dispatch or evidence write rolled back.

## Evidence boundary

Provenance is `RUNTIME_PROBE_REPORT`, not independent identity attestation. This
proves what the authenticated supervisor submitted, not that a compromised host
measured it honestly. Trusted deployment, concrete setup/capture/source/build/model
identity producers and actual VoiceOver execution remain unproven. The finalizer
is deliberately unchanged: these reports alone cannot promote INCONCLUSIVE to
PASS/FAIL or establish a verified repair.

Scoped Ruff/mypy, desktop TypeScript typecheck, OpenAPI/client generation and diff
checks run locally. Existing CI fixtures now cover native HTTP retention and the
expanded artifact tail, with focused malformed/duplicate and lost-ack cases. No
local full suite, actual reader, provider call, userdata migration, deployment or
OS change was run to implement this bridge.
