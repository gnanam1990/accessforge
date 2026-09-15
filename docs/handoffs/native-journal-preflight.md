# Native journal preflight connection

Scope: original module 08 task 6 and build-flow audit F1. The production execution bootstrap
now replaces caller-supplied journalWritable with a current FileJournal append-descriptor
and file/directory fsync check. A memory journal or failed filesystem probe cannot establish
this physical gate. The check occurs inside the existing bounded physical preflight, after
profile/startup authorization, and checks cancellation before and after measurement.

No synthetic action or completion record is written. A first probe may create the empty
private journal; existing bytes are retained. Both probe and subsequent action append refuse
non-regular files, final-component symbolic links, multiple hard links, foreign ownership and
group/other permissions. Actual action append still flushes before dispatch; a successful
preflight cannot cache permission to bypass later write failures. Trusted host directory
ownership remains an operator provisioning requirement; this is not isolation from malicious
same-user path replacement or a guarantee that future storage writes will succeed.

Validation: installed TypeScript compiler passes; journal preflight, execution bootstrap and
existing supervisor files pass 32 tests, none skipped. New tests exercise actual temporary
filesystem flushes, unchanged content, insecure mode and link rejection. No reader, provider,
live database or deployment was used. This does not prove real platform acceptance.

Still unfinished: operator entrypoint/configuration, live speech capture and stale input-source
measurement, independent forbidden-effect coverage, followed by real G2 baseline acceptance.
Do not substitute TRUE defaults for these remaining runtime evidence inputs.
