# Complete a stopped execution without replaying it

After the authenticated supervisor has acknowledged STOP and all independent producers have
closed, retain the original private journal and derive the final evaluation with one command:

```sh
uv run python -m accessforge_orchestrator.complete_execution \
  --workspace-id WORKSPACE_UUID --run-id RUN_UUID --journal /private/original-journal.ndjson
```

Use the operator's existing private database and evidence-store environment. This command does
not build, reset a fixture, launch a reader, invoke a model, grant approval or dispatch actions.
It first reads an existing immutable evaluation. Otherwise it reads a bounded, owner-only regular
journal file, resumes create-only artifact retention and calls the deterministic finalizer.
The normal exact STOP, closed-stream, original source and required-artifact checks still apply.
It supplies no verdict, expected identity or replacement assertion.

For a bound baseline, invoke this **after** `execute_baseline_session` returns successfully,
not inside its reader callback: the protected runtime must finish its regressions and publish
its original functional receipt before the required functional artifact can be retained.
Missing functional evidence refuses completion; it is not omitted to make the run succeed.

On `COMPLETION_UNCONFIRMED`, do not restart the desktop session or original one-shot runtime.
Correct the store/spool issue and invoke this command again with the identical original journal.
A lost acknowledgement may have committed objects or an evaluation. There is no internal retry:
the next invocation first reconciles any original evaluation, otherwise resumes tracked quarantine.
A concurrent completion may cause one invocation to refuse; subsequent reconciliation reads the
winner's immutable snapshot. Deleted or conflicting evidence is never rewritten by this command.

`ORIGINAL_EVALUATION_SNAPSHOT` means historical evaluation, including INCONCLUSIVE or failure.
Replay does not re-check present-day object retention and no longer requires the spool file.
It is not proof of an actual reader run, repair verification or production readiness.

Focused tests cover call ordering, failures without retries, bounded private spool handling and
historical replay. Existing PostgreSQL/S3 finalizer integration also exercises this composition;
its desktop observations remain synthetic. Full baseline-to-finalizer retention and actual
VoiceOver acceptance remain separate pending evidence.
