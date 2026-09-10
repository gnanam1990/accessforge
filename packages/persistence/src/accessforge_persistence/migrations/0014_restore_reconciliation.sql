-- Module 27: a lease ended by a restore says so, rather than borrowing another reason.
--
-- Reconciliation after a restore releases every lease that the backup believed was live. The
-- existing reasons all assert something this situation cannot support:
--
--   * COMPLETED and STOP_ACKNOWLEDGED claim the supervisor finished or acknowledged a stop. Neither
--     is known: the supervisor is on a machine this database can no longer see.
--   * EXPIRED_WITHOUT_STOP_PROOF claims a deadline passed while the server watched. The server was
--     not watching; it was restoring.
--   * AMBIGUOUS_ACTION is the closest, and it is the one that would be reached for. It says a
--     specific dispatched action has an unknown result, which is a claim about that lease. Using it
--     here would put every restored lease into the same bucket an operator searches when hunting a
--     real ambiguous action, and the two need different follow-up.
--   * OPERATOR_RESET and SUPERSEDED_BY_RESET both claim a person or a newer lease intervened.
--
-- So RESTORED_DATABASE. An operator reading the journal can tell "this lease ended because the
-- database was rolled back" from "this lease ended because we do not know what the desktop did",
-- and only the second needs a human to go and look at a machine.
--
-- Widening a CHECK constraint is forward-compatible in both directions that matter: rows written by
-- older code still satisfy it, and the only rows that would violate the old constraint are ones
-- this release writes. That makes it a safe subject for the forward-migration drill, which is
-- deliberate — a migration test that only ever ran on a no-op would prove nothing.

ALTER TABLE desktop_lease DROP CONSTRAINT IF EXISTS desktop_lease_release_reason_check;

ALTER TABLE desktop_lease ADD CONSTRAINT desktop_lease_release_reason_check
    CHECK (release_reason IN (
        'COMPLETED', 'STOP_ACKNOWLEDGED', 'EXPIRED_WITHOUT_STOP_PROOF', 'AMBIGUOUS_ACTION',
        'OPERATOR_RESET', 'SUPERSEDED_BY_RESET', 'RESTORED_DATABASE'
    ));
