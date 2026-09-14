-- Original protected measurements, not a reader verdict. Never backfill old runtime records.
ALTER TABLE baseline_regression_attempt ADD COLUMN functional_receipt JSONB
 CHECK(functional_receipt IS NULL OR (state='PASSED' AND jsonb_typeof(functional_receipt)='object'));

CREATE OR REPLACE FUNCTION preserve_baseline_regression() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state IN ('PASSED','FAILED','UNKNOWN') OR
   (to_jsonb(NEW)-ARRAY['state','epoch','dispatched_at','finished_at','cleanup_confirmed',
                       'checks','validation','failure_code','functional_receipt']) IS DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['state','epoch','dispatched_at','finished_at','cleanup_confirmed',
                       'checks','validation','failure_code','functional_receipt']) OR
   NOT ((OLD.state='CLAIMED' AND NEW.state IN ('DISPATCHED','FAILED','UNKNOWN')) OR
        (OLD.state='DISPATCHED' AND NEW.state IN ('PASSED','FAILED','UNKNOWN'))) THEN
   RAISE EXCEPTION 'baseline regression identity, transition or terminal receipt is immutable'
    USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
