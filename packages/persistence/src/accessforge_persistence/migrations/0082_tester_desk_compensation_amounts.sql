-- Briefs store safe-integer minor units, including explicitly unpaid assignments.
ALTER TABLE td_payable ALTER COLUMN amount_minor TYPE BIGINT;
ALTER TABLE td_ledger_entry ALTER COLUMN amount_minor TYPE BIGINT;
ALTER TABLE td_payable DROP CONSTRAINT td_payable_amount_minor_check;
ALTER TABLE td_payable ADD CHECK (amount_minor > 0 OR (amount_minor = 0 AND status = 'SETTLED'));
