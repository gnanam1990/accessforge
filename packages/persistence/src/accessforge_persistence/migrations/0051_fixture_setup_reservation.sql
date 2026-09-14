-- Setup is reserved before a desktop lease; an unknown HTTP result never admits the desktop.
CREATE TABLE fixture_setup_reservation (
 run_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 context JSONB NOT NULL CHECK(jsonb_typeof(context)='object' AND octet_length(context::text)<=8192),
 context_digest TEXT NOT NULL CHECK(context_digest ~ '^[0-9a-f]{64}$'),
 observation JSONB CHECK(jsonb_typeof(observation)='object' AND octet_length(observation::text)<=8192),
 observation_digest TEXT CHECK(observation_digest ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 observed_at TIMESTAMPTZ,
 CHECK ((observation IS NULL AND observation_digest IS NULL AND observed_at IS NULL)
     OR (observation IS NOT NULL AND observation_digest IS NOT NULL AND observed_at IS NOT NULL)),
 FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE
);
ALTER TABLE fixture_setup_reservation ENABLE ROW LEVEL SECURITY;
ALTER TABLE fixture_setup_reservation FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON fixture_setup_reservation
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION guard_fixture_setup_reservation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) THEN
     RAISE EXCEPTION 'fixture setup reservation cannot be erased';
   END IF;
   RETURN OLD;
 END IF;
 IF TG_OP='INSERT' AND NEW.observation IS NOT NULL THEN
   RAISE EXCEPTION 'fixture setup must first reserve an unconfirmed context';
 END IF;
 PERFORM id FROM run WHERE id=NEW.run_id AND workspace_id=NEW.workspace_id
   AND status='QUEUED' AND cancel_requested_at IS NULL AND NOT quarantined FOR UPDATE;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM desktop_lease WHERE run_id=NEW.run_id) THEN
   RAISE EXCEPTION 'fixture setup requires an unleased queued run';
 END IF;
 IF TG_OP='UPDATE' AND (
     OLD.observation IS NOT NULL OR NEW.observation IS NULL
     OR (to_jsonb(NEW)-'observation'-'observation_digest'-'observed_at')
        IS DISTINCT FROM (to_jsonb(OLD)-'observation'-'observation_digest'-'observed_at')) THEN
   RAISE EXCEPTION 'fixture setup context and confirmed observation are immutable';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER fixture_setup_guard BEFORE INSERT OR UPDATE OR DELETE ON fixture_setup_reservation
 FOR EACH ROW EXECUTE FUNCTION guard_fixture_setup_reservation();

CREATE FUNCTION guard_lease_fixture_setup() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 -- Serialize with reservation insertion, not just with a row that may not exist yet.
 PERFORM id FROM run WHERE id=NEW.run_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM fixture_setup_reservation
           WHERE run_id=NEW.run_id AND observation IS NULL) THEN
   RAISE EXCEPTION 'fixture setup is unresolved; desktop lease refused';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER lease_fixture_setup_guard BEFORE INSERT ON desktop_lease
 FOR EACH ROW EXECUTE FUNCTION guard_lease_fixture_setup();
