-- Original worker measurements and authored conditions, published only with successful cleanup.
-- NULL on historical attempts: no backfill from check names or current rules.
ALTER TABLE candidate_regression_attempt ADD COLUMN functional_receipt JSONB;
ALTER TABLE candidate_regression_attempt ADD CONSTRAINT functional_receipt_completed CHECK (
 functional_receipt IS NULL OR
 (state='PASSED' AND jsonb_typeof(functional_receipt)='object')
);

CREATE OR REPLACE FUNCTION preserve_candidate_regression() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state IN ('PASSED','FAILED','UNKNOWN') OR
   (to_jsonb(NEW) - ARRAY['state','epoch','dispatched_at','finished_at','cleanup_confirmed',
                        'checks','failure_code','functional_receipt']) IS DISTINCT FROM
   (to_jsonb(OLD) - ARRAY['state','epoch','dispatched_at','finished_at','cleanup_confirmed',
                        'checks','failure_code','functional_receipt']) THEN
   RAISE EXCEPTION 'regression input or terminal record is immutable'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NOT ((OLD.state='CLAIMED' AND NEW.state IN ('DISPATCHED','FAILED','UNKNOWN')) OR
         (OLD.state='DISPATCHED' AND NEW.state IN ('PASSED','FAILED','UNKNOWN'))) THEN
   RAISE EXCEPTION 'invalid regression transition' USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
