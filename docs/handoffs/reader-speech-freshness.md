# Reader speech-log freshness correction

Scope: original module 08 observation provenance. While tracing the missing live capture
preflight, inspection of the installed Guidepup 0.34.0 VoiceOverClient showed that
lastSpokenPhrase returns the stored speech log tail. It is not an independent live capture
health probe. No such readiness flag was added.

The action adapter previously attached that cached tail to NEXT/PREVIOUS/ACTIVATE/TYPE_TEXT/
KEY_CHORD even when no new speech event followed the action. Those actions now snapshot the
bounded speech log before dispatch and require an appended sample with an unchanged prefix
afterwards. An unchanged or replaced history yields CAPTURE_UNKNOWN, not an empty success or
an earlier utterance with a new action identity. The snapshot is copied because the SDK stores
a mutable array. Further inspection of the SDK capture loop showed that an append is only a
sample: polling can return previous text or an empty string after swallowed capture errors.
Therefore an identical tail or an empty/whitespace-only appended value also remains UNKNOWN.
This can conservatively classify a genuinely repeated utterance or real silence as unknown;
the SDK sample lacks independent evidence to distinguish those cases. Logs stay private.

This checks a distinguishable sample across the action boundary, not acoustic output or causal
attribution against every possible background announcement. READ_CURRENT remains the separate
current-item API and WAIT_FOR_READER_IDLE retains its existing behavior. Errors or oversized
logs remain unavailable; no automatic replay is introduced.

API provenance: installed pinned Guidepup VoiceOverClient.js and the official
[Guidepup examples](https://github.com/guidepup/guidepup) expose spokenPhraseLog and
lastSpokenPhrase. No SDK version change, setup command, OS permission change or actual reader
execution occurred.

Validation: adapter and runner TypeScript compilation; 15 focused runtime tests using explicitly
synthetic clients, including cached text, replaced history, empty/repeated samples and a changed
sample appended to the same mutable array. These are
not actual VoiceOver traces. Physical capture health and complete G2 acceptance remain pending.
