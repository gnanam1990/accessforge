-- In a BEFORE DELETE trigger NEW is NULL. Returning it silently cancels deletion,
-- including an FK cascade, leaving a run whose parent workspace was deleted.
-- Terminal immutability still rejects both UPDATE and DELETE; only the nonterminal
-- DELETE return value changes. Existing orphaned data is not guessed at or deleted.
CREATE OR REPLACE FUNCTION refuse_terminal_run_mutation() RETURNS TRIGGER
    LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('COMPLETED', 'INTERRUPTED', 'CANCELLED') THEN
        RAISE EXCEPTION
            'run % is terminal in % and is immutable; a retry creates a new linked run',
            OLD.id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
