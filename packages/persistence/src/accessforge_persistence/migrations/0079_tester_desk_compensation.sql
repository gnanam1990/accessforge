-- Tester Desk M05: payables and the local compensation ledger.
-- INV-05 is a unique constraint, not a convention: at most one compensation
-- intent can ever exist for an accepted assignment.

CREATE TABLE td_payable (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    assignment_id UUID NOT NULL,
    tester_id UUID NOT NULL,
    commission_id UUID NOT NULL,
    amount_minor INT NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    payout_reference TEXT NOT NULL,
    -- INTENT: persisted before any dispatch. DISPATCHED: the adapter was invoked and the
    -- outcome is not yet recorded -- a worker that dies here leaves the payable stuck, and
    -- stuck is correct: dispatching again could pay twice. UNKNOWN: the adapter reported an
    -- uncertain outcome with a reference to reconcile. SETTLED: confirmed, exactly once.
    status TEXT NOT NULL DEFAULT 'INTENT' CHECK (status IN
        ('INTENT','DISPATCHED','UNKNOWN','SETTLED','CANCELLED')),
    provider_reference TEXT,
    dispatch_attempts INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    dispatched_at TIMESTAMPTZ,
    settled_at TIMESTAMPTZ,
    -- INV-05 in the schema: one accepted assignment, at most one compensation intent.
    UNIQUE (assignment_id),
    UNIQUE (id, workspace_id),
    FOREIGN KEY (assignment_id, workspace_id)
        REFERENCES td_assignment(id, workspace_id),
    FOREIGN KEY (tester_id, workspace_id) REFERENCES td_tester(id, workspace_id),
    FOREIGN KEY (commission_id, workspace_id)
        REFERENCES td_commission(id, workspace_id)
);
ALTER TABLE td_payable ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_payable FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_payable
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
CREATE INDEX td_payable_status ON td_payable (workspace_id, status)
    WHERE status = 'INTENT';

-- The local-ledger adapter's books. It stands in for the real provider: same dispatch
-- contract, same reconcile-by-reference flow, no external network. A live provider adapter
-- slots into the same seam once a seller route and authority exist.
CREATE TABLE td_ledger_entry (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    payable_id UUID NOT NULL,
    reference TEXT NOT NULL,
    amount_minor INT NOT NULL,
    currency TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (workspace_id, reference),
    UNIQUE (payable_id),
    FOREIGN KEY (payable_id, workspace_id) REFERENCES td_payable(id, workspace_id)
);
ALTER TABLE td_ledger_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_ledger_entry FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_ledger_entry
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
