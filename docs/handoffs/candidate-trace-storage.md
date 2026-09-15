# Candidate trace storage

The file-backed first-reader qualification sink now requires a new file in an existing canonical,
private, current-user-owned directory. It no longer creates parent directories or appends a new
attempt to an existing trace. Provision the directory independently and resolve its canonical
path before constructing the sink; the trace file itself must not already exist.

The same sink instance pins the parent and file identities, requires a private regular single-link
file with the expected length, completes the entire UTF-8 line write, and fsyncs both file and
directory before acknowledging retention. Symlinks, replacement, external appends, permission
changes and uncertain writes fence that instance. Retain partial files for reconciliation; do not
delete them to replay a proof. This is a trusted same-user storage boundary, not isolation from a
malicious process with the same user's filesystem authority.

Focused TypeScript compilation and 21 candidate trace/proof checks pass, including existing-file
and symlink refusal, same-length inode replacement, permanent fencing, private-directory refusal,
and the existing candidate action/cleanup suite. These checks are synthetic/local filesystem
evidence. No VoiceOver action was performed, no canonical reader proof was minted, and the
verified matrix remains unchanged. Concrete candidate/operator provisioning is still required.
