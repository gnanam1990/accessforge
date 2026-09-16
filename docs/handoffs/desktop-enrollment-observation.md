# Read-only desktop enrollment observation

From a built checkout, on the actual dedicated macOS console:

```sh
node apps/desktop-runner/dist/main.js --enrollment-observation
```

The command reads the OS hardware UUID, foreground console audit session, the probing process's
audit session, lock state and the existing host-observed VoiceOver/Safari profile. Both applications
must already be running. Missing, locked, mismatched or observed-changing identities fail with exit
78 and no JSON. The command never starts either application, probes or grants TCC permission,
loads an operator module, contacts the API, mints credentials or performs a reader action.

Successful stdout contains `session`, `profile`, their canonical `profileDigest`, and the explicit
meaning `LOCAL_DECLARATION_NOT_ENROLLMENT_OR_QUALIFICATION`. Keep the output private: it includes
the machine's stable hardware identifier. This is a local declaration and a bounded series of
reads, not an atomic desktop snapshot, trusted service attestation or successful preflight.

An authorized operator can review `session` and `profile` for the existing enrollment route,
alongside an independently issued single-use token and a chosen runner name. Do not post the whole
observation envelope: the enrollment route accepts exactly `token`, `name`, `session`, `profile`.
Do not put the token into logs, shell history, public issue text or this observation output.
Enrollment still produces `PREFLIGHT_REQUIRED`, and the result's stored profile digest must match
the reviewed profile. Reader qualification, explicit consent, run admission and runtime evidence
remain separate. A changed console/session requires fresh observation, not editing an old JSON.

Supply one bounded `Idempotency-Key` (1–200 characters) on the enrollment POST and retain that key
with the reviewed request until the result is known. If a response is lost, retry only the exact
same key and body while current workspace authority remains valid. The server replays the original
receipt with `Idempotent-Replay: true`, without consuming the token a second time. A changed body
with the same key conflicts. The receipt describes enrollment, not current runner readiness: read
the inventory for current status. The existing idempotency retention window applies; do not assume
indefinite replay. Without a key, legacy callers remain single-use and cannot recover by retry.
Token issuance itself is not replayed; never create another token automatically after uncertainty.

Local development checks use injected observations only. They do not establish actual host support
or authorize startup. A native observation on the user's machine has not been performed for this
change.
