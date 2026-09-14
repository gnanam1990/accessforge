-- A fresh-fixture environment cannot bypass setup by omitting the reservation entirely.
-- Keep old environment strategies unchanged; never invent historical setup evidence.
CREATE FUNCTION fixture_setup_unresolved(target_run UUID) RETURNS BOOLEAN
LANGUAGE SQL STABLE AS $$
 SELECT EXISTS (
   SELECT 1 FROM fixture_setup_reservation f
   WHERE f.run_id=target_run AND f.observation IS NULL
 ) OR (
   EXISTS (
     SELECT 1 FROM sealed_manifest s
     JOIN environment_manifest e ON e.id=s.environment_manifest_id
       AND e.workspace_id=s.workspace_id
     WHERE s.run_id=target_run AND e.fixture_reset_strategy='FRESH_FIXTURE_NONCE'
   ) AND NOT EXISTS (
     SELECT 1 FROM fixture_setup_reservation f
     WHERE f.run_id=target_run AND f.observation IS NOT NULL
   )
 );
$$;

CREATE OR REPLACE FUNCTION guard_lease_fixture_setup() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 -- Same lock as reservation creation/confirmation, including the absent-row boundary.
 PERFORM id FROM run WHERE id=NEW.run_id FOR UPDATE;
 IF fixture_setup_unresolved(NEW.run_id) THEN
   RAISE EXCEPTION 'fixture setup is unresolved; desktop lease refused';
 END IF;
 RETURN NEW;
END;
$$;
