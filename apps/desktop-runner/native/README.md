# Native Safari origin integration

Build on the dedicated macOS runner with existing Xcode command-line tools:

```sh
pnpm --filter @accessforge/desktop-runner build
pnpm --filter @accessforge/desktop-runner build:native
```

This compiles repository-owned Swift into `dist/native/safari-origin-probe`, using a private
temporary directory and atomic replacement after success. The output must be an owned directory
not writable by others; the executable is mode0700. No external binary, setup package or permission
is installed. Linux does not build a synthetic helper.

## Read-only operator diagnostic

Supply JSON through a private operator file/stdin, not arguments or navigator data:

```json
{"expectedUrl":"http://127.0.0.1:3000/form/OWNED_FIXTURE_NONCE","expectedBrowserVersion":"26.6"}
```

Use the exact sealed owned fixture: `/form/` plus a16–64-character nonce, no credentials, query or
fragment, and the sealed profile's browser version.

```sh
node apps/desktop-runner/dist/probe-safari.js < /private/operator/safari-probe.json
```

Exit0/OBSERVED_ONCE means one browser-origin observation, not readiness, reader proof, execution
authority or PASS. Exit78/UNKNOWN carries a closed diagnostic code. Private URL/nonce, native stderr,
titles and page content are never printed. Input is bounded to8KiB and the command to5seconds.
Extra fields are refused. It holds no ticket/session secret and does not focus/start Safari.

The helper uses NSWorkspace, Security and read-only Accessibility attributes: foreground
Apple-signed Safari, process launch identity, bundle version, focused nonmodal window and its exact
document URL. Foreground/window/document observations are repeated before reporting. Existing
Accessibility permission is required; there is no prompt/grant or Apple Event. Missing attributes
refuse, not TRUE. The client limits each helper call to1.5seconds/16KiB, checks the owned executable
and fences malformed/mismatched results, process replacement and concurrent sampling.

## Authenticated runner embedding

Use `createSafariAuthenticatedRunner` from `src/safari-origin.ts` in trusted bootstrap code. It
accepts existing AuthenticatedRunner options plus `safari: { expectedUrl, expectedBrowserVersion }`
and supplies live origin observation. Independent `preflight` and `authorizePhysicalAction`
callbacks, authenticated session, lease, journal, adapter and evidence sink remain mandatory.

Each check is fresh and binds the first observed PID plus launch time. The runner checks again
after durable intent and focus/effect authorization, before the adapter. Only origin reaches the
server intent; private fixture URL/OS metadata do not enter navigator observations. This is not an
atomic lock on focus or proof of VoiceOver cursor location. Dedicated session and physical
focus/effect authorization remain required. Helper/service isolation from other code under the
same OS user is a deployment boundary, not established by executable ownership checks.

## Evidence limits

### Session-bound physical preflight

`createPhysicalSafariRunner` in `src/physical-preflight.ts` supplies host/session preflight and
native Safari origin. Pass `desktopClaimDirectory` (the shared private host claim root), the normal
runner dependencies and `physicalPreflight` containing
`expectedDesktopSessionId` and `observeRuntimeEvidence(signal)`. The latter reads owned setup,
capture and deployment observations; it must not reset, type or start a reader and should honor its
AbortSignal. The collector uses the same monotonic clock as the action supervisor.

The expected audit ID must be independently assigned to the dedicated runner and match the
controller's desktop-session binding, not derived by accepting the current foreground session.
The host compares console and process identity using Apple's read-only
[SessionGetInfo](https://developer.apple.com/documentation/security/sessiongetinfo(_:_:_:)).
The ioreg parser supports arrays of registry roots and rejects ambiguous active consoles or malformed
lock/identity values. Merely finding a signed-in console does not establish ownership.

The collector samples console identity before/after its read and measures clock progress rather
than accepting caller-supplied healthy-clock flags. Nonfinite/backwards/non-advancing clocks,
session drift/ambiguity and concurrent/late reads fence reuse. The async evidence read has a bounded
deadline; synchronous OS commands retain their individual timeouts, and over-budget completed
samples are refused as stale. Missing setup/build/speech remains UNKNOWN. The collector neither
mints a canonical runtime receipt nor changes the finalizer's missing-identity result. Session
equality alone is not exclusivity against another runner/process in the same login session.

### Durable desktop exclusion

The physical factory now returns a restricted runner holding `desktop-<assigned audit ID>.json`
inside `desktopClaimDirectory`. Provision ONE canonical absolute private directory owned by the
runner OS user, shared across ALL its run and runner registrations on this host. A different root
per registration defeats this cooperative exclusion and is not a supported deployment. Neither
the factory nor the preflight starts a reader: bootstrap must construct this claimed runner before
starting the reader, and must not start one when construction refuses.

Creation is exclusive, mode0600, and flushes both the exact session/reference/nonce payload and
parent directory before returning. Each action checks the original directory and file ownership,
permissions, device/inode and bounded content; the physical factory rechecks around authorization
and synchronously immediately before the adapter. Claims contain dispatch identities, not tickets,
execution session secrets or reader content. The private root is not replaced/restored concurrently.
This is cooperative isolation, not a sandbox against malicious same-user/root code or manual input.

Only successful STOP, complete exact local journal readback and successful server finish ACK allow
release. Cancellation, unknown execution, lost ACK, changed claim or partial/crash creation keeps
the claim; no age/PID check, startup cleanup or automatic reclaim exists. Reconcile the exact
attempt and physical state independently before any operator removal. A release I/O failure is
reported as unconfirmed, never blindly retried. The finished wrapper cannot send more input.
Low-level `AuthenticatedRunner`, `createSafariAuthenticatedRunner` and candidate proof helpers
remain primitives: they do not themselves acquire this claim or establish host-wide exclusivity.

Desktop-claim regressions are authored for CI, not executed locally. Physical runtime identity
ingestion, production bootstrap, real reader proof and deployed isolation remain incomplete.

TypeScript builds and a compile-only Swift expression check passed. Ownership/ioreg/clock/collector
cases are committed for CI and were not run locally. No physical-reader test loop was performed.

TypeScript and native Swift compilation passed. Earlier focused guard/runner cases passed with
synthetic physical adapters. A prior live read-only diagnostic refused outside its target; the
controlled Safari smoke did not establish a successful observation (document unavailable/different,
then lost foreground). The smoke was stopped at the user's request and was not repeated. URL-valued
AXDocument is now handled alongside string attributes, but positive physical compatibility remains
unverified. CLI rejection cases are committed for CI, not run locally.

No verified profile matrix or default reader capability changed. Physical identity/preflight
ingestion, production execution bootstrap and actual VoiceOver/matched repair remain pending.
