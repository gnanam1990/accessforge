# Private native keyboard-focus measurement

`createSafariKeyboardFocusProbe` explicitly requests keyboard-focus metadata from the existing
owned Swift helper. Construction is inert. Sampling is read-only: it sends no AX actions, Apple
Events, keys, focus changes, reader startup or permission prompts. The origin-only probe and its
operator diagnostic keep their original request/output contract.

The native sample requires foreground signed Safari, its exact version, the exact owned fixture
document and nonmodal focused window. It reads the application's focused UI element, requires its
`AXFocused` flag, matches its containing window and walks at most 32 parents to require a web-area
ancestor. Toolbar focus, missing attributes, unsupported roles or missing identifiers are UNKNOWN.
Only text fields/areas, buttons, checkboxes, radio buttons, popups, combos and links are supported.

The focus element, role and identifier are sampled again, with final browser/window/document checks.
This is a bounded observation, not an atomic OS lock: undetected ABA changes remain possible.
The existing client binds PID plus launch identity, fences replacement/concurrent/failed probes,
and enforces the 1.5-second/16-KiB helper limits. Missing focus never falls back to spoken text.

Only origin, AX role, measurement-kind and SHA-256 of
`accessforge.keyboard-focus-identifier.v1` + NUL + the UTF-8 native identifier are returned to the
private controller. No input value, title, raw identifier, tree or selector is returned. A digest
is not anonymization or uniqueness proof; native identifier stability and uniqueness require
qualification against the exact fixture/profile before a predicate can rely on them.

This collector is **not yet action-bound retained evidence** and creates no focus outcome. It is
not passed to the navigator. Next integration must bind original action/lease/manifest timing,
retain the authenticated sample, define the protected predicate and independently qualify native
Safari accessibility behavior. A missing native identifier must stay UNKNOWN, not be synthesized
from an HTML source field or a reader phrase. Actual VoiceOver acceptance remains unproven.

Verification: TypeScript build and Swift native compile passed; 31 scoped synthetic client/origin
checks passed. The helper was compiled, not sampled against Safari; no actual AT startup occurred.
Required CI remains separate from these checks.

Apple documents [AXFocused](https://developer.apple.com/documentation/applicationservices/kaxfocusedattribute)
as keyboard focus and [AXWindow](https://developer.apple.com/documentation/applicationservices/kaxwindowattribute)
as the containing window. Those definitions guide collection; they do not prove this host's
Safari compatibility.
