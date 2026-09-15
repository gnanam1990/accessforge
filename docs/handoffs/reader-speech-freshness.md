# Reader speech-log freshness correction

Scope: original module 08 observation provenance. While tracing the missing live capture
preflight, inspection of the installed Guidepup 0.34.0 VoiceOverClient showed that
lastSpokenPhrase returns the stored speech log tail. It is not an independent live capture
health probe. No such readiness flag was added.

The action adapter previously attached that cached tail to NEXT/PREVIOUS/ACTIVATE/TYPE_TEXT/
KEY_CHORD even when no new speech event followed the action. Those actions now snapshot the
bounded speech log before dispatch and require an appended event with an unchanged prefix
afterwards. An unchanged or replaced history yields CAPTURE_UNKNOWN, not an empty success or
an earlier utterance with a new action identity. The snapshot is copied because the SDK stores
a mutable array. A newly appended identical phrase is still a new event; an explicitly appended
empty string remains distinct from no event. Logs remain private and are not navigator output.

This checks event appearance across the action boundary, not acoustic output or causal
attribution against every possible background announcement. READ_CURRENT remains the separate
current-item API and WAIT_FOR_READER_IDLE retains its existing behavior. Errors or oversized
logs remain unavailable; no automatic replay is introduced.

API provenance: installed pinned Guidepup VoiceOverClient.js and the official
[Guidepup examples](https://github.com/guidepup/guidepup) expose spokenPhraseLog and
lastSpokenPhrase. No SDK version change, setup command, OS permission change or actual reader
execution occurred.

Validation: adapter and runner TypeScript compilation; 14 focused runtime tests using explicitly
synthetic clients, including cached text, replaced history and repeated appended text. These are
not actual VoiceOver traces. Physical capture health and complete G2 acceptance remain pending.
