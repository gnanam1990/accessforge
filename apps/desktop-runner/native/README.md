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

The same build also compiles `dist/native/voiceover-capture-probe`. Unlike the Safari AX probe,
this helper uses one read-only Apple Event addressed to an already-running Apple-signed VoiceOver
PID. It checks existing Automation permission without prompting and requests last-phrase content
with interaction disabled. It cannot launch an app by name, type, copy, speak, reset or grant
permissions. Only a static channel-health result is returned; phrase text is discarded.
See [the capture handoff](../../../docs/handoffs/voiceover-capture-probe.md) for evidence limits.

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

### Explicit browser launch port

`createSafariReferenceLauncher` in `src/safari-launcher.ts` supplies the concrete `BrowserLauncher`
port for trusted reference-app setup. Construct it with the exact controller-reserved fixture URL,
sealed browser version, an AbortSignal, fresh `authorize(signal)` callback for this browser effect,
and `assertDesktopHeld()` for the controller's already-held exclusive desktop claim. Construction
is inert. The returned function issues one bounded `/usr/bin/open -b com.apple.Safari` invocation;
no shell, AppleScript, reader startup or permission-grant command is used.

Use the function as `prepareReferenceApp({ ...privateSetupOptions, launch })` only for the trusted
baseline setup path that can reconcile its private setup endpoint. Candidate gateway setup is
separate; this launcher does not expose private candidate setup routes or authorize their use.
Never give its callbacks or private options to the navigator. Callback implementations and overall
host-controller composition remain the trusted embedding's responsibility, not JSON configuration.

`prepareSafariReferenceApp(privateSetup, host)` supplies this composition directly and derives the
launch URL from the same private origin/reserved nonce used for reconciliation. Both expected and
independently observed build digests must agree before any setup HTTP request. The existing HTTP200
confirmation gate runs before launch; no second destination can be supplied through host options.
This configured build check is not a new independent artifact measurement or environment attestation.

The native probe must independently confirm foreground signed Safari, the exact fixture document
and browser version. Native document-not-ready refusals may be resampled within an eight-second
overall deadline, but `open` is never retried. Authority is checked again and the same browser
process/document reobserved before returning a setup result. Failed/cancelled/concurrent attempts
remain one-shot even when the OS might already have opened the URL: reconcile the retained desktop
claim, do not infer no side effect or create an automatic retry. This code has mocked-host coverage;
actual launch/AT acceptance still requires explicit operator approval and real host evidence.

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
runner dependencies, an adapter with `start()`, `readerStartup: { authorize(signal), timeoutMs }`,
and `physicalPreflight` containing
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
construction nor preflight starts a reader. Call the returned `initialize()` once; actions remain
refused until initialization and its fresh post-start preflight complete. Do not start the adapter
separately. An unproven profile is refused before claim creation, network or Guidepup loading.

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

### Claimed reader/session bootstrap

`createExecutionBootstrap` in `src/execution-bootstrap.ts` composes the receiver and concrete lazy
Guidepup driver. Supply normal physical-runner dependencies except `session`/`adapter`, plus private
`receiver` configuration and the exact controller `dispatchEnvelope`. It snapshots only the bounded
reference/ticket fields and refuses mismatched identity. Construction acquires the desktop claim;
`initialize()` opens `NativeExecutionSession` once under that claim, keeping the independent secret
private. The run reception claim remains separate and is never cleaned up or replayed. Handshake
latency counts against session expiry and local lease time; clock rollback refuses startup.

Reader startup needs fresh trusted `readerStartup.authorize(signal)` checks before probes and again
before entering the SDK. This authorization must cover Guidepup's actual start behavior: its pinned
implementation terminates/restarts VoiceOver and mounts its preferences, with its own internal
startup attempts. It is not a read-only attach. Existing AppleScript configuration, OS permissions,
desktop ownership, reset/build/origin, journal and stale-input checks must already be known-good.
Only READER_ACTIVE and SPEECH_CAPTURE_WORKING are deferred until after start; post-start readiness
requires the full vocabulary plus another native Safari sample. The verified-profile gate is not
relaxed, and this path does not establish the first actual-reader proof.

After each operator startup-authorization callback, the bootstrap now calls the real machine-only
`POST /supervisor-sessions/{sessionId}/startup-authority` route with an empty body and the private
execution-session secret. The server rechecks exact current run/attempt, manual approval, lease,
session/ticket revocation, preflight and sealed policy. Expiry is capped by session, lease, approval,
manifest and elapsed policy wall budget. A prior action intent closes this startup-read path.
Repeated reads create no actions, approvals or canonical events and never extend authority.

The native client verifies exact session/reference/meaning/expiry, honors the startup AbortSignal,
and only shortens its deadline. Missing/refused/malformed/expired responses fence initialization,
with no fallback to a cached receipt. The response deliberately says
`EXECUTION_AUTHORITY_RECHECKED_NOT_READER_START_CONSENT`: the separate operator callback remains
mandatory for SDK preference/restart effects; run consent cannot silently become OS-settings consent.

Initialization is one-shot and bounded by both its timeout (at most30seconds) and the absolute
execution-lease deadline on the same supervisor clock. Every guarded step checks nonfinite/backward
clock samples and expiry, including time spent reading the desktop claim. Startup authorization is
rechecked after the SDK and postflight, before publishing readiness; physical checks alone cannot
extend expired session/consent authority. Cancellation settles the wrapper and
blocks late guarded operations. An already-entered SDK call cannot be forcibly undone by rejecting
its promise: uncertainty retains the claim and requires reconciliation, not an automatic stop,
cleanup/restart or another run. Successful initialization enables the existing authenticated action
bridge; successful STOP is still the only route to normal finish and release.

Desktop-claim/bootstrap regressions are authored for CI, not executed locally. This is a trusted
embedding API, not a deployed daemon or a navigator stdin transport. Controller startup-consent
integration, owned setup/capture/runtime identity producers, independent observer coordination,
real reader proof and deployed isolation remain incomplete. The default CLI still exits EX_CONFIG.

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
