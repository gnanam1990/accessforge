# AccessForge capability matrix

**Produced by:** module 00 (repository and runtime capability gate)
**Probe date:** 2026-09-09
**Host:** macOS 26.6 (build 25G72), arm64 (Apple silicon)
**Implementation checkout:** `<implementation-checkout>`
**Repository:** `github.com/gnanam1990/accessforge` (public), default branch `main`

Rows are of three different kinds and must not be read as if they were one. Most record a
**command that was actually executed** and the output actually observed. Some record the
**absence of a configuration file or binary**, which establishes that a checked source was not
present — not that no such capability could exist by another route. One (§9) records an
**owner decision that has not been made**, which no command can settle. Where a capability was not
probed at all, the row says so rather than implying a result.

Status values are **GO** (proven usable now), **PARTIAL** (present but not yet usable for its
required proof), and **BLOCKED** (cannot be used until a named external action occurs).
**NOT YET CONFIGURED** is not a capability status; it describes repository state and is used only
for CI in §10.

---

## 1. Summary

| # | Capability | Status | Blocks |
|---|---|---|---|
| 1 | Implementation checkout and Git identity | GO | — |
| 2 | Node / pnpm runtime | GO | — |
| 3 | Python / uv runtime | GO | — |
| 4 | PostgreSQL (authoritative business state) | GO | — |
| 5 | Guidepup adapter availability (registry) | GO | — |
| 6 | Browsers for journey execution | PARTIAL | pinned E0 browser profile |
| 7 | S3-compatible object storage | PARTIAL | modules 10, 17 real-artifact proof |
| 8 | **macOS VoiceOver actual-AT control** | **BLOCKED** | modules 08, 12; all E0 acceptance |
| 9 | **Windows NVDA actual-AT control** | **BLOCKED** | module 09; R1 completion |
| 10 | **Strands / Bedrock model access** | **BLOCKED** | modules 12, 13, 14 real-model proof |
| 11 | **Authorized target application** | **BLOCKED** | modules 05, 08; all E0 acceptance |
| 12 | Remote CI | n/a — repository state, see §10 | introduced by module 01 |

Four blockers are load-bearing. Three of them (8, 10, 11) sit directly on the E0 critical path,
so **E0 cannot be completed from the current environment** regardless of how much code lands.
Blocker 9 additionally prevents any honest claim of full R1.

---

## 2. Environment and runtime

| Item | Observed | Command |
|---|---|---|
| Operating system | macOS 26.6, build 25G72 | `sw_vers` |
| CPU architecture | arm64 | `uname -m` |
| Xcode command line tools | `<external-volume>/Xcode.app/Contents/Developer` | `xcode-select -p` |
| Node.js | v22.23.1 | `node -v` |
| npm | 10.9.8 | `npm -v` |
| pnpm | 11.10.0 | `pnpm -v` |
| Python | 3.13.14 | `python3 -V` |
| uv | 0.11.28 (aarch64-apple-darwin) | `uv --version` |
| Git | 2.55.0 | `git --version` |

**Status: GO** for Node and Python toolchains.

These are the versions *present*, not versions *certified*. Module 01 owns pinning the supported
matrix; nothing here asserts that AccessForge's eventual dependency set builds on Node 22.23.1 or
Python 3.13.14. Notably, Python 3.13 is recent enough that individual pinned dependencies may not
publish arm64 wheels — that must be discovered by module 01's actual lockfile resolution, not
assumed here.

---

## 3. PostgreSQL — GO

| Item | Observed | Command |
|---|---|---|
| Client version | PostgreSQL 17.10 (Homebrew) | `psql --version` |
| Server reachable | `/tmp:5432 - accepting connections` | `pg_isready` |
| Server version | PostgreSQL 17.10 on aarch64-apple-darwin24.6.0 | `psql -d postgres -tAc "select version();"` |
| Connected role | local development superuser (name redacted) | `psql -d postgres -tAc "select current_user;"` |
| Service management | `postgresql@17 started` via launchd | `brew services list` |

A real local PostgreSQL server is running and accepting connections. Integration proof that
requires authoritative business state (CONTRACTS §"PostgreSQL is authoritative for jobs,
transitions, outbox and idempotency") can be executed locally without an in-memory substitute.

Not yet done: no AccessForge role, database, or schema exists. Module 01 owns creating a dedicated
non-superuser role and database rather than reusing the local superuser login.

---

## 4. Object storage — PARTIAL

| Item | Observed | Command |
|---|---|---|
| MinIO server binary | not present | `command -v minio` |
| MinIO client (`mc`) | not present | `command -v mc` |
| Docker CLI | 29.6.1 | `docker --version` |
| Docker daemon | **not running** — `Cannot connect to the Docker daemon at unix://<user-home>/.colima/default/docker.sock` | `docker info` |
| Colima | installed but stopped — `colima is not running` | `colima status` |

Docker is installed via Colima and is startable with `colima start`, which would then make a
containerised MinIO viable. The daemon was deliberately **not** started during this gate: module 00
must not change machine state to make its own result look better, and nothing yet needs it.

Consequence: evidence-artifact modules (10, 17) can begin schema and contract work, but their
real object-store boundary proof is not yet available. Per SESSION-HEADER §12 this must not be
hidden behind a filesystem fallback presented as object-store success.

---

## 5. Screen reader control — the critical path

### 5.1 macOS VoiceOver — BLOCKED

| Item | Observed | Command |
|---|---|---|
| VoiceOver application | present | `ls -d /System/Library/CoreServices/VoiceOver.app` |
| VoiceOver process | not running | `pgrep -x VoiceOver` |
| Legacy prefs (`~/Library/Preferences/com.apple.VoiceOver4/`) | **absent** | `ls -la` |
| Sequoia+ sandboxed prefs (`~/Library/Group Containers/group.com.apple.VoiceOver/.../com.apple.VoiceOver4/`) | **absent** | `ls -la` |
| `SCREnableAppleScript` | **not set at either path** | `defaults read`, `PlistBuddy -c Print` |
| Accessibility (TCC) grant for a controlling terminal/IDE | **not determinable read-only** | not probed — see below |

**Finding: no VoiceOver configuration was found at either inspected path.** The preferences
container is absent from the legacy path *and* from the sandboxed path that macOS Sequoia (15) and
later use. `SCREnableAppleScript` is therefore unreadable rather than set to `0`, so the "Allow
VoiceOver to be controlled with AppleScript" option is at its default of **off** and Guidepup cannot
drive VoiceOver as things stand.

The evidence supports "not configured at the paths Guidepup and macOS use", which is what blocks the
run. It does not support the stronger claim that VoiceOver was never configured at any time — a
deleted or relocated container would look identical from here, and that distinction is not needed to
establish the blocker.

The initial probe in this session checked only the legacy path. That was wrong for macOS 26.6 and
was corrected by re-probing the sandboxed Group Containers path; the conclusion is the same at both
locations, and both commands are recorded above rather than only the corrected one.

Accessibility permission was **not** probed. The TCC database is SIP-protected and unreadable
without elevation, and attempting a grant-triggering call would raise a system permission dialog on
the user's active desktop. Module 00 explicitly forbids toggling Accessibility permissions or
disrupting the desktop, so this is recorded as *unknown*, not as *absent* and not as *present*.

**No actual-reader smoke probe was run.** Module 00 authorises one only "if authorized and already
configured". It is not configured, and starting VoiceOver would begin speaking aloud on the owner's
active desktop. Attempting it would also have required enabling the very setting this gate is
supposed to report on.

#### Remediation (owner action, cannot be performed by the agent)

Sources retrieved 2026-09-09:
[Guidepup manual VoiceOver setup](https://www.guidepup.dev/docs/guides/manual-voiceover-setup),
[runner-images #11257 (Sequoia path change)](https://github.com/actions/runner-images/issues/11257),
[Apple: VoiceOver Utility General settings](https://support.apple.com/guide/voiceover/general-category-cpvougen/mac).

1. Open **VoiceOver Utility → General** and tick **"Allow VoiceOver to be controlled with AppleScript"**.
   (Opening VoiceOver Utility for the first time creates the preferences container observed missing above.)
2. Open **System Settings → Privacy & Security → Accessibility** and add *and tick* the exact
   application that will execute the Guidepup process — the terminal or IDE, not `node` itself.
3. Grant **Automation** permission to that same application when macOS prompts on first control attempt.
4. Provide a **dedicated interactive desktop session** for runs. VoiceOver speaks aloud and takes
   over keyboard focus; it must not run on the owner's working desktop during ordinary use. A
   locked screen or a headless session cannot drive VoiceOver.

**Safe stop procedure for any future run:** `Cmd+F5` toggles VoiceOver off; `guidepup`'s
`voiceOver.stop()` should be called in a test teardown that runs even on failure; if a run wedges
with VoiceOver still speaking, `Cmd+F5` or `pkill -x VoiceOver` restores the desktop. Runs must
never be started on a desktop the owner is actively using.

### 5.2 Windows NVDA — BLOCKED, no path from this host

| Item | Observed | Command |
|---|---|---|
| Kernel | `Darwin` | `uname -s` |
| UTM | not installed | `ls -d /Applications/UTM.app` |
| Parallels Desktop | not installed | `ls -d "/Applications/Parallels Desktop.app"` |
| VMware Fusion | not installed | `ls -d "/Applications/VMware Fusion.app"` |
| VirtualBox | not installed | `ls -d /Applications/VirtualBox.app` |
| qemu | not present | `command -v qemu-system-x86_64` |

There is no Windows machine and no virtualization host installed on this Mac. NVDA cannot be run,
and per SESSION-HEADER §4 and INV-02 no macOS observation may be relabelled as a Windows result.

This blocks **module 09** outright and therefore blocks any claim of **full R1**. It does not block
E0, and it does not block foundation work in modules 01–07 that is platform-independent.

### 5.3 What the virtual screen reader does *not* buy us

`@guidepup/virtual-screen-reader` (0.32.1) is **available from the registry**. It was not installed
and not run here, so this section claims nothing about what it would output.

The point rests on policy, not on a result: it simulates a reader rather than driving one, so under
**INV-02** — *"Missing AT capability/evidence never becomes PASS or an automatic confirmed defect"*
— its output could not constitute E0 proof at any value. It is admissible only as a labelled
unit-level fake (SESSION-HEADER §3), never as the actual-AT boundary.

---

## 6. Browsers — PARTIAL

| Browser | Observed version | Command |
|---|---|---|
| Safari | 26.6 | `defaults read /Applications/Safari.app/Contents/Info.plist CFBundleShortVersionString` |
| Google Chrome | 152.0.7977.83 | same, Chrome bundle |
| Firefox | not installed | bundle absent |
| Microsoft Edge | not installed | bundle absent |
| Playwright browser binaries | none installed (cache holds only `ffmpeg-1011`) | `ls ~/Library/Caches/ms-playwright` |

Two real browsers are present at known versions. No Playwright-managed browser binaries are
installed, so any journey claiming "chromium" today would be running an *unpinned* browser — which
**INV-03** forbids, since every outcome must bind exact runner and build versions. Module 01 owns
pinning the browser profile and installing the matching binaries.

---

## 7. Agent orchestration: Strands and Bedrock — BLOCKED

| Item | Observed | Command |
|---|---|---|
| AWS CLI | **not found** | `command -v aws` |
| `~/.aws/config` | **absent** | `ls` |
| `~/.aws/credentials` | **absent** | `ls` |
| `strands` Python package | not installed | `importlib.util.find_spec('strands')` |
| Caller identity | not attempted (no CLI, no credentials) | — |

None of the credential sources checked above were observed, and Bedrock access was **not verified**.
Environment variables were deliberately not enumerated, per the module's explicit prohibition, so
this is not a proof that no credential of any kind exists — an environment-injected key or an
attached role would not have been visible to these checks. What is established is narrower and
sufficient: no verified path to a real model invocation exists, and none may be assumed.

This blocks the *real-model* proof for modules 12 (Strands navigator), 13 (diagnosis) and 14 (patch
proposal). It does not block their schemas, tool-restriction logic, supervisor allowlists, or
adversarial tests against recorded fixtures — those are legitimate independent work.

**Note on authority:** configuring AWS access would create billable model invocations. Per
SESSION-HEADER §10 that requires the owner's specific approval and is not implied by this gate.

---

## 8. Guidepup adapter availability — GO (availability only)

| Package | Latest version on registry |
|---|---|
| `@guidepup/guidepup` | 0.34.0 |
| `@guidepup/playwright` | 0.19.1 |
| `@guidepup/virtual-screen-reader` | 0.32.1 |
| `@guidepup/setup` | 0.25.3 |

Retrieved 2026-09-09 via `npm view <pkg> version`. The unscoped name `guidepup` is **not** a valid
package (registry returned `E404`); the core package is `@guidepup/guidepup`.

This row proves the packages are *obtainable*. It does not prove any adapter method exists or
behaves as expected — no adapter API was invoked, and none is described from memory here.

---

## 9. Authorized target application — BLOCKED (owner decision)

No target application, backend, seeded test account, or permitted-effects inventory has been
supplied. AccessForge's E0 slice requires exactly one authorized application with a real backend
and a form-error recovery journey.

Per module 00 task 9 and SESSION-HEADER §10, public reachability of some website is **not**
permission to automate it. This cannot be resolved by picking a site; it needs the owner to name an
application they control or are explicitly authorized to test, along with the effects permitted on it.

---

## 10. Remote CI — NOT YET CONFIGURED

No CI workflow exists in this repository. Module 01 owns introducing meaningful CI on pull-request
and main-push triggers. Under MASTER-BUILD-AND-MERGE §2 the module-00 documentation-only delivery
is the single permitted pre-CI exception, and it is recorded here as **NOT YET CONFIGURED — never
passed**, not as skipped or green.

---

## 11. Negative verification

Module 00 requires demonstrating that the following cannot be reported as real E0 proof.

| Claim that must fail | Why it fails here | Basis |
|---|---|---|
| "Linux-only environment proves E0" | Host is macOS; but note that a Linux CI runner has no VoiceOver at all, so an all-green Linux pipeline would prove nothing about actual AT. | INV-02 |
| "Virtual screen reader results prove E0" | The package is registry-available but was not installed or run, so no output is claimed. It simulates a reader rather than driving one, so its result could not be E0 proof at any value — and VoiceOver is not configured to have produced a real one. | INV-02 |
| "Accessibility permission is fine" | Not established. The VoiceOver preferences container does not exist at either the legacy or the Sequoia+ sandboxed path; the grant state is recorded as unknown, not assumed. | INV-02 |
| "Browser version is known" | No Playwright binaries are installed. A run today would bind no exact browser build. | INV-03 |
| "Model access works" | No AWS CLI, config, or credentials exist. A credential file's mere existence would not have proven service access either. | INV-02, SESSION-HEADER §3 |

**Honest limitation of this section.** These are evidence-backed documentary demonstrations, not
executed guard tests — this repository has no test harness yet, because module 01 owns creating it.
The corresponding *executable* negative tests (a run that asserts an unconfigured reader yields
BLOCKED/INCONCLUSIVE rather than PASS) are owed by modules 01 and 08 and are recorded as
outstanding in `docs/delivery/STATUS.md`.

---

## 12. Blocked-module map

| Blocker | Blocks outright | Permits independent work on |
|---|---|---|
| VoiceOver not configured | 08 real proof, 12 real navigation, E0 acceptance | 01–07, 10, 11 schemas/reducers/logic |
| No Windows host | 09, full R1 | everything else |
| No AWS/Bedrock access | 12, 13, 14 real-model proof | their schemas, tool allowlists, supervisor fences, fixture tests |
| No authorized target app | 05 real manifests, 08 journeys, E0 acceptance | contracts, DSL, runner control plane |
| No object store running | 10, 17 real-artifact proof | their schemas and ingestion logic |

**Module 01 may begin.** It depends only on module 00 and needs none of the blocked capabilities.
