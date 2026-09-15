# READ_CURRENT uses an actual reader command

Original module 08's READ_CURRENT mapping previously returned Guidepup itemText(), which the
installed 0.34.0 VoiceOverClient implements as a cached item-text log tail. It did not issue a
fresh describe/read command and could return a previous item under a new action identity.

The adapter's private client port now has readCurrent(). The production factory binds this to
the pinned SDK's perform(keyboardCommands.describeItem): the installed keyCodeCommands table
identifies it as VO-F3, describing the item in the VoiceOver cursor. It is a single fixed mapping
inside the already admitted READ_CURRENT action, not an expansion of the KEY_CHORD allowlist.
The arbitrary perform API, other keyboard commands and cached itemText are not exposed through
the narrow client port. SDK methods retain their original receiver binding.

READ_CURRENT now goes through the same bounded speech-log comparison as other actions. Missing,
empty, repeated or replaced samples remain CAPTURE_UNKNOWN, rather than borrowing cached text.
This is conservative for an actual repeated utterance: the SDK log does not establish independent
acoustic freshness. Existing journal, lease, focus/effect authority and verified-profile gates
remain in front of the command. No physical command was executed in this change.

Validation: adapter and runner TypeScript compilation; runtime and adapter files pass 72 focused
tests using synthetic clients. New cases prove that the cached accessor is not called and that
no-new-sample remains unknown. These checks do not establish a real VoiceOver trace or prove the
pinned host qualification. The live capture preflight and full baseline acceptance remain open.
