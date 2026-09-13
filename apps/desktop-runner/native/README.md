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

TypeScript and native Swift compilation passed. Earlier focused guard/runner cases passed with
synthetic physical adapters. A prior live read-only diagnostic refused outside its target; the
controlled Safari smoke did not establish a successful observation (document unavailable/different,
then lost foreground). The smoke was stopped at the user's request and was not repeated. URL-valued
AXDocument is now handled alongside string attributes, but positive physical compatibility remains
unverified. CLI rejection cases are committed for CI, not run locally.

No verified profile matrix or default reader capability changed. Physical identity/preflight
ingestion, production execution bootstrap and actual VoiceOver/matched repair remain pending.
